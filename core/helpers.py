"""Shared helpers, decorators, and operational logic for PRAGtico blueprints."""

import logging
import math
import os
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for

from core import services
from domain.berth_layout import (
    build_slot_occupancy,
    canonicalize_berth_label,
    find_occupied_berth_conflict,
    is_anchorage_berth,
    is_known_berth_label,
)
from domain.chat_actions import (
    ACTION_SPECS,
    action_for_maneuver_type,
    action_prefers_explicit_maneuver_id,
    build_action_reply_template,
    build_operational_action_prompt,
    build_pending_action_update_prompt,
    build_slash_help,
    build_validate_maneuver_reply_template,
    candidate_maneuvers_for_action,
    display_missing_field_labels,
    extract_json_object,
    extract_pending_field_updates,
    format_action_summary,
    infer_maneuver_type,
    looks_like_abort_payload,
    looks_like_maneuver_report_payload,
    looks_like_port_call_payload,
    looks_like_operational_command,
    looks_like_operational_query,
    looks_like_slash_command,
    merge_action_candidate,
    normalize_action_candidate,
    parse_slash_command,
    proposal_missing_field_labels,
    required_missing_fields,
    resolve_maneuver,
    resolve_port_call,
    extract_pending_target_updates,
    visible_port_calls_from_activity,
)
from domain.cost_engine import (
    UP_NORMAL,
    UP_SHIFT_ALONG,
    ManoeuvreInput,
    ManoeuvreType,
    calculate_scale_cost,
    format_cost_summary,
)
from domain.migration_service import get_database_runtime_status
from core.reindex_scheduler import DeferredTaskScheduler, next_provider_quota_reset_utc
from core.validators import normalize_thruster_state, validate_not_past_datetime
from storage import (
    PASSWORD_HASH_METHOD,
    format_constraint_labels,
    is_user_profile_complete,
    normalize_constraint_codes,
)

logger = logging.getLogger(__name__)
RULE_CODE_TITLES = {
    "005": "IT-005 Multiusos Z1",
    "006": "IT-006 Multiusos Z2",
    "007": "IT-007 Autoeuropa",
    "008": "IT-008 Ecooil",
    "009": "IT-009 Secil",
    "010": "IT-010 Tanquisado",
    "011": "IT-011 Termitrena",
    "012": "IT-012 Praias do Sado",
    "013": "IT-013 Uralada",
    "014": "IT-014 Lisnave",
    "015": "IT-015 Fundeadouros",
    "016": "IT-016 Rebocadores",
    "017": "IT-017 Pilotagem Assistida",
    "018": "IT-018 Normas Especiais",
    "029": "IT-029 Cais da SAPEC",
    "036": "IT-036 Regulação de Agulhas",
    "038": "IT-038 Cais Alstom",
    "041": "IT-041 Entrada e Saída de Navios",
    "042": "IT-042 Recomendações Navios Canal Norte",
    "062": "IT-062 Cais da Teporset",
}

TIDE_QUERY_RE = re.compile(r"\b(mare|mares|preia mar|preia-mar|baixa mar|baixa-mar)\b")
WEATHER_QUERY_RE = re.compile(
    r"\b(meteorologia|meteorologic|condicoes meteorologicas|condicoes do tempo|tempo no porto|vento|visibilidade|humidade|temperatura|chuva)\b"
)
CURRENT_WEATHER_RE = re.compile(r"\b(atual|atuais|agora|neste momento|corrente|correntes|hoje)\b")


def available_rule_code_titles() -> dict[str, str]:
    """Return the rule-code map limited to documents actually present in the knowledge base."""
    knowledge_dir = getattr(services, "KNOWLEDGE_DIR", "") or ""
    if not knowledge_dir or not os.path.isdir(knowledge_dir):
        return dict(RULE_CODE_TITLES)

    available_codes = set()
    try:
        for entry in os.listdir(knowledge_dir):
            match = re.match(r"IT-(\d{3})_", entry)
            if match:
                available_codes.add(match.group(1))
    except OSError:
        return dict(RULE_CODE_TITLES)

    filtered = {
        code: title
        for code, title in RULE_CODE_TITLES.items()
        if code in available_codes
    }
    return filtered or dict(RULE_CODE_TITLES)


def build_rule_catalog_text() -> str:
    """Return a user-facing catalog of the available operational rules by code."""
    lines = [
        "Regras/instruções disponíveis por código:",
    ]
    for code, title in sorted(available_rule_code_titles().items(), key=lambda item: item[0]):
        lines.append(f"- {code} — {title}")
    lines.extend(
        [
            "",
            "Usa `/regra 015` para resumir uma regra específica.",
        ]
    )
    return "\n".join(lines)


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
    archived_scales = [
        item for item in port_activity.get("archived_scales", [])
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

    slot_occupancy = build_slot_occupancy(in_port, berth_options=services.BERTH_OPTIONS)
    berthed = slot_occupancy["berthed"]
    anchorages = slot_occupancy["anchorages"]

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
        "anchorages": anchorages,
        "departed": departed,
        "aborted": aborted,
        "planned_maneuvers": planned_maneuvers,
        "archived_maneuvers": archived_maneuvers,
        "archived_scales": archived_scales,
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
        "quay_vessel_count": slot_occupancy["quay_vessel_count"],
        "anchorage_vessel_count": slot_occupancy["anchorage_vessel_count"],
        "quadro_count": slot_occupancy["anchorage_vessel_count"],
        "departed_count": len(departed),
        "berth_count": slot_occupancy["occupied_slot_count"],
        "occupied_slot_count": slot_occupancy["occupied_slot_count"],
        "free_slot_count": slot_occupancy["free_slot_count"],
        "slot_capacity_count": slot_occupancy["slot_capacity_count"],
        "aborted_count": len(aborted),
        "planned_count": len(planned_maneuvers),
        "archive_count": len(archived_maneuvers),
        "archive_scale_count": len(archived_scales),
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
        wants_json = (
            request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") in {"fetch", "XMLHttpRequest"}
        )
        if not session.get("username"):
            if wants_json:
                return jsonify({"error": "Sessão expirada. Faz login novamente."}), 401
            return redirect(url_for("auth.login"))
        if not ensure_session_user_profile():
            if wants_json:
                return jsonify({"error": "Sessão expirada. Faz login novamente."}), 401
            flash("Sessao expirada. Inicia sessao novamente.", "error")
            return redirect(url_for("auth.login"))
        if (
            session_profile_incomplete()
            and request.endpoint not in {"auth.profile", "auth.logout", "static", "dashboard_bp.image_asset"}
        ):
            if wants_json:
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
                wants_json = (
                    request.path.startswith("/api/")
                    or request.accept_mimetypes.best == "application/json"
                    or request.headers.get("X-Requested-With") in {"fetch", "XMLHttpRequest"}
                )
                if wants_json:
                    return jsonify({"error": "Nao tens permissao para esta acao."}), 403
                flash("Nao tens permissao para esta acao.", "error")
                return redirect(url_for("dashboard_bp.dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def redirect_to_portal_target(port_call_id: str):
    """Redirect to the scale detail, registration, or dashboard based on the form's redirect_to field."""
    target = request.form.get("redirect_to", "").strip().lower()
    maneuver_id = request.form.get("redirect_maneuver_id", "").strip()
    if target == "maneuver" and maneuver_id:
        return redirect(url_for("port_calls.maneuver_detail", port_call_id=port_call_id, maneuver_id=maneuver_id))
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
        retry_at = next_provider_quota_reset_utc(
            getattr(services.rag, "_embedding_quota_provider_name", services.rag.embedding_provider_name)
        )
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
    workflow_progress_pct = float(status_payload.get("progress_pct") or 0.0)
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
        "embedding_provider": (
            services.rag.embedding_provider.model_name
            if services.rag._use_local_embeddings and services.rag.embedding_provider
            else services.rag.embedding_provider_label
            if services.rag.client
            else "indisponivel"
        ),
        "query_embedding_status": (
            "blocked" if services.rag.is_embedding_quota_exhausted()
            else "available" if services.rag._use_local_embeddings or services.rag.client
            else "disabled"
        ),
        "query_embedding_summary": (
            "Pesquisa semântica bloqueada até renovar quota."
            if services.rag.is_embedding_quota_exhausted()
            else "Pesquisa semântica disponível."
            if services.rag._use_local_embeddings or services.rag.client
            else "Pesquisa semântica em modo lexical: embeddings locais indisponíveis."
        ),
        "scheduled_retry_at": retry_status.get("scheduled_for"),
        "scheduled_retry_eta_seconds": retry_status.get("eta_seconds"),
        "scheduled_retry_reason": retry_status.get("reason", ""),
        "workflow_progress_pct": workflow_progress_pct,
    }

    if status_payload.get("state") == "completed":
        pending_documents_total = int(status_payload.get("pending_documents_total") or 0)
        total_chunks = int(status_payload.get("total_chunks") or 0)
        if pending_documents_total > 0:
            status_payload["progress_pct"] = (
                float(status_payload.get("semantic_chunk_coverage_pct") or 0.0)
                if total_chunks > 0
                else 0.0
            )
        else:
            status_payload["progress_pct"] = 100.0

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
    wave_enabled = bool(getattr(services, "wave_service", None) and services.wave_service.enabled)
    warning_enabled = bool(getattr(services, "local_warning_service", None) and services.local_warning_service.enabled)
    wave_conditions = None
    wave_status = {}
    wave_status_error = ""
    wave_fetch_ok = False
    if getattr(services, "wave_service", None):
        try:
            if wave_enabled:
                wave_conditions = services.wave_service.probe_current_conditions()
                wave_fetch_ok = True
            wave_status = services.wave_service.status()
        except Exception as exc:
            wave_status_error = str(exc)
            try:
                wave_status = services.wave_service.status()
            except Exception:
                wave_status = {}
    local_warnings = []
    local_warning_status = {}
    local_warning_status_error = ""
    local_warning_fetch_ok = False
    if getattr(services, "local_warning_service", None):
        try:
            if warning_enabled:
                local_warnings = services.local_warning_service.probe_warnings()
                local_warning_fetch_ok = True
            local_warning_status = services.local_warning_service.status()
        except Exception as exc:
            local_warning_status_error = str(exc)
            try:
                local_warning_status = services.local_warning_service.status()
            except Exception:
                local_warning_status = {}
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
            "stats": {
                "scheduled_count": 0,
                "in_port_count": 0,
                "quay_vessel_count": 0,
                "anchorage_vessel_count": 0,
                "quadro_count": 0,
                "departed_count": 0,
                "planned_count": 0,
                "berth_count": 0,
                "occupied_slot_count": 0,
                "free_slot_count": 0,
                "slot_capacity_count": 0,
                "archive_count": 0,
                "archive_scale_count": 0,
                "aborted_count": 0,
                "pending_count": 0,
            },
            "arrivals": [],
            "in_port": [],
            "berthed": [],
            "anchorages": [],
            "departed": [],
            "planned_maneuvers": [],
            "error": str(exc),
        }

    def _state(ok: bool, *, degraded: bool = False) -> str:
        if ok and degraded:
            return "degraded"
        return "online" if ok else "offline"

    def _backend_label(value: str) -> str:
        mapping = {
            "local": "Local JSON",
            "postgres": "PostgreSQL",
            "unknown": "Desconhecido",
        }
        return mapping.get((value or "").strip().lower(), (value or "").strip() or "Desconhecido")

    def _build_service_item(
        label: str,
        *,
        ok: bool,
        headline: str,
        detail: str,
        technical: str = "",
        degraded: bool = False,
    ) -> dict:
        state = _state(ok, degraded=degraded)
        return {
            "label": label,
            "headline": headline,
            "detail": detail,
            "technical": technical,
            "state": state,
            "state_label": {
                "online": "Ligado",
                "degraded": "Degradado",
                "offline": "Desligado",
            }[state],
        }

    def _status_timestamp(label: str) -> str:
        return f"Última tentativa {label}" if label else "Última tentativa sem registo"

    def _wave_service_item() -> dict:
        station_name = getattr(getattr(services, "wave_service", None), "station_name", "Sines")
        error_message = (
            wave_status_error
            or wave_status.get("error")
            or (wave_conditions or {}).get("source_error")
            or ""
        ).strip()
        is_stale = bool(wave_status.get("stale") or (wave_conditions or {}).get("cache_stale"))
        cache_label = wave_status.get("cache_updated_at_label") or (wave_conditions or {}).get("cache_updated_at_label") or ""
        if not wave_enabled:
            return _build_service_item(
                "Ondulação",
                ok=False,
                headline="Desligado",
                detail="Sem endpoint configurado para leitura costeira.",
                technical=station_name,
            )
        if is_stale:
            technical = (
                f"Snapshot {cache_label}"
                if cache_label
                else _status_timestamp(wave_status.get("last_attempt_at_label") or "")
            )
            if wave_conditions and wave_conditions.get("last_reading_label"):
                technical = f"{technical} · leitura {wave_conditions['last_reading_label']}"
            return _build_service_item(
                "Ondulação",
                ok=True,
                degraded=True,
                headline="Cache local",
                detail=error_message or "Origem remota indisponível, a usar a última leitura guardada.",
                technical=technical,
            )
        if wave_fetch_ok and wave_conditions:
            return _build_service_item(
                "Ondulação",
                ok=True,
                headline="Leitura confirmada",
                detail=f"{station_name} · {wave_conditions.get('last_reading_label', '--')}",
                technical=(
                    f"{wave_conditions.get('significant_height_label', '--')} sig. · "
                    f"{wave_conditions.get('mean_period_label', '--')} · "
                    f"{wave_conditions.get('direction', '--')}"
                ),
            )
        return _build_service_item(
            "Ondulação",
            ok=False,
            headline="Indisponível",
            detail=error_message or "Sem leitura costeira disponível.",
            technical=_status_timestamp(wave_status.get("last_attempt_at_label") or ""),
        )

    def _local_warning_service_item() -> dict:
        error_message = (
            local_warning_status_error
            or local_warning_status.get("error")
            or ""
        ).strip()
        is_stale = bool(local_warning_status.get("stale"))
        cache_label = local_warning_status.get("cache_updated_at_label") or ""
        warning_count = int(local_warning_status.get("count") or len(local_warnings))
        if not warning_enabled:
            return _build_service_item(
                "Avisos locais",
                ok=False,
                headline="Desligado",
                detail="Sem endpoint configurado para a fonte oficial.",
                technical="Instituto Hidrográfico",
            )
        if is_stale:
            technical = (
                f"{warning_count} aviso(s) em cache · atualizado {cache_label}"
                if cache_label
                else f"{warning_count} aviso(s) em cache"
            )
            return _build_service_item(
                "Avisos locais",
                ok=True,
                degraded=True,
                headline="Cache local",
                detail=error_message or "Fonte oficial indisponível, a usar a última lista guardada.",
                technical=technical,
            )
        if local_warning_fetch_ok:
            technical = (
                f"Sincronizado {cache_label}"
                if cache_label
                else _status_timestamp(local_warning_status.get("last_attempt_at_label") or "")
            )
            return _build_service_item(
                "Avisos locais",
                ok=True,
                headline="Fonte confirmada",
                detail=f"{len(local_warnings)} aviso(s) em vigor",
                technical=technical,
            )
        return _build_service_item(
            "Avisos locais",
            ok=False,
            headline="Indisponível",
            detail=error_message or "Sem dados dos avisos locais.",
            technical=_status_timestamp(local_warning_status.get("last_attempt_at_label") or ""),
        )

    rag_chunk_total = int(rag_status.get("chunk_count") or 0)
    rag_embedded_total = int(rag_status.get("embedded_chunks") or 0)
    rag_coverage_pct = round((rag_embedded_total / rag_chunk_total) * 100) if rag_chunk_total else 0
    llm_ready = services.rag.can_generate()
    embeddings_ready = bool(services.rag._use_local_embeddings or services.rag.client)
    weather_ready = bool(getattr(services, "weather_service", None) and services.weather_service.enabled)
    storage_backend = getattr(services.store, "backend_name", "unknown")
    storage_label = _backend_label(storage_backend)
    db_runtime_ok = bool(db_runtime)
    db_degraded = bool(db_runtime_error)

    storage_detail = (
        f"{db_runtime.get('database_name', '--')} · utilizador {db_runtime.get('database_user', '--')}"
        if db_runtime
        else "Persistência local em ficheiro JSON"
        if storage_backend == "local"
        else db_runtime_error or "Runtime PostgreSQL indisponível"
    )
    storage_technical = (
        "pgvector ativo" if db_runtime and db_runtime.get("vector_installed")
        else "pgvector em falta" if db_runtime
        else "Sem ligação runtime PostgreSQL"
    )
    startup_migration = services.startup_migration_status or {}
    startup_migration_status = (startup_migration.get("status") or "unknown").strip().lower()
    migration_state = {
        "completed": "online",
        "skipped": "degraded",
        "disabled": "degraded",
        "not_applicable": "online",
        "error": "offline",
    }.get(startup_migration_status, "degraded")
    migration_label = {
        "completed": "Concluída",
        "skipped": "Ignorada",
        "disabled": "Desativada",
        "not_applicable": "Não aplicável",
        "error": "Com erro",
    }.get(startup_migration_status, "Sem estado")
    migration_detail = startup_migration.get("reason") or "Estado da migração automática no arranque."
    embedding_detail = (
        services.rag.embedding_provider.model_name
        if services.rag._use_local_embeddings and services.rag.embedding_provider
        else services.rag.embedding_model
        if embeddings_ready
        else f"Configurar {services.rag.embedding_api_key_hint}"
    )

    service_health = [
        _build_service_item(
            "Geração IA",
            ok=llm_ready,
            headline=services.rag.generation_provider_label if llm_ready else "Sem geração disponível",
            detail=services.rag.generation_model if llm_ready else services.rag.generation_unavailable_reason(),
            technical="cadeia de providers ativa",
        ),
        _build_service_item(
            "Embeddings",
            ok=embeddings_ready,
            headline="Modelo local" if services.rag._use_local_embeddings else services.rag.embedding_provider_label if embeddings_ready else "Indisponíveis",
            detail=embedding_detail,
            technical="indexação semântica e procura vetorial",
        ),
        _build_service_item(
            "Meteorologia",
            ok=weather_ready,
            headline="WeatherAPI configurado" if weather_ready else "WeatherAPI em falta",
            detail=getattr(getattr(services, "weather_service", None), "location", "") or "Setúbal",
            technical="previsão horária e vento",
        ),
        _wave_service_item(),
        _build_service_item(
            "AIS",
            ok=bool(ais_status.get("map_available")),
            headline="Embed web público",
            detail=f"{ais_status.get('provider_name', 'AIS')} · zoom {ais_status.get('embed', {}).get('zoom', '--')}",
            technical=f"{ais_status.get('embed', {}).get('latitude_dm', '--')} · {ais_status.get('embed', {}).get('longitude_dm', '--')}",
        ),
        _local_warning_service_item(),
        _build_service_item(
            "Persistência",
            ok=storage_backend == "local" or db_runtime_ok,
            degraded=db_degraded,
            headline=storage_label,
            detail=storage_detail,
            technical=storage_technical,
        ),
    ]

    alerts = []
    if db_runtime_error:
        alerts.append(f"Base de dados: {db_runtime_error}")
    if rag_status.get("index_error"):
        alerts.append(f"Índice documental: {rag_status['index_error']}")
    if port_activity.get("error"):
        alerts.append(f"Snapshot operacional: {port_activity['error']}")
    if wave_enabled and (wave_status.get("error") or wave_status_error):
        alerts.append(f"Ondulação: {wave_status_error or wave_status.get('error')}")
    if warning_enabled and (local_warning_status.get("error") or local_warning_status_error):
        alerts.append(f"Avisos locais: {local_warning_status_error or local_warning_status.get('error')}")
    if not llm_ready:
        alerts.append("Geração IA indisponível.")

    overall_state = "online"
    if alerts:
        overall_state = "degraded"
    if not llm_ready or (storage_backend == "postgres" and not db_runtime_ok):
        overall_state = "offline"

    operational_metrics = [
        {"label": "Chegadas previstas", "value": port_activity["stats"].get("scheduled_count", 0), "tone": "neutral"},
        {"label": "Em porto", "value": port_activity["stats"].get("in_port_count", 0), "tone": "success"},
        {"label": "Em quadro", "value": port_activity["stats"].get("quadro_count", 0), "tone": "warning"},
        {"label": "Em cais", "value": port_activity["stats"].get("quay_vessel_count", 0), "tone": "neutral"},
        {
            "label": "Slots ocupados",
            "value": f"{port_activity['stats'].get('occupied_slot_count', 0)}/{port_activity['stats'].get('slot_capacity_count', 0)}",
            "tone": "neutral",
        },
        {"label": "Livres", "value": port_activity["stats"].get("free_slot_count", 0), "tone": "success"},
        {"label": "Manobras planeadas", "value": port_activity["stats"].get("planned_count", 0), "tone": "neutral"},
        {"label": "Pendentes", "value": port_activity["stats"].get("pending_count", 0), "tone": "warning"},
        {"label": "Arquivo de escalas", "value": port_activity["stats"].get("archive_scale_count", 0), "tone": "neutral"},
        {"label": "Abortadas", "value": port_activity["stats"].get("aborted_count", 0), "tone": "danger"},
    ]

    db_metrics = [
        {"label": "Base", "value": db_runtime.get("database_name", "--") if db_runtime else storage_label},
        {"label": "Utilizador", "value": db_runtime.get("database_user", "--") if db_runtime else "n/d"},
        {"label": "pgvector", "value": "Ligado" if db_runtime and db_runtime.get("vector_installed") else "Desligado"},
        {"label": "Users", "value": db_runtime.get("counts", {}).get("app_users", local_counts.get("users", 0)) if db_runtime else local_counts.get("users", 0)},
        {"label": "Docs", "value": db_runtime.get("counts", {}).get("documents", local_counts.get("documents", 0)) if db_runtime else local_counts.get("documents", 0)},
        {"label": "Conversas", "value": db_runtime.get("counts", {}).get("conversations", local_counts.get("conversations", 0)) if db_runtime else local_counts.get("conversations", 0)},
        {"label": "Mensagens", "value": db_runtime.get("counts", {}).get("messages", 0) if db_runtime else 0},
        {"label": "Escalas", "value": db_runtime.get("counts", {}).get("port_calls", 0) if db_runtime else port_activity["stats"].get("scheduled_count", 0) + port_activity["stats"].get("in_port_count", 0) + port_activity["stats"].get("departed_count", 0)},
        {"label": "Chunks", "value": db_runtime.get("counts", {}).get("rag_chunks", rag_chunk_total) if db_runtime else rag_chunk_total},
    ]

    rag_metrics = [
        {"label": "Backend", "value": _backend_label(rag_status.get("index_backend", "unknown"))},
        {"label": "Documentos", "value": rag_status.get("document_count", 0)},
        {"label": "Chunks", "value": rag_chunk_total},
        {"label": "Com embedding", "value": rag_embedded_total},
        {"label": "Cobertura", "value": f"{rag_coverage_pct}%"},
        {"label": "Novos", "value": rag_reindex_status.get("new_documents", 0)},
        {"label": "Alterados", "value": rag_reindex_status.get("changed_documents", 0)},
        {"label": "Removidos", "value": rag_reindex_status.get("removed_documents", 0)},
        {"label": "Sem alterações", "value": rag_reindex_status.get("unchanged_documents", 0)},
    ]

    return {
        "auth_backend": getattr(services.auth_service, "backend_name", "unknown"),
        "auth_backend_label": _backend_label(getattr(services.auth_service, "backend_name", "unknown")),
        "auth_method_label": f"Werkzeug · {PASSWORD_HASH_METHOD}",
        "storage_backend": storage_backend,
        "storage_backend_label": storage_label,
        "rag_backend": getattr(services.index_store, "backend_name", "unknown"),
        "rag_backend_label": _backend_label(getattr(services.index_store, "backend_name", "unknown")),
        "config": {
            "llm_ready": llm_ready,
            "embeddings_local": services.rag._use_local_embeddings,
            "embedding_model": services.rag.embedding_model if not services.rag._use_local_embeddings else (services.rag.embedding_provider.model_name if services.rag.embedding_provider else "N/A"),
            "weather_ready": weather_ready,
            "wave_ready": wave_enabled,
            "warnings_ready": warning_enabled,
            "ais_ready": bool(ais_status.get("configured")),
            "database_url_ready": bool(database_url),
            "migrate_on_start": os.getenv("MIGRATE_LOCAL_DATA_ON_START", "1"),
        },
        "startup_migration": services.startup_migration_status,
        "startup_migration_status": {
            "label": migration_label,
            "state": migration_state,
            "detail": migration_detail,
        },
        "db_runtime": db_runtime,
        "db_runtime_error": db_runtime_error,
        "rag_status": rag_status,
        "rag_reindex_status": rag_reindex_status,
        "rag_coverage_pct": rag_coverage_pct,
        "ais_status": ais_status,
        "wave_status": wave_status,
        "wave_status_error": wave_status_error,
        "local_warning_status": local_warning_status,
        "local_warning_status_error": local_warning_status_error,
        "port_activity": port_activity,
        "local_counts": local_counts,
        "service_health": service_health,
        "alerts": alerts,
        "overall_status": {
            "state": overall_state,
            "label": {
                "online": "Operacional",
                "degraded": "Com avisos",
                "offline": "Requer intervenção",
            }[overall_state],
        },
        "operational_metrics": operational_metrics,
        "db_metrics": db_metrics,
        "rag_metrics": rag_metrics,
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
                "date_label": group.get("date_label") or group.get("date", ""),
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
        "- O quadro operacional conta ocupação apenas por slots de cais; fundeadouros são quadro e não ocupam slots.",
        (
            f"- Chegadas previstas: {port_activity['stats']['scheduled_count']} | "
            f"Navios em porto: {port_activity['stats']['in_port_count']} | "
            f"em cais: {port_activity['stats'].get('quay_vessel_count', 0)} | "
            f"em quadro: {port_activity['stats'].get('quadro_count', 0)} | "
            f"slots ocupados: {port_activity['stats'].get('occupied_slot_count', 0)}/"
            f"{port_activity['stats'].get('slot_capacity_count', 0)} | "
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
        "- Fundeadouros representam navios em quadro/espera e não contam como slots de cais ocupados.",
        (
            f"- Escalas em porto: {port_activity['stats'].get('in_port_count', 0)} | "
            f"em cais: {port_activity['stats'].get('quay_vessel_count', 0)} | "
            f"em quadro: {port_activity['stats'].get('quadro_count', 0)} | "
            f"slots ocupados: {port_activity['stats'].get('occupied_slot_count', 0)}/"
            f"{port_activity['stats'].get('slot_capacity_count', 0)} | "
            f"chegadas previstas: {port_activity['stats'].get('scheduled_count', 0)} | "
            f"escalas com saída recente: {port_activity['stats'].get('departed_count', 0)}"
        ),
    ]
    for item in selected:
        status_label = (
            "Em quadro" if item.get("status") == "in_port" and is_anchorage_berth(item.get("berth_label"))
            else "Em porto" if item.get("status") == "in_port"
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


def build_berth_catalog_source(question: str) -> dict | None:
    """Build a berth catalog source for terminal/berth questions, with explicit Lisnave aliases."""
    clean = _operational_lookup_key(question)
    if not clean:
        return None
    if not re.search(r"\b(lisnave|doca|cais|fundeadouro|teporset|autoeuropa|sapec|tms)\b", clean):
        return None

    lisnave_berths = [item for item in services.BERTH_OPTIONS if item.startswith("Lisnave - ")]
    lines = [
        "Catálogo canónico de cais/fundeadouros do portal:",
        "- 'Lisnave' identifica o terminal/estaleiro; para registo operacional usa-se um cais ou doca específicos.",
        "- Aliases Lisnave reconhecidos pelo sistema: 'Doca 21' e 'Doca seca 21' -> 'Lisnave - Doca 21'; 'Cais 2 A' -> 'Lisnave - Cais 2 A'.",
        "- Cais/docas Lisnave disponíveis no sistema:",
    ]
    for item in lisnave_berths:
        lines.append(f"  {item}")
    return {
        "source_id": "OPS4",
        "document": "catalogo_cais_portal",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "berth_catalog",
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
    berth_catalog_source = build_berth_catalog_source(question)
    if berth_catalog_source:
        sources.append(berth_catalog_source)
    maneuver_case_source = build_maneuver_case_context_source(question, current_resolvable_port_calls())
    if maneuver_case_source:
        sources.append(maneuver_case_source)
    cost_source = build_cost_context_source(question, recent_port_activity)
    if cost_source:
        sources.append(cost_source)
    return sources


def answer_direct_operational_query(question: str) -> dict | None:
    """Answer deterministic operational lookup questions that should not rely on generic RAG wording."""
    clean_question = _operational_lookup_key(question)
    live_environment_answer = _answer_live_environment_query(question, clean_question)
    if live_environment_answer:
        return live_environment_answer
    port_calls = current_resolvable_port_calls()
    matched_port_call = _match_port_call_from_question(question, port_calls)

    maneuver_type = ""
    maneuver_label = "manobra"
    if re.search(r"\b(entrada|entry)\b", clean_question):
        maneuver_type = "entry"
        maneuver_label = "manobra de entrada"
    elif re.search(r"\b(saida|saida|departure)\b", clean_question):
        maneuver_type = "departure"
        maneuver_label = "manobra de saída"
    elif re.search(r"\b(mudanca|mudança|shift)\b", clean_question):
        maneuver_type = "shift"
        maneuver_label = "manobra de mudança"

    if not re.search(r"\b(id|identificador)\b", clean_question) or "manobra" not in clean_question:
        return None
    if not matched_port_call:
        return None

    resolved_port_call = services.store.get_port_call(matched_port_call["id"])
    maneuvers = list(resolved_port_call.get("maneuver_history", []) or [])
    if maneuver_type:
        maneuvers = [item for item in maneuvers if (item.get("type") or "").strip().lower() == maneuver_type]
    if not maneuvers:
        answer = f"Não encontrei {maneuver_label} para {resolved_port_call.get('vessel_name', 'este navio')}."
        return {"answer": answer, "sources": [], "answer_origin": "operational_lookup"}

    maneuvers.sort(
        key=lambda item: (
            item.get("planned_at") or "",
            item.get("completed_at") or "",
            item.get("updated_at") or "",
            item.get("created_at") or "",
        )
    )
    maneuver = maneuvers[-1]
    maneuver_id = maneuver.get("id", "")
    short_id = maneuver_id[:8].upper() if maneuver_id else "--"
    type_label = maneuver_label if maneuver_type else f"manobra {((maneuver.get('type') or '').strip().lower() or '--')}"
    answer = (
        f"O ID da {type_label} de {resolved_port_call.get('vessel_name', 'este navio')} "
        f"é {short_id} (completo: {maneuver_id})."
    )
    return {
        "answer": answer,
        "sources": [
            {
                "document": resolved_port_call.get("vessel_name", "Manobra"),
                "source_id": resolved_port_call.get("reference_code", ""),
                "retrieval_mode": "operational_lookup",
                "snippet": answer,
            }
        ],
        "answer_origin": "operational_lookup",
    }


def _build_tide_lookup_answer(question: str) -> tuple[str, list[dict]]:
    summaries = [
        services.tide_service.summary_for_date(target_date)
        for target_date in services.tide_service.resolve_query_dates(question)
    ]
    if not summaries:
        return "", []

    lines: list[str] = []
    for summary in summaries:
        lines.append(f"Marés para {summary.get('date_label', summary.get('date', 'a data pedida'))} em {summary.get('location', 'Setúbal / Tróia')}:")
        events = summary.get("events") or []
        if not events:
            lines.append("- Sem eventos de maré registados.")
            continue
        for item in events:
            lines.append(
                f"- {item.get('time', '--')} — {item.get('type', '--')} de {item.get('height_m', '--')} m"
            )
    context = services.tide_service.context_for_question(question)
    sources = [context] if context else []
    return "\n".join(lines), sources


def _build_weather_lookup_answer(question: str, clean_question: str) -> tuple[str, list[dict]]:
    weather_service = getattr(services, "weather_service", None)
    if not weather_service or not weather_service.enabled:
        return "A meteorologia live não está configurada neste ambiente.", []

    forecast = weather_service.get_forecast(days=3)
    if not forecast:
        return "Não consegui obter as condições meteorológicas atuais.", []

    location = forecast.get("location", {})
    current = forecast.get("current", {})
    if CURRENT_WEATHER_RE.search(clean_question):
        lines = [
            f"Condições meteorológicas atuais em {location.get('name', 'Setúbal')} ({location.get('localtime', '--')}):",
            f"- Estado do tempo: {current.get('condition', '--')}",
            f"- Temperatura: {current.get('temp_c', '--')} °C",
            f"- Vento: {current.get('wind_kts', '--')} kts de {current.get('wind_dir', '--')}",
            f"- Rajadas: {current.get('gust_kts', '--')} kts",
            f"- Humidade: {current.get('humidity', '--')}%",
            f"- Visibilidade: {current.get('vis_km', '--')} km",
            f"- Precipitação: {current.get('precip_mm', '--')} mm",
        ]
        context = weather_service.context_source()
        sources = [context] if context else []
        return "\n".join(lines), sources

    context = weather_service.context_for_question(question)
    if context:
        return context.get("text") or context.get("snippet", ""), [context]
    return "Não consegui obter a previsão meteorológica pedida.", []


def _answer_live_environment_query(question: str, clean_question: str) -> dict | None:
    wants_tides = bool(TIDE_QUERY_RE.search(clean_question))
    wants_weather = bool(WEATHER_QUERY_RE.search(clean_question))
    if not wants_tides and not wants_weather:
        return None

    answer_parts: list[str] = []
    sources: list[dict] = []

    if wants_tides:
        try:
            tide_answer, tide_sources = _build_tide_lookup_answer(question)
        except Exception as exc:
            logger.exception("Falha ao obter marés para consulta direta.")
            tide_answer = f"Falha ao obter marés: {exc}"
            tide_sources = []
        if tide_answer:
            answer_parts.append(tide_answer)
            sources.extend(tide_sources)

    if wants_weather:
        try:
            weather_answer, weather_sources = _build_weather_lookup_answer(question, clean_question)
        except Exception as exc:
            logger.exception("Falha ao obter meteorologia para consulta direta.")
            weather_answer = f"Falha ao obter meteorologia: {exc}"
            weather_sources = []
        if weather_answer:
            answer_parts.append(weather_answer)
            sources.extend(weather_sources)

    if not answer_parts:
        return None
    return {
        "answer": "\n\n".join(answer_parts),
        "sources": sources,
        "answer_origin": "operational_live",
    }


def _select_validation_maneuver(scale_context: dict, port_call: dict, target: dict) -> tuple[dict | None, list[dict]]:
    """Resolve a maneuver for hard validation, returning the decorated maneuver and same-type candidates."""
    maneuvers_by_id = {
        str(item.get("id") or ""): item
        for item in list(scale_context.get("maneuvers") or [])
        if item.get("id")
    }
    maneuver_id = str((target or {}).get("maneuver_id") or "").strip()
    maneuver_type = str((target or {}).get("maneuver_type") or "").strip().lower()
    if maneuver_id:
        raw_maneuver = resolve_maneuver(port_call, "delete_maneuver", maneuver_type, maneuver_id=maneuver_id)
        if not raw_maneuver:
            return None, []
        return maneuvers_by_id.get(str(raw_maneuver.get("id") or "")), []
    if maneuver_type not in {"entry", "departure", "shift"}:
        return None, []
    candidates = []
    for item in candidate_maneuvers_for_action(port_call, "delete_maneuver", maneuver_type):
        decorated = maneuvers_by_id.get(str(item.get("id") or ""))
        if decorated:
            candidates.append(decorated)
    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates


def answer_slash_validation(target: dict, role: str) -> dict:
    """Run the deterministic checklist/casebook validation for a specific maneuver."""
    del role  # Read-only command; access is already scoped by visible port calls.

    template = build_validate_maneuver_reply_template()
    port_call_match = resolve_port_call(current_resolvable_port_calls(), target or {})
    if not port_call_match:
        return {
            "answer": "Não encontrei a escala/manobra pedida para validar.\n\n" + template,
            "sources": [],
            "answer_origin": "slash_validation",
        }

    resolved_port_call = services.store.get_port_call(port_call_match["id"])
    scale_context = build_scale_context(resolved_port_call)
    maneuver, candidates = _select_validation_maneuver(scale_context, resolved_port_call, target or {})
    if not maneuver and len(candidates) > 1:
        lines = [
            (
                f"Encontrei {len(candidates)} manobras do mesmo tipo para a escala "
                f"{resolved_port_call.get('reference_code', '--')}. Indica o ID da manobra para fazer a validação dura."
            ),
            "",
            "Candidatas:",
        ]
        for item in candidates[:5]:
            lines.append(
                f"- {item.get('id', '--')} | {item.get('title', 'Manobra')} | "
                f"{item.get('status', '--')} | {item.get('planned_label') or item.get('when_label') or '--'}"
            )
        lines.extend(["", template])
        return {
            "answer": "\n".join(lines),
            "sources": [],
            "answer_origin": "slash_validation",
        }
    if not maneuver:
        return {
            "answer": "Não encontrei a manobra pedida para validar.\n\n" + template,
            "sources": [],
            "answer_origin": "slash_validation",
        }

    recommendation = maneuver.get("casebook_recommendation") or {}
    checklist = list(maneuver.get("analysis_checklist") or [])
    similar_cases = maneuver.get("similar_cases") or []
    details = [
        (
            f"Validação da {maneuver.get('title', 'manobra').lower()} da escala "
            f"{resolved_port_call.get('reference_code', '--')} ({resolved_port_call.get('vessel_name', 'navio')})"
        ),
        f"- ID da manobra: {maneuver.get('id', '--')}",
        f"- Estado atual: {maneuver.get('status', '--')}",
        f"- Janela planeada: {maneuver.get('planned_label') or maneuver.get('when_label') or '--'}",
        f"- Trajeto: {maneuver.get('origin', '--')} -> {maneuver.get('destination', '--')}",
        "",
        _format_operational_opinion_answer(
            port_call=resolved_port_call,
            maneuver=maneuver,
            recommendation=recommendation,
            similar_cases=similar_cases,
            checklist=checklist,
        ),
    ]
    snippet = recommendation.get("summary") or details[0]
    return {
        "answer": "\n".join(details),
        "sources": [
            {
                "document": resolved_port_call.get("vessel_name", "Validação de manobra"),
                "source_id": resolved_port_call.get("reference_code", ""),
                "retrieval_mode": "maneuver_validation",
                "snippet": snippet,
            }
        ],
        "answer_origin": "slash_validation",
    }


def _match_port_call_from_question(question: str, port_calls: list[dict]) -> dict | None:
    """Resolve a single visible port call from free text using reference code or vessel name."""
    clean_question = _operational_lookup_key(question)
    if not clean_question:
        return None
    padded_question = f" {clean_question} "
    by_reference = [
        item for item in port_calls
        if item.get("reference_code") and f" {_operational_lookup_key(item.get('reference_code'))} " in padded_question
    ]
    if len(by_reference) == 1:
        return by_reference[0]

    by_name = []
    for item in port_calls:
        vessel_key = _operational_lookup_key(item.get("vessel_name"))
        if vessel_key and f" {vessel_key} " in padded_question:
            by_name.append(item)
    if len(by_name) == 1:
        return by_name[0]
    return None


def _format_maneuver_case_flags(flags: list[str] | None) -> list[str]:
    mapping = {
        "wave_related": "ondulação relevante",
        "pilotage_suspended": "pilotagem suspensa",
        "pilotage_cancelled": "pilotagem cancelada",
        "entry_aborted_by_sea_state": "entrada abortada por estado do mar",
    }
    labels = []
    for flag in flags or []:
        clean = mapping.get((flag or "").strip().lower())
        if clean:
            labels.append(clean)
    return labels


def _format_case_feedback_label(value: str | None) -> str:
    return {
        "approved": "referência positiva",
        "avoid": "evitar como padrão",
        "review": "rever caso",
    }.get((value or "").strip().lower(), "")


def _build_checklist_item(status: str, title: str, detail: str) -> dict:
    return {
        "status": status,
        "title": title,
        "detail": detail,
    }


def _format_operational_opinion_answer(
    *,
    port_call: dict,
    maneuver: dict,
    recommendation: dict,
    similar_cases: list[dict],
    checklist: list[dict],
) -> str:
    """Format a professional, structured opinion answer for a maneuver."""
    alerts = [item for item in checklist if item.get("status") == "caution"]
    infos = [item for item in checklist if item.get("status") == "info"]
    top_case = similar_cases[0] if similar_cases else {}

    lines = [
        "Leitura rápida",
        (
            f"- {maneuver.get('title', 'Manobra')} de {port_call.get('vessel_name', 'navio')}: "
            f"{recommendation.get('title', 'sem leitura histórica forte')}."
        ),
    ]
    if recommendation.get("basis_label"):
        lines.append(f"- {recommendation['basis_label']}.")

    lines.append("")
    lines.append("Alertas")
    if alerts:
        for item in alerts[:3]:
            lines.append(f"- {item.get('title', '')}: {item.get('detail', '')}")
    else:
        lines.append("- Sem alertas críticos nesta leitura determinística.")
    if not alerts and infos:
        lines.append(f"- Nota: {infos[0].get('detail', '')}")

    lines.append("")
    lines.append("Recomendação")
    lines.append(f"- {recommendation.get('summary', 'Sem recomendação automática disponível.')}")
    if recommendation.get("signals_label"):
        lines.append(f"- Sinais: {recommendation['signals_label']}.")

    lines.append("")
    lines.append("Base usada")
    lines.append("- Checklist operacional determinística do portal.")
    if similar_cases:
        base_line = (
            f"- Histórico semelhante: {len(similar_cases)} caso(s); mais próximo {top_case.get('reference_code', '--')} "
            f"({top_case.get('state_label', '--')} · {top_case.get('route_label', '--')})."
        )
        lines.append(base_line)
        if top_case.get("feedback_status_label"):
            lines.append(f"- Feedback validado do caso mais próximo: {top_case['feedback_status_label']}.")
    else:
        lines.append("- Histórico semelhante: sem casos suficientes para comparação.")
    lines.append("- Base documental: não foi invocada regra específica nesta leitura; pede regra/norma se precisares de enquadramento normativo.")
    lines.append("")
    lines.append("Isto apoia a decisão, mas não substitui a validação operacional do momento.")
    return "\n".join(lines).strip()


def _build_maneuver_analysis_checklist(
    port_call: dict,
    maneuver: dict,
    *,
    similar_cases: list[dict],
    casebook_recommendation: dict,
) -> tuple[list[dict], dict]:
    """Build a deterministic operational checklist for a maneuver analysis."""
    items: list[dict] = []
    maneuver_type = (maneuver.get("type") or "").strip().lower()
    origin = (maneuver.get("origin") or "").strip()
    destination = (maneuver.get("destination") or "").strip()
    tug_count_raw = str(maneuver.get("tug_count") or "").strip()
    tug_count = int(tug_count_raw) if tug_count_raw.isdigit() else 0
    bow_thruster = (port_call.get("vessel_bow_thruster") or "").strip().lower()
    stern_thruster = (port_call.get("vessel_stern_thruster") or "").strip().lower()

    required_profile = [
        ("tipo", port_call.get("vessel_type")),
        ("LOA", port_call.get("vessel_loa_m")),
        ("boca", port_call.get("vessel_beam_m")),
        ("GT", port_call.get("vessel_gt_t")),
        ("calado máximo", port_call.get("vessel_max_draft_m")),
    ]
    missing_profile = [label for label, value in required_profile if not str(value or "").strip()]
    if missing_profile:
        items.append(
            _build_checklist_item(
                "caution",
                "Perfil do navio",
                f"Faltam dados para análise segura: {', '.join(missing_profile)}.",
            )
        )
    else:
        items.append(
            _build_checklist_item(
                "ok",
                "Perfil do navio",
                "Tipo, dimensões, GT e calado máximo estão preenchidos.",
            )
        )

    if maneuver_type in {"entry", "shift"}:
        canonical_destination = canonicalize_berth_label(destination, berth_options=services.BERTH_OPTIONS)
        if not destination:
            items.append(_build_checklist_item("caution", "Destino operacional", "Falta indicar cais ou fundeadouro de destino."))
        elif not is_known_berth_label(canonical_destination, berth_options=services.BERTH_OPTIONS):
            items.append(
                _build_checklist_item(
                    "caution",
                    "Destino operacional",
                    f"O destino '{destination}' não está no catálogo canónico do porto.",
                )
            )
        else:
            items.append(
                _build_checklist_item(
                    "ok",
                    "Destino operacional",
                    f"Destino normalizado para {canonical_destination}.",
                )
            )
            in_port_items = [
                item
                for item in current_resolvable_port_calls()
                if (item.get("status") or "").strip().lower() == "in_port"
            ]
            conflict = find_occupied_berth_conflict(
                canonical_destination,
                in_port_items,
                current_port_call_id=port_call.get("id", ""),
                berth_options=services.BERTH_OPTIONS,
            )
            if conflict:
                items.append(
                    _build_checklist_item(
                        "caution",
                        "Disponibilidade do destino",
                        f"{canonical_destination} está ocupado por {conflict.get('vessel_name', 'outro navio')}.",
                    )
                )
            else:
                items.append(
                    _build_checklist_item(
                        "ok",
                        "Disponibilidade do destino",
                        f"{canonical_destination} está livre no snapshot operacional atual.",
                    )
                )
    else:
        if origin:
            items.append(
                _build_checklist_item(
                    "ok",
                    "Origem operacional",
                    f"A saída segue o último local conhecido do navio: {origin}.",
                )
            )
        else:
            items.append(
                _build_checklist_item(
                    "caution",
                    "Origem operacional",
                    "A origem da saída não está definida no registo atual.",
                )
            )
        if destination:
            items.append(
                _build_checklist_item(
                    "ok",
                    "Destino externo",
                    f"Próximo destino indicado: {destination}.",
                )
            )
        else:
            items.append(
                _build_checklist_item(
                    "caution",
                    "Destino externo",
                    "Falta indicar o próximo destino da saída.",
                )
            )

    if tug_count > 0:
        items.append(
            _build_checklist_item(
                "ok",
                "Meios de governo",
                f"Estão previstos {tug_count} rebocador(es) para a manobra.",
            )
        )
    elif bow_thruster == "yes" or stern_thruster == "yes":
        items.append(
            _build_checklist_item(
                "info",
                "Meios de governo",
                "Sem rebocadores previstos; o navio tem thruster(s) declarado(s).",
            )
        )
    elif bow_thruster == "unknown" or stern_thruster == "unknown":
        items.append(
            _build_checklist_item(
                "caution",
                "Meios de governo",
                "Sem rebocadores previstos e os thrusters do navio ainda não estão totalmente confirmados.",
            )
        )
    else:
        items.append(
            _build_checklist_item(
                "caution",
                "Meios de governo",
                "Sem rebocadores previstos e sem thrusters declarados.",
            )
        )

    constraint_labels = format_constraint_labels(maneuver.get("constraint_codes") or [])
    if constraint_labels:
        items.append(
            _build_checklist_item(
                "caution",
                "Restrições operacionais",
                f"Há restrições ativas: {', '.join(constraint_labels)}.",
            )
        )
    else:
        items.append(
            _build_checklist_item(
                "ok",
                "Restrições operacionais",
                "Não há restrições explícitas registadas nesta manobra.",
            )
        )

    if maneuver_type in {"entry", "departure"}:
        items.append(
            _build_checklist_item(
                "info",
                "Ondulação e barra",
                "Validar leitura costeira fora da barra, Pilar 2 e zona do Outão antes de decidir.",
            )
        )
    else:
        items.append(
            _build_checklist_item(
                "info",
                "Ondulação e barra",
                "Não é fator primário para mudanças internas, salvo condicionantes excecionais.",
            )
        )

    if casebook_recommendation:
        checklist_status = (
            "ok"
            if casebook_recommendation.get("status_key") == "positive"
            else "caution"
            if casebook_recommendation.get("status_key") == "caution"
            else "info"
        )
        detail = casebook_recommendation.get("summary", "")
        if casebook_recommendation.get("basis_label"):
            detail = f"{detail} Base: {casebook_recommendation['basis_label']}"
        items.append(
            _build_checklist_item(
                checklist_status,
                "Histórico semelhante",
                detail.strip(),
            )
        )
    elif similar_cases:
        items.append(
            _build_checklist_item(
                "info",
                "Histórico semelhante",
                f"Foram encontrados {len(similar_cases)} caso(s) semelhante(s), sem padrão decisivo único.",
            )
        )
    else:
        items.append(
            _build_checklist_item(
                "info",
                "Histórico semelhante",
                "Ainda não há casos semelhantes suficientes para apoiar a decisão.",
            )
        )

    caution_count = sum(1 for item in items if item.get("status") == "caution")
    ok_count = sum(1 for item in items if item.get("status") == "ok")
    summary = {
        "caution_count": caution_count,
        "ok_count": ok_count,
        "info_count": sum(1 for item in items if item.get("status") == "info"),
        "headline": (
            "Checklist com alertas operacionais"
            if caution_count
            else "Checklist operacional coerente"
            if ok_count
            else "Checklist operacional informativa"
        ),
    }
    return items, summary


def _format_thruster_case_label(value: str | None) -> str:
    clean = (value or "").strip().lower()
    if clean in {"yes", "true", "1", "sim"}:
        return "Sim"
    if clean in {"no", "false", "0", "nao", "não"}:
        return "Não"
    return "Desconhecido"


def _extract_case_decision_excerpt(case: dict) -> str:
    for value in (
        ((case.get("execution_snapshot") or {}).get("report_note") or "").strip(),
        ((case.get("decision_snapshot") or {}).get("aborted_reason") or "").strip(),
        ((case.get("decision_snapshot") or {}).get("approval_note") or "").strip(),
        ((case.get("planning_snapshot") or {}).get("plan_observations") or "").strip(),
        ((case.get("planning_snapshot") or {}).get("plan_note") or "").strip(),
    ):
        if value:
            compact = " ".join(value.split())
            return compact[:180] + "…" if len(compact) > 180 else compact
    return ""


def _build_similar_case_cards(port_call: dict, maneuver: dict, limit: int = 3) -> list[dict]:
    try:
        ranked_cases = services.store.find_similar_maneuver_cases(
            maneuver_type=maneuver.get("type", ""),
            origin=maneuver.get("origin", ""),
            destination=maneuver.get("destination", ""),
            vessel_type=port_call.get("vessel_type", ""),
            vessel_loa_m=port_call.get("vessel_loa_m", ""),
            bow_thruster=port_call.get("vessel_bow_thruster", ""),
            stern_thruster=port_call.get("vessel_stern_thruster", ""),
            tug_count=maneuver.get("tug_count", ""),
            limit=max(limit + 1, 4),
        )
    except Exception:
        logger.exception("Falha ao procurar casos semelhantes para a manobra %s.", maneuver.get("id"))
        return []

    cards = []
    for case in ranked_cases:
        if case.get("maneuver_id") == maneuver.get("id"):
            continue
        features = case.get("feature_snapshot") or {}
        decision_flags = _format_maneuver_case_flags((case.get("outcome_snapshot") or {}).get("decision_flags"))
        reasons = list(case.get("similarity_reasons") or [])
        cards.append(
            {
                "maneuver_id": case.get("maneuver_id", ""),
                "port_call_id": case.get("port_call_id", ""),
                "reference_code": case.get("reference_code", "--"),
                "vessel_name": case.get("vessel_name", "--"),
                "state_label": case.get("current_state_label", "--"),
                "status_class": (
                    "completed"
                    if case.get("current_state") == "completed"
                    else "aborted"
                    if case.get("current_state") == "aborted"
                    else "pending"
                ),
                "route_label": f"{case.get('origin_label') or '--'} → {case.get('destination_label') or '--'}",
                "latest_event_label": case.get("latest_event_label", "--"),
                "planned_label": case.get("planned_label", "--"),
                "similarity_score": case.get("similarity_score", 0),
                "reasons_label": ", ".join(reasons) if reasons else "perfil semelhante",
                "decision_flags": decision_flags,
                "decision_excerpt": _extract_case_decision_excerpt(case),
                "feedback_status": case.get("feedback_status", ""),
                "feedback_status_label": case.get("feedback_status_label", ""),
                "feedback_note": case.get("feedback_note", ""),
                "tug_count": features.get("tug_count") or "--",
                "loa_label": (
                    f"{features.get('vessel_loa_m'):.1f} m"
                    if isinstance(features.get("vessel_loa_m"), (int, float))
                    else "--"
                ),
                "bow_thruster_label": _format_thruster_case_label(features.get("bow_thruster")),
                "stern_thruster_label": _format_thruster_case_label(features.get("stern_thruster")),
            }
        )
        if len(cards) >= limit:
            break
    return cards


def _build_casebook_recommendation(maneuver: dict, similar_cases: list[dict]) -> dict:
    """Summarize similar historical cases into a short operational recommendation."""
    if not similar_cases:
        return {}

    completed = sum(1 for item in similar_cases if item.get("status_class") == "completed")
    aborted = sum(1 for item in similar_cases if item.get("status_class") == "aborted")
    approved_feedback = sum(1 for item in similar_cases if item.get("feedback_status") == "approved")
    avoid_feedback = sum(1 for item in similar_cases if item.get("feedback_status") == "avoid")
    review_feedback = sum(1 for item in similar_cases if item.get("feedback_status") == "review")
    wave_related = sum(
        1
        for item in similar_cases
        if "ondulação relevante" in (item.get("decision_flags") or [])
    )

    tug_counter: dict[str, int] = {}
    for item in similar_cases:
        tug_value = str(item.get("tug_count") or "").strip()
        if tug_value and tug_value != "--":
            tug_counter[tug_value] = tug_counter.get(tug_value, 0) + 1
    dominant_tug_count = ""
    if tug_counter:
        dominant_tug_count = sorted(tug_counter.items(), key=lambda pair: (pair[1], pair[0]), reverse=True)[0][0]

    status_key = "neutral"
    title = "Leitura histórica mista"
    if avoid_feedback > approved_feedback:
        status_key = "caution"
        title = "Feedback validado recomenda prudência"
    elif approved_feedback and avoid_feedback == 0:
        status_key = "positive"
        title = "Feedback validado favorável"
    elif completed and aborted == 0:
        status_key = "positive"
        title = "Histórico favorável"
    elif aborted and completed == 0:
        status_key = "caution"
        title = "Histórico desfavorável"
    elif aborted > completed:
        status_key = "caution"
        title = "Histórico conservador"
    elif completed > aborted:
        status_key = "positive"
        title = "Histórico maioritariamente favorável"

    basis = f"{completed} realizada(s) e {aborted} abortada(s) em {len(similar_cases)} caso(s) semelhante(s)"
    recommendation_parts = []
    if dominant_tug_count:
        recommendation_parts.append(f"rebocadores mais usados: {dominant_tug_count}")
    if wave_related and maneuver.get("type") in {"entry", "departure"}:
        recommendation_parts.append(f"ondulação relevante em {wave_related} caso(s)")
    if approved_feedback:
        recommendation_parts.append(f"feedback positivo validado em {approved_feedback} caso(s)")
    if avoid_feedback:
        recommendation_parts.append(f"feedback a evitar em {avoid_feedback} caso(s)")
    if review_feedback:
        recommendation_parts.append(f"{review_feedback} caso(s) marcado(s) para revisão")

    if avoid_feedback > approved_feedback:
        summary = "Casos semelhantes foram sinalizados para evitar este padrão sem validação reforçada."
    elif approved_feedback and avoid_feedback == 0:
        summary = "Casos semelhantes com feedback validado apoiam esta abordagem, mantendo confirmação humana."
    elif status_key == "caution":
        summary = "Pede validação mais conservadora antes de confirmar esta manobra."
    elif status_key == "positive":
        summary = "O histórico semelhante é globalmente favorável, mantendo validação operacional normal."
    else:
        summary = "O histórico semelhante não aponta para um padrão único; valida pelos fatores do momento."

    return {
        "status_key": status_key,
        "title": title,
        "basis_label": basis,
        "summary": summary,
        "signals_label": " · ".join(recommendation_parts),
    }


def build_maneuver_case_context_source(question: str, port_calls: list[dict]) -> dict | None:
    """Build a compact historical casebook source for the matched scale/maneuver in chat."""
    clean_question = _operational_lookup_key(question)
    if not clean_question:
        return None
    if not re.search(r"\b(manobra|entrada|saida|departure|mudanca|shift|rebocador|rebocadores|thruster|cais|fundeadouro|aprovar|abortar|cancelar|opiniao|opiniao|achar|recomend)\b", clean_question):
        return None

    matched_port_call = _match_port_call_from_question(question, port_calls)
    if not matched_port_call:
        return None

    try:
        resolved_port_call = services.store.get_port_call(matched_port_call["id"])
        scale_context = build_scale_context(resolved_port_call)
    except Exception:
        logger.exception("Falha ao montar contexto de casos para %s.", matched_port_call.get("id"))
        return None

    maneuver_type = ""
    if re.search(r"\b(entrada|entry)\b", clean_question):
        maneuver_type = "entry"
    elif re.search(r"\b(saida|departure)\b", clean_question):
        maneuver_type = "departure"
    elif re.search(r"\b(mudanca|mudança|shift)\b", clean_question):
        maneuver_type = "shift"

    maneuvers = list(scale_context.get("maneuvers") or [])
    if maneuver_type:
        maneuvers = [item for item in maneuvers if item.get("type") == maneuver_type]
    maneuvers.sort(
        key=lambda item: (
            0 if item.get("status_key") in {"pending", "approved"} else 1,
            item.get("planned_label") or "",
        )
    )

    lines = []
    for maneuver in maneuvers[:2]:
        if not maneuver.get("similar_cases"):
            continue
        lines.append(
            f"Casos semelhantes para {maneuver.get('title', 'manobra')} "
            f"de {resolved_port_call.get('vessel_name', 'navio')} ({maneuver.get('origin', '--')} -> {maneuver.get('destination', '--')}):"
        )
        recommendation = maneuver.get("casebook_recommendation") or {}
        if recommendation:
            lines.append(
                f"- recomendação histórica: {recommendation.get('title', '')} | "
                f"{recommendation.get('basis_label', '')} | {recommendation.get('summary', '')}"
            )
            if recommendation.get("signals_label"):
                lines.append(f"  sinais: {recommendation['signals_label']}")
        checklist_summary = maneuver.get("analysis_summary") or {}
        checklist_items = list(maneuver.get("analysis_checklist") or [])
        if checklist_summary:
            lines.append(
                f"- checklist operacional: {checklist_summary.get('headline', 'sem resumo')} | "
                f"alertas {checklist_summary.get('caution_count', 0)} | "
                f"ok {checklist_summary.get('ok_count', 0)}"
            )
        for checklist_item in checklist_items[:3]:
            lines.append(
                f"  checklist [{checklist_item.get('status', 'info')}]: "
                f"{checklist_item.get('title', '')} - {checklist_item.get('detail', '')}"
            )
        for case in maneuver.get("similar_cases", [])[:3]:
            lines.append(
                f"- {case.get('reference_code', '--')} | {case.get('vessel_name', '--')} | "
                f"{case.get('state_label', '--')} | {case.get('route_label', '--')} | "
                f"{case.get('latest_event_label', '--')} | afinidade {case.get('similarity_score', 0)} | "
                f"{case.get('reasons_label', 'perfil semelhante')}"
            )
            if case.get("decision_flags"):
                lines.append(f"  flags: {', '.join(case['decision_flags'])}")
            if case.get("decision_excerpt"):
                lines.append(f"  nota: {case['decision_excerpt']}")
    if not lines:
        return None

    return {
        "source_id": f"CASEBOOK:{resolved_port_call.get('reference_code', '')}",
        "document": "casebook_manobras",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "maneuver_casebook",
        "snippet": "\n".join(lines),
    }


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
    proposal["missing_fields"] = proposal_missing_field_labels(
        proposal.get("action", ""),
        proposal.get("fields") or {},
        proposal.get("target") or {},
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


def answer_slash_query(command: str, argument: str, role: str) -> dict:
    """Answer direct slash-query commands without entering the operational proposal flow."""
    clean_argument = " ".join((argument or "").strip().split())
    if command == "help":
        return {"answer": build_slash_help(role), "sources": [], "answer_origin": "slash_help"}
    if command == "local_warnings":
        if not getattr(services, "local_warning_service", None) or not services.local_warning_service.enabled:
            return {"answer": "Os avisos locais não estão configurados neste ambiente.", "sources": [], "answer_origin": "slash_local_warnings"}
        try:
            return {
                "answer": services.local_warning_service.codes_summary_text(),
                "sources": [],
                "answer_origin": "slash_local_warnings",
            }
        except Exception as exc:
            return {"answer": f"Falha ao obter avisos locais: {exc}", "sources": [], "answer_origin": "slash_local_warnings"}
    if command == "wave":
        if not getattr(services, "wave_service", None) or not services.wave_service.enabled:
            return {"answer": "A leitura costeira não está configurada neste ambiente.", "sources": [], "answer_origin": "slash_wave"}
        try:
            return {
                "answer": services.wave_service.summary_text(),
                "sources": [],
                "answer_origin": "slash_wave",
            }
        except Exception as exc:
            return {"answer": f"Falha ao obter leitura costeira: {exc}", "sources": [], "answer_origin": "slash_wave"}
    if command == "tides":
        dates = services.tide_service.resolve_query_dates(clean_argument or "hoje")
        parts = []
        for target_date in dates[:2]:
            summary = services.tide_service.summary_for_date(target_date)
            parts.append(summary.get("summary", "Sem dados de maré."))
        return {"answer": "\n\n".join(parts), "sources": [], "answer_origin": "slash_tides"}
    if command == "weather":
        if not services.weather_service.enabled:
            return {"answer": "A meteorologia não está configurada neste ambiente.", "sources": [], "answer_origin": "slash_weather"}
        try:
            forecast = services.weather_service.get_forecast(days=3)
        except Exception as exc:
            return {"answer": f"Falha ao obter meteorologia: {exc}", "sources": [], "answer_origin": "slash_weather"}
        question = clean_argument or "hoje"
        context = services.weather_service.context_for_question(question)
        if context:
            return {"answer": context.get("text") or context.get("snippet", "Sem previsão disponível."), "sources": [], "answer_origin": "slash_weather"}
        return {"answer": f"Sem previsão disponível para {question}.", "sources": [], "answer_origin": "slash_weather"}
    if command == "rule":
        code_match = re.search(r"\b(\d{3})\b", clean_argument)
        if not code_match:
            return {
                "answer": build_rule_catalog_text(),
                "sources": [],
                "answer_origin": "slash_rule",
            }
        code = code_match.group(1)
        available_titles = available_rule_code_titles()
        title = available_titles.get(code)
        if not title:
            return {
                "answer": f"Não encontrei a regra {code} neste ambiente.\n\n{build_rule_catalog_text()}",
                "sources": [],
                "answer_origin": "slash_rule",
            }
        if not services.rag.can_generate():
            return {
                "answer": f"Pedido da regra {title} recebido, mas o LLM está indisponível neste ambiente.",
                "sources": [],
                "answer_origin": "slash_rule",
            }
        answer = services.rag.answer(
            question=f"Resume a regra {title} e destaca os pontos operacionais mais importantes.",
            role=role,
            history=[],
            supplemental_sources=[],
            trusted_answers=[],
        )
        answer["answer_origin"] = "slash_rule"
        return answer
    return {"answer": "Comando não suportado.", "sources": [], "answer_origin": "slash_unknown"}


def action_target_port_call(port_call_id: str) -> dict:
    """Fetch a port call and enforce agent scope access, returning the decorated record."""
    port_call = services.store.get_port_call(port_call_id)
    if (session.get("role") or "").strip().lower() == "agente":
        ensure_port_call_scope_access(port_call_id)
    return port_call


def heuristic_operational_proposal(question: str, role: str, port_calls: list[dict]) -> dict | None:
    """Apply deterministic pattern matching to derive an operational action proposal from the question."""
    from domain.chat_actions import _extract_labelled_values

    clean = _operational_lookup_key(question)
    if not clean:
        return None

    extracted = _extract_labelled_values(question)

    explicit_scale_request = bool(
        re.search(r"\b(regist\w*\s+(esta\s+)?escala|nova escala|cria\w*\s+escala|register scale)\b", clean)
    )
    if (
        (explicit_scale_request or looks_like_port_call_payload(question))
        and "manobra" not in clean
        and not looks_like_maneuver_report_payload(question)
        and not looks_like_abort_payload(question)
    ):
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

    extracted_fields = _extract_labelled_values(question)
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
            "fields": extracted_fields, "missing_fields": [],
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
        proposal = refresh_proposal_missing_fields(proposal)
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
    if looks_like_operational_query(question) and not looks_like_operational_command(question):
        return {
            "intent": "question",
            "action": "",
            "confidence": 0.99,
            "reason": "Pergunta consultiva sem pedido explícito de execução operacional.",
            "target": {},
            "fields": {},
            "missing_fields": [],
        }
    if not looks_like_operational_command(question):
        return None
    resolvable_port_calls = current_resolvable_port_calls()
    heuristic_proposal = heuristic_operational_proposal(question, role, resolvable_port_calls)
    if heuristic_proposal:
        return finalize_operational_proposal(heuristic_proposal, resolvable_port_calls)
    if not services.rag.can_generate():
        unavailable_reason = services.rag.generation_unavailable_reason()
        return {
            "intent": "unsupported", "action": "", "confidence": 0.0,
            "reason": f"O bot operador está indisponível: {unavailable_reason}",
            "target": {}, "fields": {}, "missing_fields": [],
        }
    port_calls = current_visible_port_calls()
    prompt = build_operational_action_prompt(
        question=question, role=role, now_local=datetime.now().astimezone(),
        port_calls=port_calls, berth_options=services.BERTH_OPTIONS,
        constraint_options=services.CONSTRAINT_OPTIONS,
    )
    try:
        gen_result = services.rag.generate_text(prompt)
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
    proposal_target = proposal.setdefault("target", {})
    target = None
    existing_port_call_id = (proposal.get("port_call_id") or "").strip()
    if existing_port_call_id:
        try:
            target = services.store.get_port_call(existing_port_call_id)
        except Exception:
            target = None
    visible_port_calls = port_calls if port_calls is not None else current_visible_port_calls()
    if not target:
        target = resolve_port_call(visible_port_calls, proposal_target)
    if (
        not target
        and proposal.get("action") != "create_port_call"
        and proposal_target.get("reference_code")
        and not proposal_target.get("maneuver_id")
    ):
        maneuver_reference = " ".join(str(proposal_target.get("reference_code") or "").split())
        if maneuver_reference:
            maneuver_id_target = {
                **proposal_target,
                "maneuver_id": maneuver_reference,
                "reference_code": "",
            }
            target = resolve_port_call(visible_port_calls, maneuver_id_target)
            if target:
                proposal_target["maneuver_id"] = maneuver_id_target["maneuver_id"]
                proposal_target["reference_code"] = target.get("reference_code", "")
    if (
        not target
        and proposal.get("action") != "create_port_call"
        and proposal_target.get("maneuver_id")
        and not proposal_target.get("reference_code")
        and not proposal_target.get("vessel_name")
    ):
        legacy_reference_target = {
            **proposal_target,
            "reference_code": proposal_target.get("maneuver_id", ""),
            "maneuver_id": "",
        }
        target = resolve_port_call(visible_port_calls, legacy_reference_target)
        if target:
            proposal_target["reference_code"] = target.get("reference_code", "") or legacy_reference_target["reference_code"]
            proposal_target["maneuver_id"] = ""
            proposal["maneuver_id"] = ""
    if not target and proposal.get("action") != "create_port_call":
        resolvable_port_calls = current_resolvable_port_calls()
        target = resolve_port_call(resolvable_port_calls, proposal_target)
        if (
            not target
            and proposal_target.get("reference_code")
            and not proposal_target.get("maneuver_id")
        ):
            maneuver_reference = " ".join(str(proposal_target.get("reference_code") or "").split())
            if maneuver_reference:
                maneuver_id_target = {
                    **proposal_target,
                    "maneuver_id": maneuver_reference,
                    "reference_code": "",
                }
                target = resolve_port_call(resolvable_port_calls, maneuver_id_target)
                if target:
                    proposal_target["maneuver_id"] = maneuver_id_target["maneuver_id"]
                    proposal_target["reference_code"] = target.get("reference_code", "")
        if (
            not target
            and proposal_target.get("maneuver_id")
            and not proposal_target.get("reference_code")
            and not proposal_target.get("vessel_name")
        ):
            legacy_reference_target = {
                **proposal_target,
                "reference_code": proposal_target.get("maneuver_id", ""),
                "maneuver_id": "",
            }
            target = resolve_port_call(resolvable_port_calls, legacy_reference_target)
            if target:
                proposal_target["reference_code"] = target.get("reference_code", "") or legacy_reference_target["reference_code"]
                proposal_target["maneuver_id"] = ""
                proposal["maneuver_id"] = ""

    fields = proposal.setdefault("fields", {})
    if fields.get("docking_depth") and not fields.get("draft_m"):
        fields["draft_m"] = fields.pop("docking_depth")

    if proposal.get("action") == "create_port_call":
        has_scale_context = any(
            " ".join(str(fields.get(key) or "").split())
            for key in (
                "eta_local",
                "berth",
                "last_port",
                "next_port",
                "vessel_imo",
                "vessel_call_sign",
                "vessel_flag",
                "vessel_type",
                "vessel_loa_m",
                "vessel_beam_m",
                "vessel_gt_t",
                "vessel_dwt_t",
                "vessel_max_draft_m",
                "vessel_bow_thruster",
                "vessel_stern_thruster",
            )
        )
        has_report_fields = any(
            " ".join(str(fields.get(key) or "").split())
            for key in ("maneuver_started_local", "maneuver_finished_local", "draft_m")
        )
        if has_scale_context or not has_report_fields:
            proposal["port_call_id"] = ""
            proposal["target"]["reference_code"] = ""
            proposal["target"]["maneuver_type"] = ""
            return refresh_proposal_missing_fields(proposal)

    if proposal.get("action") != "create_port_call" and not target:
        proposal["intent"] = "unsupported"
        proposal["action"] = ""
        proposal["reason"] = "Não consegui identificar uma escala correspondente para executar a ação. Usa a Ref da escala, o nome do navio ou o ID da manobra."
        return proposal

    if target:
        proposal["port_call_id"] = target.get("id", "")
        proposal["target"]["reference_code"] = target.get("reference_code", "")
        proposal["target"]["vessel_name"] = target.get("vessel_name", "")

    if target and proposal.get("action") == "create_port_call":
        report_like_fields = any(
            " ".join(str(fields.get(key) or "").split())
            for key in ("maneuver_started_local", "maneuver_finished_local", "draft_m")
        )
        if report_like_fields:
            resolved_target = services.store.get_port_call(target["id"])
            inferred_existing_type = (
                (proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
                or infer_maneuver_type(resolved_target, "entry_report")
                or "entry"
            )
            if inferred_existing_type in {"entry", "departure", "shift"}:
                proposal["action"] = f"{inferred_existing_type}_report"
                proposal["target"]["maneuver_type"] = inferred_existing_type

    target_maneuver_id = " ".join(str((proposal.get("target", {}) or {}).get("maneuver_id") or proposal.get("maneuver_id") or "").split())
    maneuver_type = (proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
    resolved_port_call = services.store.get_port_call(target["id"]) if target else None
    resolved_maneuver = (
        resolve_maneuver(
            resolved_port_call or {},
            proposal.get("action", ""),
            maneuver_type,
            target_maneuver_id,
        )
        if resolved_port_call and target_maneuver_id
        else None
    )
    if resolved_maneuver:
        proposal["maneuver_id"] = resolved_maneuver.get("id", "")
        proposal["target"]["maneuver_id"] = resolved_maneuver.get("id", "")
        if resolved_maneuver.get("type") in {"entry", "departure", "shift"}:
            proposal["target"]["maneuver_type"] = resolved_maneuver.get("type", "")
    maneuver_type = (proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
    inferred_type = (
        resolved_maneuver.get("type", "")
        if resolved_maneuver
        else infer_maneuver_type(resolved_port_call or {}, proposal.get("action", "")) if resolved_port_call else ""
    )
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

    maneuver_type = (proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
    if (
        target
        and action_prefers_explicit_maneuver_id(proposal.get("action", ""))
        and not ((proposal.get("maneuver_id") or proposal.get("target", {}).get("maneuver_id", "")).strip())
        and maneuver_type in {"entry", "departure", "shift"}
    ):
        matching_maneuvers = candidate_maneuvers_for_action(
            services.store.get_port_call(target["id"]),
            proposal["action"],
            maneuver_type,
        )
        if len(matching_maneuvers) > 1:
            proposal["missing_fields"] = proposal_missing_field_labels(
                proposal.get("action", ""),
                proposal.get("fields") or {},
                {**(proposal.get("target") or {}), "maneuver_id": ""},
            )
            if "ID da manobra" not in proposal["missing_fields"]:
                proposal["missing_fields"].append("ID da manobra")
            return proposal

    if target and proposal.get("action") in {"edit_maneuver_plan", "edit_maneuver_report", "delete_maneuver", "delete_maneuver_report"}:
        maneuver = resolve_maneuver(
            services.store.get_port_call(target["id"]),
            proposal["action"],
            proposal["target"].get("maneuver_type", ""),
            proposal.get("maneuver_id") or proposal.get("target", {}).get("maneuver_id", ""),
        )
        if not maneuver:
            proposal["intent"] = "unsupported"
            proposal["action"] = ""
            proposal["reason"] = "Não encontrei a manobra certa para editar nesta escala."
            return proposal
        proposal["maneuver_id"] = maneuver.get("id", "")
        proposal["target"]["maneuver_id"] = maneuver.get("id", "")
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
    target_updates = extract_pending_target_updates(question)
    if direct_updates or target_updates:
        updates = normalize_action_candidate(
            {
                "intent": "action",
                "action": pending_proposal.get("action", ""),
                "confidence": pending_proposal.get("confidence", 0.0),
                "reason": pending_proposal.get("reason", ""),
                "target": {
                    **(pending_proposal.get("target", {}) or {}),
                    **target_updates,
                },
                "fields": direct_updates, "missing_fields": [],
            },
            role,
        )
        merged = merge_action_candidate(pending_proposal, updates or {}, role)
        return {"intent": "update", "proposal": finalize_operational_proposal(merged)}

    if not services.rag.can_generate():
        unavailable_reason = services.rag.generation_unavailable_reason()
        return {
            "intent": "unsupported",
            "reason": f"O bot operador está indisponível: {unavailable_reason}",
        }

    prompt = build_pending_action_update_prompt(
        question=question, role=role, proposal=pending_proposal,
        berth_options=services.BERTH_OPTIONS, constraint_options=services.CONSTRAINT_OPTIONS,
    )
    try:
        gen_result = services.rag.generate_text(prompt)
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

    def resolve_target_maneuver(current_port_call: dict, current_action: str, current_maneuver_type: str) -> dict | None:
        explicit_maneuver_id = (proposal.get("maneuver_id") or target.get("maneuver_id", "")).strip()
        if explicit_maneuver_id:
            return resolve_maneuver(current_port_call, current_action, current_maneuver_type, explicit_maneuver_id)
        candidates = candidate_maneuvers_for_action(current_port_call, current_action, current_maneuver_type)
        if action_prefers_explicit_maneuver_id(current_action) and len(candidates) > 1:
            raise ValueError("Há várias manobras deste tipo nesta escala. Indica o ID da manobra para evitar alterar a errada.")
        return candidates[-1] if candidates else None

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
        origin_value = field_text("origin", current_maneuver.get("origin") or current_port_call.get("last_port", ""))
        destination_value = destination
        if current_maneuver_type == "entry":
            destination_value = normalize_portal_berth(destination_value, "Destino")
        elif current_maneuver_type in {"departure", "shift"}:
            origin_value = normalize_portal_berth(origin_value, "Origem")
            if current_maneuver_type == "shift":
                destination_value = normalize_portal_berth(destination_value, "Destino")
                if destination_value == origin_value:
                    raise ValueError("O cais destino tem de ser diferente do local atual do navio.")
        services.store.edit_maneuver_plan(
            port_call_id=port_call_id, maneuver_id=current_maneuver.get("id", ""),
            updated_by=username, actor_role=role,
            planned_at=parse_local_datetime_input(planned_at_value or current_maneuver.get("planned_input_value") or current_maneuver.get("planned_at") or ""),
            origin=require_form_text(origin_value, "Origem"),
            destination=require_form_text(destination_value, "Destino"),
            draft_m=field_text("draft_m", current_maneuver.get("planned_draft_m", "")),
            tug_count=field_text("tug_count", current_maneuver.get("tug_count", "")),
            constraints=normalize_constraint_codes(fields.get("constraints") or current_maneuver.get("constraints", [])),
            plan_note=field_text("plan_observations", field_text("notes", current_maneuver.get("plan_observations", ""))),
            change_reason=require_form_text(field_text("change_reason", field_text("reason")), "Motivo da alteração"),
        )

    if action == "create_port_call":
        eta = parse_local_datetime_input(field_text("eta_local"), "ETA")
        validate_not_past_datetime(eta, "ETA")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        draft_m = field_text("draft_m")
        tug_count = field_text("tug_count")
        port_call = services.store.create_port_call(
            vessel_name=field_text("vessel_name"), eta=eta, created_by=username,
            constraints=constraints,
            berth=normalize_portal_berth(field_text("berth"), "Cais previsto"),
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
            vessel_bow_thruster=normalize_thruster_state(fields.get("vessel_bow_thruster"), "Bow thruster"),
            vessel_stern_thruster=normalize_thruster_state(fields.get("vessel_stern_thruster"), "Stern thruster"),
            notes=build_entry_request_note({"draft_m": draft_m, "tug_count": tug_count, "constraints": constraints, "notes": fields.get("notes", "")}),
        )
        return port_call, _build_created_port_call_message(port_call)
    if action == "edit_port_call":
        current_port_call = apply_scope(port_call_id) if port_call_id else None
        parsed_eta = None
        if field_text("eta_local"):
            parsed_eta = parse_optional_local_datetime_input(field_text("eta_local"), "ETA")
            if (current_port_call or {}).get("status") == "scheduled":
                validate_not_past_datetime(parsed_eta, "ETA")
        berth_value = fields.get("berth")
        normalized_berth = None
        if berth_value not in {None, ""}:
            if (current_port_call or {}).get("status") == "in_port":
                normalized_berth = ensure_portal_berth_is_available(berth_value, current_port_call_id=port_call_id, label="Cais")
            else:
                normalized_berth = normalize_portal_berth(berth_value, "Cais")
        result = services.store.edit_port_call(
            port_call_id=port_call_id,
            updated_by=username,
            vessel_name=fields.get("vessel_name"),
            eta=parsed_eta,
            berth=normalized_berth,
            last_port=fields.get("last_port"),
            next_port=fields.get("next_port"),
            notes=fields.get("notes"),
            constraints=normalize_constraint_codes(fields.get("constraints")) if "constraints" in fields else None,
            vessel_short_name=fields.get("vessel_short_name"),
            vessel_imo=fields.get("vessel_imo"),
            vessel_call_sign=fields.get("vessel_call_sign"),
            vessel_flag=fields.get("vessel_flag"),
            vessel_type=fields.get("vessel_type"),
            vessel_loa_m=fields.get("vessel_loa_m"),
            vessel_beam_m=fields.get("vessel_beam_m"),
            vessel_gt_t=fields.get("vessel_gt_t"),
            vessel_max_draft_m=fields.get("vessel_max_draft_m"),
            vessel_dwt_t=fields.get("vessel_dwt_t"),
            vessel_bow_thruster=(
                normalize_thruster_state(fields.get("vessel_bow_thruster"), "Bow thruster")
                if "vessel_bow_thruster" in fields else None
            ),
            vessel_stern_thruster=(
                normalize_thruster_state(fields.get("vessel_stern_thruster"), "Stern thruster")
                if "vessel_stern_thruster" in fields else None
            ),
        )
        return result, f"Escala atualizada para {result['vessel_name']}."
    if action == "delete_port_call":
        removed = services.store.delete_port_call(port_call_id)
        return removed, f"Escala apagada: {removed['reference_code']} · {removed['vessel_name']}."

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
        target_maneuver = resolve_target_maneuver(port_call, action, "entry")
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
        target_berth = field_text("berth", port_call.get("berth"))
        berth_for_arrival = ensure_portal_berth_is_available(target_berth, current_port_call_id=port_call_id, label="Cais")
        result = services.store.mark_port_call_arrived(port_call_id=port_call_id, arrived_at=parse_optional_local_datetime_input(arrived_at_value, "ATA") or datetime.now().astimezone().isoformat(), updated_by=username, berth=berth_for_arrival)
        return result, f"Entrada confirmada para {result['vessel_name']} às {result['ata_label']}. Já podes preencher o registo operacional."
    if action == "entry_report":
        target_maneuver = resolve_target_maneuver(port_call, action, "entry")
        if not target_maneuver:
            raise ValueError("A proposta não identifica a manobra a registar.")
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": fields.get("notes", "")}, "Entrada")
        result = services.store.attach_entry_report(port_call_id=port_call_id, updated_by=username, maneuver_started_at=started_at, maneuver_finished_at=finished_at, draft_m=draft_m, notes=note, maneuver_id=target_maneuver.get("id"))
        return result, f"Registo de entrada guardado para {result['vessel_name']}."
    if action == "schedule_departure":
        planned_departure_at = parse_local_datetime_input(field_text("planned_departure_at_local"), "Hora prevista de saída")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        result = services.store.schedule_departure_plan(
            port_call_id=port_call_id,
            planned_departure_at=planned_departure_at,
            updated_by=username,
            next_port=require_form_text(field_text("next_port", port_call.get("next_port", "")), "Próximo destino"),
            constraints=constraints,
            departure_plan_note=build_departure_plan_note(
                {
                    "origin_berth": port_call.get("berth", ""),
                    "draft_m": field_text("draft_m"),
                    "tug_count": field_text("tug_count"),
                    "constraints": constraints,
                    "notes": field_text("notes"),
                }
            ),
        )
        return result, f"Saída planeada para {result['vessel_name']} às {result['planned_departure_label']}."
    if action == "approve_departure":
        apply_plan_updates_before_approval(port_call, "departure")
        result = services.store.approve_port_call(port_call_id=port_call_id, decided_by=username, approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))))
        return result, f"Saída aprovada para {result['vessel_name']}."
    if action == "abort_departure":
        target_m = resolve_target_maneuver(port_call, action, "departure")
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
        target_maneuver = resolve_target_maneuver(port_call, action, "departure")
        if not target_maneuver:
            raise ValueError("A proposta não identifica a manobra a registar.")
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": fields.get("notes", "")}, "Saída")
        result = services.store.attach_departure_report(port_call_id=port_call_id, updated_by=username, maneuver_started_at=started_at, maneuver_finished_at=finished_at, draft_m=draft_m, notes=note, maneuver_id=target_maneuver.get("id"))
        return result, f"Registo de saída guardado para {result['vessel_name']}."
    if action == "schedule_shift":
        planned_shift_at = parse_local_datetime_input(field_text("planned_shift_at_local"), "Hora prevista da mudança")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        origin_berth = normalize_portal_berth(field_text("origin_berth", port_call.get("berth", "")), "Cais origem")
        destination_berth = normalize_portal_berth(field_text("destination_berth"), "Cais destino")
        if destination_berth == origin_berth:
            raise ValueError("O cais destino tem de ser diferente do local atual do navio.")
        result = services.store.schedule_shift_plan(port_call_id=port_call_id, planned_shift_at=planned_shift_at, updated_by=username, destination_berth=destination_berth, constraints=constraints, shift_plan_note=build_shift_plan_note({"origin_berth": origin_berth, "destination_berth": destination_berth, "draft_m": field_text("draft_m"), "tug_count": field_text("tug_count"), "constraints": constraints, "notes": field_text("notes")}))
        return result, f"Mudança planeada para {result['vessel_name']} às {result['planned_shift_label']}."
    if action == "approve_shift":
        apply_plan_updates_before_approval(port_call, "shift")
        result = services.store.approve_shift_plan(port_call_id=port_call_id, decided_by=username, approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))))
        return result, f"Mudança aprovada para {result['vessel_name']}."
    if action == "abort_shift":
        target_ms = resolve_target_maneuver(port_call, action, "shift")
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
        shift_destination = normalize_portal_berth(
            field_text("destination_berth", port_call.get("shift_destination_berth", "") or port_call.get("berth", "")),
            "Cais destino",
        )
        ensure_portal_berth_is_available(shift_destination, current_port_call_id=port_call_id, label="Cais destino")
        result = services.store.mark_shift_completed(port_call_id=port_call_id, shifted_at=parse_optional_local_datetime_input(shifted_at_value, "Hora da mudança") or datetime.now().astimezone().isoformat(), updated_by=username)
        return result, f"Mudança concluída para {result['vessel_name']} às {result['shift_label']}. Já podes preencher o registo operacional."
    if action == "shift_report":
        target_maneuver = resolve_target_maneuver(port_call, action, "shift")
        if not target_maneuver:
            raise ValueError("A proposta não identifica a manobra a registar.")
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": fields.get("notes", "")}, "Mudança")
        result = services.store.attach_shift_report(port_call_id=port_call_id, updated_by=username, maneuver_started_at=started_at, maneuver_finished_at=finished_at, draft_m=draft_m, notes=note, maneuver_id=target_maneuver.get("id"))
        return result, f"Registo de mudança guardado para {result['vessel_name']}."
    if action == "edit_maneuver_plan":
        current_port_call = services.store.get_port_call(port_call_id)
        maneuver = resolve_target_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "")
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
        current_port_call = services.store.get_port_call(port_call_id)
        maneuver = resolve_target_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "")
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
    if action == "delete_maneuver":
        current_port_call = services.store.get_port_call(port_call_id)
        maneuver = resolve_target_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "")
        if not maneuver_id:
            raise ValueError("A proposta não identifica a manobra a apagar.")
        removed_or_updated = services.store.delete_maneuver(
            port_call_id=port_call_id,
            maneuver_id=maneuver_id,
            updated_by=username,
        )
        return removed_or_updated, f"Manobra apagada para {removed_or_updated['vessel_name']}."
    if action == "delete_maneuver_report":
        current_port_call = services.store.get_port_call(port_call_id)
        maneuver = resolve_target_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "")
        if not maneuver_id:
            raise ValueError("A proposta não identifica o registo a apagar.")
        result = services.store.delete_maneuver_report(
            port_call_id=port_call_id,
            maneuver_id=maneuver_id,
            updated_by=username,
        )
        return result, f"Registo da manobra removido para {result['vessel_name']}."

    raise ValueError("Ação operacional não suportada.")


# ---------------------------------------------------------------------------
# Scale context builder (for port_call_detail page)
# ---------------------------------------------------------------------------

def build_scale_context(port_call: dict) -> dict:
    """Build the rich context dict for the port call detail page including maneuvers and actions."""
    current_role = (session.get("role") or "").strip().lower()
    casebook_enabled = hasattr(services.store, "find_similar_maneuver_cases")

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
            if item.get("type") == maneuver_type and item.get("state") in {"approved", "completed"}
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
        similar_cases = _build_similar_case_cards(port_call, item, limit=3) if casebook_enabled else []
        casebook_recommendation = _build_casebook_recommendation(item, similar_cases)
        analysis_checklist, analysis_summary = _build_maneuver_analysis_checklist(
            port_call,
            item,
            similar_cases=similar_cases,
            casebook_recommendation=casebook_recommendation,
        )
        maneuvers.append({
            "id": item.get("id"), "type": item.get("type"),
            "status_key": item.get("state", ""),
            "title": item.get("type_label", item.get("type", "")),
            "status": item.get("state_label", item.get("state", "")),
            "status_class": (
                "completed" if item.get("state") == "completed"
                else "approved" if item.get("state") == "approved"
                else "aborted" if item.get("state") == "aborted"
                else "pending"
            ),
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
            "report_completed": bool((item.get("report_note") or "").strip()),
            "similar_cases": similar_cases,
            "casebook_recommendation": casebook_recommendation,
            "analysis_checklist": analysis_checklist,
            "analysis_summary": analysis_summary,
            "can_edit_plan": (
                (current_role == "admin")
                or (item.get("state") != "completed" and current_role == "piloto")
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
        "bow_thruster": port_call["ship_bow_thruster_label"],
        "stern_thruster": port_call["ship_stern_thruster_label"],
    }
    actions = {
        "can_approve_entry": port_call.get("status") == "scheduled" and port_call.get("approval_status") == "pending",
        "can_abort_entry": port_call.get("status") == "scheduled" and port_call.get("approval_status") != "aborted" and port_call.get("can_abort"),
        "can_complete_entry": False,
        "can_plan_departure": port_call.get("status") == "in_port" and not active_departure and not completed_departure,
        "can_approve_departure": port_call.get("status") == "in_port" and bool(active_departure) and active_departure.get("state") == "pending",
        "can_abort_departure": port_call.get("status") == "in_port" and bool(active_departure) and active_departure.get("state") in {"pending", "approved"},
        "can_complete_departure": False,
        "can_register_entry": bool(reportable_entry),
        "can_register_departure": bool(reportable_departure),
        "can_plan_shift": port_call.get("status") == "in_port" and not active_shift,
        "can_approve_shift": port_call.get("status") == "in_port" and bool(active_shift) and active_shift.get("state") == "pending",
        "can_abort_shift": port_call.get("status") == "in_port" and bool(active_shift) and active_shift.get("state") in {"pending", "approved"},
        "can_complete_shift": False,
        "can_register_shift": bool(reportable_shift),
        "must_report_label": (
            "Registar entrada" if bool(reportable_entry)
            else "Registar saída" if bool(reportable_departure)
            else "Registar mudança" if bool(reportable_shift)
            else ""
        ),
        "entry_report_exists": entry_report_exists,
        "departure_report_exists": departure_report_exists,
        "shift_report_exists": shift_report_exists,
    }
    return {
        "ship_profile": ship_profile,
        "summary": summary,
        "maneuvers": maneuvers,
        "has_casebook_support": any(item.get("similar_cases") for item in maneuvers),
        "change_log_rows": sorted(change_log_rows, key=lambda item: item.get("changed_at") or "", reverse=True),
        "actions": actions,
    }


def build_maneuver_context(port_call: dict, maneuver_id: str) -> dict:
    """Build a dedicated maneuver detail context from a port call and maneuver id."""
    scale = build_scale_context(port_call)
    maneuver = next((item for item in scale["maneuvers"] if item.get("id") == maneuver_id), None)
    if not maneuver:
        raise ValueError("Manobra não encontrada.")
    case_record = services.store.get_maneuver_case(maneuver_id) if hasattr(services.store, "get_maneuver_case") else None

    state_key = (maneuver.get("status_key") or "").strip().lower()
    plan_status = "done" if maneuver.get("planned_label") and maneuver.get("planned_label") != "Sem hora" else "current"
    if state_key == "aborted":
        validation_status = "muted"
        report_status = "muted"
    else:
        validation_status = "done" if state_key in {"approved", "completed"} else "current" if state_key == "pending" else "muted"
        report_status = "done" if maneuver.get("report_completed") else "current" if state_key in {"approved", "completed"} else "muted"
    report_detail = (
        maneuver.get("execution_finished_label")
        if maneuver.get("report_completed")
        else "Registo do piloto em falta"
        if state_key in {"approved", "completed"}
        else "Manobra abortada"
        if state_key == "aborted"
        else "Aguarda validação"
    )
    validation_detail = (
        "Manobra abortada"
        if state_key == "aborted"
        else maneuver.get("validated_by_profile", {}).get("label")
        or "--"
        if state_key in {"approved", "completed"}
        else "Aguarda confirmação"
    )
    timeline = [
        {"label": "Planeamento", "status": plan_status, "detail": maneuver.get("planned_label") or "Sem hora"},
        {"label": "Validação", "status": validation_status, "detail": validation_detail},
        {"label": "Registo do piloto", "status": report_status, "detail": report_detail},
    ]
    return {
        "scale": scale,
        "maneuver": maneuver,
        "case_record": case_record or {},
        "similar_cases": maneuver.get("similar_cases", []),
        "timeline": timeline,
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
        dt = None
        relaxed = clean.replace(", ", " ").replace(",", " ")
        for fmt in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(relaxed, fmt)
                break
            except ValueError:
                continue
        if dt is None:
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


def normalize_portal_berth(value: str, label: str = "Cais") -> str:
    """Resolve a berth label against the canonical Setubal berth catalog."""
    clean = require_form_text(value, label)
    canonical = canonicalize_berth_label(clean, berth_options=services.BERTH_OPTIONS)
    if not is_known_berth_label(canonical, berth_options=services.BERTH_OPTIONS):
        raise ValueError(f"{label} inválido. Usa um dos cais/fundeadouros conhecidos do porto.")
    return canonical


def occupied_portal_berth_conflict(berth: str, *, current_port_call_id: str = "") -> dict | None:
    """Return the conflicting in-port vessel occupying a quay berth, ignoring anchorages."""
    port_activity = services.store.get_port_activity_snapshot(window_days=3650)
    return find_occupied_berth_conflict(
        berth,
        port_activity.get("in_port", []) or [],
        current_port_call_id=current_port_call_id,
        berth_options=services.BERTH_OPTIONS,
    )


def ensure_portal_berth_is_available(berth: str, *, current_port_call_id: str = "", label: str = "Cais") -> str:
    """Validate a canonical berth and raise when the quay is already occupied by another in-port vessel."""
    canonical = normalize_portal_berth(berth, label=label)
    conflict = occupied_portal_berth_conflict(canonical, current_port_call_id=current_port_call_id)
    if conflict:
        conflict_name = conflict.get("vessel_name") or conflict.get("reference_code") or "outro navio"
        raise ValueError(f"{label} {canonical} já está ocupado por {conflict_name}.")
    return canonical


def format_note_datetime(value: str) -> str:
    """Format an ISO datetime string as a local dd/mm/yyyy HH:MM label for display in notes."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value


def _build_created_port_call_message(port_call: dict) -> str:
    """Return the success message shown after creating a scale from chat."""
    eta_value = port_call.get("eta") or ""
    eta_label = format_note_datetime(eta_value) or port_call.get("eta_label") or "--"
    message = f"Escala criada para {port_call['vessel_name']} com ETA {eta_label}."
    if not eta_value:
        return message
    try:
        eta_dt = datetime.fromisoformat(eta_value)
    except ValueError:
        return message
    if eta_dt.tzinfo is None:
        eta_dt = eta_dt.replace(tzinfo=timezone.utc)
    if eta_dt < datetime.now(eta_dt.tzinfo) - timedelta(days=5):
        message += " Atenção: o ETA ficou no passado e esta escala pode não aparecer na vista operacional atual."
    return message


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
        ("Origem", form_data.get("origin_berth", "")),
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
