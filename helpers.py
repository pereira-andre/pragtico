"""Shared helpers, decorators, and operational logic for PRAGtico blueprints."""

import logging
import math
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone
from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for

import services
from chat_actions import (
    ACTION_SPECS,
    action_for_maneuver_type,
    build_operational_action_prompt,
    build_pending_action_update_prompt,
    display_missing_field_labels,
    extract_json_object,
    extract_pending_field_updates,
    format_action_summary,
    infer_maneuver_type,
    looks_like_operational_command,
    merge_action_candidate,
    normalize_action_candidate,
    required_missing_fields,
    resolve_maneuver,
    resolve_port_call,
    visible_port_calls_from_activity,
)
from cost_engine import (
    UP_NORMAL,
    UP_SHIFT_ALONG,
    ManoeuvreInput,
    ManoeuvreType,
    calculate_scale_cost,
    format_cost_summary,
)
from migration_service import get_database_runtime_status
from reindex_scheduler import DeferredTaskScheduler, next_gemini_quota_reset_utc
from storage import (
    PASSWORD_HASH_METHOD,
    format_constraint_labels,
    is_user_profile_complete,
    normalize_constraint_codes,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session / profile helpers
# ---------------------------------------------------------------------------

def ensure_session_user_profile() -> bool:
    """Sync the session role with the stored user profile, clearing the session if user is gone."""
    username = session.get("username", "").strip().lower()
    if not username:
        return False
    profile = services.store.get_user_profile(username)
    if profile:
        session_role = (session.get("role") or "").strip().lower()
        profile_role = (profile.get("role") or "").strip().lower()
        if session_role in {"admin", "agente", "piloto"} and session_role != profile_role:
            if session_role == "admin":
                profile = services.store.set_user_role(username, "admin")
            else:
                session["role"] = profile_role
                return True
        session["role"] = profile.get("role", session_role or "piloto")
        return True
    session.clear()
    return False


def current_user_profile() -> dict | None:
    """Return the profile dict for the currently authenticated session user, or None."""
    username = session.get("username", "").strip().lower()
    if not username:
        return None
    return services.store.get_user_profile(username)


def session_profile_incomplete() -> bool:
    """Return True if the current session user has an incomplete profile."""
    profile = current_user_profile()
    if not profile:
        return False
    if (profile.get("role") or session.get("role") or "").strip().lower() == "admin":
        return False
    return not is_user_profile_complete(profile)


# ---------------------------------------------------------------------------
# Organization / scope helpers
# ---------------------------------------------------------------------------

def _organization_scope_key(value: str | None) -> str:
    collapsed = " ".join((value or "").strip().split())
    if not collapsed:
        return ""
    normalized = unicodedata.normalize("NFKD", collapsed)
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    return ascii_only.casefold()


def _item_organization_scope_key(item: dict | None) -> str:
    payload = item or {}
    profile = (
        payload.get("agent_profile")
        or payload.get("created_by_profile")
        or payload.get("reported_by_profile")
        or {}
    )
    return _organization_scope_key(profile.get("organization"))


def _current_agent_scope_key() -> str | None:
    if (session.get("role") or "").strip().lower() != "agente":
        return None
    profile = current_user_profile() or {}
    scope_key = _organization_scope_key(profile.get("organization"))
    return scope_key or ""


def ensure_port_call_scope_access(port_call_id: str) -> None:
    """Raise PermissionError if the current agent session has no access to the given port call."""
    scope_key = _current_agent_scope_key()
    if scope_key is None:
        return
    if not scope_key:
        raise PermissionError("O perfil do agente tem de ter uma agência definida.")
    port_call = services.store.get_port_call(port_call_id)
    if _item_organization_scope_key(port_call) != scope_key:
        raise PermissionError("Esta escala pertence a outra agência.")


def filter_port_activity_for_session(port_activity: dict) -> dict:
    """Filter port activity down to entries visible to the current session's agency scope."""
    scope_key = _current_agent_scope_key()
    if scope_key is None:
        return port_activity

    arrivals = [item for item in port_activity.get("arrivals", []) if _item_organization_scope_key(item) == scope_key]
    in_port = [item for item in port_activity.get("in_port", []) if _item_organization_scope_key(item) == scope_key]
    departed = [item for item in port_activity.get("departed", []) if _item_organization_scope_key(item) == scope_key]
    aborted = [item for item in port_activity.get("aborted", []) if _item_organization_scope_key(item) == scope_key]
    planned_maneuvers = [
        item for item in port_activity.get("planned_maneuvers", [])
        if _item_organization_scope_key(item) == scope_key
    ]
    archived_maneuvers = [
        item for item in port_activity.get("archived_maneuvers", [])
        if _item_organization_scope_key(item) == scope_key
    ]

    visible_port_call_ids = {
        item.get("id")
        for item in arrivals + in_port + departed + aborted
        if item.get("id")
    }
    visible_port_call_ids.update(
        item.get("port_call_id")
        for item in planned_maneuvers + archived_maneuvers
        if item.get("port_call_id")
    )

    berthed_map = {}
    for item in in_port:
        berthed_map.setdefault(item.get("berth_label") or "Sem cais atribuído", []).append(item)

    _berth_order = {name: idx for idx, name in enumerate(services.BERTH_OPTIONS)}

    def _berth_sort_key(pair):
        berth_name = pair[0]
        if berth_name in _berth_order:
            return _berth_order[berth_name]
        for option_name, order in _berth_order.items():
            if option_name in berth_name or berth_name in option_name:
                return order
        return 9999

    berthed = [
        {"berth": berth, "count": len(vessels), "vessels": vessels}
        for berth, vessels in sorted(berthed_map.items(), key=_berth_sort_key)
    ]

    planned_groups_map = {}
    for item in planned_maneuvers:
        date_key = item.get("date_key")
        if not date_key:
            continue
        group = planned_groups_map.setdefault(
            date_key,
            {"date_key": date_key, "date_label": item.get("date_label", ""), "total": 0},
        )
        group["total"] += 1
    planned_groups = [planned_groups_map[key] for key in sorted(planned_groups_map.keys())]

    filtered_activity = {
        **port_activity,
        "arrivals": arrivals,
        "in_port": in_port,
        "berthed": berthed,
        "departed": departed,
        "aborted": aborted,
        "planned_maneuvers": planned_maneuvers,
        "archived_maneuvers": archived_maneuvers,
        "planned_groups": planned_groups,
        "departure_candidates": [
            item for item in port_activity.get("departure_candidates", [])
            if item.get("id") in visible_port_call_ids
        ],
        "maneuvers": port_activity.get("maneuvers", []),
    }
    filtered_activity["stats"] = {
        **(port_activity.get("stats") or {}),
        "scheduled_count": len(arrivals),
        "in_port_count": len(in_port),
        "departed_count": len(departed),
        "berth_count": len(berthed),
        "aborted_count": len(aborted),
        "planned_count": len(planned_maneuvers),
        "archive_count": len(archived_maneuvers),
        "pending_count": sum(1 for item in planned_maneuvers if item.get("situation_class") == "pending"),
    }
    return filtered_activity


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def login_required(view):
    """Decorator that redirects unauthenticated requests to the login page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        is_api = request.path.startswith("/api/")
        if not session.get("username"):
            if is_api:
                return jsonify({"error": "Sessão expirada. Faz login novamente."}), 401
            return redirect(url_for("auth.login"))
        if not ensure_session_user_profile():
            if is_api:
                return jsonify({"error": "Sessão expirada. Faz login novamente."}), 401
            flash("Sessao expirada. Inicia sessao novamente.", "error")
            return redirect(url_for("auth.login"))
        if (
            session_profile_incomplete()
            and request.endpoint not in {"auth.profile", "auth.logout", "static", "dashboard_bp.image_asset"}
        ):
            if is_api:
                return jsonify({"error": "Completa o teu perfil antes de usar o sistema."}), 403
            return redirect(url_for("auth.profile", next=request.full_path if request.query_string else request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    """Decorator factory that restricts a view to users with one of the given roles."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                flash("Nao tens permissao para esta acao.", "error")
                return redirect(url_for("dashboard_bp.dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def redirect_to_portal_target(port_call_id: str):
    """Redirect to the scale detail, registration, or dashboard based on the form's redirect_to field."""
    target = request.form.get("redirect_to", "").strip().lower()
    if target == "scale":
        return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))
    if target == "register":
        return redirect(url_for("port_calls.port_call_register"))
    return redirect(url_for("dashboard_bp.dashboard"))


def port_call_scope_required(view):
    """Decorator that enforces agency-scope access control for port call views."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        port_call_id = kwargs.get("port_call_id")
        if not port_call_id:
            return view(*args, **kwargs)
        try:
            ensure_port_call_scope_access(port_call_id)
        except (ValueError, PermissionError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard_bp.dashboard")) if request.method == "GET" else redirect_to_portal_target(port_call_id)
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Reindex / knowledge management
# ---------------------------------------------------------------------------

def safe_rebuild_index(force: bool = False) -> bool:
    """Rebuild the RAG index, returning True on success and storing any error message."""
    try:
        services.rag.rebuild_index(force=force)
        return True
    except Exception as exc:
        services.rag.last_index_error = str(exc)
        logger.exception("Falha na reindexação do conhecimento")
        return False
    finally:
        sync_reindex_retry_schedule()


def start_reindex_job(force: bool = False) -> bool:
    """Start a background reindex thread if none is running, returning True if one was started."""
    with services.reindex_thread_lock:
        if services.reindex_thread and services.reindex_thread.is_alive():
            return False

        def worker():
            safe_rebuild_index(force=force)

        services.reindex_thread = threading.Thread(
            target=worker, name="knowledge-reindex", daemon=True,
        )
        services.rag.mark_reindex_pending()
        if services.reindex_retry_scheduler is not None:
            services.reindex_retry_scheduler.cancel()
        services.reindex_thread.start()
        return True


def sync_reindex_retry_schedule() -> None:
    """Update the retry scheduler based on missing embeddings and quota state."""
    if services.reindex_retry_scheduler is None:
        return
    if services.rag.has_active_reindex_worker():
        return
    try:
        missing_embeddings = bool(services.rag.client) and services.rag.index_has_missing_embeddings()
    except Exception as exc:
        logger.exception("Falha ao validar embeddings pendentes")
        services.rag.last_index_error = str(exc)
        return
    if not missing_embeddings:
        services.reindex_retry_scheduler.cancel()
        return
    if services.rag.is_embedding_quota_exhausted():
        retry_at = next_gemini_quota_reset_utc()
        services.reindex_retry_scheduler.schedule(
            retry_at,
            reason="Quota diária de embeddings esgotada; nova tentativa automática no próximo reset.",
        )
        return
    services.reindex_retry_scheduler.cancel()


def refresh_knowledge_state(force_reindex: bool = False, rebuild_index: bool = True) -> bool:
    """Sync knowledge documents and optionally trigger a reindex, returning True on success."""
    try:
        services.store.list_documents()
    except Exception as exc:
        logger.exception("Falha ao sincronizar a pasta knowledge")
        services.rag.last_index_error = str(exc)
        return False
    if not rebuild_index:
        return True
    try:
        if not force_reindex:
            if services.rag.has_active_reindex_worker():
                return True
            if services.rag.has_pending_reindex():
                return start_reindex_job(force=False)
            sync_reindex_retry_schedule()
            return True
    except Exception as exc:
        logger.exception("Falha ao avaliar o estado do índice documental")
        services.rag.last_index_error = str(exc)
        return False
    return safe_rebuild_index(force=force_reindex)


def current_reindex_status_payload() -> dict:
    """Return a combined reindex status payload including sync summary and retry schedule."""
    status_payload = services.rag.get_reindex_status()
    try:
        sync_summary = services.rag.get_sync_status_summary()
    except Exception as exc:
        logger.exception("Falha ao gerar resumo de sincronização do índice")
        services.rag.last_index_error = str(exc)
        sync_summary = {
            "knowledge_documents": 0, "indexed_documents": 0,
            "missing_embedding_chunks": 0, "semantic_chunk_coverage_pct": 0,
            "fully_embedded_documents": 0, "partially_embedded_documents": 0,
            "documents_with_missing_embeddings": 0, "pending_documents_total": 0,
            "sync_summary": "Resumo de sincronização indisponível.",
            "pending_summary": str(exc), "pending_documents_preview": [],
            "document_sync_rows": [],
        }
    sync_reindex_retry_schedule()
    retry_status = services.reindex_retry_scheduler.status() if services.reindex_retry_scheduler is not None else {}
    with services.reindex_thread_lock:
        thread_alive = bool(services.reindex_thread and services.reindex_thread.is_alive())
    worker_active = thread_alive or services.rag.has_active_reindex_worker()
    status_payload = {
        **status_payload,
        **sync_summary,
        "embedding_provider": "Gemini" if services.rag.client else "indisponivel",
        "query_embedding_status": (
            "blocked" if services.rag.is_embedding_quota_exhausted()
            else "available" if services.rag.client
            else "disabled"
        ),
        "query_embedding_summary": (
            "Pesquisa semântica bloqueada até renovar quota."
            if services.rag.is_embedding_quota_exhausted()
            else "Pesquisa semântica disponível."
            if services.rag.client
            else "Pesquisa semântica indisponível: API key LLM em falta."
        ),
        "scheduled_retry_at": retry_status.get("scheduled_for"),
        "scheduled_retry_eta_seconds": retry_status.get("eta_seconds"),
        "scheduled_retry_reason": retry_status.get("reason", ""),
    }

    if retry_status.get("scheduled") and status_payload.get("state") != "running":
        base_message = status_payload.get("message") or "Índice pronto."
        if "Nova tentativa automática" not in base_message:
            status_payload["message"] = f"{base_message} Nova tentativa automática após reset da quota."
    if status_payload.get("state") != "running":
        return status_payload

    updated_at = status_payload.get("updated_at")
    updated_dt = None
    if updated_at:
        try:
            updated_dt = datetime.fromisoformat(updated_at)
        except ValueError:
            updated_dt = None
    stale_running = False
    if updated_dt is not None:
        stale_running = (datetime.now(updated_dt.tzinfo) - updated_dt).total_seconds() >= 180

    if not worker_active or stale_running:
        return {
            **status_payload,
            "state": "error", "phase": "stale",
            "message": "A reindexação anterior ficou interrompida. Já podes iniciar nova tentativa.",
            "eta_seconds": None,
            "error": status_payload.get("error") or (
                "Thread de reindexação já não está ativa."
                if not worker_active
                else "A reindexação ficou sem progresso visível durante demasiado tempo."
            ),
        }
    return status_payload


def load_admin_status() -> dict:
    """Collect and return a comprehensive status payload for the admin dashboard."""
    ais_status = services.ais_service.dashboard_context()
    local_counts = {
        "users": len(services.store.list_users()),
        "documents": len(services.store.list_documents()),
    }
    if session.get("username"):
        local_counts["conversations"] = len(services.store.list_conversations(session["username"]))

    db_runtime = None
    db_runtime_error = ""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if getattr(services.store, "backend_name", "") == "postgres" and database_url:
        try:
            db_runtime = get_database_runtime_status(database_url)
        except Exception as exc:
            db_runtime_error = str(exc)

    try:
        rag_status = services.rag.index_summary()
    except Exception as exc:
        rag_status = {
            "document_count": 0, "chunk_count": 0, "embedded_chunks": 0,
            "index_backend": getattr(services.index_store, "backend_name", "unknown"),
            "index_error": str(exc),
        }
    rag_reindex_status = current_reindex_status_payload()
    try:
        port_activity = services.store.get_port_activity_snapshot(window_days=5)
    except Exception as exc:
        port_activity = {
            "stats": {"scheduled_count": 0, "in_port_count": 0, "departed_count": 0, "planned_count": 0},
            "arrivals": [], "in_port": [], "departed": [], "planned_maneuvers": [],
            "error": str(exc),
        }

    return {
        "auth_backend": getattr(services.auth_service, "backend_name", "unknown"),
        "auth_method_label": f"Werkzeug · {PASSWORD_HASH_METHOD}",
        "storage_backend": getattr(services.store, "backend_name", "unknown"),
        "rag_backend": getattr(services.index_store, "backend_name", "unknown"),
        "config": {
            "llm_ready": services.rag.client is not None,
            "embeddings_local": services.rag._use_local_embeddings,
            "embedding_model": services.rag.embedding_model if not services.rag._use_local_embeddings else (services.rag.embedding_provider.model_name if services.rag.embedding_provider else "N/A"),
            "weather_ready": bool(os.getenv("WEATHERAPI_KEY", "").strip()),
            "ais_ready": bool(ais_status.get("configured")),
            "database_url_ready": bool(database_url),
            "migrate_on_start": os.getenv("MIGRATE_LOCAL_DATA_ON_START", "1"),
        },
        "startup_migration": services.startup_migration_status,
        "db_runtime": db_runtime,
        "db_runtime_error": db_runtime_error,
        "rag_status": rag_status,
        "rag_reindex_status": rag_reindex_status,
        "ais_status": ais_status,
        "port_activity": port_activity,
        "local_counts": local_counts,
    }


# ---------------------------------------------------------------------------
# Weather / operational snapshot helpers
# ---------------------------------------------------------------------------

def build_weather_timeline(weather_data: dict | None, max_hours: int = 48) -> list[dict]:
    """Flatten hourly weather groups into a single ordered timeline list up to max_hours entries."""
    if not weather_data:
        return []
    timeline = []
    for group in weather_data.get("hourly_groups", []):
        for hour in group.get("hours", []):
            timeline.append({
                **hour,
                "date": group.get("date", ""),
                "day_label": group.get("date", ""),
                "slot_label": f"{group.get('date', '')} {hour.get('time', '')}".strip(),
            })
            if len(timeline) >= max_hours:
                return timeline
    return timeline


def build_operational_snapshot_source(port_activity: dict, max_rows: int = 12) -> dict:
    """Build a chat supplemental source summarizing the current planned maneuvers."""
    lines = [
        "Resumo operacional das manobras planeadas e referências do quadro:",
        (
            f"- Chegadas previstas: {port_activity['stats']['scheduled_count']} | "
            f"Navios em porto: {port_activity['stats']['in_port_count']} | "
            f"Saídas recentes: {port_activity['stats']['departed_count']} | "
            f"Manobras planeadas: {port_activity['stats'].get('planned_count', 0)}"
        ),
    ]
    for item in port_activity.get("planned_maneuvers", [])[:max_rows]:
        lines.append(
            f"- {item['date_label']} | {item['reference_code']} | {item['vessel_name']} | "
            f"{item['maneuver_label']} | situação {item['situation_label']} | "
            f"Hora {item['planned_label']} | "
            f"{item['local_origin']} -> {item['local_destination']} | "
            f"agente {item['agent_label']} | piloto {item['pilot_label']}"
        )
        if item.get("detail_note"):
            lines.append(f"  observações: {item['detail_note']}")
    return {
        "source_id": "OPS1", "document": "estado_operacional_planeadas",
        "chunk_id": 1, "score": 1.0, "retrieval_mode": "operational_snapshot",
        "snippet": "\n".join(lines),
    }


def _operational_query_terms(question: str) -> list[str]:
    seen = set()
    ordered = []
    for token in re.findall(r"[a-z0-9À-ÿ/.-]+", (question or "").lower()):
        clean = token.strip(".-")
        if len(clean) < 2 or clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return ordered


def _score_operational_text(question: str, text: str) -> int:
    haystack = (text or "").lower()
    score = 0
    for token in _operational_query_terms(question):
        if token in haystack:
            score += 2 if len(token) >= 5 else 1
    return score


def _constraint_labels_from_badges(item: dict) -> str:
    labels = [badge.get("label", "") for badge in item.get("constraint_badges", []) if badge.get("label")]
    return ", ".join(labels) or "--"


def build_maneuver_archive_source(question: str, port_activity: dict, max_rows: int = 12) -> dict:
    """Build a chat supplemental source from archived maneuvers ranked by relevance to the question."""
    archive_rows = port_activity.get("archived_maneuvers", [])
    scored_rows = []
    for index, item in enumerate(archive_rows):
        row_text = " | ".join([
            item.get("date_label", ""), item.get("reference_code", ""),
            item.get("vessel_name", ""), item.get("maneuver_label", ""),
            item.get("local_origin", ""), item.get("local_destination", ""),
            item.get("validated_by_label", ""), item.get("executed_by_label", ""),
            item.get("agent_label", ""), item.get("detail_note", ""),
            _constraint_labels_from_badges(item),
        ])
        scored_rows.append((_score_operational_text(question, row_text), index, item))
    scored_rows.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    selected = [item for score, _, item in scored_rows if score > 0][:max_rows]
    if not selected:
        selected = archive_rows[-max_rows:]

    lines = [
        "Arquivo operacional de manobras concluídas:",
        f"- Total no arquivo disponível para consulta: {port_activity['stats'].get('archive_count', 0)}",
    ]
    for item in selected:
        lines.append(
            f"- {item.get('date_label', '--')} | {item.get('reference_code', '--')} | {item.get('vessel_name', '--')} | "
            f"{item.get('maneuver_label', '--')} | Hora {item.get('execution_window_label') or item.get('actual_label') or item.get('planned_label') or '--'} | "
            f"{item.get('local_origin', '--')} -> {item.get('local_destination', '--')} | "
            f"agente {item.get('agent_label', '--')} | validado por {item.get('validated_by_label', '--')} | "
            f"executado por {item.get('executed_by_label', '--')} | rebocadores {item.get('tug_count_label', '--')} | "
            f"restrições {_constraint_labels_from_badges(item)}"
        )
        if item.get("detail_note"):
            lines.append(f"  observações: {item['detail_note']}")
    return {
        "source_id": "OPS2", "document": "arquivo_maneuvers_concluidas",
        "chunk_id": 1, "score": 1.0, "retrieval_mode": "operational_archive",
        "snippet": "\n".join(lines),
    }


def build_scale_registry_source(question: str, port_activity: dict, max_rows: int = 12) -> dict:
    """Build a chat supplemental source from the port call registry ranked by relevance to the question."""
    scale_rows = []
    for group_name in ("arrivals", "in_port", "departed", "aborted"):
        for item in port_activity.get(group_name, []):
            scale_rows.append(item)

    deduped = []
    seen_ids = set()
    for item in scale_rows:
        if item.get("id") in seen_ids:
            continue
        seen_ids.add(item.get("id"))
        deduped.append(item)

    scored_rows = []
    for index, item in enumerate(deduped):
        row_text = " | ".join([
            item.get("reference_code", ""), item.get("vessel_name", ""),
            item.get("berth_label", ""), item.get("last_port", ""),
            item.get("next_port", ""), item.get("status", ""),
            item.get("eta_label", ""), item.get("departure_label", ""),
            item.get("agent_label", ""), item.get("pilot_label", ""),
            item.get("notes", ""),
        ])
        scored_rows.append((_score_operational_text(question, row_text), index, item))
    scored_rows.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    selected = [item for score, _, item in scored_rows if score > 0][:max_rows]
    if not selected:
        selected = deduped[:max_rows]

    lines = [
        "Registo de escalas do portal:",
        (
            f"- Escalas em porto: {port_activity['stats'].get('in_port_count', 0)} | "
            f"chegadas previstas: {port_activity['stats'].get('scheduled_count', 0)} | "
            f"escalas com saída recente: {port_activity['stats'].get('departed_count', 0)}"
        ),
    ]
    for item in selected:
        status_label = (
            "Em porto" if item.get("status") == "in_port"
            else "Concluída" if item.get("status") == "departed"
            else "Abortada" if item.get("approval_status") == "aborted"
            else "Prevista"
        )
        lines.append(
            f"- {item.get('reference_code', '--')} | {item.get('vessel_name', '--')} | estado {status_label} | "
            f"ETA {item.get('eta_label', '--')} | cais {item.get('berth_label', '--')} | "
            f"porto anterior {item.get('last_port', '--') or '--'} | próximo destino {item.get('next_port', '--') or '--'} | "
            f"agente {item.get('agent_label', '--')} | piloto {item.get('pilot_label', '--')}"
        )
        if item.get("notes"):
            lines.append(f"  observações: {item['notes']}")
    return {
        "source_id": "OPS3", "document": "registo_escalas_portal",
        "chunk_id": 1, "score": 1.0, "retrieval_mode": "operational_scales",
        "snippet": "\n".join(lines),
    }


def _looks_like_cost_question(question: str) -> bool:
    clean = (question or "").lower()
    cost_keywords = {
        "custo", "custos", "preço", "preco", "precos", "preços",
        "tarifa", "tarifas", "fatura", "faturação", "faturacao",
        "pilotagem", "taxa", "taxas", "up", "cobrar", "cobrado",
        "pagar", "pagamento", "valor", "estimativa", "orçamento",
        "orcamento", "simulação", "simulacao", "simular",
    }
    return any(kw in clean for kw in cost_keywords)


def build_cost_context_source(question: str, port_activity: dict) -> dict | None:
    """Build a pilotage cost context source if the question appears cost-related, else return None."""
    if not _looks_like_cost_question(question):
        return None
    lines = [
        "Motor de cálculo de custos de pilotagem do Porto de Setúbal (tarifário 2024):",
        f"- UP serviços normais (entrada, saída, atracar): {UP_NORMAL} €/√GT",
        f"- UP mudança ao longo do cais: {UP_SHIFT_ALONG} €/√GT",
        "- Fórmula: Taxa = UP × √GT (raiz quadrada da arqueação bruta, Art. 15º)",
        "- Agravamento +25%: navio sem propulsão ou assistência especial",
        "- Reduções linha regular (Art. 16º): 6-24 escalas -10%, 25-52 -15%, 53-100 -20%, >100 -25%",
        "- Redução -10% cabotagem, -30% escala técnica (só a melhor aplica)",
        "- Pilotagem à ordem: 74.64 €/hora + 25% da taxa base",
        "- Cancelamentos: 30% (2h antes), 50% (1h depois), 100% (no-show), 25% (meteo c/ piloto)",
        "- TUP por tipo: contentores 0.1144/0.0263, RoRo 0.1186/0.0274, passag. 0.0620/0.0263, "
        "tanque/restantes 0.1459/0.0274 (€/GT, 1ºdia/restantes)",
        "- Não inclui rebocadores (privados), amarração, lanchas ou resíduos.",
        "",
    ]
    in_port = port_activity.get("in_port", [])[:3]
    for vessel in in_port:
        gt_str = vessel.get("vessel_gt_t") or vessel.get("vessel_gt") or ""
        gt_clean = gt_str.replace(".", "").replace(",", ".").strip()
        try:
            gt = float(gt_clean)
        except (ValueError, TypeError):
            continue
        if gt <= 0:
            continue
        name = vessel.get("vessel_name", "Navio")
        cost_entry = round(UP_NORMAL * math.sqrt(gt), 2)
        cost_departure = round(UP_NORMAL * math.sqrt(gt), 2)
        lines.append(
            f"- Exemplo {name} (GT {gt:.0f}): entrada ~{cost_entry:.2f}€, "
            f"saída ~{cost_departure:.2f}€, total ~{cost_entry + cost_departure:.2f}€"
        )
    lines.append("")
    lines.append("O utilizador pode pedir estimativas ao bot. Usa a API /api/cost/estimate ou /api/cost/quick para cálculos detalhados.")
    return {
        "source_id": "COST1", "document": "motor_custos_pilotagem",
        "chunk_id": 1, "score": 1.0, "retrieval_mode": "cost_engine",
        "snippet": "\n".join(lines),
    }


def build_operational_chat_sources(question: str) -> list[dict]:
    """Assemble supplemental operational context sources for the chat RAG pipeline."""
    recent_port_activity = services.store.get_port_activity_snapshot(window_days=30)
    historical_port_activity = services.store.get_port_activity_snapshot(window_days=3650)
    sources = [
        build_operational_snapshot_source(recent_port_activity),
        build_maneuver_archive_source(question, historical_port_activity),
        build_scale_registry_source(question, historical_port_activity),
    ]
    cost_source = build_cost_context_source(question, recent_port_activity)
    if cost_source:
        sources.append(cost_source)
    return sources


# ---------------------------------------------------------------------------
# Chat operational action helpers
# ---------------------------------------------------------------------------

def _operational_lookup_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


def pending_action_state_key(username: str, conversation_id: str) -> str:
    """Return the runtime state key for a user's pending chat action in a given conversation."""
    return f"chat_pending_action:{username}:{conversation_id}"


def looks_like_pending_confirmation(question: str) -> bool:
    """Return True if the question is a simple confirmation phrase like 'sim' or 'ok'."""
    clean = _operational_lookup_key(question)
    return clean in {
        "ok", "okay", "okey", "sim", "confirma", "confirmar",
        "confirmado", "podes confirmar", "avanca", "avancar", "segue",
    }


def refresh_proposal_missing_fields(proposal: dict) -> dict:
    """Recompute and update the missing_fields list on a proposal dict in-place."""
    proposal["missing_fields"] = display_missing_field_labels(
        required_missing_fields(proposal.get("action", ""), proposal.get("fields") or {})
    )
    return proposal


def current_visible_port_calls(window_days: int = 120) -> list[dict]:
    """Return port calls visible to the current session filtered by the given window."""
    port_activity = services.store.get_port_activity_snapshot(window_days=window_days)
    port_activity = filter_port_activity_for_session(port_activity)
    return visible_port_calls_from_activity(port_activity)


def current_resolvable_port_calls() -> list[dict]:
    """Return all port calls visible to the session over a 10-year window for action resolution."""
    return current_visible_port_calls(window_days=3650)


def action_target_port_call(port_call_id: str) -> dict:
    """Fetch a port call and enforce agent scope access, returning the decorated record."""
    port_call = services.store.get_port_call(port_call_id)
    if (session.get("role") or "").strip().lower() == "agente":
        ensure_port_call_scope_access(port_call_id)
    return port_call


def heuristic_operational_proposal(question: str, role: str, port_calls: list[dict]) -> dict | None:
    """Apply deterministic pattern matching to derive an operational action proposal from the question."""
    clean = _operational_lookup_key(question)
    if not clean:
        return None

    if re.search(r"\b(registar escala|nova escala|criar escala|register scale)\b", clean):
        from chat_actions import _extract_labelled_values
        extracted = _extract_labelled_values(question)
        vessel_name = extracted.pop("vessel_name", "")
        maneuver_type = "entry"
        if re.search(r"\b(saida|saída|departure)\b", clean):
            maneuver_type = "departure"
        elif re.search(r"\b(mudanca|mudança|shift)\b", clean):
            maneuver_type = "shift"
        proposal = normalize_action_candidate(
            {
                "intent": "action", "action": "create_port_call", "confidence": 0.99,
                "reason": "Heurística: registo de escala com campos extraídos da mensagem.",
                "target": {"vessel_name": vessel_name, "maneuver_type": maneuver_type},
                "fields": extracted, "missing_fields": [],
            },
            role,
        )
        if proposal and proposal.get("intent") == "action":
            return proposal
        return None

    wants_previsto = bool(re.search(r"\b(previsto|prevista|planeado|planeada)\b", clean))

    action_verb = ""
    if re.search(r"\b(aprova|approve|aprovar|valida|validar|confirma|confirmar)\b", clean):
        action_verb = "approve"
    elif re.search(r"\b(aborta|abortar|cancela|cancelar|anula|anular)\b", clean):
        action_verb = "abort"
    elif re.search(r"\b(regist|registar|registra|relatorio|relatório|realizada|concluida|concluído|fechar|fecha|concluir)\b", clean):
        action_verb = "report"
    elif wants_previsto:
        action_verb = "approve"
    if not action_verb:
        return None

    maneuver_type = ""
    if re.search(r"\b(saida|saída|departure|sair)\b", clean):
        maneuver_type = "departure"
    elif re.search(r"\b(mudanca|mudança|shift|mudar)\b", clean):
        maneuver_type = "shift"
    elif re.search(r"\b(entrada|entry|entrar)\b", clean):
        maneuver_type = "entry"

    action_suffix = maneuver_type or "entry"
    if action_verb == "report":
        action = f"{action_suffix}_report"
    else:
        action = f"{action_verb}_{action_suffix}"

    if action not in ACTION_SPECS:
        if action_verb == "report":
            action = "entry_report"
        else:
            action = f"{action_verb}_entry"
        if action not in ACTION_SPECS:
            return None

    matched_port_call = None
    clean_question = f" {clean} "
    by_reference = [
        item for item in port_calls
        if item.get("reference_code") and f" {_operational_lookup_key(item.get('reference_code'))} " in clean_question
    ]
    if len(by_reference) == 1:
        matched_port_call = by_reference[0]
    else:
        by_name = []
        for item in port_calls:
            vessel_key = _operational_lookup_key(item.get("vessel_name"))
            if vessel_key and f" {vessel_key} " in clean_question:
                by_name.append(item)
        if len(by_name) == 1:
            matched_port_call = by_name[0]

    if not matched_port_call:
        return None

    resolved_port_call = services.store.get_port_call(matched_port_call["id"])
    if wants_previsto:
        inferred_type = maneuver_type or infer_maneuver_type(resolved_port_call, "edit_maneuver_plan") or "entry"
        target_maneuver = resolve_maneuver(resolved_port_call, "edit_maneuver_plan", inferred_type)
        if target_maneuver and target_maneuver.get("state") == "pending":
            type_label = {"entry": "entrada", "departure": "saída", "shift": "mudança"}.get(inferred_type, "manobra")
            return {
                "intent": "unsupported", "action": "", "confidence": 0.99,
                "reason": f"A {type_label} de {matched_port_call.get('vessel_name', 'este navio')} já está prevista.",
                "target": {}, "fields": {}, "missing_fields": [],
            }

    proposal = normalize_action_candidate(
        {
            "intent": "action", "action": action, "confidence": 0.99,
            "reason": (
                "Heurística determinística para comando equivalente a confirmar a manobra."
                if action == "complete_entry" and re.search(r"\b(previsto|prevista|planeado|planeada)\b", clean)
                else "Heurística determinística para ação operacional direta."
            ),
            "target": {
                "reference_code": matched_port_call.get("reference_code", ""),
                "vessel_name": matched_port_call.get("vessel_name", ""),
                "maneuver_type": maneuver_type,
            },
            "fields": {}, "missing_fields": [],
        },
        role,
    )
    if proposal and proposal.get("intent") == "action":
        return proposal
    return None


def build_tracked_scales(port_activity: dict) -> list[dict]:
    """Build a flat list of tracked scale summaries for vessels currently in port or with planned maneuvers."""
    tracked = []
    seen_ids: set[str] = set()
    for item in port_activity.get("in_port", []) or []:
        item_id = (item.get("id") or "").strip()
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        tracked.append({
            "id": item_id, "reference_code": item.get("reference_code", ""),
            "vessel_name": item.get("vessel_name", ""),
            "location_label": item.get("berth_label", ""),
            "status_label": "Em porto", "status_class": "approved",
            "meta": f"ETA {item.get('eta_label', '--')} · ATA {item.get('ata_label', '--')} · agente {item.get('agent_label', '--')}",
        })
    for item in port_activity.get("planned_maneuvers", []) or []:
        item_id = (item.get("port_call_id") or "").strip()
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        tracked.append({
            "id": item_id, "reference_code": item.get("reference_code", ""),
            "vessel_name": item.get("vessel_name", ""),
            "location_label": item.get("local_destination", "") or item.get("berth_label", ""),
            "status_label": item.get("situation_label", "Prevista"),
            "status_class": item.get("situation_class", "pending"),
            "meta": (
                f"{item.get('maneuver_label', 'Manobra')} · {item.get('date_label', '--')} "
                f"às {item.get('planned_label', '--')} · agente {item.get('agent_label', '--')}"
            ),
        })
    return tracked


def load_pending_chat_action(username: str, conversation_id: str) -> dict | None:
    """Load and normalize the pending chat action for a conversation, or return None."""
    payload = services.store.get_runtime_state(pending_action_state_key(username, conversation_id))
    if not payload:
        return None
    if payload.get("username") != username or payload.get("conversation_id") != conversation_id:
        return None
    proposal = payload.get("proposal") or {}
    normalized = normalize_action_candidate(
        {
            "intent": proposal.get("intent", "action"),
            "action": proposal.get("action", ""),
            "confidence": proposal.get("confidence", 0.0),
            "reason": proposal.get("reason", ""),
            "target": proposal.get("target", {}),
            "fields": proposal.get("fields", {}),
            "missing_fields": proposal.get("missing_fields", []),
        },
        session.get("role", "piloto"),
    )
    if normalized and normalized.get("intent") == "action":
        proposal = {**proposal, **normalized, "port_call_id": proposal.get("port_call_id", ""), "maneuver_id": proposal.get("maneuver_id", "")}
        payload = {**payload, "proposal": proposal}
        services.store.set_runtime_state(pending_action_state_key(username, conversation_id), payload)
    target_port_call = None
    port_call_id = (proposal.get("port_call_id") or "").strip()
    if port_call_id:
        try:
            target_port_call = action_target_port_call(port_call_id)
        except Exception:
            target_port_call = None
    return {
        **payload,
        "proposal": proposal,
        "summary": payload.get("summary") or format_action_summary(proposal, target_port_call),
        "target_reference": target_port_call.get("reference_code") if target_port_call else proposal.get("target", {}).get("reference_code", ""),
        "target_vessel_name": target_port_call.get("vessel_name") if target_port_call else proposal.get("target", {}).get("vessel_name", ""),
        "can_confirm": bool(proposal.get("action")) and not proposal.get("missing_fields"),
    }


def save_pending_chat_action(username: str, conversation_id: str, proposal: dict, question: str) -> dict:
    """Persist a pending action proposal to runtime state and return the stored payload."""
    port_call = None
    if proposal.get("port_call_id"):
        try:
            port_call = action_target_port_call(proposal["port_call_id"])
        except Exception:
            port_call = None
    payload = {
        "username": username, "conversation_id": conversation_id,
        "question": question, "proposal": proposal,
        "summary": format_action_summary(proposal, port_call),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    services.store.set_runtime_state(pending_action_state_key(username, conversation_id), payload)
    return payload


def clear_pending_chat_action(username: str, conversation_id: str) -> None:
    """Delete the pending chat action for the given user and conversation from runtime state."""
    services.store.delete_runtime_state(pending_action_state_key(username, conversation_id))


def propose_operational_action(question: str, role: str) -> dict | None:
    """Attempt to derive an operational action proposal from the question using heuristics or LLM."""
    if not looks_like_operational_command(question):
        return None
    resolvable_port_calls = current_resolvable_port_calls()
    heuristic_proposal = heuristic_operational_proposal(question, role, resolvable_port_calls)
    if heuristic_proposal:
        return finalize_operational_proposal(heuristic_proposal, resolvable_port_calls)
    if not services.rag.client:
        return {
            "intent": "unsupported", "action": "", "confidence": 0.0,
            "reason": "O bot operador precisa de uma API key LLM para interpretar ações operacionais.",
            "target": {}, "fields": {}, "missing_fields": [],
        }
    port_calls = current_visible_port_calls()
    prompt = build_operational_action_prompt(
        question=question, role=role, now_local=datetime.now().astimezone(),
        port_calls=port_calls, berth_options=services.BERTH_OPTIONS,
        constraint_options=services.CONSTRAINT_OPTIONS,
    )
    try:
        gen_result = services.rag.provider.generate(prompt=prompt, model=services.rag.generation_model)
    except Exception as exc:
        return {
            "intent": "unsupported", "action": "", "confidence": 0.0,
            "reason": f"Falha a interpretar a ação operacional: {exc}",
            "target": {}, "fields": {}, "missing_fields": [],
        }
    candidate = extract_json_object(gen_result.text or "")
    proposal = normalize_action_candidate(candidate or {}, role)
    if proposal and proposal.get("intent") == "unsupported":
        heuristic_proposal = heuristic_operational_proposal(question, role, resolvable_port_calls)
        if heuristic_proposal:
            return finalize_operational_proposal(heuristic_proposal, resolvable_port_calls)
    return finalize_operational_proposal(proposal, current_visible_port_calls())


def finalize_operational_proposal(proposal: dict | None, port_calls: list[dict] | None = None) -> dict | None:
    """Resolve the target port call and maneuver for an action proposal and refresh missing fields."""
    if not proposal or proposal.get("intent") != "action":
        return proposal
    target = None
    existing_port_call_id = (proposal.get("port_call_id") or "").strip()
    if existing_port_call_id:
        try:
            target = services.store.get_port_call(existing_port_call_id)
        except Exception:
            target = None
    visible_port_calls = port_calls if port_calls is not None else current_visible_port_calls()
    if not target:
        target = resolve_port_call(visible_port_calls, proposal.get("target", {}))
    if not target and proposal.get("action") != "create_port_call":
        target = resolve_port_call(current_resolvable_port_calls(), proposal.get("target", {}))
    if proposal.get("action") != "create_port_call" and not target:
        proposal["intent"] = "unsupported"
        proposal["action"] = ""
        proposal["reason"] = "Não consegui identificar uma escala correspondente para executar a ação."
        return proposal

    if target:
        proposal["port_call_id"] = target.get("id", "")
        proposal["target"]["reference_code"] = target.get("reference_code", "")
        proposal["target"]["vessel_name"] = target.get("vessel_name", "")

    maneuver_type = (proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
    resolved_port_call = services.store.get_port_call(target["id"]) if target else None
    inferred_type = infer_maneuver_type(resolved_port_call or {}, proposal.get("action", "")) if resolved_port_call else ""
    if inferred_type and proposal.get("action") in {
        "approve_entry", "approve_departure", "approve_shift",
        "abort_entry", "abort_departure", "abort_shift",
        "entry_report", "departure_report", "shift_report",
    }:
        proposal["action"] = action_for_maneuver_type(proposal["action"], inferred_type)

    if proposal.get("action") in {"approve_entry", "abort_entry", "entry_report"}:
        proposal["target"]["maneuver_type"] = "entry"
    elif proposal.get("action") in {"approve_departure", "abort_departure", "departure_report", "schedule_departure"}:
        proposal["target"]["maneuver_type"] = "departure"
    elif proposal.get("action") in {"approve_shift", "abort_shift", "shift_report", "schedule_shift"}:
        proposal["target"]["maneuver_type"] = "shift"
    elif maneuver_type not in {"entry", "departure", "shift"} and proposal.get("action") == "edit_maneuver_plan":
        if inferred_type:
            proposal["target"]["maneuver_type"] = inferred_type
        else:
            proposal["intent"] = "unsupported"
            proposal["action"] = ""
            proposal["reason"] = "Indica se queres alterar a entrada, a saída ou a mudança."
            return proposal
    elif maneuver_type not in {"entry", "departure", "shift"} and inferred_type and proposal.get("action") in {
        "approve_entry", "approve_departure", "approve_shift",
        "abort_entry", "abort_departure", "abort_shift",
        "entry_report", "departure_report", "shift_report",
    }:
        proposal["target"]["maneuver_type"] = inferred_type

    if target and proposal.get("action") in {"edit_maneuver_plan"}:
        maneuver = resolve_maneuver(
            services.store.get_port_call(target["id"]),
            proposal["action"],
            proposal["target"].get("maneuver_type", ""),
        )
        if not maneuver:
            proposal["intent"] = "unsupported"
            proposal["action"] = ""
            proposal["reason"] = "Não encontrei a manobra certa para editar nesta escala."
            return proposal
        proposal["maneuver_id"] = maneuver.get("id", "")
        if proposal.get("action") == "edit_maneuver_plan":
            fields = proposal.setdefault("fields", {})
            planned_value = " ".join(str(fields.get("planned_at_local") or "").split())
            if not planned_value:
                fields["planned_at_local"] = (
                    maneuver.get("planned_input_value")
                    or _iso_to_datetime_local_value(maneuver.get("planned_at"))
                    or ""
                )
            mt = proposal["target"].get("maneuver_type", "")
            if mt == "entry" and fields.get("berth") and not fields.get("destination"):
                fields["destination"] = fields["berth"]
            elif mt == "departure" and fields.get("next_port") and not fields.get("destination"):
                fields["destination"] = fields["next_port"]
            elif mt == "shift" and fields.get("destination_berth") and not fields.get("destination"):
                fields["destination"] = fields["destination_berth"]

    proposal["fields"]["constraints"] = normalize_constraint_codes(proposal.get("fields", {}).get("constraints", []))
    return refresh_proposal_missing_fields(proposal)


def pending_action_override(question: str, pending_proposal: dict, role: str) -> dict | None:
    """Check if the question replaces the pending action with a different verb, returning the replacement or None."""
    clean = _operational_lookup_key(question)
    if not clean:
        return None
    maneuver_type = (pending_proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
    if maneuver_type not in {"entry", "departure", "shift"}:
        return None

    if re.search(r"\b(aprova|approve|aprovar|confirma|confirmar)\b", clean):
        action = action_for_maneuver_type("approve_entry", maneuver_type)
    elif re.search(r"\b(aborta|cancela|anula)\b", clean):
        action = action_for_maneuver_type("abort_entry", maneuver_type)
    elif re.search(r"\b(previsto|prevista|planeado|planeada)\b", clean):
        action = action_for_maneuver_type("approve_entry", maneuver_type)
    elif re.search(r"\b(regist|registar|relatorio|relatório|realizada|concluida|fechar|fecha|concluir)\b", clean):
        maneuver_suffix = maneuver_type or "entry"
        action = f"{maneuver_suffix}_report"
    else:
        return None

    if action == pending_proposal.get("action"):
        return None

    replacement = normalize_action_candidate(
        {
            "intent": "action", "action": action, "confidence": 0.99,
            "reason": "Troca direta da ação pendente pelo pedido do utilizador.",
            "target": pending_proposal.get("target", {}), "fields": {}, "missing_fields": [],
        },
        role,
    )
    if not replacement or replacement.get("intent") != "action":
        return None
    if pending_proposal.get("port_call_id"):
        replacement["port_call_id"] = pending_proposal["port_call_id"]
    if pending_proposal.get("maneuver_id"):
        replacement["maneuver_id"] = pending_proposal["maneuver_id"]
    return finalize_operational_proposal(replacement)


def refine_pending_operational_action(question: str, pending_proposal: dict, role: str) -> dict | None:
    """Update, replace, cancel, or reject a pending proposal based on the user's follow-up question."""
    replacement = pending_action_override(question, pending_proposal, role)
    if replacement and replacement.get("intent") == "action":
        return {"intent": "replace", "proposal": replacement}

    direct_updates = extract_pending_field_updates(question, pending_proposal)
    if direct_updates:
        updates = normalize_action_candidate(
            {
                "intent": "action",
                "action": pending_proposal.get("action", ""),
                "confidence": pending_proposal.get("confidence", 0.0),
                "reason": pending_proposal.get("reason", ""),
                "target": pending_proposal.get("target", {}),
                "fields": direct_updates, "missing_fields": [],
            },
            role,
        )
        merged = merge_action_candidate(pending_proposal, updates or {}, role)
        return {"intent": "update", "proposal": finalize_operational_proposal(merged)}

    if not services.rag.client:
        return {"intent": "unsupported", "reason": "O bot operador precisa de uma API key LLM para atualizar propostas pendentes."}

    prompt = build_pending_action_update_prompt(
        question=question, role=role, proposal=pending_proposal,
        berth_options=services.BERTH_OPTIONS, constraint_options=services.CONSTRAINT_OPTIONS,
    )
    try:
        gen_result = services.rag.provider.generate(prompt=prompt, model=services.rag.generation_model)
    except Exception as exc:
        return {"intent": "unsupported", "reason": f"Falha a atualizar a proposta pendente: {exc}"}

    candidate = extract_json_object(gen_result.text or "") or {}
    intent = (candidate.get("intent") or "").strip().lower()
    if intent in {"cancel", "question", "unsupported"}:
        return {"intent": intent or "unsupported", "reason": " ".join(str(candidate.get("reason") or "").strip().split())}
    if intent == "replace":
        proposal = normalize_action_candidate(candidate, role)
        return {"intent": "replace", "proposal": finalize_operational_proposal(proposal)}

    updates = normalize_action_candidate(
        {
            "intent": "action",
            "action": candidate.get("action") or pending_proposal.get("action", ""),
            "confidence": candidate.get("confidence", pending_proposal.get("confidence", 0.0)),
            "reason": candidate.get("reason", pending_proposal.get("reason", "")),
            "target": candidate.get("target") if isinstance(candidate.get("target"), dict) else {},
            "fields": candidate.get("fields") if isinstance(candidate.get("fields"), dict) else {},
            "missing_fields": [],
        },
        role,
    )
    merged = merge_action_candidate(pending_proposal, updates or {}, role)
    return {"intent": "update", "proposal": finalize_operational_proposal(merged)}


def execute_pending_operational_action(proposal: dict, username: str, role: str) -> tuple[dict, str]:
    """Execute an approved operational action proposal against the store and return the result and message."""
    action = proposal.get("action") or ""
    target = proposal.get("target") or {}
    fields = proposal.get("fields") or {}
    port_call_id = (proposal.get("port_call_id") or "").strip()
    role = (role or "").strip().lower()

    _action_redirects = {
        "edit_maneuver_report": "entry_report",
    }
    _conditional_approve_redirects = {
        "complete_entry": ("approve_entry", "entry"),
        "complete_departure": ("approve_departure", "departure"),
        "complete_shift": ("approve_shift", "shift"),
    }
    if action in _action_redirects:
        action = _action_redirects[action]

    def apply_scope(port_call_id_value: str) -> dict:
        return action_target_port_call(port_call_id_value)

    def field_text(name: str, fallback="") -> str:
        raw = fields.get(name)
        if raw in (None, ""):
            raw = fallback
        return " ".join(str(raw or "").strip().split())

    def apply_plan_updates_before_approval(current_port_call: dict, current_maneuver_type: str) -> None:
        current_maneuver = resolve_maneuver(current_port_call, "edit_maneuver_plan", current_maneuver_type)
        if not current_maneuver:
            raise ValueError("Não encontrei a manobra certa para atualizar antes da aprovação.")
        if current_maneuver_type == "entry":
            destination = field_text("destination", field_text("berth", current_maneuver.get("destination") or current_port_call.get("berth", "")))
        elif current_maneuver_type == "departure":
            destination = field_text("destination", field_text("next_port", current_maneuver.get("destination") or current_port_call.get("next_port", "")))
        else:
            destination = field_text("destination", field_text("destination_berth", current_maneuver.get("destination") or current_port_call.get("shift_destination_berth", "") or current_port_call.get("berth", "")))
        planned_at_value = field_text("planned_at_local", field_text("eta_local"))
        if current_maneuver_type == "departure":
            planned_at_value = field_text("planned_at_local", field_text("planned_departure_at_local", planned_at_value))
        elif current_maneuver_type == "shift":
            planned_at_value = field_text("planned_at_local", field_text("planned_shift_at_local", planned_at_value))
        if not any([planned_at_value, field_text("draft_m"), field_text("tug_count"), field_text("notes"), field_text("plan_observations"), field_text("change_reason"), fields.get("constraints")]):
            return
        services.store.edit_maneuver_plan(
            port_call_id=port_call_id, maneuver_id=current_maneuver.get("id", ""),
            updated_by=username, actor_role=role,
            planned_at=parse_local_datetime_input(planned_at_value or current_maneuver.get("planned_input_value") or current_maneuver.get("planned_at") or ""),
            origin=require_form_text(field_text("origin", current_maneuver.get("origin") or current_port_call.get("last_port", "")), "Origem"),
            destination=require_form_text(destination, "Destino"),
            draft_m=field_text("draft_m", current_maneuver.get("planned_draft_m", "")),
            tug_count=field_text("tug_count", current_maneuver.get("tug_count", "")),
            constraints=normalize_constraint_codes(fields.get("constraints") or current_maneuver.get("constraints", [])),
            plan_note=field_text("plan_observations", field_text("notes", current_maneuver.get("plan_observations", ""))),
            change_reason=require_form_text(field_text("change_reason", field_text("reason")), "Motivo da alteração"),
        )

    if action == "create_port_call":
        eta = parse_local_datetime_input(field_text("eta_local"), "ETA")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        draft_m = field_text("draft_m")
        tug_count = field_text("tug_count")
        port_call = services.store.create_port_call(
            vessel_name=field_text("vessel_name"), eta=eta, created_by=username,
            constraints=constraints,
            berth=require_form_text(field_text("berth"), "Cais previsto"),
            last_port=require_form_text(field_text("last_port"), "Porto anterior"),
            next_port=require_form_text(field_text("next_port"), "Próximo destino"),
            vessel_short_name=field_text("vessel_short_name"),
            vessel_imo=require_form_text(field_text("vessel_imo"), "IMO"),
            vessel_call_sign=require_form_text(field_text("vessel_call_sign"), "Indicativo"),
            vessel_flag=require_form_text(field_text("vessel_flag"), "Bandeira"),
            vessel_type=require_form_text(field_text("vessel_type"), "Tipo de navio"),
            vessel_loa_m=require_form_text(field_text("vessel_loa_m"), "LOA"),
            vessel_beam_m=require_form_text(field_text("vessel_beam_m"), "Boca"),
            vessel_gt_t=require_form_text(field_text("vessel_gt_t"), "GT"),
            vessel_max_draft_m=require_form_text(field_text("vessel_max_draft_m"), "Calado máximo"),
            vessel_dwt_t=require_form_text(field_text("vessel_dwt_t"), "DWT"),
            notes=build_entry_request_note({"draft_m": draft_m, "tug_count": tug_count, "constraints": constraints, "notes": fields.get("notes", "")}),
        )
        return port_call, f"Escala criada para {port_call['vessel_name']} com ETA {port_call['eta_label']}."

    if not port_call_id:
        raise ValueError("A proposta não tem escala associada.")

    port_call = apply_scope(port_call_id)
    maneuver_type = target.get("maneuver_type", "")

    if action in _conditional_approve_redirects:
        approve_action, m_type = _conditional_approve_redirects[action]
        target_maneuver = resolve_maneuver(port_call, action, m_type)
        if target_maneuver and target_maneuver.get("state") == "pending":
            action = approve_action

    if action == "approve_entry":
        apply_plan_updates_before_approval(port_call, "entry")
        result = services.store.approve_port_call(port_call_id=port_call_id, decided_by=username, approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))))
        return result, f"Entrada aprovada para {result['vessel_name']}."
    if action == "abort_entry":
        target_maneuver = resolve_maneuver(port_call, action, "entry")
        maneuver_state = (target_maneuver or {}).get("state", "pending")
        if role == "agente" and maneuver_state == "approved":
            raise ValueError("Só o piloto/admin pode abortar uma manobra já aprovada (piloto a bordo).")
        if role == "piloto" and maneuver_state == "pending":
            raise ValueError("Manobra ainda pendente. Só o agente/admin pode cancelar antes da aprovação.")
        result = services.store.abort_port_call(port_call_id=port_call_id, decided_by=username, aborted_reason=require_form_text(field_text("aborted_reason", field_text("reason")), "Motivo"), approval_note=field_text("approval_note"))
        label = "cancelada" if maneuver_state == "pending" else "abortada (piloto a bordo)"
        return result, f"Entrada {label} para {result['vessel_name']}."
    if action == "complete_entry":
        arrived_at_value = field_text("arrived_at_local", field_text("maneuver_finished_local"))
        result = services.store.mark_port_call_arrived(port_call_id=port_call_id, arrived_at=parse_optional_local_datetime_input(arrived_at_value, "ATA") or datetime.now().astimezone().isoformat(), updated_by=username, berth=field_text("berth", port_call.get("berth")))
        return result, f"Entrada confirmada para {result['vessel_name']} às {result['ata_label']}. Já podes preencher o registo operacional."
    if action == "entry_report":
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": fields.get("notes", "")}, "Entrada")
        result = services.store.attach_entry_report(port_call_id=port_call_id, updated_by=username, maneuver_started_at=started_at, maneuver_finished_at=finished_at, draft_m=draft_m, notes=note)
        return result, f"Registo de entrada guardado para {result['vessel_name']}."
    if action == "schedule_departure":
        planned_departure_at = parse_local_datetime_input(field_text("planned_departure_at_local"), "Hora prevista de saída")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        result = services.store.schedule_departure_plan(port_call_id=port_call_id, planned_departure_at=planned_departure_at, updated_by=username, next_port=require_form_text(field_text("next_port", port_call.get("next_port", "")), "Próximo destino"), constraints=constraints, departure_plan_note=build_departure_plan_note({"draft_m": field_text("draft_m"), "tug_count": field_text("tug_count"), "constraints": constraints, "notes": field_text("notes")}))
        return result, f"Saída planeada para {result['vessel_name']} às {result['planned_departure_label']}."
    if action == "approve_departure":
        apply_plan_updates_before_approval(port_call, "departure")
        result = services.store.approve_port_call(port_call_id=port_call_id, decided_by=username, approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))))
        return result, f"Saída aprovada para {result['vessel_name']}."
    if action == "abort_departure":
        target_m = resolve_maneuver(port_call, action, "departure")
        m_state = (target_m or {}).get("state", "pending")
        if role == "agente" and m_state == "approved":
            raise ValueError("Só o piloto/admin pode abortar uma saída já aprovada.")
        if role == "piloto" and m_state == "pending":
            raise ValueError("Saída ainda pendente. Só o agente/admin pode cancelar antes da aprovação.")
        result = services.store.abort_departure_plan(port_call_id=port_call_id, updated_by=username, aborted_reason=require_form_text(field_text("aborted_reason", field_text("reason")), "Motivo"))
        label = "cancelada" if m_state == "pending" else "abortada (piloto a bordo)"
        return result, f"Saída {label} para {result['vessel_name']}."
    if action == "complete_departure":
        departed_at_value = field_text("departed_at_local", field_text("maneuver_finished_local"))
        result = services.store.mark_port_call_departed(port_call_id=port_call_id, departed_at=parse_optional_local_datetime_input(departed_at_value, "ATD") or datetime.now().astimezone().isoformat(), updated_by=username, next_port=field_text("next_port", port_call.get("next_port")))
        return result, f"Saída confirmada para {result['vessel_name']} às {result['departure_label']}. Já podes preencher o registo operacional."
    if action == "departure_report":
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": fields.get("notes", "")}, "Saída")
        result = services.store.attach_departure_report(port_call_id=port_call_id, updated_by=username, maneuver_started_at=started_at, maneuver_finished_at=finished_at, draft_m=draft_m, notes=note)
        return result, f"Registo de saída guardado para {result['vessel_name']}."
    if action == "schedule_shift":
        planned_shift_at = parse_local_datetime_input(field_text("planned_shift_at_local"), "Hora prevista da mudança")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        result = services.store.schedule_shift_plan(port_call_id=port_call_id, planned_shift_at=planned_shift_at, updated_by=username, destination_berth=require_form_text(field_text("destination_berth"), "Cais destino"), constraints=constraints, shift_plan_note=build_shift_plan_note({"origin_berth": field_text("origin_berth", port_call.get("berth", "")), "destination_berth": field_text("destination_berth"), "draft_m": field_text("draft_m"), "tug_count": field_text("tug_count"), "constraints": constraints, "notes": field_text("notes")}))
        return result, f"Mudança planeada para {result['vessel_name']} às {result['planned_shift_label']}."
    if action == "approve_shift":
        apply_plan_updates_before_approval(port_call, "shift")
        result = services.store.approve_shift_plan(port_call_id=port_call_id, decided_by=username, approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))))
        return result, f"Mudança aprovada para {result['vessel_name']}."
    if action == "abort_shift":
        target_ms = resolve_maneuver(port_call, action, "shift")
        ms_state = (target_ms or {}).get("state", "pending")
        if role == "agente" and ms_state == "approved":
            raise ValueError("Só o piloto/admin pode abortar uma mudança já aprovada.")
        if role == "piloto" and ms_state == "pending":
            raise ValueError("Mudança ainda pendente. Só o agente/admin pode cancelar antes da aprovação.")
        result = services.store.abort_shift_plan(port_call_id=port_call_id, updated_by=username, aborted_reason=require_form_text(field_text("aborted_reason", field_text("reason")), "Motivo"))
        label = "cancelada" if ms_state == "pending" else "abortada (piloto a bordo)"
        return result, f"Mudança {label} para {result['vessel_name']}."
    if action == "complete_shift":
        shifted_at_value = field_text("shifted_at_local", field_text("maneuver_finished_local"))
        result = services.store.mark_shift_completed(port_call_id=port_call_id, shifted_at=parse_optional_local_datetime_input(shifted_at_value, "Hora da mudança") or datetime.now().astimezone().isoformat(), updated_by=username)
        return result, f"Mudança concluída para {result['vessel_name']} às {result['shift_label']}. Já podes preencher o registo operacional."
    if action == "shift_report":
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": fields.get("notes", "")}, "Mudança")
        result = services.store.attach_shift_report(port_call_id=port_call_id, updated_by=username, maneuver_started_at=started_at, maneuver_finished_at=finished_at, draft_m=draft_m, notes=note)
        return result, f"Registo de mudança guardado para {result['vessel_name']}."
    if action == "edit_maneuver_plan":
        maneuver_id = (proposal.get("maneuver_id") or "").strip()
        current_port_call = services.store.get_port_call(port_call_id)
        maneuver = resolve_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "") or maneuver_id
        if not maneuver_id:
            raise ValueError("A proposta não identifica a manobra a editar.")
        base_origin = field_text("origin", (maneuver or {}).get("origin") or current_port_call.get("last_port", ""))
        if maneuver_type == "entry":
            base_destination = field_text("destination", field_text("berth", (maneuver or {}).get("destination") or current_port_call.get("berth", "")))
        elif maneuver_type == "departure":
            base_destination = field_text("destination", field_text("next_port", (maneuver or {}).get("destination") or current_port_call.get("next_port", "")))
        else:
            base_destination = field_text("destination", field_text("destination_berth", (maneuver or {}).get("destination") or current_port_call.get("shift_destination_berth", "") or current_port_call.get("berth", "")))
        result = services.store.edit_maneuver_plan(
            port_call_id=port_call_id, maneuver_id=maneuver_id, updated_by=username, actor_role=role,
            planned_at=parse_local_datetime_input(field_text("planned_at_local", (maneuver or {}).get("planned_input_value", ""))),
            origin=require_form_text(base_origin, "Origem"),
            destination=require_form_text(base_destination, "Destino"),
            draft_m=field_text("draft_m", (maneuver or {}).get("planned_draft_m", "")),
            tug_count=field_text("tug_count", (maneuver or {}).get("tug_count", "")),
            constraints=normalize_constraint_codes(fields.get("constraints") or (maneuver or {}).get("constraints", [])),
            plan_note=field_text("plan_observations", field_text("notes", (maneuver or {}).get("plan_observations", ""))),
            change_reason=require_form_text(field_text("change_reason"), "Motivo da alteração"),
        )
        return result, f"Planeamento atualizado para {result['vessel_name']}."
    if action == "edit_maneuver_report":
        maneuver_id = (proposal.get("maneuver_id") or "").strip()
        current_port_call = services.store.get_port_call(port_call_id)
        maneuver = resolve_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "") or maneuver_id
        if not maneuver_id:
            raise ValueError("A proposta não identifica a manobra a editar.")
        started_at = parse_local_datetime_input(field_text("maneuver_started_local", (maneuver or {}).get("execution_started_input_value", "")), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local", (maneuver or {}).get("execution_finished_input_value", "")), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m", (maneuver or {}).get("reported_draft_m", "")), "Calado")
        result = services.store.edit_maneuver_report(
            port_call_id=port_call_id, maneuver_id=maneuver_id, updated_by=username,
            maneuver_started_at=started_at, maneuver_finished_at=finished_at, draft_m=draft_m,
            notes=build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": field_text("notes", (maneuver or {}).get("report_note", ""))}, "Entrada" if maneuver_type == "entry" else "Saída" if maneuver_type == "departure" else "Mudança", existing_note=""),
            change_reason=require_form_text(field_text("change_reason"), "Motivo da alteração"),
        )
        return result, f"Registo operacional revisto para {result['vessel_name']}."

    raise ValueError("Ação operacional não suportada.")


# ---------------------------------------------------------------------------
# Scale context builder (for port_call_detail page)
# ---------------------------------------------------------------------------

def build_scale_context(port_call: dict) -> dict:
    """Build the rich context dict for the port call detail page including maneuvers and actions."""
    current_role = (session.get("role") or "").strip().lower()

    def _hours_between(start_value: str | None, end_value: str | None) -> str:
        if not start_value or not end_value:
            return "--"
        try:
            start_dt = datetime.fromisoformat(start_value)
            end_dt = datetime.fromisoformat(end_value)
        except ValueError:
            return "--"
        hours = max((end_dt - start_dt).total_seconds() / 3600, 0)
        return f"{hours:.0f} horas"

    def _latest(history: list[dict], maneuver_type: str, states: set[str] | None = None) -> dict | None:
        items = [item for item in history if item.get("type") == maneuver_type]
        if states is not None:
            items = [item for item in items if item.get("state") in states]
        if not items:
            return None
        items.sort(key=lambda item: item.get("planned_at") or item.get("completed_at") or item.get("created_at") or "")
        return items[-1]

    def _latest_reportable(history: list[dict], maneuver_type: str) -> dict | None:
        items = [
            item for item in history
            if item.get("type") == maneuver_type and item.get("state") == "completed"
            and not (item.get("report_note") or "").strip()
        ]
        if not items:
            return None
        items.sort(key=lambda item: item.get("completed_at") or item.get("planned_at") or item.get("created_at") or "")
        return items[-1]

    history = port_call.get("maneuver_history", [])
    entry = _latest(history, "entry")
    active_departure = _latest(history, "departure", {"pending", "approved"})
    latest_departure = _latest(history, "departure")
    completed_departure = _latest(history, "departure", {"completed"})
    active_shift = _latest(history, "shift", {"pending", "approved"})
    latest_shift = _latest(history, "shift")
    completed_shift = _latest(history, "shift", {"completed"})
    reportable_entry = _latest_reportable(history, "entry")
    reportable_departure = _latest_reportable(history, "departure")
    reportable_shift = _latest_reportable(history, "shift")

    etd_value = (active_departure or completed_departure or latest_departure or {}).get("planned_at") or (completed_departure or {}).get("completed_at")
    etd_label = (
        port_call.get("planned_departure_label")
        if active_departure and active_departure.get("planned_at")
        else port_call.get("departure_label")
        if completed_departure and completed_departure.get("completed_at")
        else "Sem ETD"
    )
    ship_doc_number = f"PTSETSHP{(port_call.get('vessel_imo') or port_call['reference_code'])[-8:]}"
    maneuvers = []
    change_log_rows = []
    for item in history:
        maneuvers.append({
            "id": item.get("id"), "type": item.get("type"),
            "title": item.get("type_label", item.get("type", "")),
            "status": item.get("state_label", item.get("state", "")),
            "when_label": item.get("effective_time_label") if item.get("state") == "completed" else item.get("planned_label"),
            "planned_label": item.get("planned_label"),
            "planned_input_value": item.get("planned_input_value", ""),
            "execution_started_label": item.get("execution_started_label"),
            "execution_started_input_value": item.get("execution_started_input_value", ""),
            "execution_finished_label": item.get("execution_finished_label"),
            "execution_finished_input_value": item.get("execution_finished_input_value", ""),
            "draft": item.get("reported_draft_m") or item.get("planned_draft_m") or (port_call["ship_max_draft_label"] if port_call.get("vessel_max_draft_m") else "--"),
            "tug_count": item.get("tug_count") or "",
            "origin": item.get("origin") or "--",
            "destination": item.get("destination") or "--",
            "plan_note": item.get("plan_note") or "",
            "plan_observations": item.get("plan_observations") or item.get("plan_note") or "",
            "report_note": item.get("report_note") or "",
            "notes": item.get("report_note") or item.get("plan_note") or item.get("approval_note") or item.get("aborted_reason") or "",
            "agent_profile": item.get("agent_profile", {}),
            "validated_by_profile": item.get("pilot_profile", {}),
            "executed_by_profile": item.get("reported_by_profile", {}),
            "constraints": item.get("constraint_badges", []),
            "constraint_codes": item.get("constraints", []),
            "change_count": item.get("change_count", 0),
            "has_changes": item.get("has_changes", False),
            "can_edit_plan": (
                (item.get("state") != "completed" and current_role in {"admin", "piloto"})
                or (current_role == "agente" and item.get("state") == "pending")
            ),
            "can_edit_report": current_role in {"admin", "piloto"} and item.get("state") == "completed",
        })
        for log in item.get("change_log", []):
            actor_profile = log.get("changed_by_profile") or {}
            change_log_rows.append({
                "maneuver_title": item.get("type_label", item.get("type", "")),
                "changed_at": log.get("changed_at"),
                "changed_at_label": _local_iso_to_label(log.get("changed_at")),
                "changed_by_label": actor_profile.get("full_name") or actor_profile.get("username") or "--",
                "changed_by_contact": actor_profile.get("email") or actor_profile.get("phone") or actor_profile.get("organization") or "--",
                "reason": log.get("reason") or "--",
                "summary": log.get("summary") or "--",
            })
    entry_report_exists = bool(entry and entry.get("state") == "completed" and entry.get("report_note"))
    departure_report_exists = bool(completed_departure and completed_departure.get("report_note"))
    shift_report_exists = bool(completed_shift and completed_shift.get("report_note"))

    summary = {
        "scale_reference": port_call["reference_code"],
        "status_label": "Concluída" if port_call.get("status") == "departed" else "Em porto" if port_call.get("status") == "in_port" else "Prevista",
        "eta_label": port_call["eta_label"],
        "etd_label": etd_label or "Sem ETD",
        "eta_status_label": "Confirmado" if entry and entry.get("state") == "completed" else "Previsto",
        "etd_status_label": "Confirmado" if completed_departure else "Previsto",
        "current_location": port_call["berth_label"],
        "last_port": port_call.get("last_port") or "--",
        "next_port": port_call.get("next_port") or "--",
        "agent_label": port_call["agent_label"],
        "pilot_label": port_call["pilot_label"],
        "agent_profile": port_call.get("agent_profile", {}),
        "pilot_profile": port_call.get("pilot_profile", {}),
        "maneuver_count": len(maneuvers),
        "report_points_count": len(maneuvers) * 4,
        "stay_hours_label": _hours_between(
            (entry or {}).get("completed_at") or (entry or {}).get("planned_at"), etd_value,
        ),
    }
    ship_profile = {
        "doc_number": ship_doc_number,
        "scale_reference": port_call["reference_code"],
        "name": port_call["vessel_name"],
        "imo": port_call["ship_imo_label"],
        "call_sign": port_call["ship_call_sign_label"],
        "flag": port_call["ship_flag_label"],
        "type": port_call["ship_type_label"],
        "type_icon": port_call.get("ship_type_icon"),
        "loa": port_call["ship_loa_label"],
        "beam": port_call["ship_beam_label"],
        "gt": port_call["ship_gt_label"],
        "draft": port_call["ship_max_draft_label"],
        "dwt": port_call["ship_dwt_label"],
    }
    actions = {
        "can_approve_entry": port_call.get("status") == "scheduled" and port_call.get("approval_status") == "pending",
        "can_abort_entry": port_call.get("status") == "scheduled" and port_call.get("approval_status") != "aborted" and port_call.get("can_abort"),
        "can_complete_entry": port_call.get("status") == "scheduled" and bool(entry) and entry.get("state") == "approved",
        "can_plan_departure": port_call.get("status") == "in_port" and not active_departure and not completed_departure,
        "can_approve_departure": port_call.get("status") == "in_port" and bool(active_departure) and active_departure.get("state") == "pending",
        "can_abort_departure": port_call.get("status") == "in_port" and bool(active_departure) and active_departure.get("state") in {"pending", "approved"},
        "can_complete_departure": port_call.get("status") == "in_port" and bool(active_departure) and active_departure.get("state") == "approved",
        "can_register_entry": bool(reportable_entry),
        "can_register_departure": bool(reportable_departure),
        "can_plan_shift": port_call.get("status") == "in_port" and not active_shift,
        "can_approve_shift": port_call.get("status") == "in_port" and bool(active_shift) and active_shift.get("state") == "pending",
        "can_abort_shift": port_call.get("status") == "in_port" and bool(active_shift) and active_shift.get("state") in {"pending", "approved"},
        "can_complete_shift": port_call.get("status") == "in_port" and bool(active_shift) and active_shift.get("state") == "approved",
        "can_register_shift": bool(reportable_shift),
        "must_complete_label": (
            "Concluir entrada" if port_call.get("status") == "scheduled" and bool(entry) and entry.get("state") == "approved"
            else "Concluir saída" if port_call.get("status") == "in_port" and bool(active_departure) and active_departure.get("state") == "approved"
            else "Concluir mudança" if port_call.get("status") == "in_port" and bool(active_shift) and active_shift.get("state") == "approved"
            else ""
        ),
        "entry_report_exists": entry_report_exists,
        "departure_report_exists": departure_report_exists,
        "shift_report_exists": shift_report_exists,
    }
    return {
        "ship_profile": ship_profile, "summary": summary,
        "maneuvers": maneuvers,
        "change_log_rows": sorted(change_log_rows, key=lambda item: item.get("changed_at") or "", reverse=True),
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# Form parsing utilities
# ---------------------------------------------------------------------------

def parse_local_datetime_input(value: str, label: str = "ETA") -> str:
    """Parse a local datetime string from a form input and return it as a timezone-aware ISO string."""
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        raise ValueError(f"{label} é obrigatória.")
    try:
        dt = datetime.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError(f"{label} inválida. Usa data e hora válidas.") from exc
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone().isoformat()


def parse_optional_local_datetime_input(value: str, label: str = "Data e hora") -> str:
    """Parse an optional local datetime string, returning an empty string if the value is blank."""
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        return ""
    return parse_local_datetime_input(clean, label=label)


def require_form_text(value: str, label: str) -> str:
    """Return a cleaned form text value, raising ValueError if it is empty."""
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        raise ValueError(f"{label} é obrigatório.")
    return clean


def format_note_datetime(value: str) -> str:
    """Format an ISO datetime string as a local dd/mm/yyyy HH:MM label for display in notes."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value


def _local_iso_to_label(value: str | None) -> str:
    if not value:
        return "Sem hora"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value


def _iso_to_datetime_local_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


def compact_multiline_note(title: str, fields: list[tuple[str, str]]) -> str:
    """Build a multiline note string from a title and a list of (label, value) pairs, omitting blank values."""
    lines = [title]
    for label, value in fields:
        clean = " ".join((value or "").strip().split())
        if clean:
            lines.append(f"{label}: {clean}")
    return "\n".join(lines)


def build_entry_request_note(form_data: dict) -> str:
    """Build the agent entry request note from form data fields."""
    return compact_multiline_note("Registo do agente · Entrada", [
        ("Calado", form_data.get("draft_m", "")),
        ("Rebocadores", form_data.get("tug_count", "")),
        ("Restrições", format_constraint_labels(form_data.get("constraints", []))),
        ("Observações", form_data.get("notes", "")),
    ])


def build_departure_plan_note(form_data: dict) -> str:
    """Build the agent departure plan note from form data fields."""
    return compact_multiline_note("Registo do agente · Saída", [
        ("Calado", form_data.get("draft_m", "")),
        ("Rebocadores", form_data.get("tug_count", "")),
        ("Restrições", format_constraint_labels(form_data.get("constraints", []))),
        ("Observações", form_data.get("notes", "")),
    ])


def build_shift_plan_note(form_data: dict) -> str:
    """Build the agent berth-shift plan note from form data fields."""
    return compact_multiline_note("Registo do agente · Mudança", [
        ("Origem", form_data.get("origin_berth", "")),
        ("Destino", form_data.get("destination_berth", "")),
        ("Calado", form_data.get("draft_m", "")),
        ("Rebocadores", form_data.get("tug_count", "")),
        ("Restrições", format_constraint_labels(form_data.get("constraints", []))),
        ("Observações", form_data.get("notes", "")),
    ])


def build_pilot_report_note(form_data: dict, maneuver_label: str, existing_note: str = "") -> str:
    """Build the pilot operational report note, optionally appending to an existing note."""
    report = compact_multiline_note(f"Registo simplificado de pilotagem · {maneuver_label}", [
        ("Início da manobra", format_note_datetime(form_data.get("maneuver_started_at", ""))),
        ("Fim da manobra", format_note_datetime(form_data.get("maneuver_finished_at", ""))),
        ("Calado", form_data.get("draft_m", "")),
        ("Observações", form_data.get("notes", "")),
    ])
    if existing_note.strip():
        return f"{existing_note.strip()}\n\n{report}"
    return report


def get_current_conversation(username: str):
    """Return the current conversation for the user based on the request's conversation_id parameter."""
    requested_id = request.args.get("conversation_id", "").strip() or None
    return services.store.ensure_conversation(username=username, conversation_id=requested_id)
