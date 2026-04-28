"""Admin blueprint — users, documents, status and reindex."""

from collections import Counter
from datetime import date, datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from domain.error_catalog import flash_error_message

from core import services
from core.whatsapp_support import build_user_whatsapp_view, verify_user_whatsapp
from core.validators import (
    validate_email,
    validate_feedback_status,
    validate_operational_feedback_status,
    validate_password,
    validate_phone,
    validate_required_text,
    validate_role,
    validate_whatsapp_phone,
)
from domain.knowledge_companions import companion_directory, load_document_companion
from domain.knowledge_evals import evaluate_companion_cases, load_eval_cases_from_dir, load_eval_cases_from_store
from domain.practice_experience import (
    clear_practice_experience_records,
    delete_practice_experience_record,
    list_practice_experience_records,
    load_practice_experience_records_from_json,
    PRACTICE_EXPERIENCE_KNOWLEDGE_FILENAME,
    practice_experience_state,
    prepare_practice_experience_records_for_import,
    save_practice_experience_records,
    update_practice_experience_feedback,
)
from domain.event_reports import (
    EVENT_REPORT_STATUS_OPTIONS,
    EVENT_REPORT_TAG_OPTIONS,
    event_report_photo_path,
    get_event_report,
    list_event_reports,
    update_event_report,
)
from core.admin_status import load_admin_status
from core.helpers import login_required, role_required
from core.knowledge_runtime import (
    current_reindex_status_payload,
    refresh_knowledge_state,
    safe_rebuild_index,
    start_reindex_job,
)
from core.chat_feedback import sync_feedback_correction_eval_case
from core.bot_insights import (
    build_bot_monitor_snapshot,
    build_exceptions,
    build_learning_signals,
    build_quality_snapshot,
    build_sources_snapshot,
    compute_health_score,
)
from core.bot_settings import DEFAULTS as BOT_SETTINGS_DEFAULTS, load_bot_settings, reset_bot_settings, save_bot_settings
from storage.maneuver_case_helpers import build_case_environment_signature, rank_similar_maneuver_cases
from storage.utils import _local_iso_to_label

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)

PRACTICE_EXPERIENCE_SOURCE_FILENAME = PRACTICE_EXPERIENCE_KNOWLEDGE_FILENAME

BOT_DATABASE_EXPORT_KIND = "pragtico.bot_database_export"
SYSTEM_DATABASE_EXPORT_KIND = "pragtico.system_database_export"
DATABASE_EXPORT_VERSION = 1
ADMIN_CASEBOOK_DEFAULT_LIMIT = 8
ADMIN_CASEBOOK_ALLOWED_LIMITS = (8, 16, 40, 80)
SYSTEM_KNOWLEDGE_ALLOWED_SUFFIXES = {".txt", ".md", ".json"}
POSTGRES_EXPORT_TABLES = {
    "app_users": {
        "pk": ("username",),
        "jsonb": set(),
        "columns": (
            "username",
            "password_hash",
            "role",
            "full_name",
            "organization",
            "email",
            "phone",
            "whatsapp_number",
            "whatsapp_opt_in",
            "whatsapp_opt_in_at",
            "profile_completed_at",
        ),
    },
    "documents": {
        "pk": ("name",),
        "jsonb": set(),
        "columns": (
            "name",
            "original_name",
            "doc_type",
            "size_bytes",
            "updated_at",
            "created_at",
            "uploaded_by",
            "preview",
            "file_path",
        ),
    },
    "port_calls": {
        "pk": ("id",),
        "jsonb": {"maneuver_history"},
        "columns": (
            "id",
            "vessel_name",
            "vessel_short_name",
            "vessel_imo",
            "vessel_call_sign",
            "vessel_flag",
            "vessel_type",
            "vessel_loa_m",
            "vessel_beam_m",
            "vessel_gt_t",
            "vessel_max_draft_m",
            "vessel_dwt_t",
            "vessel_bow_thruster",
            "vessel_stern_thruster",
            "status",
            "approval_status",
            "approval_note",
            "aborted_reason",
            "decided_by",
            "decided_at",
            "eta",
            "ata",
            "planned_departure_at",
            "departure_plan_note",
            "departure_at",
            "planned_shift_at",
            "shift_plan_note",
            "shift_at",
            "shift_origin_berth",
            "shift_destination_berth",
            "shift_approval_status",
            "shift_approval_note",
            "shift_aborted_reason",
            "shift_decided_by",
            "shift_decided_at",
            "maneuver_history",
            "berth",
            "last_port",
            "next_port",
            "created_by",
            "notes",
            "created_at",
            "updated_at",
        ),
    },
    "conversations": {
        "pk": ("id",),
        "jsonb": set(),
        "columns": ("id", "username", "title", "created_at", "updated_at"),
    },
    "messages": {
        "pk": ("id",),
        "jsonb": {"citations", "channel_metadata"},
        "columns": (
            "id",
            "conversation_id",
            "role",
            "content",
            "citations",
            "feedback_status",
            "feedback_note",
            "feedback_correction",
            "feedback_correction_document",
            "feedback_updated_by",
            "feedback_updated_at",
            "channel",
            "channel_user_id",
            "external_message_id",
            "external_reply_to_id",
            "channel_metadata",
            "created_at",
        ),
    },
    "channel_events": {
        "pk": ("id",),
        "jsonb": {"payload"},
        "columns": (
            "id",
            "channel",
            "event_type",
            "username",
            "conversation_id",
            "local_message_id",
            "channel_user_id",
            "external_event_id",
            "external_message_id",
            "payload",
            "created_at",
        ),
    },
    "app_runtime_state": {
        "pk": ("key",),
        "jsonb": {"value"},
        "columns": ("key", "value", "updated_at"),
    },
    "feedback_eval_cases": {
        "pk": ("id",),
        "jsonb": {"expected_substrings"},
        "columns": (
            "id",
            "source_message_id",
            "document",
            "question",
            "expected_answer",
            "expected_substrings",
            "feedback_note",
            "updated_by",
            "source",
            "created_at",
            "updated_at",
        ),
    },
    "maneuver_cases": {
        "pk": ("maneuver_id",),
        "jsonb": {
            "vessel_snapshot",
            "scale_snapshot",
            "planning_snapshot",
            "decision_snapshot",
            "execution_snapshot",
            "outcome_snapshot",
            "environment_snapshot",
            "feature_snapshot",
            "change_log",
        },
        "columns": (
            "maneuver_id",
            "port_call_id",
            "reference_code",
            "vessel_name",
            "maneuver_type",
            "current_state",
            "origin_label",
            "destination_label",
            "planned_at",
            "decided_at",
            "completed_at",
            "reported_at",
            "latest_event_at",
            "case_summary",
            "vessel_snapshot",
            "scale_snapshot",
            "planning_snapshot",
            "decision_snapshot",
            "execution_snapshot",
            "outcome_snapshot",
            "environment_snapshot",
            "feature_snapshot",
            "change_log",
            "feedback_status",
            "feedback_note",
            "feedback_updated_by",
            "feedback_updated_at",
            "created_at",
            "updated_at",
        ),
    },
}
POSTGRES_INSERT_ORDER = (
    "app_users",
    "documents",
    "port_calls",
    "conversations",
    "messages",
    "channel_events",
    "app_runtime_state",
    "feedback_eval_cases",
    "maneuver_cases",
)
POSTGRES_DELETE_ORDER = (
    "channel_events",
    "messages",
    "conversations",
    "maneuver_cases",
    "port_calls",
    "feedback_eval_cases",
    "app_runtime_state",
    "documents",
    "app_users",
)


def _practice_experience_source_path() -> Path:
    knowledge_dir = getattr(services.store, "knowledge_dir", "") or getattr(services, "KNOWLEDGE_DIR", "")
    base_dir = Path(knowledge_dir) if knowledge_dir else Path(current_app.root_path) / "knowledge"
    return base_dir / PRACTICE_EXPERIENCE_SOURCE_FILENAME


def _normalize_digits(value: str | None) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _resolved_whatsapp_opt_in_at(existing_user: dict | None, whatsapp_number: str, whatsapp_opt_in: bool) -> str:
    if not whatsapp_opt_in or not whatsapp_number:
        return ""
    existing = existing_user or {}
    if bool(existing.get("whatsapp_opt_in")) and _normalize_digits(existing.get("whatsapp_number")) == whatsapp_number:
        return str(existing.get("whatsapp_opt_in_at") or "").strip()
    return ""


def _admin_users_payload() -> list[dict]:
    service = getattr(services, "whatsapp_service", None)
    return [
        build_user_whatsapp_view(user, service, services.store)
        for user in services.store.list_users()
    ]


def _manual_knowledge_authoring_enabled() -> bool:
    return bool(current_app.config.get("MANUAL_KNOWLEDGE_AUTHORING_ENABLED", False))


def _dedupe_eval_cases(cases: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for item in cases:
        key = (
            str(item.get("source_message_id") or "").strip()
            or f"{str(item.get('document') or '').strip().lower()}::{str(item.get('question') or '').strip().lower()}"
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _feedback_source_label(source: str) -> str:
    clean_source = str(source or "").strip().lower()
    if clean_source == "whatsapp":
        return "WhatsApp"
    if clean_source == "web":
        return "Site"
    return "Operador"


def _preview_text(value: str, limit: int = 220) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _normalized_int_filter(value: str | None, allowed: tuple[int, ...], default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return default
    return parsed if parsed in allowed else default


def _casebook_query_matches(item: dict, fields: tuple[str, ...], q_search: str) -> bool:
    if not q_search:
        return True
    chunks = []
    for field in fields:
        value = item.get(field)
        if isinstance(value, (dict, list)):
            chunks.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        else:
            chunks.append(str(value or ""))
    haystack = " ".join(chunks).lower()
    return q_search in haystack


def _exported_at() -> str:
    return datetime.now().astimezone().isoformat()


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return str(value)


def _json_download_response(payload: dict, filename: str):
    body = json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n"
    response = current_app.response_class(body, mimetype="application/json; charset=utf-8")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


def _read_admin_json_upload(field_name: str) -> dict:
    uploaded_file = request.files.get(field_name)
    raw_payload = ""
    if uploaded_file and uploaded_file.filename:
        raw_payload = uploaded_file.read().decode("utf-8-sig")
    if not raw_payload.strip():
        raw_payload = request.form.get("payload_json", "")
    if not raw_payload.strip():
        raise ValueError("Carrega um ficheiro JSON ou cola o conteúdo JSON.")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido na linha {exc.lineno}, coluna {exc.colno}.") from exc
    if not isinstance(payload, dict):
        raise ValueError("O backup tem de ser um objeto JSON principal.")
    return payload


def _safe_knowledge_export_files() -> list[dict]:
    knowledge_dir = getattr(services.store, "knowledge_dir", "") or getattr(services, "KNOWLEDGE_DIR", "")
    if not knowledge_dir or not os.path.isdir(knowledge_dir):
        return []
    base_dir = Path(knowledge_dir)
    files = []
    for path in sorted(base_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SYSTEM_KNOWLEDGE_ALLOWED_SUFFIXES:
            continue
        try:
            relative_path = path.relative_to(base_dir).as_posix()
            files.append(
                {
                    "path": relative_path,
                    "content": path.read_text(encoding="utf-8"),
                }
            )
        except (OSError, UnicodeDecodeError):
            logger.exception("Falha ao exportar ficheiro de knowledge %s.", path)
    return files


def _restore_knowledge_files(files: list[dict]) -> int:
    knowledge_dir = getattr(services.store, "knowledge_dir", "") or getattr(services, "KNOWLEDGE_DIR", "")
    if not knowledge_dir:
        raise ValueError("Diretoria knowledge indisponível para restaurar ficheiros.")
    base_dir = Path(knowledge_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    restored = 0
    for item in files:
        if not isinstance(item, dict):
            continue
        relative_path = str(item.get("path") or "").strip()
        content = item.get("content")
        if not relative_path or not isinstance(content, str):
            continue
        target = (base_dir / relative_path).resolve()
        if base_dir.resolve() not in target.parents and target != base_dir.resolve():
            continue
        if target.suffix.lower() not in SYSTEM_KNOWLEDGE_ALLOWED_SUFFIXES:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        restored += 1
    return restored


def _postgres_table_rows() -> dict:
    store = services.store
    connect = getattr(store, "_connect", None)
    if not callable(connect):
        return {}
    tables = {}
    with connect() as conn:
        with conn.cursor() as cur:
            for table, config in POSTGRES_EXPORT_TABLES.items():
                order_by = ", ".join(config["pk"])
                cur.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
                tables[table] = [_json_safe(row) for row in cur.fetchall()]
    return tables


def _build_bot_database_export() -> dict:
    return {
        "kind": BOT_DATABASE_EXPORT_KIND,
        "version": DATABASE_EXPORT_VERSION,
        "exported_at": _exported_at(),
        "backend": getattr(services.store, "backend_name", ""),
        "payload": {
            "feedback_eval_cases": services.store.list_feedback_eval_cases(),
            "reviewable_chat_messages": services.store.list_reviewable_chat_messages(limit=5000),
            "maneuver_cases": services.store.list_maneuver_cases(limit=5000),
            "practice_experience": practice_experience_state(services.store),
            "bot_settings": load_bot_settings(),
        },
    }


def _build_system_database_export() -> dict:
    tables = _postgres_table_rows()
    return {
        "kind": SYSTEM_DATABASE_EXPORT_KIND,
        "version": DATABASE_EXPORT_VERSION,
        "exported_at": _exported_at(),
        "backend": getattr(services.store, "backend_name", ""),
        "payload": {
            "tables": tables,
            "knowledge_files": _safe_knowledge_export_files(),
            "bot_database": _build_bot_database_export()["payload"],
        },
    }


def _normalize_bot_import_payload(payload: dict) -> dict:
    kind = payload.get("kind")
    body = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    if kind == SYSTEM_DATABASE_EXPORT_KIND:
        system_payload = body
        bot_payload = system_payload.get("bot_database")
        if isinstance(bot_payload, dict):
            return bot_payload
        return {
            "feedback_eval_cases": [],
            "reviewable_chat_messages": [],
            "maneuver_cases": [],
            "practice_experience": None,
        }
    if kind in {BOT_DATABASE_EXPORT_KIND, None, ""}:
        return body
    raise ValueError("Tipo de backup do bot não suportado.")


def _import_bot_database_payload(payload: dict) -> dict:
    body = _normalize_bot_import_payload(payload)
    stats = {
        "feedback_eval_cases": 0,
        "chat_feedback": 0,
        "maneuver_feedback": 0,
        "practice_records": 0,
        "settings": 0,
        "skipped": 0,
    }

    imported_settings = body.get("bot_settings")
    if isinstance(imported_settings, dict):
        try:
            save_bot_settings(imported_settings, updated_by=session.get("username", "admin"))
            stats["settings"] = 1
        except Exception:
            logger.exception("Falha ao importar bot_settings.")
            stats["skipped"] += 1

    for item in body.get("feedback_eval_cases") or []:
        if not isinstance(item, dict):
            stats["skipped"] += 1
            continue
        document = str(item.get("document") or "").strip()
        question = str(item.get("question") or "").strip()
        expected_answer = str(item.get("expected_answer") or "").strip()
        if not document or not question or not expected_answer:
            stats["skipped"] += 1
            continue
        services.store.upsert_feedback_eval_case(
            source_message_id=str(item.get("source_message_id") or "").strip(),
            document=document,
            question=question,
            expected_answer=expected_answer,
            expected_substrings=list(item.get("expected_substrings") or []),
            feedback_note=str(item.get("feedback_note") or "").strip(),
            updated_by=session.get("username", "admin"),
            source=str(item.get("source") or "import").strip(),
        )
        stats["feedback_eval_cases"] += 1

    practice_state = body.get("practice_experience")
    if isinstance(practice_state, dict) and isinstance(practice_state.get("records"), list):
        services.store.set_runtime_state(PRACTICE_EXPERIENCE_STATE_KEY, practice_state)
        stats["practice_records"] = len(practice_state.get("records") or [])

    for item in body.get("reviewable_chat_messages") or []:
        if not isinstance(item, dict):
            stats["skipped"] += 1
            continue
        feedback_status = str(item.get("feedback_status") or "").strip().lower()
        if feedback_status not in {"approved", "corrected", "review", "ignored"}:
            continue
        username = str(item.get("username") or item.get("owner_username") or "").strip().lower()
        conversation_id = str(item.get("conversation_id") or "").strip()
        message_id = str(item.get("id") or item.get("message_id") or "").strip()
        if not username or not conversation_id or not message_id:
            stats["skipped"] += 1
            continue
        try:
            services.store.update_message_feedback(
                username=username,
                conversation_id=conversation_id,
                message_id=message_id,
                feedback_status=feedback_status,
                feedback_note=str(item.get("feedback_note") or "").strip(),
                feedback_correction=str(item.get("feedback_correction") or "").strip(),
                feedback_correction_document=str(item.get("feedback_correction_document") or "").strip(),
                feedback_updated_by=session.get("username", "admin"),
            )
            stats["chat_feedback"] += 1
        except ValueError:
            stats["skipped"] += 1

    for item in body.get("maneuver_cases") or []:
        if not isinstance(item, dict):
            stats["skipped"] += 1
            continue
        feedback_status = str(item.get("feedback_status") or "").strip().lower()
        maneuver_id = str(item.get("maneuver_id") or "").strip()
        if feedback_status not in {"approved", "avoid", "review"} or not maneuver_id:
            continue
        try:
            services.store.update_maneuver_case_feedback(
                maneuver_id=maneuver_id,
                feedback_status=feedback_status,
                feedback_note=str(item.get("feedback_note") or "").strip(),
                feedback_by=session.get("username", "admin"),
            )
            stats["maneuver_feedback"] += 1
        except ValueError:
            stats["skipped"] += 1

    return stats


def _validate_system_import_has_admin(tables: dict, mode: str) -> None:
    if mode != "replace":
        return
    users = tables.get("app_users")
    if isinstance(users, list) and not any((item.get("role") or "").strip().lower() == "admin" for item in users if isinstance(item, dict)):
        raise ValueError("O backup de sistema não contém nenhum utilizador admin; importação cancelada.")


def _restore_postgres_tables(tables: dict, *, mode: str) -> dict:
    store = services.store
    connect = getattr(store, "_connect", None)
    if not callable(connect):
        raise ValueError("Backend PostgreSQL indisponível para importação de tabelas.")
    stats = {"tables": 0, "records": 0}
    with connect() as conn:
        with conn.cursor() as cur:
            if mode == "replace":
                for table in POSTGRES_DELETE_ORDER:
                    if table in tables:
                        cur.execute(f"DELETE FROM {table}")
            for table in POSTGRES_INSERT_ORDER:
                rows = tables.get(table)
                if not isinstance(rows, list) or table not in POSTGRES_EXPORT_TABLES:
                    continue
                config = POSTGRES_EXPORT_TABLES[table]
                pk_fields = set(config["pk"])
                jsonb_fields = set(config["jsonb"])
                columns_order = list(config["columns"])
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    columns = [column for column in columns_order if column in row]
                    if not columns:
                        continue
                    placeholders = [
                        "%s::jsonb" if column in jsonb_fields else "%s"
                        for column in columns
                    ]
                    values = [
                        json.dumps(row.get(column), ensure_ascii=False) if column in jsonb_fields else row.get(column)
                        for column in columns
                    ]
                    update_columns = [column for column in columns if column not in pk_fields]
                    if update_columns:
                        updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
                    else:
                        updates = f"{columns[0]} = EXCLUDED.{columns[0]}"
                    conflict = ", ".join(config["pk"])
                    cur.execute(
                        f"""
                        INSERT INTO {table} ({", ".join(columns)})
                        VALUES ({", ".join(placeholders)})
                        ON CONFLICT ({conflict}) DO UPDATE SET {updates}
                        """,
                        tuple(values),
                    )
                stats["tables"] += 1
                stats["records"] += len(rows)
        conn.commit()
    return stats


def _import_system_database_payload(payload: dict, *, mode: str) -> dict:
    if payload.get("kind") not in {SYSTEM_DATABASE_EXPORT_KIND, None, ""}:
        raise ValueError("Tipo de backup de sistema não suportado.")
    body = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    tables = body.get("tables") if isinstance(body.get("tables"), dict) else {}
    _validate_system_import_has_admin(tables, mode)
    stats = _restore_postgres_tables(tables, mode=mode)

    knowledge_files = body.get("knowledge_files")
    if isinstance(knowledge_files, list):
        stats["knowledge_files"] = _restore_knowledge_files(knowledge_files)
    else:
        stats["knowledge_files"] = 0
    return stats


def _build_admin_bot_payload() -> dict:
    knowledge_dir = getattr(services.store, "knowledge_dir", "") or getattr(services, "KNOWLEDGE_DIR", "")
    documents = services.store.list_documents()
    manual_companion_files: list[str] = []
    companions_dir = companion_directory(knowledge_dir) if knowledge_dir else ""
    if companions_dir and os.path.isdir(companions_dir):
        manual_companion_files = sorted(
            name
            for name in os.listdir(companions_dir)
            if name.lower().endswith(".json")
        )

    resolved_companions_total = 0
    for document in documents:
        try:
            if load_document_companion(document.get("name", ""), knowledge_dir):
                resolved_companions_total += 1
        except Exception:
            logger.exception("Falha ao resolver companion para %s.", document.get("name", ""))

    static_cases = load_eval_cases_from_dir(os.path.join(knowledge_dir, "evals")) if knowledge_dir else []
    feedback_cases = load_eval_cases_from_store(services.store)
    active_cases = _dedupe_eval_cases(static_cases + feedback_cases)
    results = evaluate_companion_cases(active_cases, knowledge_dir) if active_cases else []
    passed_cases = sum(1 for item in results if item.get("passed"))
    failed_cases = [item for item in results if not item.get("passed")]
    pass_rate_pct = round((passed_cases / len(results)) * 100) if results else 0

    source_counter = Counter(_feedback_source_label(item.get("source", "")) for item in feedback_cases)
    source_rows = [
        {"label": label, "count": count}
        for label, count in sorted(source_counter.items(), key=lambda item: (-item[1], item[0]))
    ]

    latest_feedback_updated_at = ""
    if feedback_cases:
        latest_feedback_updated_at = max(
            (str(item.get("updated_at") or "").strip() for item in feedback_cases),
            default="",
        )

    document_summary: dict[str, dict] = {}
    for case in active_cases:
        name = str(case.get("document") or "").strip()
        if not name:
            continue
        bucket = document_summary.setdefault(
            name,
            {
                "document": name,
                "total_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
                "feedback_cases": 0,
            },
        )
        bucket["total_cases"] += 1
    for result in results:
        name = str(result.get("document") or "").strip()
        if not name:
            continue
        bucket = document_summary.setdefault(
            name,
            {
                "document": name,
                "total_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
                "feedback_cases": 0,
            },
        )
        if result.get("passed"):
            bucket["passed_cases"] += 1
        else:
            bucket["failed_cases"] += 1
    for case in feedback_cases:
        name = str(case.get("document") or "").strip()
        if not name:
            continue
        bucket = document_summary.setdefault(
            name,
            {
                "document": name,
                "total_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
                "feedback_cases": 0,
            },
        )
        bucket["feedback_cases"] += 1

    document_rows = []
    for item in document_summary.values():
        total_cases = item["total_cases"]
        passed = item["passed_cases"]
        failed = item["failed_cases"]
        coverage_pct = round((passed / total_cases) * 100) if total_cases else 0
        state = "online" if failed == 0 and total_cases else "degraded" if total_cases else "offline"
        document_rows.append(
            {
                **item,
                "coverage_pct": coverage_pct,
                "state": state,
            }
        )
    document_rows.sort(
        key=lambda item: (
            -item["failed_cases"],
            -item["feedback_cases"],
            item["document"],
        )
    )

    failure_rows = []
    for item in failed_cases[:12]:
        missing_bits = list(item.get("missing_substrings") or [])
        if not missing_bits:
            missing_bits = list(item.get("missing_terms") or [])[:4]
        failure_rows.append(
            {
                "document": item.get("document", ""),
                "question": item.get("question", ""),
                "missing_summary": ", ".join(missing_bits) or "Resposta vazia ou desalinhada com o esperado.",
                "answer_preview": _preview_text(item.get("answer", ""), limit=260) or "Sem resposta gerada.",
            }
        )

    recent_feedback_cases = []
    for item in sorted(
        feedback_cases,
        key=lambda record: str(record.get("updated_at") or ""),
        reverse=True,
    )[:12]:
        recent_feedback_cases.append(
            {
                **item,
                "source_label": _feedback_source_label(item.get("source", "")),
                "updated_at_label": _local_iso_to_label(item.get("updated_at")),
                "expected_answer_preview": _preview_text(item.get("expected_answer", ""), limit=260),
                "feedback_note_preview": _preview_text(item.get("feedback_note", ""), limit=160),
            }
        )

    if not results:
        state = "offline"
        state_label = "Sem avaliação"
    elif failed_cases:
        state = "degraded"
        state_label = "Falhas ativas"
    else:
        state = "online"
        state_label = "Conforme"

    return {
        "state": state,
        "state_label": state_label,
        "knowledge_documents_total": len(documents),
        "manual_companions_total": len(manual_companion_files),
        "resolved_companions_total": resolved_companions_total,
        "static_cases_total": len(static_cases),
        "feedback_cases_total": len(feedback_cases),
        "active_cases_total": len(active_cases),
        "passed_cases_total": passed_cases,
        "failed_cases_total": len(failed_cases),
        "pass_rate_pct": pass_rate_pct,
        "documents_covered_total": len(document_rows),
        "latest_feedback_updated_at_label": (
            _local_iso_to_label(latest_feedback_updated_at) if latest_feedback_updated_at else "Nunca"
        ),
        "source_rows": source_rows,
        "recent_feedback_cases": recent_feedback_cases,
        "failure_rows": failure_rows,
        "document_rows": document_rows[:16],
    }


def _safe_return_to(value: str | None) -> str:
    target = (value or "").strip()
    if not target:
        return ""
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return ""
    rebuilt = parsed.path
    if parsed.query:
        rebuilt = f"{rebuilt}?{parsed.query}"
    return rebuilt


def _documents_return_to() -> str:
    if request.query_string:
        return f"{request.path}?{request.query_string.decode('utf-8', errors='ignore')}"
    return request.path


EVENT_REPORT_STATUS_META = {
    "novo": {"label": "Novo", "badge": "degraded"},
    "em_revisao": {"label": "Em revisão", "badge": "neutral"},
    "resolvido": {"label": "Resolvido", "badge": "online"},
    "arquivado": {"label": "Arquivado", "badge": "offline"},
}


def _current_request_return_to() -> str:
    if request.query_string:
        return f"{request.path}?{request.query_string.decode('utf-8', errors='ignore')}"
    return request.path


def _event_report_status_options() -> list[dict]:
    return [
        {
            "value": status,
            "label": EVENT_REPORT_STATUS_META.get(status, {}).get("label", status.replace("_", " ").capitalize()),
        }
        for status in EVENT_REPORT_STATUS_OPTIONS
    ]


def _event_report_status_meta(status: str) -> dict:
    return EVENT_REPORT_STATUS_META.get(status, {"label": status.replace("_", " ").capitalize(), "badge": "neutral"})


def _event_report_view(event: dict) -> dict:
    status = event.get("estado") or "novo"
    meta = _event_report_status_meta(status)
    has_photo = event_report_photo_path(event) is not None
    description = (event.get("descricao_processada") or event.get("descricao_original") or "").strip()
    return {
        **event,
        "estado": status,
        "status_label": meta["label"],
        "status_badge": meta["badge"],
        "descricao": description,
        "has_photo": has_photo,
        "revisto_em_label": _local_iso_to_label(event.get("revisto_em")) if event.get("revisto_em") else "",
        "photo_url": url_for("admin.event_report_photo", event_id=event.get("id")) if has_photo else "",
    }


def _build_event_report_filters(events: list[dict]) -> dict:
    q = " ".join((request.args.get("q") or "").strip().split())
    q_lookup = q.lower()
    tag = (request.args.get("tag") or "").strip().upper()
    status = (request.args.get("estado") or "").strip().lower()
    photo = (request.args.get("photo") or "").strip().lower()
    if status not in EVENT_REPORT_STATUS_OPTIONS:
        status = ""
    if photo not in {"with", "without"}:
        photo = ""
    tags = sorted(
        {
            (item.get("tag") or "").strip().upper()
            for item in events
            if (item.get("tag") or "").strip()
        }
        | set(EVENT_REPORT_TAG_OPTIONS)
    )
    return {
        "q": q,
        "q_lookup": q_lookup,
        "tag": tag,
        "estado": status,
        "photo": photo,
        "tags": tags,
        "statuses": _event_report_status_options(),
        "has_active_filters": bool(q or tag or status or photo),
    }


def _filter_event_reports(events: list[dict], filters: dict) -> list[dict]:
    filtered = []
    for item in events:
        if filters["tag"] and (item.get("tag") or "").strip().upper() != filters["tag"]:
            continue
        if filters["estado"] and (item.get("estado") or "").strip().lower() != filters["estado"]:
            continue
        if filters["photo"] == "with" and not item.get("has_photo"):
            continue
        if filters["photo"] == "without" and item.get("has_photo"):
            continue
        if filters["q_lookup"]:
            haystack = " ".join(
                str(item.get(key) or "")
                for key in (
                    "id",
                    "tag",
                    "local",
                    "descricao_original",
                    "descricao_processada",
                    "utilizador",
                    "username",
                    "nota_admin",
                )
            ).lower()
            if filters["q_lookup"] not in haystack:
                continue
        filtered.append(item)
    return filtered


def _event_report_stats(events: list[dict], filtered_events: list[dict]) -> dict:
    return {
        "total": len(events),
        "filtered": len(filtered_events),
        "with_photo": sum(1 for item in events if item.get("has_photo")),
        "open": sum(1 for item in events if item.get("estado") in {"novo", "em_revisao"}),
    }


def _selected_event_report_ids() -> list[str]:
    values = request.values.getlist("event_ids")
    raw_ids = request.values.get("ids", "")
    if raw_ids:
        values.extend(raw_ids.split(","))
    selected: list[str] = []
    for value in values:
        clean = " ".join((value or "").strip().split())
        if clean and clean not in selected:
            selected.append(clean)
    return selected


def _build_document_filters(docs: list[dict]) -> dict:
    q = " ".join((request.args.get("q") or "").strip().split())
    q_search = q.lower()
    doc_types = sorted(
        {
            (item.get("doc_type") or "").strip()
            for item in docs
            if (item.get("doc_type") or "").strip()
        }
    )
    uploaded_bys = sorted(
        {
            (item.get("uploaded_by") or "").strip()
            for item in docs
            if (item.get("uploaded_by") or "").strip()
        }
    )
    doc_type = (request.args.get("doc_type") or "").strip()
    if doc_type not in doc_types:
        doc_type = ""
    uploaded_by = (request.args.get("uploaded_by") or "").strip()
    if uploaded_by not in uploaded_bys:
        uploaded_by = ""
    editable = (request.args.get("editable") or "").strip().lower()
    if editable not in {"", "editable", "read_only"}:
        editable = ""
    return {
        "q": q,
        "q_search": q_search,
        "doc_type": doc_type,
        "uploaded_by": uploaded_by,
        "editable": editable,
        "doc_types": doc_types,
        "uploaded_bys": uploaded_bys,
        "has_active_filters": bool(q or doc_type or uploaded_by or editable),
    }


def _document_matches_filters(document: dict, filters: dict) -> bool:
    if filters.get("doc_type") and (document.get("doc_type") or "") != filters["doc_type"]:
        return False
    if filters.get("uploaded_by") and (document.get("uploaded_by") or "") != filters["uploaded_by"]:
        return False
    if filters.get("editable") == "editable" and not document.get("editable"):
        return False
    if filters.get("editable") == "read_only" and document.get("editable"):
        return False
    if filters.get("q_search"):
        haystack = " ".join(
            str(document.get(field) or "")
            for field in ("name", "original_name", "doc_type", "uploaded_by", "preview")
        ).lower()
        if filters["q_search"] not in haystack:
            return False
    return True


def _filter_documents(docs: list[dict], filters: dict) -> list[dict]:
    return [item for item in docs if _document_matches_filters(item, filters)]


def _admin_casebooks_return_to() -> str:
    if request.query_string:
        target = f"{request.path}?{request.query_string.decode('utf-8', errors='ignore')}"
    else:
        target = request.path
    if request.endpoint == "admin.admin_bot":
        return f"{target}#casebooks"
    return target


def _normalized_filter_value(value: str | None, allowed: set[str], default: str) -> str:
    clean = (value or "").strip().lower()
    return clean if clean in allowed else default


def _chat_feedback_state_meta(value: str | None) -> tuple[str, str]:
    clean = (value or "").strip().lower()
    if clean == "approved":
        return "Resposta original aprovada", "online"
    if clean == "corrected":
        return "Correção reutilizável", "online"
    if clean == "review":
        return "Bloqueada para revisão", "degraded"
    if clean == "ignored":
        return "Ignorada", "neutral"
    return "Por rever", "neutral"


def _normalized_chat_feedback_status(item: dict) -> str:
    status = (item.get("feedback_status") or "").strip().lower()
    if status == "review" and (item.get("feedback_correction") or "").strip():
        return "corrected"
    return status


def _case_feedback_state_meta(value: str | None) -> tuple[str, str]:
    clean = (value or "").strip().lower()
    if clean == "approved":
        return "Experiência validada", "online"
    if clean == "avoid":
        return "Evitar como padrão", "degraded"
    if clean == "review":
        return "Rever caso", "degraded"
    return "Sem validação", "neutral"


def _case_governance_meta(value: str | None) -> tuple[str, str, str, str]:
    clean = (value or "").strip().lower()
    if clean == "approved":
        return (
            "governed",
            "Experiência validada",
            "Pode sustentar recomendação histórica, sempre com validação operacional do momento.",
            "online",
        )
    if clean == "avoid":
        return (
            "governed",
            "Padrão a evitar",
            "Este caso foi mantido como memória útil, mas como contraexemplo operacional.",
            "degraded",
        )
    if clean == "review":
        return (
            "governed",
            "Caso em revisão",
            "Há sinal relevante, mas a leitura operacional ainda precisa de decisão final.",
            "degraded",
        )
    return (
        "observation",
        "Por validar",
        "O sistema encontrou uma correlação, mas ainda não houve decisão humana sobre o seu valor operacional.",
        "neutral",
    )


def _casebook_feature_label(case: dict) -> str:
    features = case.get("feature_snapshot") or {}
    vessel_type = (features.get("vessel_type") or "").strip() or "Tipo não identificado"
    parts = [f"{case.get('origin_label') or '--'} -> {case.get('destination_label') or '--'}", vessel_type]
    loa_value = features.get("vessel_loa_m")
    if isinstance(loa_value, (int, float)):
        parts.append(f"LOA {loa_value:.1f} m")
    tug_count = (features.get("tug_count") or "").strip()
    if tug_count:
        parts.append(f"rebocadores {tug_count}")
    return " | ".join(parts)


def _build_casebook_match_rows(case: dict, case_pool: list[dict], limit: int = 3) -> list[dict]:
    features = case.get("feature_snapshot") or {}
    environment_signature = build_case_environment_signature(case)
    ranked = rank_similar_maneuver_cases(
        case_pool,
        maneuver_type=case.get("maneuver_type", ""),
        origin=features.get("origin") or case.get("origin_label", ""),
        destination=features.get("destination") or case.get("destination_label", ""),
        vessel_type=features.get("vessel_type", ""),
        vessel_loa_m=str(features.get("vessel_loa_m") or ""),
        bow_thruster=features.get("bow_thruster", ""),
        stern_thruster=features.get("stern_thruster", ""),
        tug_count=features.get("tug_count", ""),
        environment_signature=environment_signature,
        limit=max(limit + 1, 4),
    )

    rows = []
    for match in ranked:
        if match.get("maneuver_id") == case.get("maneuver_id"):
            continue
        label, badge = _case_feedback_state_meta(match.get("feedback_status"))
        rows.append(
            {
                "maneuver_id": match.get("maneuver_id", ""),
                "port_call_id": match.get("port_call_id", ""),
                "reference_code": match.get("reference_code", "--"),
                "vessel_name": match.get("vessel_name", "--"),
                "route_label": f"{match.get('origin_label') or '--'} -> {match.get('destination_label') or '--'}",
                "latest_event_label": match.get("latest_event_label", "--"),
                "state_label": match.get("current_state_label", "--"),
                "similarity_score": match.get("similarity_score", 0),
                "reasons_label": ", ".join(match.get("similarity_reasons") or []) or "perfil semelhante",
                "experience_label": match.get("experience_label", "Semelhança operacional"),
                "experience_badge": match.get("experience_badge", "neutral"),
                "feedback_status_label": label,
                "feedback_badge": badge,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _build_admin_casebooks_payload() -> dict:
    chat_feedback_allowed = {"all", "pending", "approved", "corrected", "review", "ignored"}
    case_feedback_allowed = {"all", "pending", "approved", "avoid", "review"}
    case_type_allowed = {"", "entry", "departure", "shift"}
    source_type_allowed = {"all", "chat", "maneuver", "practice"}
    chat_feedback = _normalized_filter_value(request.args.get("chat_feedback"), chat_feedback_allowed, "pending")
    case_feedback = _normalized_filter_value(request.args.get("case_feedback"), case_feedback_allowed, "pending")
    case_type = _normalized_filter_value(request.args.get("case_type"), case_type_allowed, "")
    source_type = _normalized_filter_value(request.args.get("source_type"), source_type_allowed, "all")
    display_limit = _normalized_int_filter(
        request.args.get("limit"),
        ADMIN_CASEBOOK_ALLOWED_LIMITS,
        ADMIN_CASEBOOK_DEFAULT_LIMIT,
    )
    q = " ".join((request.args.get("q") or "").strip().split())
    q_search = q.lower()
    next_limit = ADMIN_CASEBOOK_ALLOWED_LIMITS[-1]
    for option in ADMIN_CASEBOOK_ALLOWED_LIMITS:
        if option > display_limit:
            next_limit = option
            break

    all_chat_messages = services.store.list_reviewable_chat_messages(limit=240)
    all_cases = services.store.list_maneuver_cases(limit=240)
    all_practice_records = list_practice_experience_records(services.store)
    all_cases.sort(
        key=lambda item: (
            item.get("latest_event_at") or "",
            item.get("updated_at") or "",
        ),
        reverse=True,
    )

    def _message_visible(item: dict) -> bool:
        if source_type not in {"all", "chat"}:
            return False
        status = _normalized_chat_feedback_status(item)
        if chat_feedback == "pending":
            visible = not status
        elif chat_feedback in {"approved", "corrected", "review", "ignored"}:
            visible = status == chat_feedback
        else:
            visible = True
        if not visible:
            return False
        return _casebook_query_matches(
            item,
            (
                "username",
                "conversation_title",
                "question",
                "content",
                "feedback_note",
                "feedback_correction",
                "feedback_correction_document",
                "channel",
                "citation_documents",
            ),
            q_search,
        )

    def _case_visible(item: dict) -> bool:
        if source_type not in {"all", "maneuver"}:
            return False
        status = (item.get("feedback_status") or "").strip().lower()
        if case_type and (item.get("maneuver_type") or "").strip().lower() != case_type:
            return False
        if case_feedback == "pending":
            visible = not status
        elif case_feedback in {"approved", "avoid", "review"}:
            visible = status == case_feedback
        else:
            visible = True
        if not visible:
            return False
        return _casebook_query_matches(
            item,
            (
                "reference_code",
                "vessel_name",
                "maneuver_type_label",
                "origin_label",
                "destination_label",
                "current_state_label",
                "feedback_note",
                "case_summary",
                "feature_snapshot",
                "outcome_snapshot",
            ),
            q_search,
        )

    def _practice_visible(item: dict) -> bool:
        if source_type not in {"all", "practice"}:
            return False
        status = (item.get("feedback_status") or "").strip().lower()
        if case_type and (item.get("maneuver_type") or "").strip().lower() != case_type:
            return False
        if case_feedback in {"approved", "avoid", "review"}:
            visible = status == case_feedback
        else:
            visible = True
        if not visible:
            return False
        return _casebook_query_matches(
            item,
            (
                "reference_code",
                "source_filename",
                "vessel_name",
                "maneuver_type_label",
                "origin_label",
                "destination_label",
                "route_label",
                "profile_label",
                "comments_label",
                "tug_distribution_label",
                "vessel_examples_label",
                "feature_snapshot",
                "practice_metrics",
            ),
            q_search,
        )

    visible_chat_messages = [item for item in all_chat_messages if _message_visible(item)]
    visible_cases = [item for item in all_cases if _case_visible(item)]
    visible_practice_records = [item for item in all_practice_records if _practice_visible(item)]

    chat_rows = []
    for item in visible_chat_messages[:display_limit]:
        status_clean = _normalized_chat_feedback_status(item)
        status_label, badge = _chat_feedback_state_meta(status_clean)
        answer = str(item.get("content") or "")
        question = str(item.get("question") or "")
        chat_rows.append(
            {
                "id": item.get("id", ""),
                "conversation_id": item.get("conversation_id", ""),
                "owner_username": item.get("username", ""),
                "conversation_title": item.get("conversation_title", "Conversa"),
                "question": question,
                "question_preview": _preview_text(question, limit=220) or "Sem pergunta anterior identificada.",
                "answer": answer,
                "answer_preview": _preview_text(answer, limit=420) or "Sem resposta guardada.",
                "feedback_status": status_clean,
                "feedback_status_label": status_label,
                "feedback_badge": badge,
                "feedback_note": item.get("feedback_note", ""),
                "feedback_correction": item.get("feedback_correction", ""),
                "feedback_correction_document": item.get("feedback_correction_document", ""),
                "feedback_updated_by": item.get("feedback_updated_by", ""),
                "feedback_updated_at_label": item.get("feedback_updated_at_label", ""),
                "created_at_label": item.get("created_at_label", ""),
                "channel_label": _feedback_source_label(item.get("channel", "")),
                "citation_documents": list(item.get("citation_documents") or []),
            }
        )

    observation_case_rows = []
    governed_case_rows = []
    case_rows = []
    for item in visible_cases[:display_limit]:
        status_clean = (item.get("feedback_status") or "").strip().lower()
        status_label, badge = _case_feedback_state_meta(status_clean)
        bucket, learning_title, learning_summary, learning_badge = _case_governance_meta(status_clean)
        top_matches = _build_casebook_match_rows(item, all_cases)
        row = {
            "maneuver_id": item.get("maneuver_id", ""),
            "port_call_id": item.get("port_call_id", ""),
            "reference_code": item.get("reference_code", "--"),
            "vessel_name": item.get("vessel_name", "--"),
            "maneuver_type": item.get("maneuver_type", ""),
            "maneuver_type_label": item.get("maneuver_type_label", item.get("maneuver_type", "")),
            "route_label": f"{item.get('origin_label') or '--'} -> {item.get('destination_label') or '--'}",
            "latest_event_label": item.get("latest_event_label", "--"),
            "state_label": item.get("current_state_label", "--"),
            "feedback_status": status_clean,
            "feedback_status_label": status_label,
            "feedback_badge": badge,
            "feedback_note": item.get("feedback_note", ""),
            "feedback_updated_by": item.get("feedback_updated_by", ""),
            "feedback_updated_at_label": item.get("feedback_updated_at_label", ""),
            "feature_label": _casebook_feature_label(item),
            "decision_flags": list((item.get("outcome_snapshot") or {}).get("decision_flags") or []),
            "top_matches": top_matches,
            "top_matches_label": "; ".join(
                f"{match['reference_code']} ({match['similarity_score']}, {match['feedback_status_label']})"
                for match in top_matches[:2]
            ),
            "learning_title": learning_title,
            "learning_summary": learning_summary,
            "learning_badge": learning_badge,
        }
        case_rows.append(row)
        target_rows = observation_case_rows if bucket == "observation" else governed_case_rows
        if len(target_rows) < 12:
            target_rows.append(row)

    practice_rows = []
    for item in visible_practice_records[:display_limit]:
        status_clean = (item.get("feedback_status") or "").strip().lower()
        status_label, badge = _case_feedback_state_meta(status_clean)
        practice_rows.append(
            {
                "id": item.get("id", ""),
                "reference_code": item.get("reference_code", "--"),
                "source_filename": item.get("source_filename", ""),
                "maneuver_type": item.get("maneuver_type", ""),
                "maneuver_type_label": item.get("maneuver_type_label", item.get("maneuver_type", "")),
                "vessel_name": item.get("vessel_name", "--"),
                "route_label": item.get("route_label") or f"{item.get('origin_label') or '--'} -> {item.get('destination_label') or '--'}",
                "profile_label": item.get("profile_label") or _casebook_feature_label(item),
                "case_count": item.get("case_count", 0),
                "duration_median_label": item.get("duration_median_label", "--"),
                "tug_distribution_label": item.get("tug_distribution_label", "--"),
                "vessel_examples_label": item.get("vessel_examples_label", "--"),
                "comments_label": _preview_text(item.get("comments_label", ""), limit=220),
                "feedback_status": status_clean,
                "feedback_status_label": status_label,
                "feedback_badge": badge,
                "feedback_note": item.get("feedback_note", ""),
            }
        )

    if not visible_cases and not visible_chat_messages and not visible_practice_records:
        state = "neutral"
        state_label = "Sem itens"
    elif any(not (item.get("feedback_status") or "").strip() for item in visible_cases) or any(
        not (item.get("feedback_status") or "").strip() for item in visible_chat_messages
    ):
        state = "degraded"
        state_label = "Por validar"
    elif any((item.get("feedback_status") or "").strip() in {"avoid", "review"} for item in visible_cases + visible_practice_records):
        state = "degraded"
        state_label = "Com alertas"
    else:
        state = "online"
        state_label = "Governado"

    return {
        "state": state,
        "state_label": state_label,
        "return_to": _admin_casebooks_return_to(),
        "filters": {
            "chat_feedback": chat_feedback,
            "case_feedback": case_feedback,
            "case_type": case_type,
            "source_type": source_type,
            "q": q,
            "limit": display_limit,
        },
        "display_limit": display_limit,
        "next_limit": next_limit,
        "allowed_limits": ADMIN_CASEBOOK_ALLOWED_LIMITS,
        "has_active_filters": bool(q or source_type != "all" or chat_feedback != "pending" or case_feedback != "pending" or case_type),
        "chat_pending_total": sum(1 for item in all_chat_messages if not _normalized_chat_feedback_status(item)),
        "chat_approved_total": sum(1 for item in all_chat_messages if _normalized_chat_feedback_status(item) == "approved"),
        "chat_corrected_total": sum(1 for item in all_chat_messages if _normalized_chat_feedback_status(item) == "corrected"),
        "chat_review_total": sum(1 for item in all_chat_messages if _normalized_chat_feedback_status(item) == "review"),
        "chat_ignored_total": sum(1 for item in all_chat_messages if _normalized_chat_feedback_status(item) == "ignored"),
        "case_pending_total": sum(1 for item in all_cases if not (item.get("feedback_status") or "").strip()),
        "case_approved_total": sum(1 for item in all_cases if (item.get("feedback_status") or "").strip() == "approved"),
        "case_review_total": sum(1 for item in all_cases if (item.get("feedback_status") or "").strip() in {"avoid", "review"}),
        "practice_total": len(all_practice_records),
        "practice_active_total": sum(1 for item in all_practice_records if (item.get("feedback_status") or "").strip() in {"approved", "avoid"}),
        "practice_review_total": sum(1 for item in all_practice_records if (item.get("feedback_status") or "").strip() == "review"),
        "practice_local_source_exists": _practice_experience_source_path().exists(),
        "practice_source_filename": PRACTICE_EXPERIENCE_SOURCE_FILENAME,
        "chat_visible_total": len(visible_chat_messages),
        "case_visible_total": len(visible_cases),
        "practice_visible_total": len(visible_practice_records),
        "chat_has_more": len(visible_chat_messages) > len(chat_rows),
        "case_has_more": len(visible_cases) > len(case_rows),
        "practice_has_more": len(visible_practice_records) > len(practice_rows),
        "chat_rows": chat_rows,
        "case_rows": case_rows,
        "practice_rows": practice_rows,
        "observation_case_rows": observation_case_rows,
        "governed_case_rows": governed_case_rows,
    }


@bp.route("/admin/status")
@login_required
@role_required("admin")
def admin_status():
    """Painel de estado do sistema para administradores."""
    refresh_knowledge_state(force_reindex=False)
    return render_template("admin_status.html", admin=load_admin_status())


@bp.route("/admin/bot")
@login_required
@role_required("admin")
def admin_bot():
    """Painel de saúde, fontes, sinais e governança do bot."""
    refresh_knowledge_state(force_reindex=False)
    settings = load_bot_settings()
    signals = build_learning_signals(window_hours=int(settings.get("signals_window_hours", 168)))
    sources = build_sources_snapshot()
    quality = build_quality_snapshot()
    exceptions = build_exceptions(limit=12)
    health = compute_health_score(quality, signals, exceptions, sources)
    monitor = build_bot_monitor_snapshot(
        settings=settings,
        sources=sources,
        quality=quality,
        signals=signals,
        exceptions=exceptions,
        health=health,
    )
    return render_template(
        "admin_bot.html",
        health=health,
        signals=signals,
        sources=sources,
        quality=quality,
        exceptions=exceptions,
        monitor=monitor,
        settings=settings,
        settings_defaults=BOT_SETTINGS_DEFAULTS,
        casebooks=_build_admin_casebooks_payload(),
        title="Bot e evals",
    )


@bp.route("/admin/bot/monitor")
@login_required
@role_required("admin")
def admin_bot_monitor():
    """Estado JSON para atualização periódica do painel do bot."""
    settings = load_bot_settings()
    signals = build_learning_signals(window_hours=int(settings.get("signals_window_hours", 168)))
    sources = build_sources_snapshot()
    quality = build_quality_snapshot()
    exceptions = build_exceptions(limit=12)
    health = compute_health_score(quality, signals, exceptions, sources)
    monitor = build_bot_monitor_snapshot(
        settings=settings,
        sources=sources,
        quality=quality,
        signals=signals,
        exceptions=exceptions,
        health=health,
    )
    return jsonify(
        {
            "health": health,
            "signals": signals,
            "sources": sources,
            "quality": quality,
            "exceptions": exceptions,
            "monitor": monitor,
        }
    )


@bp.route("/admin/bot/settings", methods=["POST"])
@login_required
@role_required("admin")
def admin_bot_settings():
    """Atualizar definições do bot (auto-promote, thresholds, janela)."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot")
    try:
        updates: dict = {}
        for key in BOT_SETTINGS_DEFAULTS.keys():
            if key in request.form:
                updates[key] = request.form.get(key)
        for bool_key in ("auto_promote_corrections", "auto_trust_positive_feedback", "require_admin_validation"):
            updates[bool_key] = bool_key in request.form
        save_bot_settings(updates, updated_by=session.get("username") or "admin")
        flash("Definições do bot atualizadas.", "success")
    except Exception as exc:
        logger.exception("Falha ao guardar definições do bot.")
        flash(f"Falha ao guardar definições do bot: {exc}", "error")
    return redirect(return_to)


@bp.route("/admin/bot/settings/reset", methods=["POST"])
@login_required
@role_required("admin")
def admin_bot_settings_reset():
    """Repor definições do bot para os valores por defeito."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot")
    reset_bot_settings(updated_by=session.get("username") or "admin")
    flash("Definições do bot repostas aos valores por defeito.", "success")
    return redirect(return_to)


@bp.route("/admin/bot/rerun-evals", methods=["POST"])
@login_required
@role_required("admin")
def admin_bot_rerun_evals():
    """Forçar uma nova avaliação dos eval cases e devolver ao painel com resumo."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot", _anchor="quality")
    try:
        refresh_knowledge_state(force_reindex=False)
        snapshot = build_quality_snapshot()
        passed = snapshot.get("passed_total", 0)
        total = snapshot.get("active_cases_total", 0)
        failed = snapshot.get("failed_total", 0)
        flash(f"Evals executados: {passed}/{total} passam ({failed} a falhar).", "success")
    except Exception as exc:
        logger.exception("Falha a correr evals.")
        flash(f"Falha a correr evals: {exc}", "error")
    return redirect(return_to)


@bp.route("/admin/bot/playground", methods=["POST"])
@login_required
@role_required("admin")
def admin_bot_playground():
    """Avaliar uma pergunta no pipeline real do bot e devolver resposta + citações."""
    from core.chat_runtime import playground_answer

    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "Pergunta vazia."}), 400

    username = session.get("username") or "admin"
    role = session.get("role") or "admin"

    try:
        result = playground_answer(username=username, role=role, question=question)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Playground do bot falhou.")
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            "answer_origin": result.get("answer_origin", ""),
            "trace": result.get("trace", {}),
        }
    )


@bp.route("/admin/bot/export")
@login_required
@role_required("admin")
def export_bot_database():
    """Exportar dados de governação/aprendizagem do bot em JSON."""
    return _json_download_response(_build_bot_database_export(), "pragtico-bot-database.json")


@bp.route("/admin/system/export")
@login_required
@role_required("admin")
def export_system_database():
    """Exportar dados aplicacionais e ficheiros knowledge em JSON."""
    return _json_download_response(_build_system_database_export(), "pragtico-system-database.json")


@bp.route("/admin/bot/import", methods=["POST"])
@login_required
@role_required("admin")
def import_bot_database():
    """Importar JSON de governação do bot sem dependências externas."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot", _anchor="casebooks")
    try:
        payload = _read_admin_json_upload("bot_database_file")
        stats = _import_bot_database_payload(payload)
        flash(
            "Base do bot importada: "
            f"{stats['feedback_eval_cases']} eval(s), {stats['chat_feedback']} feedback(s) de chat, "
            f"{stats['maneuver_feedback']} validação(ões) de manobra, "
            f"{stats['practice_records']} padrão(ões) de experiência. "
            f"Ignorados: {stats['skipped']}.",
            "success",
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
    except Exception as exc:
        logger.exception("Falha inesperada ao importar base do bot.")
        flash(f"Falha inesperada ao importar base do bot: {exc}", "error")
    return redirect(return_to)


@bp.route("/admin/system/import", methods=["POST"])
@login_required
@role_required("admin")
def import_system_database():
    """Importar backup JSON completo do sistema."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot", _anchor="casebooks")
    mode = (request.form.get("import_mode") or "merge").strip().lower()
    if mode not in {"merge", "replace"}:
        mode = "merge"
    try:
        payload = _read_admin_json_upload("system_database_file")
        stats = _import_system_database_payload(payload, mode=mode)
        refresh_knowledge_state(force_reindex=True)
        flash(
            "Base do sistema importada em modo "
            f"{'substituir' if mode == 'replace' else 'juntar'}: "
            f"{stats.get('records', 0)} registo(s), {stats.get('files', stats.get('tables', 0))} conjunto(s), "
            f"{stats.get('knowledge_files', 0)} ficheiro(s) knowledge.",
            "success",
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
    except Exception as exc:
        logger.exception("Falha inesperada ao importar base do sistema.")
        flash(f"Falha inesperada ao importar base do sistema: {exc}", "error")
    return redirect(return_to)


@bp.route("/admin/casebooks")
@login_required
@role_required("admin")
def admin_casebooks():
    """Painel admin detalhado de mensagens, casos e experiência prática."""
    refresh_knowledge_state(force_reindex=False)
    return render_template(
        "admin_casebooks.html",
        casebooks=_build_admin_casebooks_payload(),
        title="Governança detalhada",
    )


@bp.route("/admin/event-reports")
@login_required
@role_required("admin")
def admin_event_reports():
    """Painel admin para rever reportes de evento operacionais."""
    events = [_event_report_view(event) for event in list_event_reports()]
    filters = _build_event_report_filters(events)
    filtered_events = _filter_event_reports(events, filters)
    return render_template(
        "admin_event_reports.html",
        events=filtered_events,
        stats=_event_report_stats(events, filtered_events),
        filters=filters,
        reports_return_to=_current_request_return_to(),
        title="Reportes de Evento",
    )


@bp.route("/admin/event-reports/print", methods=["GET", "POST"])
@login_required
@role_required("admin")
def print_event_reports():
    """Relatório imprimível dos reportes selecionados."""
    selected_ids = _selected_event_report_ids()
    all_events = [_event_report_view(event) for event in list_event_reports()]
    if selected_ids:
        selected = [event for event in all_events if event.get("id") in selected_ids]
    else:
        selected = all_events
    return render_template(
        "admin_event_reports_print.html",
        events=selected,
        selected_count=len(selected),
        generated_at_label=_local_iso_to_label(datetime.now(timezone.utc).isoformat()),
        auto_print=(request.values.get("print") == "1"),
        title="Relatório de Eventos",
    )


@bp.route("/admin/event-reports/<event_id>")
@login_required
@role_required("admin")
def event_report_detail(event_id: str):
    """Página de detalhe e edição de um reporte de evento."""
    event = get_event_report(event_id)
    if not event:
        abort(404)
    return_to = _safe_return_to(request.args.get("return_to")) or url_for("admin.admin_event_reports")
    return render_template(
        "admin_event_report_detail.html",
        event=_event_report_view(event),
        reports_return_to=return_to,
        status_options=_event_report_status_options(),
        tag_options=EVENT_REPORT_TAG_OPTIONS,
        title=f"Reporte {event.get('id', '')}",
    )


@bp.route("/admin/event-reports/<event_id>/photo")
@login_required
@role_required("admin")
def event_report_photo(event_id: str):
    """Mostrar a foto anexada ao reporte de evento."""
    event = get_event_report(event_id)
    if not event:
        abort(404)
    photo_path = event_report_photo_path(event)
    if not photo_path:
        abort(404)
    return send_file(photo_path, mimetype=event.get("foto_mime_type") or None, as_attachment=False)


@bp.route("/admin/event-reports/<event_id>/edit", methods=["POST"])
@login_required
@role_required("admin")
def edit_event_report(event_id: str):
    """Guardar revisão administrativa de um reporte de evento."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.event_report_detail", event_id=event_id)
    try:
        tag = validate_required_text(request.form.get("tag", ""), "Tipo", max_length=60).upper()
        if tag == "OBSERVAÇÃO":
            tag = "OBSERVACAO"
        if tag not in EVENT_REPORT_TAG_OPTIONS:
            raise ValueError("Escolhe uma tag válida para o reporte.")
        local = validate_required_text(request.form.get("local", ""), "Local", max_length=200)
        description = validate_required_text(request.form.get("descricao_processada", ""), "Descrição", max_length=5000)
        original_description = (request.form.get("descricao_original") or "").strip()
        status = (request.form.get("estado") or "").strip().lower()
        if status not in EVENT_REPORT_STATUS_OPTIONS:
            raise ValueError("Escolhe um estado válido para o reporte.")
        update_event_report(
            event_id,
            {
                "tag": tag,
                "local": local,
                "descricao_original": original_description,
                "descricao_processada": description,
                "estado": status,
                "nota_admin": request.form.get("nota_admin", ""),
                "revisto_por": session.get("username", "admin"),
                "revisto_em": datetime.now(timezone.utc).isoformat(),
            },
        )
        flash("Reporte de evento atualizado.", "success")
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
    return redirect(return_to)


@bp.route("/admin/bot/practice-experience/import", methods=["POST"])
@login_required
@role_required("admin")
def import_practice_experience():
    """Importar experiência prática de manobras como padrões estruturados governados."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot", _anchor="casebooks")
    uploaded_file = request.files.get("practice_file")
    source = None
    source_filename = PRACTICE_EXPERIENCE_SOURCE_FILENAME
    if uploaded_file and uploaded_file.filename:
        source_filename = os.path.basename(uploaded_file.filename)
        if not source_filename.lower().endswith(".json"):
            flash("Carrega um ficheiro .json de experiência prática.", "error")
            return redirect(return_to)
        source = uploaded_file.read()
    else:
        local_source = _practice_experience_source_path()
        if local_source.exists():
            source_filename = local_source.name
            source = local_source

    if source is None:
        flash(f"Carrega {PRACTICE_EXPERIENCE_SOURCE_FILENAME} em knowledge ou envia um JSON de experiência prática.", "error")
        return redirect(return_to)

    try:
        feedback_status = validate_operational_feedback_status(request.form.get("feedback_status", "approved"))
        source_records, stats = load_practice_experience_records_from_json(source)
        records = prepare_practice_experience_records_for_import(
            source_records,
            source_filename=source_filename,
            imported_by=session["username"],
            feedback_status=feedback_status,
        )
        save_practice_experience_records(
            services.store,
            records,
            source_filename=source_filename,
            updated_by=session["username"],
            replace_source=bool(request.form.get("replace_source")),
        )
        flash(
            f"Experiência prática carregada: {stats['raw_rows']} manobras agregadas em "
            f"{stats['pattern_count']} padrões. Tipos: {stats['maneuver_types_label']}.",
            "success",
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
    except Exception as exc:
        logger.exception("Falha inesperada ao importar experiência prática.")
        flash(f"Falha inesperada ao importar experiência prática: {exc}", "error")
    return redirect(return_to)


@bp.route("/admin/bot/practice-experience/<record_id>/feedback", methods=["POST"])
@login_required
@role_required("admin")
def update_practice_experience(record_id: str):
    """Guardar a decisão admin sobre um padrão importado de experiência prática."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot", _anchor="casebooks")
    try:
        feedback_status = validate_operational_feedback_status(request.form.get("feedback_status", ""))
        feedback_note = (request.form.get("feedback_note") or "").strip()
        if feedback_status in {"avoid", "review"} and not feedback_note:
            raise ValueError("Indica uma nota para justificar esta decisão sobre a experiência prática.")
        update_practice_experience_feedback(
            services.store,
            record_id,
            feedback_status=feedback_status,
            feedback_note=feedback_note,
            feedback_by=session["username"],
        )
        flash("Experiência prática atualizada.", "success")
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
    return redirect(return_to)


@bp.route("/admin/bot/practice-experience/<record_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_practice_experience(record_id: str):
    """Remover um padrão importado de experiência prática."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot", _anchor="casebooks")
    removed = delete_practice_experience_record(
        services.store,
        record_id,
        deleted_by=session["username"],
    )
    flash("Experiência prática removida." if removed else "Experiência prática não encontrada.", "success" if removed else "error")
    return redirect(return_to)


@bp.route("/admin/bot/practice-experience/clear", methods=["POST"])
@login_required
@role_required("admin")
def clear_practice_experience():
    """Limpar todos os padrões importados de experiência prática."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot", _anchor="casebooks")
    removed = clear_practice_experience_records(services.store, cleared_by=session["username"])
    flash(f"Foram removidos {removed} padrão(ões) de experiência prática.", "success")
    return redirect(return_to)


@bp.route("/admin/casebooks/messages/<message_id>/feedback", methods=["POST"])
@login_required
@role_required("admin")
def admin_casebooks_message_feedback(message_id: str):
    """Guardar a decisão admin sobre a reutilização de uma resposta do chat."""
    owner_username = (request.form.get("owner_username") or "").strip().lower()
    conversation_id = (request.form.get("conversation_id") or "").strip()
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot", _anchor="casebooks")
    try:
        if not owner_username or not conversation_id:
            raise ValueError("Mensagem inválida para revisão.")
        feedback_status = validate_feedback_status(request.form.get("feedback_status", ""))
        feedback_note = (request.form.get("feedback_note") or "").strip()
        feedback_correction = (request.form.get("feedback_correction") or "").strip()
        if feedback_status == "corrected" and not feedback_correction:
            raise ValueError("Para guardar uma correção reutilizável, preenche a resposta corrigida.")
        if feedback_status == "review" and not feedback_note:
            raise ValueError("Para manter bloqueada para revisão, indica o motivo.")
        services.store.update_message_feedback(
            username=owner_username,
            conversation_id=conversation_id,
            message_id=message_id,
            feedback_status=feedback_status,
            feedback_note=feedback_note,
            feedback_correction=feedback_correction,
            feedback_correction_document=(request.form.get("feedback_correction_document") or "").strip(),
            feedback_updated_by=session["username"],
        )
        sync_feedback_correction_eval_case(
            services.store,
            owner_username,
            conversation_id,
            message_id,
            source="web",
        )
        flash("Decisão sobre a mensagem guardada.", "success")
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
    return redirect(return_to)


@bp.route("/admin/casebooks/maneuvers/<maneuver_id>/feedback", methods=["POST"])
@login_required
@role_required("admin")
def admin_casebooks_maneuver_feedback(maneuver_id: str):
    """Guardar a validação admin de um caso operacional do casebook."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot", _anchor="casebooks")
    try:
        feedback_status = validate_operational_feedback_status(request.form.get("feedback_status", ""))
        feedback_note = (request.form.get("feedback_note") or "").strip()
        if feedback_status in {"avoid", "review"} and not feedback_note:
            raise ValueError("Indica uma nota para justificar esta decisão operacional.")
        services.store.update_maneuver_case_feedback(
            maneuver_id=maneuver_id,
            feedback_status=feedback_status,
            feedback_note=feedback_note,
            feedback_by=session["username"],
        )
        flash("Validação do caso operacional guardada.", "success")
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
    return redirect(return_to)


@bp.route("/admin/users")
@login_required
@role_required("admin")
def admin_users():
    """Página de gestão de utilizadores do sistema."""
    return render_template("admin_users.html", users=_admin_users_payload(), title="Utilizadores")


@bp.route("/admin/users/<username>", methods=["POST"])
@login_required
@role_required("admin")
def admin_update_user(username: str):
    """Atualizar o role e os dados de perfil de um utilizador."""
    target_username = username.strip().lower()
    try:
        existing_user = services.store.get_user_profile(target_username)
        if not existing_user:
            raise ValueError("Utilizador não encontrado.")
        login_email = validate_email(request.form.get("login_email", ""))
        updated_role = validate_role(request.form.get("role", ""))
        full_name = validate_required_text(request.form.get("full_name", ""), "Nome completo")
        organization = validate_required_text(request.form.get("organization", ""), "Agência/entidade")
        phone = validate_phone(request.form.get("phone", ""))
        whatsapp_number = validate_whatsapp_phone(request.form.get("whatsapp_number", ""), required=False)
        whatsapp_opt_in = request.form.get("whatsapp_opt_in", "") == "1"
        new_password_raw = request.form.get("new_password", "")
        new_password = validate_password(new_password_raw) if new_password_raw.strip() else ""
        if whatsapp_opt_in and not whatsapp_number:
            raise ValueError("Se ativares WhatsApp, tens de indicar o respetivo número.")

        effective_target_username = login_email

        if updated_role == "admin" and effective_target_username != session.get("username"):
            existing_admins = [
                u for u in services.store.list_users()
                if (u.get("role") or "").strip().lower() == "admin" and u.get("username") != target_username
            ]
            if existing_admins:
                flash("Já existe um administrador no sistema. Só pode haver 1 admin.", "error")
                return redirect(url_for("admin.admin_users"))

        if login_email != target_username:
            existing_user = services.store.rename_user(target_username, login_email)
            effective_target_username = existing_user["username"]

        services.store.update_user_profile(
            effective_target_username,
            full_name=full_name,
            organization=organization,
            email=effective_target_username,
            phone=phone,
            whatsapp_number=whatsapp_number,
            whatsapp_opt_in=whatsapp_opt_in,
            whatsapp_opt_in_at=_resolved_whatsapp_opt_in_at(existing_user, whatsapp_number, whatsapp_opt_in),
        )
        updated_user = services.store.set_user_role(effective_target_username, updated_role)
        if new_password:
            services.store.reset_user_password(effective_target_username, new_password)
        if session.get("username") == target_username:
            session["username"] = effective_target_username
            session["role"] = updated_user["role"]
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("admin.admin_users"))
    except Exception:
        logger.exception("Falha inesperada ao atualizar utilizador %s.", target_username)
        flash("Falha inesperada ao atualizar o utilizador.", "error")
        return redirect(url_for("admin.admin_users"))

    flash(f"Utilizador {effective_target_username} atualizado.", "success")
    return redirect(url_for("admin.admin_users"))


@bp.route("/admin/users/<username>/whatsapp-check", methods=["POST"])
@login_required
@role_required("admin")
def admin_check_user_whatsapp(username: str):
    target_username = username.strip().lower()
    profile = services.store.get_user_profile(target_username)
    if not profile:
        return jsonify({"ok": False, "summary": "Utilizador não encontrado."}), 404

    service = getattr(services, "whatsapp_service", None)
    result = verify_user_whatsapp(profile, service, services.store, source="admin_verify")
    refreshed_user = build_user_whatsapp_view(
        services.store.get_user_profile(target_username) or profile,
        service,
        services.store,
    )
    http_status = 200 if result.get("ok") else 400
    return jsonify({"ok": bool(result.get("ok")), "result": result, "user": refreshed_user}), http_status


@bp.route("/admin/users/<username>/delete", methods=["POST"])
@login_required
@role_required("admin")
def admin_delete_user(username: str):
    """Apagar a conta de um utilizador do sistema."""
    target_username = username.strip().lower()
    if session.get("username") == target_username:
        flash("Não podes apagar a tua própria conta enquanto estás autenticado.", "error")
        return redirect(url_for("admin.admin_users"))
    try:
        services.store.delete_user(target_username)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("admin.admin_users"))
    except Exception:
        logger.exception("Falha inesperada ao apagar utilizador %s.", target_username)
        flash("Falha inesperada ao apagar o utilizador.", "error")
        return redirect(url_for("admin.admin_users"))

    flash(f"Utilizador {target_username} apagado.", "success")
    return redirect(url_for("admin.admin_users"))


@bp.route("/admin/documents")
@login_required
@role_required("admin")
def admin_documents():
    """Página de gestão de documentos da base de conhecimento."""
    refresh_knowledge_state(force_reindex=False)
    docs = services.store.list_documents()
    document_filters = _build_document_filters(docs)
    filtered_docs = _filter_documents(docs, document_filters)
    try:
        rag_stats = services.rag.index_summary()
    except Exception as exc:
        rag_stats = {
            "document_count": 0, "chunk_count": 0, "embedded_chunks": 0,
            "index_backend": getattr(services.index_store, "backend_name", "unknown"),
            "index_error": str(exc),
        }
    reindex_status = current_reindex_status_payload()
    return render_template(
        "admin_documents.html",
        docs=filtered_docs,
        docs_total=len(docs),
        document_filters=document_filters,
        documents_return_to=_documents_return_to(),
        rag_stats=rag_stats,
        reindex_status=reindex_status,
        title="Gestão de Documentos",
    )


@bp.route("/documents", methods=["POST"])
@login_required
@role_required("admin")
def add_document():
    """Guardar um novo documento de texto na base de conhecimento e reindexar."""
    if not _manual_knowledge_authoring_enabled():
        flash(
            "Criação manual de documentos desativada. Usa upload de ficheiros oficiais ou a pasta knowledge/.",
            "error",
        )
        return redirect(url_for("admin.admin_documents"))
    try:
        title = validate_required_text(request.form.get("title", ""), "Título", max_length=200)
        content = validate_required_text(request.form.get("content", ""), "Conteúdo", max_length=50000)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("admin.admin_documents"))
    filename = services.store.save_document(title, content, created_by=session["username"])
    if safe_rebuild_index(force=False):
        flash(f"Documento {filename} indexado.", "success")
    else:
        flash(f"Documento {filename} guardado, mas a reindexação falhou: {services.rag.last_index_error}", "error")
    return redirect(url_for("admin.admin_documents"))


@bp.route("/documents/upload", methods=["POST"])
@login_required
@role_required("admin")
def upload_documents():
    """Fazer upload de um ou mais ficheiros para a base de conhecimento e reindexar."""
    uploaded_files = [item for item in request.files.getlist("files") if item and item.filename]
    if not uploaded_files:
        flash("Seleciona pelo menos um ficheiro.", "error")
        return redirect(url_for("admin.admin_documents"))
    stored = []
    failed = []
    for uploaded_file in uploaded_files:
        try:
            filename = services.store.save_uploaded_document(uploaded_file, created_by=session["username"])
            stored.append(filename)
        except Exception as exc:
            failed.append(f"{uploaded_file.filename}: {exc}")
    if stored:
        if safe_rebuild_index(force=False):
            flash(f"Foram indexados {len(stored)} ficheiro(s): {', '.join(stored)}.", "success")
        else:
            flash("Os ficheiros foram guardados, mas a reindexação falhou: " + services.rag.last_index_error, "error")
    if failed:
        flash("Falhas no upload: " + " | ".join(failed), "error")
    return redirect(url_for("admin.admin_documents"))


@bp.route("/knowledge/reindex", methods=["POST"])
@login_required
@role_required("admin")
def reindex_knowledge():
    """Iniciar uma reindexação incremental da base de conhecimento."""
    started = start_reindex_job(force=False)
    status_payload = current_reindex_status_payload()
    wants_json = (
        request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "fetch"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    if wants_json:
        if started and status_payload.get("state") != "running":
            status_payload = {
                **status_payload, "state": "running", "phase": "queued",
                "message": "A iniciar reindexação...",
                "progress_pct": 1.0,
                "error": "",
            }
        return jsonify({"started": started, "status": status_payload, "message": "Reindexação incremental iniciada." if started else "Já existe uma reindexação em curso."}), 202 if started else 200
    if started:
        flash("Reindexação incremental iniciada. O progresso aparece no painel documental.", "success")
    else:
        flash("Já existe uma reindexação em curso.", "error")
    return redirect(request.referrer or url_for("dashboard_bp.dashboard"))


@bp.route("/api/knowledge/reindex-status")
@login_required
def reindex_status():
    """API que retorna o estado atual da reindexação do conhecimento."""
    return jsonify(current_reindex_status_payload())


@bp.route("/documents/<name>")
@login_required
def document_detail(name: str):
    """Página de detalhe de um documento da base de conhecimento."""
    refresh_knowledge_state(force_reindex=False)
    document = services.store.get_document(name)
    if not document:
        abort(404)
    document_return_to = _safe_return_to(request.args.get("return_to")) or url_for("admin.admin_documents")
    try:
        document_text = services.store.get_document_text(name)
    except Exception as exc:
        document_text = f"Erro ao ler conteúdo extraído: {exc}"
    return render_template(
        "document_detail.html",
        document=document,
        document_text=document_text,
        document_return_to=document_return_to,
    )


@bp.route("/documents/<name>/download")
@login_required
def download_document(name: str):
    """Descarregar o ficheiro original de um documento da base de conhecimento."""
    refresh_knowledge_state(force_reindex=False)
    try:
        file_path = services.store.get_document_file_path(name)
    except Exception:
        abort(404)
    return send_file(file_path, as_attachment=True, download_name=os.path.basename(file_path))


@bp.route("/documents/<name>/edit", methods=["POST"])
@login_required
@role_required("admin")
def edit_document(name: str):
    """Guardar o conteúdo editado de um documento de texto e reindexar."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_documents")
    if not _manual_knowledge_authoring_enabled():
        flash(
            "Edição manual de documentos desativada. Atualiza o ficheiro original e volta a indexar.",
            "error",
        )
        return redirect(url_for("admin.document_detail", name=name, return_to=return_to))
    content = request.form.get("content", "").strip()
    try:
        services.store.update_document_text(name=name, content=content, updated_by=session["username"])
        if safe_rebuild_index(force=False):
            flash(f"Documento {name} atualizado e reindexado.", "success")
        else:
            flash(f"Documento {name} atualizado, mas a reindexação falhou: {services.rag.last_index_error}", "error")
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
    return redirect(
        url_for(
            "admin.document_detail",
            name=name,
            return_to=return_to,
        )
    )


@bp.route("/documents/<name>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_document(name: str):
    """Remover um documento da base de conhecimento e reindexar."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_documents")
    try:
        services.store.delete_document(name)
        if safe_rebuild_index(force=False):
            flash(f"Documento {name} removido do conhecimento.", "success")
        else:
            flash(f"Documento {name} removido, mas a reindexação falhou: {services.rag.last_index_error}", "error")
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(return_to)
    return redirect(return_to)


@bp.route("/documents/bulk-delete", methods=["POST"])
@login_required
@role_required("admin")
def bulk_delete_documents():
    """Remover vários documentos filtrados/selecionados da base de conhecimento."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_documents")
    selected_names = []
    for raw_name in request.form.getlist("document_names"):
        clean_name = raw_name.strip()
        if clean_name and clean_name not in selected_names:
            selected_names.append(clean_name)
    if not selected_names:
        flash("Seleciona pelo menos um ficheiro para eliminar.", "error")
        return redirect(return_to)

    removed = []
    failed = []
    for name in selected_names:
        try:
            services.store.delete_document(name)
            removed.append(name)
        except ValueError as exc:
            failed.append(f"{name}: {exc}")

    if removed:
        if safe_rebuild_index(force=False):
            flash(f"Foram removidos {len(removed)} ficheiro(s) da base documental.", "success")
        else:
            flash(
                f"Os ficheiros foram removidos, mas a reindexação falhou: {services.rag.last_index_error}",
                "error",
            )
    if failed:
        flash("Falhas na eliminação: " + " | ".join(failed), "error")
    return redirect(return_to)
