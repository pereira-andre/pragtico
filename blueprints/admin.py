"""Admin blueprint — users, documents, status and reindex."""

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
import zipfile

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from domain.error_catalog import flash_error_message

from core import services
from core.admin_backup_config import (
    BACKUP_AUTO_CHECK_THROTTLE_SECONDS,
    BACKUP_FILENAME_RE,
    BACKUP_MANIFEST_FILENAME,
    BACKUP_PACKAGE_DATA_FILENAME,
    BACKUP_PACKAGE_README_FILENAME,
    BOT_DATABASE_EXPORT_KIND,
    DATABASE_EXPORT_VERSION,
    DATABASE_WIPE_CONFIRMATION_PHRASE,
    POSTGRES_DELETE_ORDER,
    POSTGRES_EXPORT_TABLES,
    POSTGRES_INSERT_ORDER,
    SYSTEM_DATABASE_EXPORT_KIND,
    SYSTEM_KNOWLEDGE_ALLOWED_SUFFIXES,
)
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
    archive_resolved_event_reports,
    delete_event_report,
    delete_event_reports,
    event_report_photo_path,
    expire_new_event_reports,
    get_event_report,
    list_event_reports,
    update_event_report,
)
from core.admin_status import load_admin_status
from core.audit_log import AUDIT_CATEGORIES, audit_dir, audit_summary, iter_audit_events, write_audit_event
from core.helpers import login_required, role_required
from core.knowledge_runtime import (
    current_reindex_status_payload,
    refresh_knowledge_state,
    safe_rebuild_index,
    start_reindex_job,
)
from core.operational_test_suite import (
    cleanup_operational_test_records,
    operational_test_inventory,
    railway_bot_test_export_bytes,
    run_operational_flow_suite,
)
from core.chat_feedback import sync_feedback_correction_eval_case
from core.feedback_governance import feedback_governance_state, governance_options
from core.bot_insights import (
    build_bot_monitor_snapshot,
    build_exceptions,
    build_feedback_governance_snapshot,
    build_learning_signals,
    build_pipeline_snapshot,
    build_quality_snapshot,
    build_sources_snapshot,
    build_tuning_map_snapshot,
    compute_health_score,
)
from core.bot_settings import DEFAULTS as BOT_SETTINGS_DEFAULTS, load_bot_settings, reset_bot_settings, save_bot_settings
from core.bot_eval_runs import load_bot_eval_run_history, record_bot_eval_run
from storage.maneuver_case_helpers import build_case_environment_signature, rank_similar_maneuver_cases
from storage.utils import _local_iso_to_label

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)
_backup_lock = threading.Lock()
_backup_auto_lock = threading.Lock()
_backup_auto_running = False
_backup_auto_last_check = 0.0

PRACTICE_EXPERIENCE_SOURCE_FILENAME = PRACTICE_EXPERIENCE_KNOWLEDGE_FILENAME
ADMIN_CASEBOOK_DEFAULT_LIMIT = 8
ADMIN_CASEBOOK_ALLOWED_LIMITS = (8, 16, 40, 80)


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
    role_order = {"admin": 0, "piloto": 1, "agente": 2}
    users = [
        build_user_whatsapp_view(user, service, services.store)
        for user in services.store.list_users()
    ]
    return sorted(
        users,
        key=lambda item: (
            role_order.get((item.get("role") or "").strip().lower(), 9),
            (item.get("organization") or "").strip().lower(),
            (item.get("full_name") or item.get("username") or "").strip().lower(),
        ),
    )


def _admin_users_summary(users: list[dict]) -> dict:
    def profile_complete(user: dict) -> bool:
        return bool(user.get("full_name") and user.get("organization") and user.get("email") and user.get("phone"))

    complete_total = sum(1 for user in users if profile_complete(user))
    whatsapp_total = sum(1 for user in users if user.get("whatsapp_number") and user.get("whatsapp_opt_in"))
    whatsapp_ok_total = sum(1 for user in users if user.get("whatsapp_status_ok"))
    role_counts = Counter((user.get("role") or "operacional").strip().lower() for user in users)
    return {
        "total": len(users),
        "complete_total": complete_total,
        "incomplete_total": max(len(users) - complete_total, 0),
        "whatsapp_total": whatsapp_total,
        "whatsapp_ok_total": whatsapp_ok_total,
        "admin_total": role_counts.get("admin", 0),
        "pilot_total": role_counts.get("piloto", 0),
        "agent_total": role_counts.get("agente", 0),
    }


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
        filename = uploaded_file.filename.lower()
        raw_bytes = uploaded_file.read()
        if filename.endswith(".zip"):
            try:
                with zipfile.ZipFile(BytesIO(raw_bytes)) as archive:
                    names = [name for name in archive.namelist() if name.lower().endswith(".json")]
                    preferred = BACKUP_PACKAGE_DATA_FILENAME if BACKUP_PACKAGE_DATA_FILENAME in names else names[0] if names else ""
                    if not preferred:
                        raise ValueError("O pacote ZIP não contém nenhum ficheiro JSON.")
                    raw_payload = archive.read(preferred).decode("utf-8-sig")
            except zipfile.BadZipFile as exc:
                raise ValueError("ZIP de backup inválido.") from exc
        else:
            raw_payload = raw_bytes.decode("utf-8-sig")
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


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off", ""}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except ValueError:
        value = default
    return max(value, minimum)


def _backup_dir() -> Path:
    configured = os.getenv("BACKUP_DIR", "").strip()
    if configured:
        base_dir = Path(configured)
    else:
        data_dir = getattr(services, "DATA_DIR", "") or str(Path(current_app.root_path) / "data")
        base_dir = Path(data_dir) / "backups"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _backup_manifest_path() -> Path:
    return _backup_dir() / BACKUP_MANIFEST_FILENAME


def _format_size(size_bytes: int | float | None) -> str:
    size = float(size_bytes or 0)
    units = ("B", "KB", "MB", "GB")
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    if unit == "B":
        return f"{int(size)} {unit}"
    return f"{size:.1f} {unit}"


def _backup_filename(created_at: datetime) -> str:
    return f"pragtico-backup-{created_at.strftime('%Y%m%d-%H%M%S')}.zip"


def _is_backup_filename(filename: str) -> bool:
    return bool(BACKUP_FILENAME_RE.match(filename or ""))


def _read_backup_manifest() -> list[dict]:
    path = _backup_manifest_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Falha ao ler manifesto de backups.")
        return []
    records = payload.get("records") if isinstance(payload, dict) else []
    return [item for item in records if isinstance(item, dict)]


def _write_backup_manifest(records: list[dict]) -> None:
    path = _backup_manifest_path()
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _backup_table_counts(payload: dict) -> dict:
    body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    tables = body.get("tables") if isinstance(body.get("tables"), dict) else {}
    table_counts = {
        table: len(rows)
        for table, rows in tables.items()
        if isinstance(rows, list)
    }
    messages = tables.get("messages") if isinstance(tables.get("messages"), list) else []
    channel_events = tables.get("channel_events") if isinstance(tables.get("channel_events"), list) else []
    whatsapp_messages = [
        item for item in messages
        if str(item.get("channel") or "").strip().lower() == "whatsapp"
        or str(item.get("channel_user_id") or "").strip()
    ]
    bot_database = body.get("bot_database") if isinstance(body.get("bot_database"), dict) else {}
    return {
        "tables": table_counts,
        "total_records": sum(table_counts.values()),
        "users": table_counts.get("app_users", 0),
        "conversations": table_counts.get("conversations", 0),
        "messages": table_counts.get("messages", 0),
        "whatsapp_messages": len(whatsapp_messages),
        "whatsapp_events": len(channel_events),
        "knowledge_files": len(body.get("knowledge_files") or []),
        "feedback_eval_cases": len(bot_database.get("feedback_eval_cases") or []),
        "maneuver_cases": len(bot_database.get("maneuver_cases") or []),
    }


def _build_backup_readme(*, payload: dict, filename: str, created_by: str, source: str, counts: dict) -> str:
    exported_at = payload.get("exported_at") or datetime.now(timezone.utc).isoformat()
    backend = payload.get("backend") or "--"
    table_counts = counts.get("tables") or {}
    table_lines = [
        f"- {table}: {count}"
        for table, count in sorted(table_counts.items())
    ] or ["- Sem tabelas exportadas neste ambiente."]
    return "\n".join(
        [
            "# Backup PRAGtico",
            "",
            f"Ficheiro: {filename}",
            f"Criado em: {exported_at}",
            f"Criado por: {created_by}",
            f"Origem: {source}",
            f"Backend: {backend}",
            "",
            "## Conteudo do pacote",
            "",
            f"- `{BACKUP_PACKAGE_DATA_FILENAME}`: dados completos para reposicao.",
            f"- `{BACKUP_PACKAGE_README_FILENAME}`: este resumo.",
            "",
            "## O que fica incluido",
            "",
            "- Utilizadores, roles, perfis, numeros WhatsApp, opt-in e hashes de password.",
            "- Escalas, navios, manobras, historico operacional, notas e estados.",
            "- Conversas, mensagens, feedback, metadados de canal e eventos WhatsApp.",
            "- Estado runtime, definicoes operacionais, casos de avaliacao e casos de manobra.",
            "- Ficheiros de conhecimento `.txt`, `.md` e `.json` exportaveis.",
            "",
            "## Contagens principais",
            "",
            f"- Registos totais de tabelas: {counts.get('total_records', 0)}",
            f"- Utilizadores: {counts.get('users', 0)}",
            f"- Conversas: {counts.get('conversations', 0)}",
            f"- Mensagens: {counts.get('messages', 0)}",
            f"- Mensagens WhatsApp: {counts.get('whatsapp_messages', 0)}",
            f"- Eventos WhatsApp: {counts.get('whatsapp_events', 0)}",
            f"- Ficheiros de conhecimento: {counts.get('knowledge_files', 0)}",
            "",
            "## Tabelas exportadas",
            "",
            *table_lines,
            "",
            "## Reposicao",
            "",
            "1. Entra como admin.",
            "2. Abre a pagina de Backups.",
            "3. Carrega este ZIP ou o `backup.json` extraido.",
            "4. Usa `Juntar` para acrescentar/atualizar dados ou `Substituir` para trocar a base pelas tabelas do backup.",
            "5. Depois de repor, confirma que existe pelo menos um admin funcional.",
            "",
            "## Notas de seguranca",
            "",
            "- Este pacote contem dados sensiveis. Guarda-o fora do repositorio e em local protegido.",
            "- O backup guarda hashes de password, nao passwords em texto claro.",
            "- Conversas e eventos WhatsApp podem conter dados pessoais e contexto operacional.",
            "- Antes de uma reposicao destrutiva, cria sempre outro backup do estado atual.",
            "",
        ]
    )


def _backup_retention_count() -> int:
    return _env_int("BACKUP_RETENTION_COUNT", 30, minimum=1)


def _backup_record_from_file(path: Path) -> dict:
    stat = path.stat()
    created_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    return {
        "filename": path.name,
        "created_at": created_at,
        "created_at_label": _local_iso_to_label(created_at),
        "created_by": "desconhecido",
        "status": "available",
        "status_label": "Disponível",
        "backend": "",
        "size_bytes": stat.st_size,
        "size_label": _format_size(stat.st_size),
        "total_records": 0,
        "counts": {},
    }


def _list_backup_records() -> list[dict]:
    base_dir = _backup_dir()
    records_by_file = {
        str(item.get("filename") or ""): dict(item)
        for item in _read_backup_manifest()
        if _is_backup_filename(str(item.get("filename") or ""))
    }
    for path in sorted(base_dir.glob("pragtico-backup-*.json")):
        if not _is_backup_filename(path.name):
            continue
        records_by_file.setdefault(path.name, _backup_record_from_file(path))

    records = []
    for filename, record in records_by_file.items():
        path = base_dir / filename
        if not path.exists():
            continue
        size_bytes = path.stat().st_size
        record["size_bytes"] = size_bytes
        record["size_label"] = _format_size(size_bytes)
        record["created_at_label"] = _local_iso_to_label(record.get("created_at"))
        record.setdefault("status", "available")
        record.setdefault("status_label", "Disponível")
        record.setdefault("counts", {})
        records.append(record)
    records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return records


def _save_backup_records(records: list[dict]) -> list[dict]:
    records = sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    retention = _backup_retention_count()
    kept = records[:retention]
    removed = records[retention:]
    for record in removed:
        filename = str(record.get("filename") or "")
        if _is_backup_filename(filename):
            try:
                (_backup_dir() / filename).unlink(missing_ok=True)
            except OSError:
                logger.exception("Falha ao remover backup antigo %s.", filename)
    _write_backup_manifest(kept)
    return kept


def _create_system_backup(*, created_by: str, source: str = "manual") -> dict:
    with _backup_lock:
        created_at_dt = datetime.now().astimezone()
        filename = _backup_filename(created_at_dt)
        backup_path = _backup_dir() / filename
        if backup_path.exists():
            filename = f"pragtico-backup-{created_at_dt.strftime('%Y%m%d-%H%M%S-%f')}.zip"
            backup_path = _backup_dir() / filename

        payload = _build_system_database_export()
        counts = _backup_table_counts(payload)
        payload["backup"] = {
            "created_by": created_by,
            "created_at": payload.get("exported_at") or created_at_dt.isoformat(),
            "source": source,
            "filename": filename,
            "counts": counts,
        }
        body = json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n"
        tmp_path = backup_path.with_suffix(".tmp")
        readme = _build_backup_readme(
            payload=payload,
            filename=filename,
            created_by=created_by,
            source=source,
            counts=counts,
        )
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(BACKUP_PACKAGE_DATA_FILENAME, body)
            archive.writestr(BACKUP_PACKAGE_README_FILENAME, readme)
        tmp_path.replace(backup_path)

        record = {
            "filename": filename,
            "created_at": payload["backup"]["created_at"],
            "created_at_label": _local_iso_to_label(payload["backup"]["created_at"]),
            "created_by": created_by,
            "source": source,
            "status": "completed",
            "status_label": "Concluído",
            "backend": payload.get("backend", ""),
            "size_bytes": backup_path.stat().st_size,
            "size_label": _format_size(backup_path.stat().st_size),
            "total_records": counts["total_records"],
            "counts": counts,
        }

        records = [record] + [item for item in _list_backup_records() if item.get("filename") != filename]
        _save_backup_records(records)
        write_audit_event(
            "backup.create",
            category="backups",
            actor=created_by,
            severity="critical" if source == "pre_wipe" else "warning",
            result="success",
            resource="backup",
            resource_id=filename,
            details={
                "source": source,
                "size_bytes": record["size_bytes"],
                "counts": counts,
            },
        )
        return record


def _backup_auto_config() -> dict:
    interval_hours = _env_int("BACKUP_AUTO_INTERVAL_HOURS", 24, minimum=1)
    return {
        "enabled": _env_flag("BACKUP_AUTO_ENABLED", "1"),
        "interval_hours": interval_hours,
        "interval_seconds": interval_hours * 3600,
        "retention_count": _backup_retention_count(),
    }


def _parse_backup_datetime(value: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _backup_next_due_at(records: list[dict], config: dict) -> str:
    completed = [
        _parse_backup_datetime(item.get("created_at"))
        for item in records
        if item.get("status") in {"completed", "available"}
    ]
    completed = [item for item in completed if item is not None]
    if not completed:
        return ""
    return (max(completed) + timedelta(seconds=config["interval_seconds"])).isoformat()


def _backup_auto_due(records: list[dict], config: dict) -> bool:
    if not config["enabled"]:
        return False
    next_due_at = _backup_next_due_at(records, config)
    if not next_due_at:
        return True
    next_dt = _parse_backup_datetime(next_due_at)
    return bool(next_dt and datetime.now(timezone.utc) >= next_dt.astimezone(timezone.utc))


def _backup_page_payload() -> dict:
    records = _list_backup_records()
    config = _backup_auto_config()
    backup_dir = _backup_dir()
    storage_bytes = sum(int(item.get("size_bytes") or 0) for item in records)
    last_backup = records[0] if records else None
    next_due_at = _backup_next_due_at(records, config)
    return {
        "records": records,
        "last_backup": last_backup,
        "next_due_at": next_due_at,
        "next_due_label": _local_iso_to_label(next_due_at) if next_due_at else "Quando houver tráfego",
        "auto": {
            **config,
            "state": "online" if config["enabled"] else "neutral",
            "label": "Ativo" if config["enabled"] else "Desativado",
            "due": _backup_auto_due(records, config),
        },
        "backup_dir": str(backup_dir),
        "retention_count": config["retention_count"],
        "storage_bytes": storage_bytes,
        "storage_label": _format_size(storage_bytes),
        "wipe": {
            "confirmation_phrase": DATABASE_WIPE_CONFIRMATION_PHRASE,
            "preserved_user": session.get("username", ""),
        },
        "metrics": [
            {"label": "Backups guardados", "value": len(records), "detail": f"mantidos até {config['retention_count']} pacotes"},
            {"label": "Último backup", "value": last_backup.get("created_at_label") if last_backup else "Sem backup", "detail": last_backup.get("filename", "") if last_backup else "cria um pacote inicial"},
            {"label": "Registos no último", "value": last_backup.get("total_records", 0) if last_backup else 0, "detail": "tabelas aplicacionais"},
            {"label": "Espaço usado", "value": _format_size(storage_bytes), "detail": "armazenamento local"},
        ],
    }


def _audit_filters_from_request() -> dict:
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    if date_from and len(date_from) == 10:
        date_from = f"{date_from}T00:00:00+00:00"
    if date_to and len(date_to) == 10:
        date_to = f"{date_to}T23:59:59+00:00"
    return {
        "q": (request.args.get("q") or "").strip(),
        "actor": (request.args.get("actor") or "").strip(),
        "action": (request.args.get("action") or "").strip(),
        "category": (request.args.get("category") or "").strip(),
        "severity": (request.args.get("severity") or "").strip(),
        "result": (request.args.get("result") or "").strip(),
        "date_from": date_from,
        "date_to": date_to,
    }


def _audit_display_event(event: dict) -> dict:
    request_payload = event.get("request") if isinstance(event.get("request"), dict) else {}
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    return {
        **event,
        "at_label": _local_iso_to_label(event.get("at")),
        "method": request_payload.get("method", ""),
        "path": request_payload.get("path", ""),
        "endpoint": request_payload.get("endpoint", ""),
        "ip": request_payload.get("ip", ""),
        "details_preview": _preview_text(json.dumps(details, ensure_ascii=False, sort_keys=True), limit=180),
    }


def _audit_page_payload() -> dict:
    filters = _audit_filters_from_request()
    limit = _env_int("AUDIT_PAGE_LIMIT", 300, minimum=50)
    events = iter_audit_events(filters, limit=limit)
    summary = audit_summary(events)
    return {
        "filters": filters,
        "events": [_audit_display_event(item) for item in events],
        "summary": summary,
        "audit_dir": str(audit_dir()),
        "limit": limit,
        "category_options": ("", *AUDIT_CATEGORIES),
        "severity_options": ("", "info", "warning", "critical"),
        "result_options": ("", "success", "failed", "denied", "skipped"),
        "metrics": [
            {"label": "Eventos visíveis", "value": summary["total"], "detail": f"limite {limit}"},
            {"label": "Críticos", "value": summary["critical"], "detail": "ações admin e segurança"},
            {"label": "Falhas/negados", "value": summary["failed"], "detail": "a rever"},
            {"label": "Utilizadores", "value": len(summary["actors"]), "detail": "atores distintos"},
        ],
    }


def _authenticate_database_wipe(password: str) -> dict:
    username = (session.get("username") or "").strip().lower()
    if not username:
        raise ValueError("Sessão expirada. Faz login novamente.")
    if not password:
        raise ValueError("Indica a password do admin atual.")
    profile = services.auth_service.authenticate(username, password)
    if not profile or (profile.get("role") or "").strip().lower() != "admin":
        raise ValueError("Password inválida ou utilizador sem perfil admin.")
    return profile


def _validate_database_wipe_confirmation(*, password: str, phrase: str, checkbox: bool) -> dict:
    profile = _authenticate_database_wipe(password)
    if not checkbox:
        raise ValueError("Confirma explicitamente que compreendes a limpeza da base.")
    if " ".join((phrase or "").strip().split()) != DATABASE_WIPE_CONFIRMATION_PHRASE:
        raise ValueError(f"Frase de confirmação inválida. Escreve exatamente: {DATABASE_WIPE_CONFIRMATION_PHRASE}")
    return profile


def _wipe_database_preserving_admin(username: str) -> dict:
    clean_username = (username or "").strip().lower()
    if not clean_username:
        raise ValueError("Admin atual indisponível para preservar acesso.")
    store = services.store
    connect = getattr(store, "_connect", None)
    if not callable(connect):
        raise ValueError("Limpeza completa só está disponível com backend PostgreSQL.")

    stats = {"tables": 0, "records": 0, "preserved_admin": clean_username}
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, role FROM app_users WHERE username = %s AND role = 'admin'",
                (clean_username,),
            )
            if not cur.fetchone():
                raise ValueError("O admin atual não existe na base ou deixou de ser admin.")
            for table in POSTGRES_DELETE_ORDER:
                if table == "app_users":
                    continue
                cur.execute(f"DELETE FROM {table}")
                stats["tables"] += 1
                if cur.rowcount and cur.rowcount > 0:
                    stats["records"] += cur.rowcount
            cur.execute("DELETE FROM app_users WHERE username <> %s", (clean_username,))
            stats["tables"] += 1
            if cur.rowcount and cur.rowcount > 0:
                stats["records"] += cur.rowcount
        conn.commit()
    return stats


def _safe_backup_path(filename: str) -> Path:
    if not _is_backup_filename(filename):
        abort(404)
    path = (_backup_dir() / filename).resolve()
    base_dir = _backup_dir().resolve()
    if base_dir not in path.parents or not path.exists() or not path.is_file():
        abort(404)
    return path


def _start_auto_backup_if_due() -> None:
    global _backup_auto_last_check, _backup_auto_running
    if current_app.config.get("TESTING"):
        return
    now = time.monotonic()
    with _backup_auto_lock:
        if _backup_auto_running or now - _backup_auto_last_check < BACKUP_AUTO_CHECK_THROTTLE_SECONDS:
            return
        _backup_auto_last_check = now
    try:
        records = _list_backup_records()
        config = _backup_auto_config()
        if not _backup_auto_due(records, config):
            return
    except Exception:
        logger.exception("Falha ao avaliar backup automático.")
        return

    app = current_app._get_current_object()
    with _backup_auto_lock:
        if _backup_auto_running:
            return
        _backup_auto_running = True

    def worker() -> None:
        global _backup_auto_running
        try:
            with app.app_context():
                auto_config = _backup_auto_config()
                _create_system_backup(
                    created_by="automatic",
                    source="automatic",
                )
        except Exception:
            logger.exception("Falha no backup automático.")
        finally:
            with _backup_auto_lock:
                _backup_auto_running = False

    threading.Thread(target=worker, name="system-backup-auto", daemon=True).start()


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
                feedback_error_type=str(item.get("feedback_error_type") or "").strip(),
                feedback_scope=str(item.get("feedback_scope") or "").strip(),
                feedback_destination=str(item.get("feedback_destination") or "").strip(),
                feedback_criticality=str(item.get("feedback_criticality") or "").strip(),
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
        "new_expires_at_label": _local_iso_to_label(event.get("new_expires_at")) if event.get("new_expires_at") else "",
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
    open_events = [item for item in events if item.get("estado") != "arquivado"]
    closed_events = [item for item in events if item.get("estado") == "arquivado"]
    return {
        "total": len(events),
        "filtered": len(filtered_events),
        "with_photo": sum(1 for item in events if item.get("has_photo")),
        "open": len(open_events),
        "closed": len(closed_events),
        "resolved_open": sum(1 for item in open_events if item.get("estado") == "resolvido"),
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
                "feedback_error_type",
                "feedback_scope",
                "feedback_destination",
                "feedback_criticality",
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
        governance_state = feedback_governance_state(item)
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
                "feedback_error_type": item.get("feedback_error_type", ""),
                "feedback_scope": item.get("feedback_scope", ""),
                "feedback_destination": item.get("feedback_destination", ""),
                "feedback_criticality": item.get("feedback_criticality", ""),
                "feedback_governance": governance_state,
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
        "feedback_governance_options": governance_options(),
    }


@bp.before_app_request
def ensure_automatic_backups_started():
    _start_auto_backup_if_due()


@bp.route("/admin/backups")
@login_required
@role_required("admin")
def admin_backups():
    """Página de criação e consulta de backups completos."""
    return render_template("admin_backups.html", backup=_backup_page_payload(), title="Backups")


@bp.route("/admin/auditoria")
@login_required
@role_required("admin")
def admin_audit():
    """Página de consulta do audit log aplicacional."""
    return render_template("admin_audit.html", audit=_audit_page_payload(), title="Auditoria")


@bp.route("/admin/auditoria/export.json")
@login_required
@role_required("admin")
def export_audit_log_json():
    """Exportar eventos de auditoria filtrados em JSON."""
    payload = {
        "exported_at": _exported_at(),
        "filters": _audit_filters_from_request(),
        "events": iter_audit_events(_audit_filters_from_request(), limit=_env_int("AUDIT_EXPORT_LIMIT", 5000, minimum=100)),
    }
    write_audit_event(
        "audit.export_json",
        category="seguranca",
        severity="critical",
        result="success",
        resource="audit_log",
        details={"count": len(payload["events"]), "filters": payload["filters"]},
    )
    return _json_download_response(payload, f"pragtico-audit-{datetime.now().strftime('%Y%m%d-%H%M')}.json")


@bp.route("/admin/auditoria/export.jsonl")
@login_required
@role_required("admin")
def download_audit_log():
    """Exportar eventos de auditoria filtrados em JSONL."""
    filters = _audit_filters_from_request()
    events = iter_audit_events(filters, limit=_env_int("AUDIT_EXPORT_LIMIT", 5000, minimum=100))
    body = "\n".join(json.dumps(_json_safe(item), ensure_ascii=False, sort_keys=True) for item in events) + ("\n" if events else "")
    write_audit_event(
        "audit.export_jsonl",
        category="seguranca",
        severity="critical",
        result="success",
        resource="audit_log",
        details={"count": len(events), "filters": filters},
    )
    response = current_app.response_class(body, mimetype="application/x-ndjson; charset=utf-8")
    response.headers["Content-Disposition"] = f"attachment; filename=pragtico-audit-{datetime.now().strftime('%Y%m%d-%H%M')}.jsonl"
    return response


@bp.route("/admin/backups/create", methods=["POST"])
@login_required
@role_required("admin")
def create_system_backup():
    """Criar um pacote de backup completo."""
    try:
        record = _create_system_backup(
            created_by=session.get("username", "admin"),
            source="manual",
        )
        flash(
            f"Backup criado: {record['filename']} ({record['size_label']}).",
            "success",
        )
    except Exception as exc:
        logger.exception("Falha ao criar backup.")
        write_audit_event(
            "backup.create",
            category="backups",
            severity="critical",
            result="failed",
            resource="backup",
            details={"error": str(exc)},
        )
        flash(f"Falha ao criar backup: {exc}", "error")
    return redirect(url_for("admin.admin_backups"))


@bp.route("/admin/backups/<path:filename>/download")
@login_required
@role_required("admin")
def download_system_backup(filename: str):
    """Download de um pacote de backup guardado."""
    path = _safe_backup_path(filename)
    write_audit_event(
        "backup.download",
        category="backups",
        severity="critical",
        result="success",
        resource="backup",
        resource_id=filename,
        details={"size_bytes": path.stat().st_size},
    )
    return send_file(path, as_attachment=True, download_name=path.name)


@bp.route("/admin/backups/<path:filename>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_system_backup(filename: str):
    """Apagar um pacote de backup local."""
    path = _safe_backup_path(filename)
    try:
        path.unlink()
        records = [item for item in _list_backup_records() if item.get("filename") != filename]
        _write_backup_manifest(records)
        write_audit_event(
            "backup.delete",
            category="backups",
            severity="critical",
            result="success",
            resource="backup",
            resource_id=filename,
        )
        flash(f"Backup apagado: {filename}.", "success")
    except OSError as exc:
        write_audit_event(
            "backup.delete",
            category="backups",
            severity="critical",
            result="failed",
            resource="backup",
            resource_id=filename,
            details={"error": str(exc)},
        )
        flash(f"Falha ao apagar backup: {exc}", "error")
    return redirect(url_for("admin.admin_backups"))


@bp.route("/admin/backups/wipe-database", methods=["POST"])
@login_required
@role_required("admin")
def wipe_system_database():
    """Limpar a base aplicacional preservando o admin atual."""
    try:
        profile = _validate_database_wipe_confirmation(
            password=request.form.get("admin_password", ""),
            phrase=request.form.get("confirmation_phrase", ""),
            checkbox=request.form.get("understand_wipe", "") == "1",
        )
        backup_record = _create_system_backup(
            created_by=profile["username"],
            source="pre_wipe",
        )
        stats = _wipe_database_preserving_admin(profile["username"])
        write_audit_event(
            "database.wipe",
            category="base_dados",
            actor=profile["username"],
            severity="critical",
            result="success",
            resource="system_database",
            details={
                "backup_filename": backup_record["filename"],
                "deleted_records": stats["records"],
                "preserved_admin": stats["preserved_admin"],
            },
        )
        flash(
            "Base limpa com sucesso. "
            f"Backup pré-limpeza: {backup_record['filename']}. "
            f"Registos apagados: {stats['records']}. Admin preservado: {stats['preserved_admin']}.",
            "success",
        )
    except ValueError as exc:
        write_audit_event(
            "database.wipe",
            category="base_dados",
            severity="critical",
            result="denied",
            resource="system_database",
            details={"error": str(exc)},
        )
        flash(flash_error_message(str(exc)), "error")
    except Exception as exc:
        logger.exception("Falha inesperada ao limpar base de dados.")
        write_audit_event(
            "database.wipe",
            category="base_dados",
            severity="critical",
            result="failed",
            resource="system_database",
            details={"error": str(exc)},
        )
        flash(f"Falha inesperada ao limpar base de dados: {exc}", "error")
    return redirect(url_for("admin.admin_backups"))


@bp.route("/admin/status")
@login_required
@role_required("admin")
def admin_status():
    """Painel de estado do sistema para administradores."""
    refresh_knowledge_state(force_reindex=False)
    return render_template("admin_status.html", admin=load_admin_status())


@bp.route("/admin/tests", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_operational_tests():
    """Página admin para correr testes controlados de escalas e manobras."""
    result = None
    if request.method == "POST":
        action = request.form.get("action", "run").strip().lower()
        if action == "cleanup":
            result = cleanup_operational_test_records()
            flash(
                f"Limpeza concluída: {result.get('deleted_count', 0)} escala(s) de teste removida(s)."
                if result.get("state") == "passed"
                else "A limpeza dos dados de teste encontrou erros.",
                "success" if result.get("state") == "passed" else "error",
            )
        else:
            keep_records = request.form.get("keep_records") == "on"
            result = run_operational_flow_suite(
                actor_username=session.get("username", "admin"),
                cleanup_after=not keep_records,
            )
            flash(
                f"Testes operacionais concluídos: {result.get('passed_steps', 0)}/{result.get('total_steps', 0)} passo(s) passaram.",
                "success" if result.get("state") == "passed" else "error",
            )
    return render_template(
        "admin_operational_tests.html",
        result=result,
        inventory=operational_test_inventory(),
    )


@bp.route("/admin/tests/export.<export_format>")
@login_required
@role_required("admin")
def export_operational_tests(export_format: str):
    """Exportar os 150 ensaios Railway para depuração."""
    try:
        payload, mimetype, filename = railway_bot_test_export_bytes(export_format)
    except ValueError:
        abort(404)
    return send_file(
        BytesIO(payload),
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )


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
    feedback_governance = build_feedback_governance_snapshot()
    exceptions = build_exceptions(limit=12)
    tuning = build_tuning_map_snapshot(settings=settings, quality=quality)
    pipeline = build_pipeline_snapshot(tuning=tuning, quality=quality, sources=sources, exceptions=exceptions)
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
        feedback_governance=feedback_governance,
        exceptions=exceptions,
        monitor=monitor,
        pipeline=pipeline,
        tuning=tuning,
        settings=settings,
        settings_defaults=BOT_SETTINGS_DEFAULTS,
        eval_run_history=load_bot_eval_run_history(),
        casebooks=_build_admin_casebooks_payload(),
        title="Bot",
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
        write_audit_event(
            "bot.settings.update",
            category="configuracao",
            severity="critical",
            result="success",
            resource="bot_settings",
            details={"keys": sorted(updates.keys())},
        )
        flash("Definições do bot atualizadas.", "success")
    except Exception as exc:
        logger.exception("Falha ao guardar definições do bot.")
        write_audit_event(
            "bot.settings.update",
            category="configuracao",
            severity="critical",
            result="failed",
            resource="bot_settings",
            details={"error": str(exc)},
        )
        flash(f"Falha ao guardar definições do bot: {exc}", "error")
    return redirect(return_to)


@bp.route("/admin/bot/settings/reset", methods=["POST"])
@login_required
@role_required("admin")
def admin_bot_settings_reset():
    """Repor definições do bot para os valores por defeito."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_bot")
    reset_bot_settings(updated_by=session.get("username") or "admin")
    write_audit_event(
        "bot.settings.reset",
        category="configuracao",
        severity="critical",
        result="success",
        resource="bot_settings",
    )
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
        run = record_bot_eval_run(
            snapshot,
            triggered_by=session.get("username") or "admin",
            trigger="manual",
        )
        passed = snapshot.get("passed_total", 0)
        total = snapshot.get("active_cases_total", 0)
        failed = snapshot.get("failed_total", 0)
        suffix = f" Run {run.get('run_id')} guardado." if run else ""
        write_audit_event(
            "bot.evals.rerun",
            category="configuracao",
            severity="warning",
            result="success",
            resource="bot_evals",
            details={"passed": passed, "total": total, "failed": failed, "run_id": run.get("run_id") if run else ""},
        )
        flash(f"Evals executados: {passed}/{total} passam ({failed} a falhar).{suffix}", "success")
    except Exception as exc:
        logger.exception("Falha a correr evals.")
        write_audit_event(
            "bot.evals.rerun",
            category="configuracao",
            severity="warning",
            result="failed",
            resource="bot_evals",
            details={"error": str(exc)},
        )
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
            "operational_diagnostic": result.get("operational_diagnostic", {}),
            "trace": result.get("trace", {}),
        }
    )


@bp.route("/admin/bot/export")
@login_required
@role_required("admin")
def export_bot_database():
    """Exportar dados de governação/aprendizagem do bot em JSON."""
    write_audit_event(
        "bot.database.export",
        category="configuracao",
        severity="critical",
        result="success",
        resource="bot_database",
    )
    return _json_download_response(_build_bot_database_export(), "pragtico-bot-database.json")


@bp.route("/admin/system/export")
@login_required
@role_required("admin")
def export_system_database():
    """Exportar dados aplicacionais e ficheiros knowledge em JSON."""
    write_audit_event(
        "system.database.export",
        category="base_dados",
        severity="critical",
        result="success",
        resource="system_database",
    )
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
        write_audit_event(
            "bot.database.import",
            category="configuracao",
            severity="critical",
            result="success",
            resource="bot_database",
            details=stats,
        )
        flash(
            "Base do bot importada: "
            f"{stats['feedback_eval_cases']} eval(s), {stats['chat_feedback']} feedback(s) de chat, "
            f"{stats['maneuver_feedback']} validação(ões) de manobra, "
            f"{stats['practice_records']} padrão(ões) de experiência. "
            f"Ignorados: {stats['skipped']}.",
            "success",
        )
    except ValueError as exc:
        write_audit_event(
            "bot.database.import",
            category="configuracao",
            severity="critical",
            result="failed",
            resource="bot_database",
            details={"error": str(exc)},
        )
        flash(flash_error_message(str(exc)), "error")
    except Exception as exc:
        logger.exception("Falha inesperada ao importar base do bot.")
        write_audit_event(
            "bot.database.import",
            category="configuracao",
            severity="critical",
            result="failed",
            resource="bot_database",
            details={"error": str(exc)},
        )
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
        write_audit_event(
            "system.database.import",
            category="base_dados",
            severity="critical",
            result="success",
            resource="system_database",
            details={"mode": mode, "stats": stats},
        )
        flash(
            "Base do sistema importada em modo "
            f"{'substituir' if mode == 'replace' else 'juntar'}: "
            f"{stats.get('records', 0)} registo(s), {stats.get('files', stats.get('tables', 0))} conjunto(s), "
            f"{stats.get('knowledge_files', 0)} ficheiro(s) knowledge.",
            "success",
        )
    except ValueError as exc:
        write_audit_event(
            "system.database.import",
            category="base_dados",
            severity="critical",
            result="failed",
            resource="system_database",
            details={"mode": mode, "error": str(exc)},
        )
        flash(flash_error_message(str(exc)), "error")
    except Exception as exc:
        logger.exception("Falha inesperada ao importar base do sistema.")
        write_audit_event(
            "system.database.import",
            category="base_dados",
            severity="critical",
            result="failed",
            resource="system_database",
            details={"mode": mode, "error": str(exc)},
        )
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
    """Painel admin para rever relatórios de evento operacionais."""
    expire_new_event_reports()
    events = [_event_report_view(event) for event in list_event_reports()]
    filters = _build_event_report_filters(events)
    filtered_events = _filter_event_reports(events, filters)
    open_events = [event for event in filtered_events if event.get("estado") != "arquivado"]
    closed_events = [event for event in filtered_events if event.get("estado") == "arquivado"]
    return render_template(
        "admin_event_reports.html",
        events=open_events,
        closed_events=closed_events,
        archive_resolved_event_ids=[event.get("id") for event in open_events if event.get("estado") == "resolvido"],
        stats=_event_report_stats(events, filtered_events),
        filters=filters,
        reports_return_to=_current_request_return_to(),
        title="Relatórios de Evento",
    )


@bp.route("/admin/event-reports/print", methods=["GET", "POST"])
@login_required
@role_required("admin")
def print_event_reports():
    """Relatório imprimível dos relatórios selecionados."""
    expire_new_event_reports()
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


@bp.route("/admin/event-reports/archive-resolved", methods=["POST"])
@login_required
@role_required("admin")
def archive_resolved_event_reports_route():
    """Arquivar relatórios de evento resolvidos."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_event_reports")
    selected_ids = _selected_event_report_ids()
    if not selected_ids:
        flash("Não há relatórios resolvidos para arquivar nesta vista.", "error")
        return redirect(return_to)
    try:
        archived_count = archive_resolved_event_reports(
            selected_ids,
            archived_by=session.get("username", "admin"),
            archived_at=datetime.now(timezone.utc).isoformat(),
        )
    except OSError:
        logger.exception("Failed to archive resolved event reports")
        flash("Não foi possível arquivar os relatórios resolvidos.", "error")
        return redirect(return_to)
    if archived_count == 1:
        flash("1 relatório resolvido arquivado.", "success")
    elif archived_count:
        flash(f"{archived_count} relatórios resolvidos arquivados.", "success")
    else:
        flash("Nenhum relatório resolvido correspondente encontrado.", "error")
    return redirect(return_to)


@bp.route("/admin/event-reports/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_event_reports_route():
    """Apagar relatórios de evento selecionados."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_event_reports")
    selected_ids = _selected_event_report_ids()
    if not selected_ids:
        flash("Seleciona pelo menos um relatório de evento para apagar.", "error")
        return redirect(return_to)
    try:
        deleted_count = delete_event_reports(selected_ids)
    except OSError:
        logger.exception("Failed to delete event reports")
        flash("Não foi possível apagar os relatórios selecionados.", "error")
        return redirect(return_to)
    if deleted_count == 1:
        flash("1 relatório de evento apagado.", "success")
    elif deleted_count:
        flash(f"{deleted_count} relatórios de evento apagados.", "success")
    else:
        flash("Nenhum relatório de evento correspondente encontrado.", "error")
    return redirect(return_to)


@bp.route("/admin/event-reports/<event_id>")
@login_required
@role_required("admin")
def event_report_detail(event_id: str):
    """Página de detalhe e edição de um relatório de evento."""
    expire_new_event_reports()
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
        title=f"Relatório {event.get('id', '')}",
    )


@bp.route("/admin/event-reports/<event_id>/photo")
@login_required
@role_required("admin")
def event_report_photo(event_id: str):
    """Mostrar a foto anexada ao relatório de evento."""
    expire_new_event_reports()
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
    """Guardar revisão administrativa de um relatório de evento."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.event_report_detail", event_id=event_id)
    try:
        tag = validate_required_text(request.form.get("tag", ""), "Tipo", max_length=60).upper()
        if tag == "OBSERVAÇÃO":
            tag = "OBSERVACAO"
        if tag not in EVENT_REPORT_TAG_OPTIONS:
            raise ValueError("Escolhe uma tag válida para o relatório.")
        local = validate_required_text(request.form.get("local", ""), "Local", max_length=200)
        description = validate_required_text(request.form.get("descricao_processada", ""), "Descrição", max_length=5000)
        original_description = (request.form.get("descricao_original") or "").strip()
        status = (request.form.get("estado") or "").strip().lower()
        if status not in EVENT_REPORT_STATUS_OPTIONS:
            raise ValueError("Escolhe um estado válido para o relatório.")
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
        flash("Relatório de evento atualizado.", "success")
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
    return redirect(return_to)


@bp.route("/admin/event-reports/<event_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_event_report_route(event_id: str):
    """Apagar um relatório de evento."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_event_reports")
    try:
        deleted = delete_event_report(event_id)
    except OSError:
        logger.exception("Failed to delete event report %s", event_id)
        flash("Não foi possível apagar o relatório de evento.", "error")
        return redirect(return_to)
    if deleted:
        flash("Relatório de evento apagado.", "success")
    else:
        flash("Relatório de evento não encontrado.", "error")
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
            feedback_error_type=(request.form.get("feedback_error_type") or "").strip(),
            feedback_scope=(request.form.get("feedback_scope") or "").strip(),
            feedback_destination=(request.form.get("feedback_destination") or "").strip(),
            feedback_criticality=(request.form.get("feedback_criticality") or "").strip(),
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
    users = _admin_users_payload()
    return render_template(
        "admin_users.html",
        users=users,
        users_summary=_admin_users_summary(users),
        title="Utilizadores",
    )


@bp.route("/admin/users/create", methods=["POST"])
@login_required
@role_required("admin")
def admin_create_user():
    """Criar um utilizador diretamente pela gestão admin."""
    try:
        username = validate_email(request.form.get("email", ""))
        password = validate_password(request.form.get("password", ""))
        role = validate_role(request.form.get("role", "piloto"))
        full_name = validate_required_text(request.form.get("full_name", ""), "Nome completo")
        organization = validate_required_text(request.form.get("organization", ""), "Agência/entidade")
        phone = validate_phone(request.form.get("phone", ""))
        whatsapp_number = validate_whatsapp_phone(request.form.get("whatsapp_number", ""), required=False)
        whatsapp_opt_in = request.form.get("whatsapp_opt_in", "") == "1"
        if whatsapp_opt_in and not whatsapp_number:
            raise ValueError("Se ativares WhatsApp, tens de indicar o respetivo número.")
        if role == "admin" and any(
            (user.get("role") or "").strip().lower() == "admin"
            for user in services.store.list_users()
        ):
            raise ValueError("Já existe um administrador no sistema. Só pode haver 1 admin.")
        created_user = services.store.create_user(
            username=username,
            password=password,
            role=role,
            full_name=full_name,
            organization=organization,
            email=username,
            phone=phone,
            whatsapp_number=whatsapp_number,
            whatsapp_opt_in=whatsapp_opt_in,
            whatsapp_opt_in_at=_resolved_whatsapp_opt_in_at(None, whatsapp_number, whatsapp_opt_in),
        )
        write_audit_event(
            "user.create",
            category="utilizadores",
            severity="critical" if role == "admin" else "warning",
            result="success",
            resource="app_user",
            resource_id=created_user.get("username", username),
            details={"role": role, "whatsapp_opt_in": whatsapp_opt_in},
        )
        flash(f"Utilizador {created_user.get('username', username)} criado.", "success")
    except ValueError as exc:
        write_audit_event(
            "user.create",
            category="utilizadores",
            severity="warning",
            result="failed",
            resource="app_user",
            resource_id=(request.form.get("email") or "").strip().lower(),
            details={"error": str(exc)},
        )
        flash(flash_error_message(str(exc)), "error")
    except Exception:
        logger.exception("Falha inesperada ao criar utilizador.")
        write_audit_event(
            "user.create",
            category="utilizadores",
            severity="warning",
            result="failed",
            resource="app_user",
            resource_id=(request.form.get("email") or "").strip().lower(),
        )
        flash("Falha inesperada ao criar o utilizador.", "error")
    return redirect(url_for("admin.admin_users"))


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
        previous_role = (existing_user.get("role") or "").strip().lower()
        previous_username = existing_user.get("username", target_username)
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
        write_audit_event(
            "user.permissions.update",
            category="utilizadores",
            severity="critical",
            result="success",
            resource="app_user",
            resource_id=effective_target_username,
            details={
                "previous_username": previous_username,
                "new_username": effective_target_username,
                "previous_role": previous_role,
                "new_role": updated_user.get("role", updated_role),
                "password_reset": bool(new_password),
                "whatsapp_opt_in": whatsapp_opt_in,
            },
        )
    except ValueError as exc:
        write_audit_event(
            "user.permissions.update",
            category="utilizadores",
            severity="critical",
            result="failed",
            resource="app_user",
            resource_id=target_username,
            details={"error": str(exc)},
        )
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("admin.admin_users"))
    except Exception:
        logger.exception("Falha inesperada ao atualizar utilizador %s.", target_username)
        write_audit_event(
            "user.permissions.update",
            category="utilizadores",
            severity="critical",
            result="failed",
            resource="app_user",
            resource_id=target_username,
        )
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
        write_audit_event(
            "user.delete",
            category="utilizadores",
            severity="critical",
            result="denied",
            resource="app_user",
            resource_id=target_username,
            details={"reason": "self_delete_blocked"},
        )
        flash("Não podes apagar a tua própria conta enquanto estás autenticado.", "error")
        return redirect(url_for("admin.admin_users"))
    try:
        services.store.delete_user(target_username)
        write_audit_event(
            "user.delete",
            category="utilizadores",
            severity="critical",
            result="success",
            resource="app_user",
            resource_id=target_username,
        )
    except ValueError as exc:
        write_audit_event(
            "user.delete",
            category="utilizadores",
            severity="critical",
            result="failed",
            resource="app_user",
            resource_id=target_username,
            details={"error": str(exc)},
        )
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("admin.admin_users"))
    except Exception:
        logger.exception("Falha inesperada ao apagar utilizador %s.", target_username)
        write_audit_event(
            "user.delete",
            category="utilizadores",
            severity="critical",
            result="failed",
            resource="app_user",
            resource_id=target_username,
        )
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
