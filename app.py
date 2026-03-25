import csv
import json
import logging
import math
import os
import re
import threading
import unicodedata
from io import StringIO
from datetime import datetime, timezone
from functools import wraps

from ais_service import create_ais_service
from auth_service import create_auth_service
from chat_actions import (
    action_for_maneuver_type,
    build_operational_action_prompt,
    build_pending_action_update_prompt,
    display_missing_field_labels,
    extract_pending_field_updates,
    extract_json_object,
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
    ManoeuvreInput,
    ManoeuvreType,
    SurchargeType,
    ReductionType,
    calculate_scale_cost,
    format_cost_summary,
    quick_estimate,
)
from dotenv import load_dotenv
from migration_service import get_database_runtime_status, migrate_local_json_to_postgres
from reindex_scheduler import DeferredTaskScheduler, next_gemini_quota_reset_utc
from werkzeug.exceptions import RequestEntityTooLarge
from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for

from rag_engine import SimpleRAGEngine
from storage import (
    PASSWORD_HASH_METHOD,
    create_store,
    format_constraint_labels,
    get_constraint_options,
    get_vessel_type_options,
    is_user_profile_complete,
    normalize_constraint_codes,
)
from tide_service import TideService
from vector_store import create_index_store
from weather_service import WeatherService


load_dotenv()
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")
BERTH_OPTIONS = [
    "Secil W",
    "Secil E",
    "Fundeadouro Norte",
    "Cais Palmeiras",
    "TMS 1 - Cais 3",
    "TMS 1 - Cais 4",
    "TMS 1 - Cais 5",
    "TMS 1 - Cais 6",
    "TMS 1 - Cais 7",
    "TMS 1 - Cais 8",
    "TMS 2",
    "Cais 10 / Autoeuropa",
    "Cais 11 / Autoeuropa",
    "Praias do Sado / Pirites Alentejanas",
    "SAPEC Sólidos",
    "SAPEC Líquidos",
    "ALSTOM",
    "PAN Tróia",
    "Fundeadouro Sul / Tróia",
    "Tanquisado (lado jusante)",
    "Eco-Oil (lado montante)",
    "Lisnave - Cais 0 B",
    "Lisnave - Cais 0 A",
    "Lisnave - Doca 20",
    "Lisnave - Doca 21",
    "Lisnave - Doca 22",
    "Lisnave - Cais 1 B",
    "Lisnave - Cais 1 A",
    "Lisnave - Cais 2 B",
    "Lisnave - Cais 2 A",
    "Lisnave - Cais 3 B",
    "Lisnave - Cais 3 A",
    "Lisnave - Doca 31",
    "Lisnave - Doca 32",
    "Lisnave - Doca 33",
    "Teporset",
]
TERMINAL_OPTIONS = [
    "Secil",
    "Fundeadouro Norte",
    "Cais Palmeiras",
    "TMS 1",
    "TMS 2",
    "Autoeuropa",
    "Praias do Sado / Pirites Alentejanas",
    "SAPEC Sólidos",
    "SAPEC Líquidos",
    "ALSTOM",
    "PAN Tróia",
    "Fundeadouro Sul / Tróia",
    "Tanquisado",
    "Eco-Oil",
    "Lisnave",
    "Teporset",
]
VESSEL_TYPE_OPTIONS = get_vessel_type_options()
CONSTRAINT_OPTIONS = get_constraint_options()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "64")) * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV", "production") == "production"
app.config["PERMANENT_SESSION_LIFETIME"] = 28800  # 8 hours

store = create_store(data_dir=DATA_DIR, knowledge_dir=KNOWLEDGE_DIR)
auth_service = create_auth_service(store)
index_store = create_index_store(data_dir=DATA_DIR)

# LLM provider: supports Gemini, OpenRouter, OpenAI, DeepSeek
# Configured via LLM_PROVIDER env var (default: auto-detect from available keys)
from llm_provider import create_llm_provider, create_embedding_provider

_llm_provider_name = os.getenv("LLM_PROVIDER", "").strip().lower()
if not _llm_provider_name:
    # Auto-detect: prefer OpenRouter if key exists, fallback to Gemini
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        _llm_provider_name = "openrouter"
    elif os.getenv("GEMINI_API_KEY", "").strip():
        _llm_provider_name = "gemini"
    else:
        _llm_provider_name = "gemini"

_llm_api_key = (
    os.getenv("OPENROUTER_API_KEY", "").strip()
    if _llm_provider_name == "openrouter"
    else os.getenv("GEMINI_API_KEY", "").strip()
)
_llm_provider = create_llm_provider(provider=_llm_provider_name, api_key=_llm_api_key)

# Local embedding provider (sentence-transformers — zero API cost, no limits)
_embedding_provider = create_embedding_provider()
if _embedding_provider:
    app.logger.info("Embeddings locais activos: %s (%d dim)",
                    _embedding_provider.model_name, _embedding_provider.dimensions)
else:
    app.logger.warning(
        "Embeddings locais indisponíveis (sentence-transformers não instalado). "
        "Os embeddings serão feitos via API, consumindo quota."
    )

# Model defaults per provider
_default_gen_models = {
    "gemini": "gemini-2.5-flash",
    "openrouter": "openrouter/free",
}
_default_emb_models = {
    "gemini": "gemini-embedding-001",
    "openrouter": "baai/bge-m3",
}
_gen_model = os.getenv("LLM_MODEL", _default_gen_models.get(_llm_provider_name, "openrouter/free"))
_emb_model = os.getenv("EMBEDDING_MODEL", _default_emb_models.get(_llm_provider_name, "baai/bge-m3"))

rag = SimpleRAGEngine(
    api_key=_llm_api_key,
    knowledge_dir=KNOWLEDGE_DIR,
    index_store=index_store,
    generation_model=_gen_model,
    embedding_model=_emb_model,
    llm_provider=_llm_provider,
    embedding_provider=_embedding_provider,
)
tide_service = TideService(
    csv_path=os.path.join(KNOWLEDGE_DIR, "mares.2026.201.9_setubal_troia.csv")
)
weather_service = WeatherService(
    api_key=os.getenv("WEATHERAPI_KEY", ""),
    location=os.getenv("WEATHERAPI_LOCATION", "Setubal"),
    language="pt",
)
ais_service = create_ais_service(BASE_DIR)
startup_migration_status = None
reindex_thread = None
reindex_thread_lock = threading.Lock()
reindex_retry_scheduler = None


def maybe_run_startup_migration() -> None:
    global startup_migration_status
    if getattr(store, "backend_name", "") != "postgres":
        startup_migration_status = {
            "status": "not_applicable",
            "reason": "backend principal não é postgres",
        }
        return
    if os.getenv("MIGRATE_LOCAL_DATA_ON_START", "1") != "1":
        startup_migration_status = {
            "status": "disabled",
            "reason": "MIGRATE_LOCAL_DATA_ON_START=0",
        }
        return
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        startup_migration_status = {
            "status": "disabled",
            "reason": "DATABASE_URL em falta",
        }
        return
    try:
        startup_migration_status = migrate_local_json_to_postgres(
            data_dir=DATA_DIR,
            knowledge_dir=KNOWLEDGE_DIR,
            database_url=database_url,
            force=False,
        )
    except Exception as exc:
        startup_migration_status = {
            "status": "error",
            "reason": str(exc),
        }
        app.logger.exception("Falha na migração automática inicial")


def refresh_knowledge_state(force_reindex: bool = False, rebuild_index: bool = True) -> bool:
    try:
        store.list_documents()
    except Exception as exc:
        app.logger.exception("Falha ao sincronizar a pasta knowledge")
        rag.last_index_error = str(exc)
        return False
    if not rebuild_index:
        return True
    try:
        if not force_reindex:
            if rag.has_active_reindex_worker():
                return True
            if rag.has_pending_reindex():
                return start_reindex_job(force=False)
            sync_reindex_retry_schedule()
            return True
    except Exception as exc:
        app.logger.exception("Falha ao avaliar o estado do índice documental")
        rag.last_index_error = str(exc)
        return False
    return safe_rebuild_index(force=force_reindex)


def ensure_session_user_profile() -> bool:
    username = session.get("username", "").strip().lower()
    if not username:
        return False

    profile = store.get_user_profile(username)
    if profile:
        session_role = (session.get("role") or "").strip().lower()
        profile_role = (profile.get("role") or "").strip().lower()
        if session_role in {"admin", "agente", "piloto"} and session_role != profile_role:
            if session_role == "admin":
                profile = store.set_user_role(username, "admin")
            else:
                session["role"] = profile_role
                return True
        session["role"] = profile.get("role", session_role or "piloto")
        return True

    session.clear()
    return False


def current_user_profile() -> dict | None:
    username = session.get("username", "").strip().lower()
    if not username:
        return None
    return store.get_user_profile(username)


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


def filter_port_activity_for_session(port_activity: dict) -> dict:
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

    # Sort berths by geographic order (Secil → Teporset) matching BERTH_OPTIONS
    _berth_order = {name: idx for idx, name in enumerate(BERTH_OPTIONS)}

    def _berth_sort_key(pair):
        berth_name = pair[0]
        # Try exact match first, then partial match for parent terminal
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
            {
                "date_key": date_key,
                "date_label": item.get("date_label", ""),
                "total": 0,
            },
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


def ensure_port_call_scope_access(port_call_id: str) -> None:
    scope_key = _current_agent_scope_key()
    if scope_key is None:
        return
    if not scope_key:
        raise PermissionError("O perfil do agente tem de ter uma agência definida.")
    port_call = store.get_port_call(port_call_id)
    if _item_organization_scope_key(port_call) != scope_key:
        raise PermissionError("Esta escala pertence a outra agência.")


def session_profile_incomplete() -> bool:
    profile = current_user_profile()
    if not profile:
        return False
    if (profile.get("role") or session.get("role") or "").strip().lower() == "admin":
        return False
    return not is_user_profile_complete(profile)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login"))
        if not ensure_session_user_profile():
            flash("Sessao expirada. Inicia sessao novamente.", "error")
            return redirect(url_for("login"))
        if (
            session_profile_incomplete()
            and request.endpoint not in {"profile", "logout", "static", "image_asset"}
        ):
            return redirect(url_for("profile", next=request.full_path if request.query_string else request.path))
        return view(*args, **kwargs)

    return wrapped


@app.after_request
def apply_security_headers(response):
    # Cache control for authenticated pages
    if session.get("username") or request.endpoint in {"login", "profile"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if os.getenv("FLASK_ENV", "production") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                flash("Nao tens permissao para esta acao.", "error")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def redirect_to_portal_target(port_call_id: str):
    target = request.form.get("redirect_to", "").strip().lower()
    if target == "scale":
        return redirect(url_for("port_call_detail", port_call_id=port_call_id))
    if target == "register":
        return redirect(url_for("port_call_register"))
    return redirect(url_for("dashboard"))


def port_call_scope_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        port_call_id = kwargs.get("port_call_id")
        if not port_call_id:
            return view(*args, **kwargs)
        try:
            ensure_port_call_scope_access(port_call_id)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard")) if request.method == "GET" else redirect_to_portal_target(port_call_id)
        except PermissionError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard")) if request.method == "GET" else redirect_to_portal_target(port_call_id)
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_globals():
    chatbot_conversation = None
    chatbot_messages = []
    chatbot_conversations = []
    username = session.get("username")
    if username:
        try:
            requested_conv_id = request.args.get("conversation_id", "").strip() or None
            chatbot_conversation = store.ensure_conversation(username=username, conversation_id=requested_conv_id)
            chatbot_messages = store.list_messages(username, chatbot_conversation["id"])
            chatbot_conversations = store.list_conversations(username)
        except Exception:
            pass
    return {
        "current_user": username,
        "current_role": session.get("role"),
        "provider": rag.provider_name.title(),
        "auth_backend": getattr(auth_service, "backend_name", "unknown"),
        "storage_backend": getattr(store, "backend_name", "unknown"),
        "rag_backend": getattr(index_store, "backend_name", "unknown"),
        "berth_options": BERTH_OPTIONS,
        "terminal_options": TERMINAL_OPTIONS,
        "vessel_type_options": VESSEL_TYPE_OPTIONS,
        "constraint_options": CONSTRAINT_OPTIONS,
        "current_profile": current_user_profile(),
        "chatbot_conversation": chatbot_conversation,
        "chatbot_messages": chatbot_messages,
        "chatbot_conversations": chatbot_conversations,
        "chatbot_model": rag.generation_model,
    }


def get_current_conversation(username: str):
    requested_id = request.args.get("conversation_id", "").strip() or None
    return store.ensure_conversation(username=username, conversation_id=requested_id)


def load_admin_status() -> dict:
    ais_status = ais_service.dashboard_context()
    local_counts = {
        "users": len(store.list_users()),
        "documents": len(store.list_documents()),
    }
    if session.get("username"):
        local_counts["conversations"] = len(store.list_conversations(session["username"]))

    db_runtime = None
    db_runtime_error = ""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if getattr(store, "backend_name", "") == "postgres" and database_url:
        try:
            db_runtime = get_database_runtime_status(database_url)
        except Exception as exc:
            db_runtime_error = str(exc)

    try:
        rag_status = rag.index_summary()
    except Exception as exc:
        rag_status = {
            "document_count": 0,
            "chunk_count": 0,
            "embedded_chunks": 0,
            "index_backend": getattr(index_store, "backend_name", "unknown"),
            "index_error": str(exc),
        }
    rag_reindex_status = current_reindex_status_payload()
    try:
        port_activity = store.get_port_activity_snapshot(window_days=5)
    except Exception as exc:
        port_activity = {
            "stats": {
                "scheduled_count": 0,
                "in_port_count": 0,
                "departed_count": 0,
                "planned_count": 0,
            },
            "arrivals": [],
            "in_port": [],
            "departed": [],
            "planned_maneuvers": [],
            "error": str(exc),
        }

    return {
        "auth_backend": getattr(auth_service, "backend_name", "unknown"),
        "auth_method_label": f"Werkzeug · {PASSWORD_HASH_METHOD}",
        "storage_backend": getattr(store, "backend_name", "unknown"),
        "rag_backend": getattr(index_store, "backend_name", "unknown"),
        "config": {
            "llm_ready": rag.client is not None,
            "embeddings_local": rag._use_local_embeddings,
            "embedding_model": rag.embedding_model if not rag._use_local_embeddings else (rag.embedding_provider.model_name if rag.embedding_provider else "N/A"),
            "weather_ready": bool(os.getenv("WEATHERAPI_KEY", "").strip()),
            "ais_ready": bool(ais_status.get("configured")),
            "database_url_ready": bool(database_url),
            "migrate_on_start": os.getenv("MIGRATE_LOCAL_DATA_ON_START", "1"),
        },
        "startup_migration": startup_migration_status,
        "db_runtime": db_runtime,
        "db_runtime_error": db_runtime_error,
        "rag_status": rag_status,
        "rag_reindex_status": rag_reindex_status,
        "ais_status": ais_status,
        "port_activity": port_activity,
        "local_counts": local_counts,
    }


maybe_run_startup_migration()


def maybe_seed_admin() -> None:
    """Create the default admin account on startup.

    Uses ADMIN_EMAIL / ADMIN_PASSWORD from environment, or defaults to
    admin@porto.pt / 123456. Promotes existing users to admin and resets
    password if needed.
    """
    admin_email = os.getenv("ADMIN_EMAIL", "admin@porto.pt").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD", "123456").strip()
    if not admin_email:
        return
    try:
        existing = store.get_user_profile(admin_email)
        if existing:
            # Ensure role is admin
            if (existing.get("role") or "").lower() != "admin":
                store.set_user_role(admin_email, "admin")
                app.logger.info("[seed] Admin promovido: %s", admin_email)
            # Always ensure password is correct
            try:
                store.reset_user_password(admin_email, admin_password)
            except Exception:
                pass
            app.logger.info("[seed] Admin verificado: %s", admin_email)
            return
        store.create_user(
            username=admin_email,
            password=admin_password,
            role="admin",
            full_name="Administrador",
            organization="APSS",
            email=admin_email,
            phone="",
        )
        app.logger.info("[seed] Admin criado: %s", admin_email)
    except Exception as exc:
        app.logger.warning("[seed] Falha ao criar admin: %s", exc)


maybe_seed_admin()


def safe_rebuild_index(force: bool = False) -> bool:
    try:
        rag.rebuild_index(force=force)
        return True
    except Exception as exc:
        rag.last_index_error = str(exc)
        app.logger.exception("Falha na reindexação do conhecimento")
        return False
    finally:
        sync_reindex_retry_schedule()


def start_reindex_job(force: bool = False) -> bool:
    global reindex_thread
    with reindex_thread_lock:
        if reindex_thread and reindex_thread.is_alive():
            return False

        def worker():
            safe_rebuild_index(force=force)

        reindex_thread = threading.Thread(
            target=worker,
            name="knowledge-reindex",
            daemon=True,
        )
        rag.mark_reindex_pending()
        if reindex_retry_scheduler is not None:
            reindex_retry_scheduler.cancel()
        reindex_thread.start()
        return True


def _start_incremental_reindex_from_scheduler() -> None:
    start_reindex_job(force=False)


reindex_retry_scheduler = DeferredTaskScheduler(
    name="knowledge-reindex-auto-retry",
    callback=_start_incremental_reindex_from_scheduler,
)


def sync_reindex_retry_schedule() -> None:
    if reindex_retry_scheduler is None:
        return
    if rag.has_active_reindex_worker():
        return

    try:
        missing_embeddings = bool(rag.client) and rag.index_has_missing_embeddings()
    except Exception as exc:
        app.logger.exception("Falha ao validar embeddings pendentes")
        rag.last_index_error = str(exc)
        return

    if not missing_embeddings:
        reindex_retry_scheduler.cancel()
        return

    if rag.is_embedding_quota_exhausted():
        retry_at = next_gemini_quota_reset_utc()
        reindex_retry_scheduler.schedule(
            retry_at,
            reason="Quota diária de embeddings esgotada; nova tentativa automática no próximo reset.",
        )
        return

    reindex_retry_scheduler.cancel()


def current_reindex_status_payload() -> dict:
    status_payload = rag.get_reindex_status()
    try:
        sync_summary = rag.get_sync_status_summary()
    except Exception as exc:
        app.logger.exception("Falha ao gerar resumo de sincronização do índice")
        rag.last_index_error = str(exc)
        sync_summary = {
            "knowledge_documents": 0,
            "indexed_documents": 0,
            "missing_embedding_chunks": 0,
            "semantic_chunk_coverage_pct": 0,
            "fully_embedded_documents": 0,
            "partially_embedded_documents": 0,
            "documents_with_missing_embeddings": 0,
            "pending_documents_total": 0,
            "sync_summary": "Resumo de sincronização indisponível.",
            "pending_summary": str(exc),
            "pending_documents_preview": [],
            "document_sync_rows": [],
        }
    sync_reindex_retry_schedule()
    retry_status = reindex_retry_scheduler.status() if reindex_retry_scheduler is not None else {}
    with reindex_thread_lock:
        thread_alive = bool(reindex_thread and reindex_thread.is_alive())
    worker_active = thread_alive or rag.has_active_reindex_worker()
    status_payload = {
        **status_payload,
        **sync_summary,
        "embedding_provider": "Gemini" if rag.client else "indisponivel",
        "query_embedding_status": (
            "blocked"
            if rag.is_embedding_quota_exhausted()
            else "available"
            if rag.client
            else "disabled"
        ),
        "query_embedding_summary": (
            "Pesquisa semântica bloqueada até renovar quota."
            if rag.is_embedding_quota_exhausted()
            else "Pesquisa semântica disponível."
            if rag.client
            else "Pesquisa semântica indisponível: API key LLM em falta."
        ),
        "scheduled_retry_at": retry_status.get("scheduled_for"),
        "scheduled_retry_eta_seconds": retry_status.get("eta_seconds"),
        "scheduled_retry_reason": retry_status.get("reason", ""),
    }

    if retry_status.get("scheduled") and status_payload.get("state") != "running":
        base_message = status_payload.get("message") or "Índice pronto."
        if "Nova tentativa automática" not in base_message:
            status_payload["message"] = (
                f"{base_message} Nova tentativa automática após reset da quota."
            )
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
            "state": "error",
            "phase": "stale",
            "message": "A reindexação anterior ficou interrompida. Já podes iniciar nova tentativa.",
            "eta_seconds": None,
            "error": status_payload.get("error") or (
                "Thread de reindexação já não está ativa."
                if not worker_active
                else "A reindexação ficou sem progresso visível durante demasiado tempo."
            ),
        }
    return status_payload


def build_weather_timeline(weather_data: dict | None, max_hours: int = 48) -> list[dict]:
    if not weather_data:
        return []

    timeline = []
    for group in weather_data.get("hourly_groups", []):
        for hour in group.get("hours", []):
            timeline.append(
                {
                    **hour,
                    "date": group.get("date", ""),
                    "day_label": group.get("date", ""),
                    "slot_label": f"{group.get('date', '')} {hour.get('time', '')}".strip(),
                }
            )
            if len(timeline) >= max_hours:
                return timeline
    return timeline


def build_operational_snapshot_source(port_activity: dict, max_rows: int = 12) -> dict:
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
            (
                f"- {item['date_label']} | {item['reference_code']} | {item['vessel_name']} | "
                f"{item['maneuver_label']} | situação {item['situation_label']} | "
                f"Hora {item['planned_label']} | "
                f"{item['local_origin']} -> {item['local_destination']} | "
                f"agente {item['agent_label']} | piloto {item['pilot_label']}"
            )
        )
        if item.get("detail_note"):
            lines.append(f"  observações: {item['detail_note']}")

    return {
        "source_id": "OPS1",
        "document": "estado_operacional_planeadas",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "operational_snapshot",
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
    archive_rows = port_activity.get("archived_maneuvers", [])
    scored_rows = []
    for index, item in enumerate(archive_rows):
        row_text = " | ".join(
            [
                item.get("date_label", ""),
                item.get("reference_code", ""),
                item.get("vessel_name", ""),
                item.get("maneuver_label", ""),
                item.get("local_origin", ""),
                item.get("local_destination", ""),
                item.get("validated_by_label", ""),
                item.get("executed_by_label", ""),
                item.get("agent_label", ""),
                item.get("detail_note", ""),
                _constraint_labels_from_badges(item),
            ]
        )
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
            (
                f"- {item.get('date_label', '--')} | {item.get('reference_code', '--')} | {item.get('vessel_name', '--')} | "
                f"{item.get('maneuver_label', '--')} | Hora {item.get('execution_window_label') or item.get('actual_label') or item.get('planned_label') or '--'} | "
                f"{item.get('local_origin', '--')} -> {item.get('local_destination', '--')} | "
                f"agente {item.get('agent_label', '--')} | validado por {item.get('validated_by_label', '--')} | "
                f"executado por {item.get('executed_by_label', '--')} | rebocadores {item.get('tug_count_label', '--')} | "
                f"restrições {_constraint_labels_from_badges(item)}"
            )
        )
        if item.get("detail_note"):
            lines.append(f"  observações: {item['detail_note']}")

    return {
        "source_id": "OPS2",
        "document": "arquivo_maneuvers_concluidas",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "operational_archive",
        "snippet": "\n".join(lines),
    }


def build_scale_registry_source(question: str, port_activity: dict, max_rows: int = 12) -> dict:
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
        row_text = " | ".join(
            [
                item.get("reference_code", ""),
                item.get("vessel_name", ""),
                item.get("berth_label", ""),
                item.get("last_port", ""),
                item.get("next_port", ""),
                item.get("status", ""),
                item.get("eta_label", ""),
                item.get("departure_label", ""),
                item.get("agent_label", ""),
                item.get("pilot_label", ""),
                item.get("notes", ""),
            ]
        )
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
            "Em porto"
            if item.get("status") == "in_port"
            else "Concluída"
            if item.get("status") == "departed"
            else "Abortada"
            if item.get("approval_status") == "aborted"
            else "Prevista"
        )
        lines.append(
            (
                f"- {item.get('reference_code', '--')} | {item.get('vessel_name', '--')} | estado {status_label} | "
                f"ETA {item.get('eta_label', '--')} | cais {item.get('berth_label', '--')} | "
                f"porto anterior {item.get('last_port', '--') or '--'} | próximo destino {item.get('next_port', '--') or '--'} | "
                f"agente {item.get('agent_label', '--')} | piloto {item.get('pilot_label', '--')}"
            )
        )
        if item.get("notes"):
            lines.append(f"  observações: {item['notes']}")

    return {
        "source_id": "OPS3",
        "document": "registo_escalas_portal",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "operational_scales",
        "snippet": "\n".join(lines),
    }


def _looks_like_cost_question(question: str) -> bool:
    """Detect if a question is about pilotage costs, billing, or tariffs."""
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
    """Build a RAG context source with cost information when relevant.

    Parameters:
        question: The user's question.
        port_activity: Current port activity snapshot.

    Returns:
        Context source dict or None if costs not relevant.
    """
    if not _looks_like_cost_question(question):
        return None

    from cost_engine import UP_NORMAL, UP_SHIFT_ALONG, format_cost_summary, calculate_scale_cost, ManoeuvreInput, ManoeuvreType

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

    # Add cost examples from current vessels in port
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
        "source_id": "COST1",
        "document": "motor_custos_pilotagem",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "cost_engine",
        "snippet": "\n".join(lines),
    }


def build_operational_chat_sources(question: str) -> list[dict]:
    recent_port_activity = store.get_port_activity_snapshot(window_days=30)
    historical_port_activity = store.get_port_activity_snapshot(window_days=3650)
    sources = [
        build_operational_snapshot_source(recent_port_activity),
        build_maneuver_archive_source(question, historical_port_activity),
        build_scale_registry_source(question, historical_port_activity),
    ]
    cost_source = build_cost_context_source(question, recent_port_activity)
    if cost_source:
        sources.append(cost_source)
    return sources


def pending_action_state_key(username: str, conversation_id: str) -> str:
    return f"chat_pending_action:{username}:{conversation_id}"


def looks_like_pending_confirmation(question: str) -> bool:
    clean = _operational_lookup_key(question)
    return clean in {
        "ok",
        "okay",
        "okey",
        "sim",
        "confirma",
        "confirmar",
        "confirmado",
        "podes confirmar",
        "avanca",
        "avancar",
        "segue",
    }


def refresh_proposal_missing_fields(proposal: dict) -> dict:
    proposal["missing_fields"] = display_missing_field_labels(
        required_missing_fields(proposal.get("action", ""), proposal.get("fields") or {})
    )
    return proposal


def current_visible_port_calls(window_days: int = 120) -> list[dict]:
    port_activity = store.get_port_activity_snapshot(window_days=window_days)
    port_activity = filter_port_activity_for_session(port_activity)
    return visible_port_calls_from_activity(port_activity)


def current_resolvable_port_calls() -> list[dict]:
    return current_visible_port_calls(window_days=3650)


def action_target_port_call(port_call_id: str) -> dict:
    port_call = store.get_port_call(port_call_id)
    if (session.get("role") or "").strip().lower() == "agente":
        ensure_port_call_scope_access(port_call_id)
    return port_call


def _operational_lookup_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


def heuristic_operational_proposal(question: str, role: str, port_calls: list[dict]) -> dict | None:
    clean = _operational_lookup_key(question)
    if not clean:
        return None

    wants_previsto = bool(re.search(r"\b(previsto|prevista|planeado|planeada)\b", clean))
    action = ""
    if re.search(r"\b(aprova|approve|aprovar)\b", clean):
        action = "approve_entry"
    elif re.search(r"\b(aborta|aborta|cancela|anula)\b", clean):
        action = "abort_entry"
    elif re.search(r"\b(confirma|confirma|fechar|fecha)\b", clean):
        action = "complete_entry"
    elif wants_previsto:
        action = "complete_entry"
    if not action:
        return None

    maneuver_type = ""
    if re.search(r"\b(saida|saida|departure)\b", clean):
        maneuver_type = "departure"
    elif re.search(r"\b(mudanca|mudanca|shift)\b", clean):
        maneuver_type = "shift"
    elif re.search(r"\b(entrada|entry)\b", clean):
        maneuver_type = "entry"

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

    resolved_port_call = store.get_port_call(matched_port_call["id"])
    if wants_previsto:
        inferred_type = maneuver_type or infer_maneuver_type(resolved_port_call, "edit_maneuver_plan") or "entry"
        target_maneuver = resolve_maneuver(resolved_port_call, "edit_maneuver_plan", inferred_type)
        if target_maneuver and target_maneuver.get("state") == "pending":
            type_label = {
                "entry": "entrada",
                "departure": "saída",
                "shift": "mudança",
            }.get(inferred_type, "manobra")
            return {
                "intent": "unsupported",
                "action": "",
                "confidence": 0.99,
                "reason": f"A {type_label} de {matched_port_call.get('vessel_name', 'este navio')} já está prevista.",
                "target": {},
                "fields": {},
                "missing_fields": [],
            }

    proposal = normalize_action_candidate(
        {
            "intent": "action",
            "action": action,
            "confidence": 0.99,
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
            "fields": {},
            "missing_fields": [],
        },
        role,
    )
    if proposal and proposal.get("intent") == "action":
        return proposal
    return None


def build_tracked_scales(port_activity: dict) -> list[dict]:
    tracked = []
    seen_ids: set[str] = set()

    for item in port_activity.get("in_port", []) or []:
        item_id = (item.get("id") or "").strip()
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        tracked.append(
            {
                "id": item_id,
                "reference_code": item.get("reference_code", ""),
                "vessel_name": item.get("vessel_name", ""),
                "location_label": item.get("berth_label", ""),
                "status_label": "Em porto",
                "status_class": "approved",
                "meta": f"ETA {item.get('eta_label', '--')} · ATA {item.get('ata_label', '--')} · agente {item.get('agent_label', '--')}",
            }
        )

    for item in port_activity.get("planned_maneuvers", []) or []:
        item_id = (item.get("port_call_id") or "").strip()
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        tracked.append(
            {
                "id": item_id,
                "reference_code": item.get("reference_code", ""),
                "vessel_name": item.get("vessel_name", ""),
                "location_label": item.get("local_destination", "") or item.get("berth_label", ""),
                "status_label": item.get("situation_label", "Prevista"),
                "status_class": item.get("situation_class", "pending"),
                "meta": (
                    f"{item.get('maneuver_label', 'Manobra')} · {item.get('date_label', '--')} "
                    f"às {item.get('planned_label', '--')} · agente {item.get('agent_label', '--')}"
                ),
            }
        )

    return tracked


def load_pending_chat_action(username: str, conversation_id: str) -> dict | None:
    payload = store.get_runtime_state(pending_action_state_key(username, conversation_id))
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
        proposal = {
            **proposal,
            **normalized,
            "port_call_id": proposal.get("port_call_id", ""),
            "maneuver_id": proposal.get("maneuver_id", ""),
        }
        payload = {
            **payload,
            "proposal": proposal,
        }
        store.set_runtime_state(pending_action_state_key(username, conversation_id), payload)
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
        "target_reference": (
            target_port_call.get("reference_code")
            if target_port_call
            else proposal.get("target", {}).get("reference_code", "")
        ),
        "target_vessel_name": (
            target_port_call.get("vessel_name")
            if target_port_call
            else proposal.get("target", {}).get("vessel_name", "")
        ),
        "can_confirm": bool(proposal.get("action")) and not proposal.get("missing_fields"),
    }


def save_pending_chat_action(username: str, conversation_id: str, proposal: dict, question: str) -> dict:
    port_call = None
    if proposal.get("port_call_id"):
        try:
            port_call = action_target_port_call(proposal["port_call_id"])
        except Exception:
            port_call = None
    payload = {
        "username": username,
        "conversation_id": conversation_id,
        "question": question,
        "proposal": proposal,
        "summary": format_action_summary(proposal, port_call),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    store.set_runtime_state(pending_action_state_key(username, conversation_id), payload)
    return payload


def clear_pending_chat_action(username: str, conversation_id: str) -> None:
    store.delete_runtime_state(pending_action_state_key(username, conversation_id))


def propose_operational_action(question: str, role: str) -> dict | None:
    if not looks_like_operational_command(question):
        return None
    resolvable_port_calls = current_resolvable_port_calls()
    heuristic_proposal = heuristic_operational_proposal(question, role, resolvable_port_calls)
    if heuristic_proposal:
        return finalize_operational_proposal(heuristic_proposal, resolvable_port_calls)
    if not rag.client:
        return {
            "intent": "unsupported",
            "action": "",
            "confidence": 0.0,
            "reason": "O bot operador precisa de uma API key LLM para interpretar ações operacionais.",
            "target": {},
            "fields": {},
            "missing_fields": [],
        }

    port_calls = current_visible_port_calls()
    prompt = build_operational_action_prompt(
        question=question,
        role=role,
        now_local=datetime.now().astimezone(),
        port_calls=port_calls,
        berth_options=BERTH_OPTIONS,
        constraint_options=CONSTRAINT_OPTIONS,
    )
    try:
        gen_result = rag.provider.generate(
            prompt=prompt,
            model=rag.generation_model,
        )
    except Exception as exc:
        return {
            "intent": "unsupported",
            "action": "",
            "confidence": 0.0,
            "reason": f"Falha a interpretar a ação operacional: {exc}",
            "target": {},
            "fields": {},
            "missing_fields": [],
        }

    candidate = extract_json_object(gen_result.text or "")
    proposal = normalize_action_candidate(candidate or {}, role)
    if proposal and proposal.get("intent") == "unsupported":
        heuristic_proposal = heuristic_operational_proposal(question, role, resolvable_port_calls)
        if heuristic_proposal:
            return finalize_operational_proposal(heuristic_proposal, resolvable_port_calls)
    return finalize_operational_proposal(proposal, current_visible_port_calls())


def finalize_operational_proposal(proposal: dict | None, port_calls: list[dict] | None = None) -> dict | None:
    if not proposal or proposal.get("intent") != "action":
        return proposal
    target = None
    existing_port_call_id = (proposal.get("port_call_id") or "").strip()
    if existing_port_call_id:
        try:
            target = store.get_port_call(existing_port_call_id)
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
    resolved_port_call = store.get_port_call(target["id"]) if target else None
    inferred_type = infer_maneuver_type(resolved_port_call or {}, proposal.get("action", "")) if resolved_port_call else ""
    if inferred_type and proposal.get("action") in {
        "approve_entry",
        "approve_departure",
        "approve_shift",
        "abort_entry",
        "abort_departure",
        "abort_shift",
        "complete_entry",
        "complete_departure",
        "complete_shift",
        "entry_report",
        "departure_report",
        "shift_report",
    }:
        proposal["action"] = action_for_maneuver_type(proposal["action"], inferred_type)

    if proposal.get("action") in {
        "approve_entry",
        "abort_entry",
        "complete_entry",
        "entry_report",
    }:
        proposal["target"]["maneuver_type"] = "entry"
    elif proposal.get("action") in {
        "approve_departure",
        "abort_departure",
        "complete_departure",
        "departure_report",
        "schedule_departure",
    }:
        proposal["target"]["maneuver_type"] = "departure"
    elif proposal.get("action") in {
        "approve_shift",
        "abort_shift",
        "complete_shift",
        "shift_report",
        "schedule_shift",
    }:
        proposal["target"]["maneuver_type"] = "shift"
    elif maneuver_type not in {"entry", "departure", "shift"} and proposal.get("action") in {
        "edit_maneuver_plan",
        "edit_maneuver_report",
    }:
        if inferred_type:
            proposal["target"]["maneuver_type"] = inferred_type
        else:
            proposal["intent"] = "unsupported"
            proposal["action"] = ""
            proposal["reason"] = "Indica se queres alterar a entrada, a saída ou a mudança."
            return proposal
    elif maneuver_type not in {"entry", "departure", "shift"} and inferred_type and proposal.get("action") in {
        "approve_entry",
        "approve_departure",
        "approve_shift",
        "abort_entry",
        "abort_departure",
        "abort_shift",
        "complete_entry",
        "complete_departure",
        "complete_shift",
        "entry_report",
        "departure_report",
        "shift_report",
    }:
        proposal["target"]["maneuver_type"] = inferred_type

    if target and proposal.get("action") in {"edit_maneuver_plan", "edit_maneuver_report"}:
        maneuver = resolve_maneuver(
            store.get_port_call(target["id"]),
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
            maneuver_type = proposal["target"].get("maneuver_type", "")
            if maneuver_type == "entry" and fields.get("berth") and not fields.get("destination"):
                fields["destination"] = fields["berth"]
            elif maneuver_type == "departure" and fields.get("next_port") and not fields.get("destination"):
                fields["destination"] = fields["next_port"]
            elif maneuver_type == "shift" and fields.get("destination_berth") and not fields.get("destination"):
                fields["destination"] = fields["destination_berth"]

    proposal["fields"]["constraints"] = normalize_constraint_codes(proposal.get("fields", {}).get("constraints", []))
    return refresh_proposal_missing_fields(proposal)


def pending_action_override(question: str, pending_proposal: dict, role: str) -> dict | None:
    clean = _operational_lookup_key(question)
    if not clean:
        return None
    maneuver_type = (pending_proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
    if maneuver_type not in {"entry", "departure", "shift"}:
        return None

    if re.search(r"\b(aprova|approve|aprovar)\b", clean):
        action = action_for_maneuver_type("approve_entry", maneuver_type)
    elif re.search(r"\b(aborta|cancela|anula)\b", clean):
        action = action_for_maneuver_type("abort_entry", maneuver_type)
    elif re.search(r"\b(confirma|confirmar|concluir|conclui|fecha|fechar)\b", clean):
        action = action_for_maneuver_type("complete_entry", maneuver_type)
    elif re.search(r"\b(previsto|prevista|planeado|planeada)\b", clean):
        action = action_for_maneuver_type("complete_entry", maneuver_type)
    elif re.search(r"\b(registo|registar|relatorio|relatorio|relatório)\b", clean):
        action = action_for_maneuver_type("entry_report", maneuver_type)
    else:
        return None

    if action == pending_proposal.get("action"):
        return None

    replacement = normalize_action_candidate(
        {
            "intent": "action",
            "action": action,
            "confidence": 0.99,
            "reason": "Troca direta da ação pendente pelo pedido do utilizador.",
            "target": pending_proposal.get("target", {}),
            "fields": {},
            "missing_fields": [],
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
    replacement = pending_action_override(question, pending_proposal, role)
    if replacement and replacement.get("intent") == "action":
        return {
            "intent": "replace",
            "proposal": replacement,
        }

    direct_updates = extract_pending_field_updates(question, pending_proposal)
    if direct_updates:
        updates = normalize_action_candidate(
            {
                "intent": "action",
                "action": pending_proposal.get("action", ""),
                "confidence": pending_proposal.get("confidence", 0.0),
                "reason": pending_proposal.get("reason", ""),
                "target": pending_proposal.get("target", {}),
                "fields": direct_updates,
                "missing_fields": [],
            },
            role,
        )
        merged = merge_action_candidate(pending_proposal, updates or {}, role)
        return {
            "intent": "update",
            "proposal": finalize_operational_proposal(merged),
        }

    if not rag.client:
        return {
            "intent": "unsupported",
            "reason": "O bot operador precisa de uma API key LLM para atualizar propostas pendentes.",
        }

    prompt = build_pending_action_update_prompt(
        question=question,
        role=role,
        proposal=pending_proposal,
        berth_options=BERTH_OPTIONS,
        constraint_options=CONSTRAINT_OPTIONS,
    )
    try:
        gen_result = rag.provider.generate(
            prompt=prompt,
            model=rag.generation_model,
        )
    except Exception as exc:
        return {
            "intent": "unsupported",
            "reason": f"Falha a atualizar a proposta pendente: {exc}",
        }

    candidate = extract_json_object(gen_result.text or "") or {}
    intent = (candidate.get("intent") or "").strip().lower()
    if intent in {"cancel", "question", "unsupported"}:
        return {
            "intent": intent or "unsupported",
            "reason": " ".join(str(candidate.get("reason") or "").strip().split()),
        }
    if intent == "replace":
        proposal = normalize_action_candidate(candidate, role)
        return {
            "intent": "replace",
            "proposal": finalize_operational_proposal(proposal),
        }

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
    return {
        "intent": "update",
        "proposal": finalize_operational_proposal(merged),
    }


def execute_pending_operational_action(proposal: dict, username: str, role: str) -> tuple[dict, str]:
    action = proposal.get("action") or ""
    target = proposal.get("target") or {}
    fields = proposal.get("fields") or {}
    port_call_id = (proposal.get("port_call_id") or "").strip()
    role = (role or "").strip().lower()

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
            destination = field_text(
                "destination",
                field_text("berth", current_maneuver.get("destination") or current_port_call.get("berth", "")),
            )
        elif current_maneuver_type == "departure":
            destination = field_text(
                "destination",
                field_text("next_port", current_maneuver.get("destination") or current_port_call.get("next_port", "")),
            )
        else:
            destination = field_text(
                "destination",
                field_text(
                    "destination_berth",
                    current_maneuver.get("destination")
                    or current_port_call.get("shift_destination_berth", "")
                    or current_port_call.get("berth", ""),
                ),
            )
        planned_at_value = field_text("planned_at_local", field_text("eta_local"))
        if current_maneuver_type == "departure":
            planned_at_value = field_text("planned_at_local", field_text("planned_departure_at_local", planned_at_value))
        elif current_maneuver_type == "shift":
            planned_at_value = field_text("planned_at_local", field_text("planned_shift_at_local", planned_at_value))
        if not any(
            [
                planned_at_value,
                field_text("draft_m"),
                field_text("tug_count"),
                field_text("notes"),
                field_text("plan_observations"),
                field_text("change_reason"),
                fields.get("constraints"),
            ]
        ):
            return
        store.edit_maneuver_plan(
            port_call_id=port_call_id,
            maneuver_id=current_maneuver.get("id", ""),
            updated_by=username,
            actor_role=role,
            planned_at=parse_local_datetime_input(
                planned_at_value or current_maneuver.get("planned_input_value") or current_maneuver.get("planned_at") or "",
                "Hora de marcação",
            ),
            origin=require_form_text(
                field_text("origin", current_maneuver.get("origin") or current_port_call.get("last_port", "")),
                "Origem",
            ),
            destination=require_form_text(destination, "Destino"),
            draft_m=field_text("draft_m", current_maneuver.get("planned_draft_m", "")),
            tug_count=field_text("tug_count", current_maneuver.get("tug_count", "")),
            constraints=normalize_constraint_codes(fields.get("constraints") or current_maneuver.get("constraints", [])),
            plan_note=field_text("plan_observations", field_text("notes", current_maneuver.get("plan_observations", ""))),
            change_reason=require_form_text(field_text("change_reason", field_text("reason")), "Motivo da alteração"),
        )

    if action == "create_port_call":
        eta = parse_local_datetime_input(field_text("eta_local"), "ETA")
        booking_at = parse_optional_local_datetime_input(field_text("booking_local"), "Marcação")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        draft_m = field_text("draft_m")
        tug_count = field_text("tug_count")
        port_call = store.create_port_call(
            vessel_name=field_text("vessel_name"),
            eta=eta,
            created_by=username,
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
            notes=build_entry_request_note(
                {
                    "booking_at": booking_at,
                    "draft_m": draft_m,
                    "tug_count": tug_count,
                    "constraints": constraints,
                    "notes": fields.get("notes", ""),
                }
            ),
        )
        return port_call, f"Escala criada para {port_call['vessel_name']} com ETA {port_call['eta_label']}."

    if not port_call_id:
        raise ValueError("A proposta não tem escala associada.")

    port_call = apply_scope(port_call_id)
    maneuver_type = target.get("maneuver_type", "")

    if action == "approve_entry":
        apply_plan_updates_before_approval(port_call, "entry")
        result = store.approve_port_call(
            port_call_id=port_call_id,
            decided_by=username,
            approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))),
        )
        return result, f"Entrada aprovada para {result['vessel_name']}."
    if action == "abort_entry":
        result = store.abort_port_call(
            port_call_id=port_call_id,
            decided_by=username,
            aborted_reason=require_form_text(field_text("aborted_reason", field_text("reason")), "Motivo do aborto"),
            approval_note=field_text("approval_note"),
        )
        return result, f"Entrada abortada para {result['vessel_name']}."
    if action == "complete_entry":
        arrived_at_value = field_text("arrived_at_local", field_text("maneuver_finished_local"))
        result = store.mark_port_call_arrived(
            port_call_id=port_call_id,
            arrived_at=parse_optional_local_datetime_input(arrived_at_value, "ATA") or datetime.now().astimezone().isoformat(),
            updated_by=username,
            berth=field_text("berth", port_call.get("berth")),
        )
        return result, f"Entrada confirmada para {result['vessel_name']} às {result['ata_label']}. Já podes preencher o registo operacional."
    if action == "entry_report":
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note(
            {
                "maneuver_started_at": started_at,
                "maneuver_finished_at": finished_at,
                "draft_m": draft_m,
                "notes": fields.get("notes", ""),
            },
            "Entrada",
        )
        result = store.attach_entry_report(
            port_call_id=port_call_id,
            updated_by=username,
            maneuver_started_at=started_at,
            maneuver_finished_at=finished_at,
            draft_m=draft_m,
            notes=note,
        )
        return result, f"Registo de entrada guardado para {result['vessel_name']}."
    if action == "schedule_departure":
        planned_departure_at = parse_local_datetime_input(field_text("planned_departure_at_local"), "Hora prevista de saída")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        booking_at = parse_optional_local_datetime_input(field_text("booking_local"), "Marcação")
        result = store.schedule_departure_plan(
            port_call_id=port_call_id,
            planned_departure_at=planned_departure_at,
            updated_by=username,
            next_port=require_form_text(field_text("next_port", port_call.get("next_port", "")), "Próximo destino"),
            constraints=constraints,
            departure_plan_note=build_departure_plan_note(
                {
                    "booking_at": booking_at,
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
        result = store.approve_port_call(
            port_call_id=port_call_id,
            decided_by=username,
            approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))),
        )
        return result, f"Saída aprovada para {result['vessel_name']}."
    if action == "abort_departure":
        result = store.abort_departure_plan(
            port_call_id=port_call_id,
            updated_by=username,
            aborted_reason=require_form_text(field_text("aborted_reason", field_text("reason")), "Motivo do cancelamento"),
        )
        return result, f"Saída cancelada para {result['vessel_name']}."
    if action == "complete_departure":
        departed_at_value = field_text("departed_at_local", field_text("maneuver_finished_local"))
        result = store.mark_port_call_departed(
            port_call_id=port_call_id,
            departed_at=parse_optional_local_datetime_input(departed_at_value, "ATD") or datetime.now().astimezone().isoformat(),
            updated_by=username,
            next_port=field_text("next_port", port_call.get("next_port")),
        )
        return result, f"Saída confirmada para {result['vessel_name']} às {result['departure_label']}. Já podes preencher o registo operacional."
    if action == "departure_report":
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note(
            {
                "maneuver_started_at": started_at,
                "maneuver_finished_at": finished_at,
                "draft_m": draft_m,
                "notes": fields.get("notes", ""),
            },
            "Saída",
        )
        result = store.attach_departure_report(
            port_call_id=port_call_id,
            updated_by=username,
            maneuver_started_at=started_at,
            maneuver_finished_at=finished_at,
            draft_m=draft_m,
            notes=note,
        )
        return result, f"Registo de saída guardado para {result['vessel_name']}."
    if action == "schedule_shift":
        planned_shift_at = parse_local_datetime_input(field_text("planned_shift_at_local"), "Hora prevista da mudança")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        booking_at = parse_optional_local_datetime_input(field_text("booking_local"), "Marcação")
        result = store.schedule_shift_plan(
            port_call_id=port_call_id,
            planned_shift_at=planned_shift_at,
            updated_by=username,
            destination_berth=require_form_text(field_text("destination_berth"), "Cais destino"),
            constraints=constraints,
            shift_plan_note=build_shift_plan_note(
                {
                    "origin_berth": field_text("origin_berth", port_call.get("berth", "")),
                    "destination_berth": field_text("destination_berth"),
                    "booking_at": booking_at,
                    "draft_m": field_text("draft_m"),
                    "tug_count": field_text("tug_count"),
                    "constraints": constraints,
                    "notes": field_text("notes"),
                }
            ),
        )
        return result, f"Mudança planeada para {result['vessel_name']} às {result['planned_shift_label']}."
    if action == "approve_shift":
        apply_plan_updates_before_approval(port_call, "shift")
        result = store.approve_shift_plan(
            port_call_id=port_call_id,
            decided_by=username,
            approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))),
        )
        return result, f"Mudança aprovada para {result['vessel_name']}."
    if action == "abort_shift":
        result = store.abort_shift_plan(
            port_call_id=port_call_id,
            updated_by=username,
            aborted_reason=require_form_text(field_text("aborted_reason", field_text("reason")), "Motivo do cancelamento"),
        )
        return result, f"Mudança cancelada para {result['vessel_name']}."
    if action == "complete_shift":
        shifted_at_value = field_text("shifted_at_local", field_text("maneuver_finished_local"))
        result = store.mark_shift_completed(
            port_call_id=port_call_id,
            shifted_at=parse_optional_local_datetime_input(shifted_at_value, "Hora da mudança") or datetime.now().astimezone().isoformat(),
            updated_by=username,
        )
        return result, f"Mudança concluída para {result['vessel_name']} às {result['shift_label']}. Já podes preencher o registo operacional."
    if action == "shift_report":
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note(
            {
                "maneuver_started_at": started_at,
                "maneuver_finished_at": finished_at,
                "draft_m": draft_m,
                "notes": fields.get("notes", ""),
            },
            "Mudança",
        )
        result = store.attach_shift_report(
            port_call_id=port_call_id,
            updated_by=username,
            maneuver_started_at=started_at,
            maneuver_finished_at=finished_at,
            draft_m=draft_m,
            notes=note,
        )
        return result, f"Registo de mudança guardado para {result['vessel_name']}."
    if action == "edit_maneuver_plan":
        maneuver_id = (proposal.get("maneuver_id") or "").strip()
        current_port_call = store.get_port_call(port_call_id)
        maneuver = resolve_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "") or maneuver_id
        if not maneuver_id:
            raise ValueError("A proposta não identifica a manobra a editar.")
        base_origin = field_text("origin", (maneuver or {}).get("origin") or current_port_call.get("last_port", ""))
        if maneuver_type == "entry":
            base_destination = field_text(
                "destination",
                field_text("berth", (maneuver or {}).get("destination") or current_port_call.get("berth", "")),
            )
        elif maneuver_type == "departure":
            base_destination = field_text(
                "destination",
                field_text("next_port", (maneuver or {}).get("destination") or current_port_call.get("next_port", "")),
            )
        else:
            base_destination = field_text(
                "destination",
                field_text(
                    "destination_berth",
                    (maneuver or {}).get("destination")
                    or current_port_call.get("shift_destination_berth", "")
                    or current_port_call.get("berth", ""),
                ),
            )
        result = store.edit_maneuver_plan(
            port_call_id=port_call_id,
            maneuver_id=maneuver_id,
            updated_by=username,
            actor_role=role,
            planned_at=parse_local_datetime_input(
                field_text("planned_at_local", (maneuver or {}).get("planned_input_value", "")),
                "Hora de marcação",
            ),
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
        current_port_call = store.get_port_call(port_call_id)
        maneuver = resolve_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "") or maneuver_id
        if not maneuver_id:
            raise ValueError("A proposta não identifica a manobra a editar.")
        started_at = parse_local_datetime_input(
            field_text("maneuver_started_local", (maneuver or {}).get("execution_started_input_value", "")),
            "Início da manobra",
        )
        finished_at = parse_local_datetime_input(
            field_text("maneuver_finished_local", (maneuver or {}).get("execution_finished_input_value", "")),
            "Fim da manobra",
        )
        draft_m = require_form_text(field_text("draft_m", (maneuver or {}).get("reported_draft_m", "")), "Calado")
        result = store.edit_maneuver_report(
            port_call_id=port_call_id,
            maneuver_id=maneuver_id,
            updated_by=username,
            maneuver_started_at=started_at,
            maneuver_finished_at=finished_at,
            draft_m=draft_m,
            notes=build_pilot_report_note(
                {
                    "maneuver_started_at": started_at,
                    "maneuver_finished_at": finished_at,
                    "draft_m": draft_m,
                    "notes": field_text("notes", (maneuver or {}).get("report_note", "")),
                },
                "Entrada" if maneuver_type == "entry" else "Saída" if maneuver_type == "departure" else "Mudança",
                existing_note="",
            ),
            change_reason=require_form_text(field_text("change_reason"), "Motivo da alteração"),
        )
        return result, f"Registo operacional revisto para {result['vessel_name']}."

    raise ValueError("Ação operacional não suportada.")


def build_scale_context(port_call: dict) -> dict:
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
            item
            for item in history
            if item.get("type") == maneuver_type
            and item.get("state") == "completed"
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
        maneuvers.append(
            {
                "id": item.get("id"),
                "type": item.get("type"),
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
            }
        )
        for log in item.get("change_log", []):
            actor_profile = log.get("changed_by_profile") or {}
            change_log_rows.append(
                {
                    "maneuver_title": item.get("type_label", item.get("type", "")),
                    "changed_at": log.get("changed_at"),
                    "changed_at_label": _local_iso_to_label(log.get("changed_at")),
                    "changed_by_label": actor_profile.get("full_name") or actor_profile.get("username") or "--",
                    "changed_by_contact": (
                        actor_profile.get("email")
                        or actor_profile.get("phone")
                        or actor_profile.get("organization")
                        or "--"
                    ),
                    "reason": log.get("reason") or "--",
                    "summary": log.get("summary") or "--",
                }
            )
    entry_report_exists = bool(entry and entry.get("state") == "completed" and entry.get("report_note"))
    departure_report_exists = bool(completed_departure and completed_departure.get("report_note"))
    shift_report_exists = bool(completed_shift and completed_shift.get("report_note"))

    summary = {
        "scale_reference": port_call["reference_code"],
        "status_label": (
            "Concluída"
            if port_call.get("status") == "departed"
            else "Em porto"
            if port_call.get("status") == "in_port"
            else "Prevista"
        ),
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
            (entry or {}).get("completed_at") or (entry or {}).get("planned_at"),
            etd_value,
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
        "can_approve_entry": port_call.get("status") == "scheduled"
        and port_call.get("approval_status") == "pending",
        "can_abort_entry": port_call.get("status") == "scheduled"
        and port_call.get("approval_status") != "aborted"
        and port_call.get("can_abort"),
        "can_complete_entry": port_call.get("status") == "scheduled"
        and bool(entry)
        and entry.get("state") == "approved",
        "can_plan_departure": port_call.get("status") == "in_port"
        and not active_departure
        and not completed_departure,
        "can_approve_departure": port_call.get("status") == "in_port"
        and bool(active_departure)
        and active_departure.get("state") == "pending",
        "can_abort_departure": port_call.get("status") == "in_port"
        and bool(active_departure)
        and active_departure.get("state") in {"pending", "approved"},
        "can_complete_departure": port_call.get("status") == "in_port"
        and bool(active_departure)
        and active_departure.get("state") == "approved",
        "can_register_entry": bool(reportable_entry),
        "can_register_departure": bool(reportable_departure),
        "can_plan_shift": port_call.get("status") == "in_port"
        and not active_shift,
        "can_approve_shift": port_call.get("status") == "in_port"
        and bool(active_shift)
        and active_shift.get("state") == "pending",
        "can_abort_shift": port_call.get("status") == "in_port"
        and bool(active_shift)
        and active_shift.get("state") in {"pending", "approved"},
        "can_complete_shift": port_call.get("status") == "in_port"
        and bool(active_shift)
        and active_shift.get("state") == "approved",
        "can_register_shift": bool(reportable_shift),
        "must_complete_label": (
            "Concluir entrada"
            if port_call.get("status") == "scheduled" and bool(entry) and entry.get("state") == "approved"
            else "Concluir saída"
            if port_call.get("status") == "in_port" and bool(active_departure) and active_departure.get("state") == "approved"
            else "Concluir mudança"
            if port_call.get("status") == "in_port" and bool(active_shift) and active_shift.get("state") == "approved"
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
        "change_log_rows": sorted(
            change_log_rows,
            key=lambda item: item.get("changed_at") or "",
            reverse=True,
        ),
        "actions": actions,
    }


def parse_local_datetime_input(value: str, label: str = "ETA") -> str:
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
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        return ""
    return parse_local_datetime_input(clean, label=label)


def require_form_text(value: str, label: str) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        raise ValueError(f"{label} é obrigatório.")
    return clean


def format_note_datetime(value: str) -> str:
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
    lines = [title]
    for label, value in fields:
        clean = " ".join((value or "").strip().split())
        if clean:
            lines.append(f"{label}: {clean}")
    return "\n".join(lines)


def build_entry_request_note(form_data: dict) -> str:
    return compact_multiline_note(
        "Registo do agente · Entrada",
        [
            ("Marcação", format_note_datetime(form_data.get("booking_at", ""))),
            ("Calado", form_data.get("draft_m", "")),
            ("Rebocadores", form_data.get("tug_count", "")),
            ("Restrições", format_constraint_labels(form_data.get("constraints", []))),
            ("Observações", form_data.get("notes", "")),
        ],
    )


def build_departure_plan_note(form_data: dict) -> str:
    return compact_multiline_note(
        "Registo do agente · Saída",
        [
            ("Marcação", format_note_datetime(form_data.get("booking_at", ""))),
            ("Calado", form_data.get("draft_m", "")),
            ("Rebocadores", form_data.get("tug_count", "")),
            ("Restrições", format_constraint_labels(form_data.get("constraints", []))),
            ("Observações", form_data.get("notes", "")),
        ],
    )


def build_shift_plan_note(form_data: dict) -> str:
    return compact_multiline_note(
        "Registo do agente · Mudança",
        [
            ("Origem", form_data.get("origin_berth", "")),
            ("Destino", form_data.get("destination_berth", "")),
            ("Marcação", format_note_datetime(form_data.get("booking_at", ""))),
            ("Calado", form_data.get("draft_m", "")),
            ("Rebocadores", form_data.get("tug_count", "")),
            ("Restrições", format_constraint_labels(form_data.get("constraints", []))),
            ("Observações", form_data.get("notes", "")),
        ],
    )


def build_pilot_report_note(form_data: dict, maneuver_label: str, existing_note: str = "") -> str:
    report = compact_multiline_note(
        f"Registo simplificado de pilotagem · {maneuver_label}",
        [
            ("Início da manobra", format_note_datetime(form_data.get("maneuver_started_at", ""))),
            ("Fim da manobra", format_note_datetime(form_data.get("maneuver_finished_at", ""))),
            ("Calado", form_data.get("draft_m", "")),
            ("Observações", form_data.get("notes", "")),
        ],
    )
    if existing_note.strip():
        return f"{existing_note.strip()}\n\n{report}"
    return report


@app.route("/")
def home():
    if session.get("username"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/img/<path:asset_path>")
def image_asset(asset_path: str):
    return send_from_directory(os.path.join(BASE_DIR, "img"), asset_path)


@app.route("/healthz")
def healthz():
    return jsonify(
        {
            "ok": True,
            "auth_backend": getattr(auth_service, "backend_name", "unknown"),
            "storage_backend": getattr(store, "backend_name", "unknown"),
            "rag_backend": getattr(index_store, "backend_name", "unknown"),
            "startup_migration": startup_migration_status,
        }
    )


@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(_exc):
    flash(
        "Ficheiro demasiado grande para este rascunho local. "
        f"Limite atual: {int(app.config['MAX_CONTENT_LENGTH'] / (1024 * 1024))} MB.",
        "error",
    )
    return redirect(request.referrer or url_for("dashboard")), 413


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("username"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        try:
            user = auth_service.authenticate(username, password)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("login.html")
        if not user:
            flash("Credenciais invalidas.", "error")
            return render_template("login.html")

        session["username"] = user["username"]
        session["role"] = user["role"]
        flash(f"Entraste como {user['role']}.", "success")
        if session_profile_incomplete():
            flash("Completa o teu perfil operacional antes de continuar.", "error")
            return redirect(url_for("profile"))
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "piloto")
        profile_data = {
            "full_name": request.form.get("full_name", "").strip(),
            "organization": request.form.get("organization", "").strip(),
            "email": username,
            "phone": request.form.get("phone", "").strip(),
        }

        try:
            result = auth_service.register(
                username=username,
                password=password,
                role=role,
                profile_data=profile_data,
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("register.html", form_data={"role": role, **profile_data})

        flash("Conta criada. Ja podes iniciar sessao.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    existing_profile = current_user_profile() or {"username": session["username"], "role": session.get("role", "piloto")}
    if request.method == "POST":
        try:
            updated_profile = store.update_user_profile(
                session["username"],
                full_name=request.form.get("full_name", "").strip(),
                organization=request.form.get("organization", "").strip(),
                email=session["username"],
                phone=request.form.get("phone", "").strip(),
            )
            if not is_user_profile_complete(updated_profile):
                raise ValueError("Nome, agência/entidade, email e telefone são obrigatórios.")
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("profile.html", profile={**existing_profile, **request.form}, title="Perfil operacional")
        flash("Perfil operacional atualizado.", "success")
        next_target = request.form.get("next", "").strip()
        if next_target and next_target.startswith("/"):
            return redirect(next_target)
        return redirect(url_for("dashboard"))

    return render_template("profile.html", profile=existing_profile, title="Perfil operacional")


@app.route("/logout")
def logout():
    session.clear()
    flash("Sessao terminada.", "success")
    return redirect(url_for("login"))


@app.route("/logout-beacon", methods=["POST"])
def logout_beacon():
    session.clear()
    return ("", 204)


@app.route("/admin/status")
@login_required
@role_required("admin")
def admin_status():
    refresh_knowledge_state(force_reindex=False)
    return render_template("admin_status.html", admin=load_admin_status())


@app.route("/admin/users")
@login_required
@role_required("admin")
def admin_users():
    return render_template(
        "admin_users.html",
        users=store.list_users(),
        title="Utilizadores",
    )


@app.route("/admin/users/<username>", methods=["POST"])
@login_required
@role_required("admin")
def admin_update_user(username: str):
    target_username = username.strip().lower()
    try:
        updated_role = request.form.get("role", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        organization = request.form.get("organization", "").strip()
        phone = request.form.get("phone", "").strip()

        # Enforce admin uniqueness: only 1 admin allowed
        if updated_role == "admin" and target_username != session.get("username"):
            existing_admins = [
                u for u in store.list_users()
                if (u.get("role") or "").strip().lower() == "admin"
                and u.get("username") != target_username
            ]
            if existing_admins:
                flash("Já existe um administrador no sistema. Só pode haver 1 admin.", "error")
                return redirect(url_for("admin_users"))

        store.update_user_profile(
            target_username,
            full_name=full_name,
            organization=organization,
            email=target_username,
            phone=phone,
        )
        updated_user = store.set_user_role(target_username, updated_role)
        if session.get("username") == target_username:
            session["role"] = updated_user["role"]
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_users"))
    except Exception:
        logger.exception("Falha inesperada ao atualizar utilizador %s.", target_username)
        flash("Falha inesperada ao atualizar o utilizador.", "error")
        return redirect(url_for("admin_users"))

    flash(f"Utilizador {target_username} atualizado.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<username>/delete", methods=["POST"])
@login_required
@role_required("admin")
def admin_delete_user(username: str):
    target_username = username.strip().lower()
    if session.get("username") == target_username:
        flash("Não podes apagar a tua própria conta enquanto estás autenticado.", "error")
        return redirect(url_for("admin_users"))
    try:
        store.delete_user(target_username)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_users"))
    except Exception:
        logger.exception("Falha inesperada ao apagar utilizador %s.", target_username)
        flash("Falha inesperada ao apagar o utilizador.", "error")
        return redirect(url_for("admin_users"))

    flash(f"Utilizador {target_username} apagado.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/migrate-local-data", methods=["POST"])
@login_required
@role_required("admin")
def admin_migrate_local_data():
    if getattr(store, "backend_name", "") != "postgres":
        flash("A migração local -> Postgres só faz sentido com APP_STORAGE_BACKEND=postgres.", "error")
        return redirect(url_for("admin_status"))

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        flash("DATABASE_URL em falta.", "error")
        return redirect(url_for("admin_status"))

    force = request.form.get("force", "0") == "1"
    try:
        result = migrate_local_json_to_postgres(
            data_dir=DATA_DIR,
            knowledge_dir=KNOWLEDGE_DIR,
            database_url=database_url,
            force=force,
        )
        global startup_migration_status
        startup_migration_status = result
        refresh_knowledge_state(force_reindex=True)
        flash(f"Migração concluída com estado: {result['status']}.", "success")
    except Exception as exc:
        flash(f"Falha na migração: {exc}", "error")
    return redirect(url_for("admin_status"))


@app.route("/dashboard")
@login_required
def dashboard():
    refresh_knowledge_state(force_reindex=False)
    port_activity = store.get_port_activity_snapshot(window_days=5)
    port_activity = filter_port_activity_for_session(port_activity)

    # Tides for today + tomorrow
    from datetime import date, timedelta
    today = date.today()
    tomorrow = today + timedelta(days=1)
    tides_today = tide_service.summary_for_date(today)
    tides_tomorrow = tide_service.summary_for_date(tomorrow)

    weather_data = None
    weather_error = ""
    weather_timeline = []
    if weather_service.enabled:
        try:
            weather_data = weather_service.get_forecast(days=3)
            weather_timeline = build_weather_timeline(weather_data, max_hours=48)
        except Exception as exc:
            weather_error = str(exc)
    ais_context = ais_service.dashboard_context()
    return render_template(
        "dashboard.html",
        port_activity=port_activity,
        tides_today=tides_today,
        tides_tomorrow=tides_tomorrow,
        weather_data=weather_data,
        weather_timeline=weather_timeline,
        weather_error=weather_error,
        ais=ais_context,
        title="PRAGtico",
    )


@app.route("/embed/vesselfinder/setubal")
@login_required
def vesselfinder_embed_setubal():
    return render_template(
        "vesselfinder_embed.html",
        embed=ais_service.embed_context(),
        title="VesselFinder Setubal",
    )


@app.route("/maneuvers/archive")
@login_required
def maneuver_archive():
    refresh_knowledge_state(force_reindex=False)
    port_activity = store.get_port_activity_snapshot(window_days=30)
    port_activity = filter_port_activity_for_session(port_activity)
    return render_template(
        "maneuver_archive.html",
        port_activity=port_activity,
        title="Arquivo de Manobras",
    )


@app.route("/port-calls/register")
@login_required
@role_required("admin", "agente")
def port_call_register():
    port_activity = store.get_port_activity_snapshot(window_days=5)
    port_activity = filter_port_activity_for_session(port_activity)
    return render_template(
        "port_call_register.html",
        port_activity=port_activity,
        tracked_scales=build_tracked_scales(port_activity),
        title="Registo de Escalas",
    )


@app.route("/admin/documents")
@login_required
@role_required("admin")
def admin_documents():
    refresh_knowledge_state(force_reindex=False)
    docs = store.list_documents()
    try:
        rag_stats = rag.index_summary()
    except Exception as exc:
        rag_stats = {
            "document_count": 0,
            "chunk_count": 0,
            "embedded_chunks": 0,
            "index_backend": getattr(index_store, "backend_name", "unknown"),
            "index_error": str(exc),
        }
    reindex_status = current_reindex_status_payload()
    return render_template(
        "admin_documents.html",
        docs=docs,
        rag_stats=rag_stats,
        reindex_status=reindex_status,
        title="Gestão de Documentos",
    )


@app.route("/maneuvers/archive/export.csv")
@login_required
def maneuver_archive_export():
    port_activity = store.get_port_activity_snapshot(window_days=3650)
    port_activity = filter_port_activity_for_session(port_activity)
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Data",
            "Escala",
            "Navio",
            "Tipo de navio",
            "Hora",
            "Situacao",
            "Manobra",
            "Origem",
            "Destino",
            "Restricoes",
            "Agente",
            "Validado por",
            "Executado por",
            "Observacoes",
        ]
    )
    for item in port_activity.get("archived_maneuvers", []):
        writer.writerow(
            [
                item.get("date_label", ""),
                item.get("reference_code", ""),
                item.get("vessel_name", ""),
                item.get("vessel_type", ""),
                item.get("execution_window_label") or item.get("actual_label") or item.get("planned_label") or "",
                item.get("situation_label", ""),
                item.get("maneuver_label", ""),
                item.get("local_origin", ""),
                item.get("local_destination", ""),
                ", ".join(
                    badge.get("label", "")
                    for badge in item.get("constraint_badges", [])
                    if badge.get("label")
                ),
                item.get("agent_label", ""),
                item.get("validated_by_label", ""),
                item.get("executed_by_label", ""),
                (item.get("detail_note") or "").replace("\n", " ").strip(),
            ]
        )

    filename = f"arquivo_manobras_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/port-calls/<port_call_id>")
@login_required
@port_call_scope_required
def port_call_detail(port_call_id: str):
    try:
        port_call = store.get_port_call(port_call_id)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))
    except Exception:
        logger.exception("Falha inesperada ao abrir a escala %s.", port_call_id)
        flash("Falha inesperada ao abrir a escala.", "error")
        return redirect(url_for("dashboard"))
    return render_template(
        "port_call_detail.html",
        port_call=port_call,
        scale=build_scale_context(port_call),
        title=f"Escala {port_call['vessel_name']}",
    )


@app.route("/port-calls", methods=["POST"])
@login_required
@role_required("admin", "agente")
def create_port_call():
    form_data = {
        "vessel_name": request.form.get("vessel_name", "").strip(),
        "vessel_short_name": "",
        "vessel_imo": request.form.get("vessel_imo", "").strip(),
        "vessel_call_sign": request.form.get("vessel_call_sign", "").strip(),
        "vessel_flag": request.form.get("vessel_flag", "").strip(),
        "vessel_type": request.form.get("vessel_type", "").strip(),
        "vessel_loa_m": request.form.get("vessel_loa_m", "").strip(),
        "vessel_beam_m": request.form.get("vessel_beam_m", "").strip(),
        "vessel_gt_t": request.form.get("vessel_gt_t", "").strip(),
        "vessel_max_draft_m": request.form.get("vessel_max_draft_m", "").strip(),
        "vessel_dwt_t": request.form.get("vessel_dwt_t", "").strip(),
        "eta_local": request.form.get("eta_local", "").strip(),
        "berth": request.form.get("berth", "").strip(),
        "last_port": request.form.get("last_port", "").strip(),
        "next_port": request.form.get("next_port", "").strip(),
        "booking_local": request.form.get("booking_local", "").strip(),
        "draft_m": request.form.get("draft_m", "").strip(),
        "constraints": request.form.getlist("constraints"),
        "tug_count": request.form.get("tug_count", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }

    try:
        eta = parse_local_datetime_input(form_data["eta_local"], "ETA")
        booking_at = parse_local_datetime_input(form_data["booking_local"], "Marcação")
        berth = require_form_text(form_data["berth"], "Cais previsto")
        last_port = require_form_text(form_data["last_port"], "Porto anterior")
        next_port = require_form_text(form_data["next_port"], "Próximo destino")
        draft_m = require_form_text(form_data["draft_m"], "Calado")
        tug_count = require_form_text(form_data["tug_count"], "Rebocadores")
        port_call = store.create_port_call(
            vessel_name=form_data["vessel_name"],
            eta=eta,
            created_by=session["username"],
            constraints=form_data["constraints"],
            berth=berth,
            last_port=last_port,
            next_port=next_port,
            vessel_short_name=form_data["vessel_short_name"],
            vessel_imo=form_data["vessel_imo"],
            vessel_call_sign=form_data["vessel_call_sign"],
            vessel_flag=form_data["vessel_flag"],
            vessel_type=form_data["vessel_type"],
            vessel_loa_m=form_data["vessel_loa_m"],
            vessel_beam_m=form_data["vessel_beam_m"],
            vessel_gt_t=form_data["vessel_gt_t"],
            vessel_max_draft_m=form_data["vessel_max_draft_m"],
            vessel_dwt_t=form_data["vessel_dwt_t"],
            notes=build_entry_request_note(
                {
                    **form_data,
                    "booking_at": booking_at,
                    "draft_m": draft_m,
                    "tug_count": tug_count,
                }
            ),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))
    except Exception:
        logger.exception("Falha inesperada ao criar escala para %s.", session.get("username"))
        flash("Falha inesperada ao guardar a escala.", "error")
        return redirect(url_for("dashboard"))

    flash(f"Manobra registada para {port_call['vessel_name']} com ETA {port_call['eta_label']}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/port-calls/<port_call_id>/approve", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def approve_port_call(port_call_id: str):
    try:
        port_call = store.approve_port_call(
            port_call_id=port_call_id,
            decided_by=session["username"],
            approval_note=request.form.get("approval_note", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Manobra aprovada para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/abort", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def abort_port_call(port_call_id: str):
    try:
        port_call = store.abort_port_call(
            port_call_id=port_call_id,
            decided_by=session["username"],
            aborted_reason=request.form.get("aborted_reason", "").strip(),
            approval_note=request.form.get("approval_note", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Manobra abortada para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/schedule-departure", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def schedule_departure_plan(port_call_id: str):
    try:
        planned_departure_at = parse_local_datetime_input(
            request.form.get("planned_departure_at_local", "").strip(),
            "Hora prevista de saída",
        )
        booking_at = parse_local_datetime_input(request.form.get("booking_local", "").strip(), "Marcação")
        next_port = require_form_text(request.form.get("next_port", "").strip(), "Próximo destino")
        draft_m = require_form_text(request.form.get("draft_m", "").strip(), "Calado")
        tug_count = require_form_text(request.form.get("tug_count", "").strip(), "Rebocadores")
        port_call = store.schedule_departure_plan(
            port_call_id=port_call_id,
            planned_departure_at=planned_departure_at,
            updated_by=session["username"],
            next_port=next_port,
            constraints=request.form.getlist("constraints"),
            departure_plan_note=build_departure_plan_note(
                {
                    "booking_at": booking_at,
                    "draft_m": draft_m,
                    "constraints": request.form.getlist("constraints"),
                    "tug_count": tug_count,
                    "notes": request.form.get("departure_plan_note", "").strip(),
                }
            ),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(
        f"Saída planeada para {port_call['vessel_name']} às {port_call['planned_departure_label']}.",
        "success",
    )
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/abort-departure", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def abort_departure_plan(port_call_id: str):
    try:
        port_call = store.abort_departure_plan(
            port_call_id=port_call_id,
            updated_by=session["username"],
            aborted_reason=request.form.get("aborted_reason", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Planeamento de saída removido para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/schedule-shift", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def schedule_shift_plan(port_call_id: str):
    try:
        planned_shift_at = parse_local_datetime_input(
            request.form.get("planned_shift_at_local", "").strip(),
            "Hora prevista da mudança",
        )
        booking_at = parse_local_datetime_input(request.form.get("booking_local", "").strip(), "Marcação")
        origin_berth = require_form_text(request.form.get("origin_berth", "").strip(), "Cais origem")
        destination_berth = require_form_text(request.form.get("destination_berth", "").strip(), "Cais destino")
        draft_m = require_form_text(request.form.get("draft_m", "").strip(), "Calado")
        tug_count = require_form_text(request.form.get("tug_count", "").strip(), "Rebocadores")
        port_call = store.schedule_shift_plan(
            port_call_id=port_call_id,
            planned_shift_at=planned_shift_at,
            updated_by=session["username"],
            destination_berth=destination_berth,
            constraints=request.form.getlist("constraints"),
            shift_plan_note=build_shift_plan_note(
                {
                    "origin_berth": origin_berth,
                    "destination_berth": destination_berth,
                    "booking_at": booking_at,
                    "draft_m": draft_m,
                    "constraints": request.form.getlist("constraints"),
                    "tug_count": tug_count,
                    "notes": request.form.get("shift_plan_note", "").strip(),
                }
            ),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(
        f"Mudança planeada para {port_call['vessel_name']} às {port_call['planned_shift_label']}.",
        "success",
    )
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/approve-shift", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def approve_shift_plan(port_call_id: str):
    try:
        port_call = store.approve_shift_plan(
            port_call_id=port_call_id,
            decided_by=session["username"],
            approval_note=request.form.get("approval_note", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança aprovada para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/abort-shift", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def abort_shift_plan(port_call_id: str):
    try:
        port_call = store.abort_shift_plan(
            port_call_id=port_call_id,
            updated_by=session["username"],
            aborted_reason=request.form.get("aborted_reason", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança removida para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/complete-shift", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def mark_shift_completed(port_call_id: str):
    try:
        port_call = store.mark_shift_completed(
            port_call_id=port_call_id,
            shifted_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança concluída para {port_call['vessel_name']} às {port_call['shift_label']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/arrive", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def mark_port_call_arrived(port_call_id: str):
    try:
        port_call = store.mark_port_call_arrived(
            port_call_id=port_call_id,
            arrived_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
            berth=request.form.get("berth", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Entrada confirmada para {port_call['vessel_name']} às {port_call['ata_label']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/depart", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def mark_port_call_departed(port_call_id: str):
    try:
        port_call = store.mark_port_call_departed(
            port_call_id=port_call_id,
            departed_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
            next_port=request.form.get("next_port", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Saída registada para {port_call['vessel_name']} às {port_call['departure_label']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/entry-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def attach_entry_report(port_call_id: str):
    try:
        maneuver_started_at = parse_local_datetime_input(
            request.form.get("maneuver_started_local", "").strip(),
            "Início da manobra",
        )
        maneuver_finished_at = parse_local_datetime_input(
            request.form.get("maneuver_finished_local", "").strip(),
            "Fim da manobra",
        )
        draft_m = require_form_text(request.form.get("draft_m", "").strip(), "Calado")
        note = build_pilot_report_note(
            {
                "maneuver_started_at": maneuver_started_at,
                "maneuver_finished_at": maneuver_finished_at,
                "draft_m": draft_m,
                "notes": request.form.get("notes", "").strip(),
            },
            "Entrada",
        )
        port_call = store.attach_entry_report(
            port_call_id=port_call_id,
            updated_by=session["username"],
            maneuver_started_at=maneuver_started_at,
            maneuver_finished_at=maneuver_finished_at,
            draft_m=draft_m,
            notes=note,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Registo da entrada guardado para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/departure-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def attach_departure_report(port_call_id: str):
    try:
        maneuver_started_at = parse_local_datetime_input(
            request.form.get("maneuver_started_local", "").strip(),
            "Início da manobra",
        )
        maneuver_finished_at = parse_local_datetime_input(
            request.form.get("maneuver_finished_local", "").strip(),
            "Fim da manobra",
        )
        draft_m = require_form_text(request.form.get("draft_m", "").strip(), "Calado")
        note = build_pilot_report_note(
            {
                "maneuver_started_at": maneuver_started_at,
                "maneuver_finished_at": maneuver_finished_at,
                "draft_m": draft_m,
                "notes": request.form.get("notes", "").strip(),
            },
            "Saída",
        )
        port_call = store.attach_departure_report(
            port_call_id=port_call_id,
            updated_by=session["username"],
            maneuver_started_at=maneuver_started_at,
            maneuver_finished_at=maneuver_finished_at,
            draft_m=draft_m,
            notes=note,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Registo da saída guardado para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/shift-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def attach_shift_report(port_call_id: str):
    try:
        maneuver_started_at = parse_local_datetime_input(
            request.form.get("maneuver_started_local", "").strip(),
            "Início da manobra",
        )
        maneuver_finished_at = parse_local_datetime_input(
            request.form.get("maneuver_finished_local", "").strip(),
            "Fim da manobra",
        )
        draft_m = require_form_text(request.form.get("draft_m", "").strip(), "Calado")
        note = build_pilot_report_note(
            {
                "maneuver_started_at": maneuver_started_at,
                "maneuver_finished_at": maneuver_finished_at,
                "draft_m": draft_m,
                "notes": request.form.get("notes", "").strip(),
            },
            "Mudança",
        )
        port_call = store.attach_shift_report(
            port_call_id=port_call_id,
            updated_by=session["username"],
            maneuver_started_at=maneuver_started_at,
            maneuver_finished_at=maneuver_finished_at,
            draft_m=draft_m,
            notes=note,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Registo da mudança guardado para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/edit-plan", methods=["POST"])
@login_required
@role_required("admin", "agente", "piloto")
@port_call_scope_required
def edit_maneuver_plan(port_call_id: str, maneuver_id: str):
    try:
        port_call = store.edit_maneuver_plan(
            port_call_id=port_call_id,
            maneuver_id=maneuver_id,
            updated_by=session["username"],
            actor_role=session.get("role", ""),
            planned_at=parse_local_datetime_input(request.form.get("planned_at_local", "").strip(), "Hora de marcação"),
            origin=require_form_text(request.form.get("origin", "").strip(), "Origem"),
            destination=require_form_text(request.form.get("destination", "").strip(), "Destino"),
            draft_m=require_form_text(request.form.get("draft_m", "").strip(), "Calado"),
            tug_count=require_form_text(request.form.get("tug_count", "").strip(), "Rebocadores"),
            constraints=request.form.getlist("constraints"),
            plan_note=request.form.get("plan_observations", "").strip(),
            change_reason=require_form_text(request.form.get("change_reason", "").strip(), "Motivo da alteração"),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    except Exception:
        logger.exception("Falha inesperada ao editar planeamento %s/%s.", port_call_id, maneuver_id)
        flash("Falha inesperada ao editar a manobra.", "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Planeamento atualizado para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/edit-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def edit_maneuver_report(port_call_id: str, maneuver_id: str):
    try:
        maneuver_started_at = parse_local_datetime_input(
            request.form.get("maneuver_started_local", "").strip(),
            "Início da manobra",
        )
        maneuver_finished_at = parse_local_datetime_input(
            request.form.get("maneuver_finished_local", "").strip(),
            "Fim da manobra",
        )
        draft_m = require_form_text(request.form.get("draft_m", "").strip(), "Calado")
        port_call = store.edit_maneuver_report(
            port_call_id=port_call_id,
            maneuver_id=maneuver_id,
            updated_by=session["username"],
            maneuver_started_at=maneuver_started_at,
            maneuver_finished_at=maneuver_finished_at,
            draft_m=draft_m,
            notes=build_pilot_report_note(
                {
                    "maneuver_started_at": maneuver_started_at,
                    "maneuver_finished_at": maneuver_finished_at,
                    "draft_m": draft_m,
                    "notes": request.form.get("notes", "").strip(),
                },
                require_form_text(request.form.get("maneuver_label", "").strip(), "Manobra"),
            ),
            change_reason=require_form_text(request.form.get("change_reason", "").strip(), "Motivo da alteração"),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Registo revisto para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@app.route("/conversations")
@login_required
def chat_archive():
    username = session["username"]
    current_conversation = get_current_conversation(username)
    conversations = store.list_conversations(username)
    messages = store.list_messages(username, current_conversation["id"])
    return render_template(
        "chat_archive.html",
        conversations=conversations,
        current_conversation=current_conversation,
        messages=messages,
        title="Conversas",
    )


@app.route("/conversations", methods=["POST"])
@login_required
def create_conversation():
    conversation = store.create_conversation(session["username"])
    flash("Nova conversa criada.", "success")
    return redirect(url_for("dashboard", conversation_id=conversation["id"]))


@app.route("/conversations/<conversation_id>/rename", methods=["POST"])
@login_required
def rename_conversation(conversation_id: str):
    title = request.form.get("title", "")
    try:
        conversation = store.rename_conversation(session["username"], conversation_id, title)
        flash("Conversa renomeada.", "success")
        return redirect(url_for("dashboard", conversation_id=conversation["id"]))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard", conversation_id=conversation_id))


@app.route("/conversations/<conversation_id>/clear", methods=["POST"])
@login_required
def clear_conversation(conversation_id: str):
    try:
        store.clear_conversation(session["username"], conversation_id)
        flash("Mensagens da conversa removidas.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard", conversation_id=conversation_id))


@app.route("/conversations/<conversation_id>/delete", methods=["POST"])
@login_required
def delete_conversation(conversation_id: str):
    try:
        next_conversation_id = store.delete_conversation(session["username"], conversation_id)
        flash("Conversa eliminada.", "success")
        if next_conversation_id:
            return redirect(url_for("dashboard", conversation_id=next_conversation_id))
        return redirect(url_for("dashboard"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard", conversation_id=conversation_id))


@app.route("/documents", methods=["POST"])
@login_required
@role_required("admin")
def add_document():
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()

    if not title or not content:
        flash("Titulo e conteudo sao obrigatorios.", "error")
        return redirect(url_for("dashboard"))

    filename = store.save_document(title, content, created_by=session["username"])
    if safe_rebuild_index(force=False):
        flash(f"Documento {filename} indexado.", "success")
    else:
        flash(
            f"Documento {filename} guardado, mas a reindexação falhou: {rag.last_index_error}",
            "error",
        )
    return redirect(url_for("dashboard"))


@app.route("/documents/upload", methods=["POST"])
@login_required
@role_required("admin")
def upload_documents():
    uploaded_files = [item for item in request.files.getlist("files") if item and item.filename]
    if not uploaded_files:
        flash("Seleciona pelo menos um ficheiro.", "error")
        return redirect(url_for("dashboard"))

    stored = []
    failed = []
    for uploaded_file in uploaded_files:
        try:
            filename = store.save_uploaded_document(uploaded_file, created_by=session["username"])
            stored.append(filename)
        except Exception as exc:
            failed.append(f"{uploaded_file.filename}: {exc}")

    if stored:
        if safe_rebuild_index(force=False):
            flash(f"Foram indexados {len(stored)} ficheiro(s): {', '.join(stored)}.", "success")
        else:
            flash(
                "Os ficheiros foram guardados, mas a reindexação falhou: " + rag.last_index_error,
                "error",
            )
    if failed:
        flash("Falhas no upload: " + " | ".join(failed), "error")
    return redirect(url_for("dashboard"))


@app.route("/knowledge/reindex", methods=["POST"])
@login_required
@role_required("admin")
def reindex_knowledge():
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
                **status_payload,
                "state": "running",
                "phase": "queued",
                "message": "A iniciar reindexação...",
                "progress_pct": max(float(status_payload.get("progress_pct") or 0), 1.0),
                "error": "",
            }
        return jsonify(
            {
                "started": started,
                "status": status_payload,
                "message": (
                    "Reindexação incremental iniciada."
                    if started
                    else "Já existe uma reindexação em curso."
                ),
            }
        ), 202 if started else 200

    if started:
        flash("Reindexação incremental iniciada. O progresso aparece no painel documental.", "success")
    else:
        flash("Já existe uma reindexação em curso.", "error")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/api/knowledge/reindex-status")
@login_required
def reindex_status():
    return jsonify(current_reindex_status_payload())


@app.route("/documents/<name>")
@login_required
def document_detail(name: str):
    refresh_knowledge_state(force_reindex=False)
    document = store.get_document(name)
    if not document:
        abort(404)

    try:
        document_text = store.get_document_text(name)
    except Exception as exc:
        document_text = f"Erro ao ler conteúdo extraído: {exc}"

    return render_template(
        "document_detail.html",
        document=document,
        document_text=document_text,
    )


@app.route("/documents/<name>/download")
@login_required
def download_document(name: str):
    refresh_knowledge_state(force_reindex=False)
    try:
        file_path = store.get_document_file_path(name)
    except Exception:
        abort(404)
    return send_file(file_path, as_attachment=True, download_name=os.path.basename(file_path))


@app.route("/documents/<name>/edit", methods=["POST"])
@login_required
@role_required("admin")
def edit_document(name: str):
    content = request.form.get("content", "").strip()
    try:
        store.update_document_text(name=name, content=content, updated_by=session["username"])
        if safe_rebuild_index(force=False):
            flash(f"Documento {name} atualizado e reindexado.", "success")
        else:
            flash(
                f"Documento {name} atualizado, mas a reindexação falhou: {rag.last_index_error}",
                "error",
            )
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("document_detail", name=name))


@app.route("/documents/<name>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_document(name: str):
    try:
        store.delete_document(name)
        if safe_rebuild_index(force=False):
            flash(f"Documento {name} removido do conhecimento.", "success")
        else:
            flash(
                f"Documento {name} removido, mas a reindexação falhou: {rag.last_index_error}",
                "error",
            )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("document_detail", name=name))
    return redirect(url_for("dashboard"))


@app.route("/api/messages/<message_id>/feedback", methods=["POST"])
@login_required
def api_message_feedback(message_id: str):
    payload = request.get_json(silent=True) or {}
    conversation_id = (payload.get("conversation_id") or "").strip()
    feedback_status = (payload.get("feedback_status") or "").strip().lower()
    feedback_note = (payload.get("feedback_note") or "").strip()

    if not conversation_id:
        return jsonify({"error": "conversation_id em falta."}), 400
    try:
        message = store.update_message_feedback(
            username=session["username"],
            conversation_id=conversation_id,
            message_id=message_id,
            feedback_status=feedback_status,
            feedback_note=feedback_note,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(message)


@app.route("/api/chat/pending-action")
@login_required
def api_pending_chat_action():
    conversation_id = (request.args.get("conversation_id") or "").strip()
    if not conversation_id:
        return jsonify({"pending_action": None})
    pending = load_pending_chat_action(session["username"], conversation_id)
    return jsonify({"pending_action": pending})


@app.route("/api/chat/pending-action/cancel", methods=["POST"])
@login_required
def api_cancel_pending_chat_action():
    payload = request.get_json(silent=True) or {}
    conversation_id = (payload.get("conversation_id") or "").strip()
    if not conversation_id:
        return jsonify({"error": "conversation_id em falta."}), 400

    pending = load_pending_chat_action(session["username"], conversation_id)
    if not pending:
        return jsonify({"error": "Não existe ação pendente para cancelar."}), 404

    clear_pending_chat_action(session["username"], conversation_id)
    assistant_message = store.append_chat_message(
        username=session["username"],
        conversation_id=conversation_id,
        role="assistant",
        content="Ação operacional cancelada. O portal não foi alterado.",
    )
    return jsonify(
        {
            "answer": assistant_message["content"],
            "message_id": assistant_message["id"],
            "pending_action": None,
            "conversation_id": conversation_id,
        }
    )


@app.route("/api/chat/pending-action/confirm", methods=["POST"])
@login_required
def api_confirm_pending_chat_action():
    payload = request.get_json(silent=True) or {}
    conversation_id = (payload.get("conversation_id") or "").strip()
    if not conversation_id:
        return jsonify({"error": "conversation_id em falta."}), 400

    username = session["username"]
    pending = load_pending_chat_action(username, conversation_id)
    if not pending:
        return jsonify({"error": "Não existe ação pendente para confirmar."}), 404

    proposal = pending.get("proposal") or {}
    if proposal.get("missing_fields"):
        return jsonify({"error": "Ainda faltam dados obrigatórios antes de confirmar esta ação."}), 400

    try:
        result, message = execute_pending_operational_action(
            proposal,
            username=username,
            role=session.get("role", ""),
        )
    except (PermissionError, ValueError) as exc:
        clear_pending_chat_action(username, conversation_id)
        assistant_message = store.append_chat_message(
            username=username,
            conversation_id=conversation_id,
            role="assistant",
            content=f"Não consegui aplicar a ação operacional. Motivo: {exc}",
        )
        return jsonify(
            {
                "error": str(exc),
                "answer": assistant_message["content"],
                "message_id": assistant_message["id"],
                "pending_action": None,
                "conversation_id": conversation_id,
            }
        ), 400
    except Exception as exc:
        logger.exception("Falha inesperada na execução da ação operacional do chat.")
        clear_pending_chat_action(username, conversation_id)
        assistant_message = store.append_chat_message(
            username=username,
            conversation_id=conversation_id,
            role="assistant",
            content="Falha inesperada ao aplicar a ação operacional no portal.",
        )
        return jsonify(
            {
                "error": str(exc),
                "answer": assistant_message["content"],
                "message_id": assistant_message["id"],
                "pending_action": None,
                "conversation_id": conversation_id,
            }
        ), 500

    clear_pending_chat_action(username, conversation_id)
    current_port_call = result if isinstance(result, dict) else None
    citations = []
    if current_port_call and current_port_call.get("reference_code"):
        citations.append(
            {
                "document": current_port_call.get("vessel_name", "Escala"),
                "source_id": current_port_call.get("reference_code", ""),
                "retrieval_mode": "operational_action",
                "snippet": message,
            }
        )
    assistant_message = store.append_chat_message(
        username=username,
        conversation_id=conversation_id,
        role="assistant",
        content=message,
        citations=citations,
    )
    return jsonify(
        {
            "answer": assistant_message["content"],
            "message_id": assistant_message["id"],
            "pending_action": None,
            "conversation_id": conversation_id,
            "sources": citations,
        }
    )


@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    refresh_knowledge_state(force_reindex=False)
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    conversation_id = (payload.get("conversation_id") or "").strip() or None

    if not question:
        return jsonify({"error": "Pergunta vazia."}), 400

    username = session["username"]
    conversation = store.ensure_conversation(username=username, conversation_id=conversation_id)
    history = store.list_messages(username, conversation["id"])
    existing_pending = load_pending_chat_action(username, conversation["id"])
    trusted_answers = store.find_feedback_matches(username, question, limit=3)
    supplemental_sources = build_operational_chat_sources(question)
    supplemental_sources.append(tide_service.context_for_question(question))
    if weather_service.enabled:
        try:
            weather_context = weather_service.context_source()
            if weather_context:
                supplemental_sources.append(weather_context)
        except Exception:
            pass
    user_message = store.append_chat_message(
        username=username,
        conversation_id=conversation["id"],
        role="user",
        content=question,
    )

    if existing_pending:
        if looks_like_pending_confirmation(question):
            proposal = existing_pending.get("proposal", {})
            if proposal.get("missing_fields"):
                answer = {
                    "answer": "Ainda faltam dados obrigatórios antes de confirmar esta ação.",
                    "sources": [],
                    "pending_action": load_pending_chat_action(username, conversation["id"]),
                    "answer_origin": "pending_action_block",
                }
            else:
                try:
                    result, message = execute_pending_operational_action(
                        proposal,
                        username=username,
                        role=session.get("role", ""),
                    )
                except (PermissionError, ValueError) as exc:
                    clear_pending_chat_action(username, conversation["id"])
                    answer = {
                        "answer": f"Não consegui aplicar a ação operacional. Motivo: {exc}",
                        "sources": [],
                        "pending_action": None,
                        "answer_origin": "pending_action_error",
                    }
                except Exception:
                    logger.exception("Falha inesperada na execução da ação operacional do chat.")
                    clear_pending_chat_action(username, conversation["id"])
                    answer = {
                        "answer": "Falha inesperada ao aplicar a ação operacional no portal.",
                        "sources": [],
                        "pending_action": None,
                        "answer_origin": "pending_action_error",
                    }
                else:
                    clear_pending_chat_action(username, conversation["id"])
                    citations = []
                    current_port_call = result if isinstance(result, dict) else None
                    if current_port_call and current_port_call.get("reference_code"):
                        citations.append(
                            {
                                "document": current_port_call.get("vessel_name", "Escala"),
                                "source_id": current_port_call.get("reference_code", ""),
                                "retrieval_mode": "operational_action",
                                "snippet": message,
                            }
                        )
                    answer = {
                        "answer": message,
                        "sources": citations,
                        "pending_action": None,
                        "answer_origin": "pending_action_confirmed",
                    }
        else:
            pending_update = refine_pending_operational_action(
                question,
                existing_pending.get("proposal", {}),
                session.get("role", "piloto"),
            )
            if pending_update and pending_update.get("intent") == "update" and pending_update.get("proposal", {}).get("intent") == "action":
                pending_action = save_pending_chat_action(
                    username=username,
                    conversation_id=conversation["id"],
                    proposal=pending_update["proposal"],
                    question=question,
                )
                answer = {
                    "answer": "Atualizei a ação pendente com a tua resposta.\n\n" + pending_action["summary"],
                    "sources": [],
                    "pending_action": load_pending_chat_action(username, conversation["id"]),
                    "answer_origin": "operational_update",
                }
            elif pending_update and pending_update.get("intent") == "replace" and pending_update.get("proposal", {}).get("intent") == "action":
                pending_action = save_pending_chat_action(
                    username=username,
                    conversation_id=conversation["id"],
                    proposal=pending_update["proposal"],
                    question=question,
                )
                answer = {
                    "answer": "Troquei a proposta pendente por uma nova ação operacional.\n\n" + pending_action["summary"],
                    "sources": [],
                    "pending_action": load_pending_chat_action(username, conversation["id"]),
                    "answer_origin": "operational_replace",
                }
            elif pending_update and pending_update.get("intent") == "cancel":
                clear_pending_chat_action(username, conversation["id"])
                answer = {
                    "answer": "A ação operacional pendente foi cancelada. O portal não foi alterado.",
                    "sources": [],
                    "pending_action": None,
                    "answer_origin": "pending_action_cancelled",
                }
            elif pending_update and pending_update.get("intent") == "question":
                answer = None
            else:
                answer = {
                    "answer": (
                        pending_update.get("reason")
                        if pending_update and pending_update.get("reason")
                        else "Não consegui atualizar a ação pendente com essa resposta."
                    ),
                    "sources": [],
                    "pending_action": load_pending_chat_action(username, conversation["id"]),
                    "answer_origin": "pending_action_block",
                }
    else:
        action_proposal = propose_operational_action(question, session.get("role", "piloto"))
        if action_proposal and action_proposal.get("intent") == "action":
            pending_action = save_pending_chat_action(
                username=username,
                conversation_id=conversation["id"],
                proposal=action_proposal,
                question=question,
            )
            answer = {
                "answer": pending_action["summary"],
                "sources": [],
                "pending_action": load_pending_chat_action(username, conversation["id"]),
                "answer_origin": "operational_proposal",
            }
        elif action_proposal and action_proposal.get("intent") == "unsupported":
            answer = {
                "answer": action_proposal.get("reason") or "Essa ação não pode ser executada pelo bot nesta conta.",
                "sources": [],
                "answer_origin": "operational_rejected",
            }
        elif action_proposal and action_proposal.get("intent") == "question":
            answer = None
        else:
            answer = None

    if answer is None and trusted_answers and trusted_answers[0].get("similarity", 0) >= 0.96:
        best_match = trusted_answers[0]
        answer = {
            "answer": best_match["answer"],
            "sources": best_match.get("citations", []),
            "answer_origin": "approved_memory",
            "feedback_match": {
                "similarity": best_match["similarity"],
                "message_id": best_match["message_id"],
                "question": best_match["question"],
                "feedback_note": best_match.get("feedback_note", ""),
            },
        }
    elif answer is None:
        if not rag.client:
            return jsonify({"error": "Define a API key do LLM antes de usar o chatbot."}), 500
        answer = rag.answer(
            question=question,
            role=session.get("role", "piloto"),
            history=(history + [user_message])[-10:],
            supplemental_sources=supplemental_sources,
            trusted_answers=trusted_answers,
        )
        answer["answer_origin"] = "llm"
        if trusted_answers:
            answer["feedback_match"] = {
                "similarity": trusted_answers[0]["similarity"],
                "message_id": trusted_answers[0]["message_id"],
                "question": trusted_answers[0]["question"],
                "feedback_note": trusted_answers[0].get("feedback_note", ""),
            }

    assistant_message = store.append_chat_message(
        username=username,
        conversation_id=conversation["id"],
        role="assistant",
        content=answer["answer"],
        citations=answer.get("sources", []),
    )
    return jsonify(
        {
            **answer,
            "conversation_id": conversation["id"],
            "message_id": assistant_message["id"],
            "pending_action": answer.get("pending_action"),
        }
    )


@app.route("/api/cost/estimate", methods=["POST"])
@login_required
def api_cost_estimate():
    """API endpoint for pilotage cost estimation.

    Accepts JSON with vessel GT and manoeuvre details,
    returns a detailed cost breakdown.
    """
    payload = request.get_json(silent=True) or {}
    gt = payload.get("gt", 0)
    try:
        gt = float(gt)
    except (TypeError, ValueError):
        return jsonify({"error": "GT inválido."}), 400

    if gt <= 0:
        return jsonify({"error": "GT tem de ser positivo."}), 400

    vessel_name = (payload.get("vessel_name") or "Navio").strip()
    vessel_type = (payload.get("vessel_type") or "restantes").strip().lower()
    stay_days = max(float(payload.get("stay_days", 1)), 0.5)
    include_tup = payload.get("include_tup", True)

    raw_manoeuvres = payload.get("manoeuvres", [])
    if not raw_manoeuvres:
        raw_manoeuvres = [{"type": "entry"}, {"type": "departure"}]

    type_map = {
        "entry": ManoeuvreType.ENTRY,
        "entrada": ManoeuvreType.ENTRY,
        "departure": ManoeuvreType.DEPARTURE,
        "saida": ManoeuvreType.DEPARTURE,
        "shift": ManoeuvreType.SHIFT,
        "mudanca": ManoeuvreType.SHIFT,
        "anchoring": ManoeuvreType.ANCHORING,
        "standby": ManoeuvreType.STANDBY,
        "trials": ManoeuvreType.TRIALS,
    }
    surcharge_map = {
        "no_propulsion": SurchargeType.NO_PROPULSION,
        "special_assistance": SurchargeType.SPECIAL_ASSISTANCE,
    }
    reduction_map = {
        "regular_line": ReductionType.REGULAR_LINE,
        "cabotage": ReductionType.CABOTAGE,
        "technical_call": ReductionType.TECHNICAL_CALL,
    }

    manoeuvre_inputs = []
    for raw in raw_manoeuvres:
        m_type = type_map.get((raw.get("type") or "entry").lower().strip(), ManoeuvreType.ENTRY)
        surcharges = [surcharge_map[s] for s in (raw.get("surcharges") or []) if s in surcharge_map]
        reductions = [reduction_map[r] for r in (raw.get("reductions") or []) if r in reduction_map]
        manoeuvre_inputs.append(ManoeuvreInput(
            manoeuvre_type=m_type,
            gt=gt,
            surcharges=surcharges,
            reductions=reductions,
            standby_hours=float(raw.get("standby_hours", 0)),
            regular_line_calls=raw.get("regular_line_calls"),
        ))

    estimate = calculate_scale_cost(
        vessel_name=vessel_name,
        gt=gt,
        vessel_type=vessel_type,
        manoeuvres=manoeuvre_inputs,
        stay_days=stay_days,
        include_tup=include_tup,
    )

    return jsonify({
        "vessel_name": estimate.vessel_name,
        "gt": estimate.gt,
        "vessel_type": estimate.vessel_type,
        "pilotage_total": estimate.pilotage_total,
        "tup_estimate": estimate.tup_estimate,
        "stay_days": estimate.stay_days,
        "grand_total": estimate.grand_total,
        "manoeuvres": [
            {
                "type": m.manoeuvre_type,
                "base_cost": m.base_cost,
                "surcharge": m.surcharge_amount,
                "reduction": m.reduction_amount,
                "standby": m.standby_cost,
                "total": m.total_cost,
                "breakdown": m.breakdown,
            }
            for m in estimate.manoeuvres
        ],
        "notes": estimate.notes,
        "summary": format_cost_summary(estimate),
        "currency": "EUR",
        "tariff_year": 2024,
    })


@app.route("/api/cost/quick", methods=["GET"])
@login_required
def api_cost_quick():
    """Quick cost estimate for a single manoeuvre.

    Query parameters: gt (required), type (optional, default 'entry').
    """
    try:
        gt = float(request.args.get("gt", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "GT inválido."}), 400

    if gt <= 0:
        return jsonify({"error": "GT tem de ser positivo."}), 400

    m_type = request.args.get("type", "entry").strip()
    return jsonify(quick_estimate(gt, m_type))


if __name__ == "__main__":
    refresh_knowledge_state(
        force_reindex=False,
        rebuild_index=os.getenv("RAG_REINDEX_ON_START", "0") == "1",
    )
    app.run(
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
    )
