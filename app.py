"""PRAGtico — Flask application factory with Blueprint registration."""

import logging
import os
import threading
import re
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, request, session
from markupsafe import Markup, escape
from werkzeug.exceptions import RequestEntityTooLarge

from core import services
from integrations.ais_service import create_ais_service
from integrations.auth_service import create_auth_service
from core.helpers import (
    current_reindex_status_payload,
    current_user_profile,
    execute_pending_operational_action,
    finalize_operational_proposal,
    propose_operational_action,
    refresh_knowledge_state,
    safe_rebuild_index,
    save_pending_chat_action,
    start_reindex_job,
)
from integrations.llm_provider import create_embedding_provider, create_llm_provider
from integrations.local_warning_service import LocalWarningService
from domain.migration_service import migrate_local_json_to_postgres
from integrations.rag_engine import SimpleRAGEngine
from core.reindex_scheduler import DeferredTaskScheduler
from core.security import init_csrf
from storage import (
    PASSWORD_HASH_METHOD,
    create_store,
    get_constraint_options,
    get_vessel_type_options,
)
from integrations.tide_service import TideService
from integrations.vector_store import create_index_store
from integrations.weather_service import WeatherService
from integrations.wave_service import WaveService

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")

BERTH_OPTIONS = [
    "Secil W", "Secil E", "Fundeadouro Norte", "Cais Palmeiras",
    "TMS 1 - Cais 3", "TMS 1 - Cais 4", "TMS 1 - Cais 5",
    "TMS 1 - Cais 6", "TMS 1 - Cais 7", "TMS 1 - Cais 8", "TMS 2",
    "Cais 10 / Autoeuropa", "Cais 11 / Autoeuropa",
    "Praias do Sado / Pirites Alentejanas",
    "SAPEC Sólidos", "SAPEC Líquidos", "ALSTOM",
    "PAN Tróia", "Fundeadouro Sul / Tróia",
    "Tanquisado (lado jusante)", "Eco-Oil (lado montante)",
    "Lisnave - Cais 0 B", "Lisnave - Cais 0 A",
    "Lisnave - Doca 20", "Lisnave - Doca 21", "Lisnave - Doca 22",
    "Lisnave - Cais 1 B", "Lisnave - Cais 1 A",
    "Lisnave - Cais 2 B", "Lisnave - Cais 2 A",
    "Lisnave - Cais 3 B", "Lisnave - Cais 3 A",
    "Lisnave - Doca 31", "Lisnave - Doca 32", "Lisnave - Doca 33",
    "Teporset",
]
TERMINAL_OPTIONS = [
    "Secil", "Fundeadouro Norte", "Cais Palmeiras", "TMS 1", "TMS 2",
    "Autoeuropa", "Praias do Sado / Pirites Alentejanas",
    "SAPEC Sólidos", "SAPEC Líquidos", "ALSTOM",
    "PAN Tróia", "Fundeadouro Sul / Tróia",
    "Tanquisado", "Eco-Oil", "Lisnave", "Teporset",
]
VESSEL_TYPE_OPTIONS = get_vessel_type_options()
CONSTRAINT_OPTIONS = get_constraint_options()

PROVIDER_API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def _env_flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _resolve_provider_name(explicit_name: str, default: str = "gemini") -> str:
    provider_name = (explicit_name or "").strip().lower()
    if provider_name:
        return provider_name
    for candidate in ("openrouter", "gemini", "openai", "deepseek"):
        if os.getenv(PROVIDER_API_KEY_ENV[candidate], "").strip():
            return candidate
    return default


def _resolve_provider_api_key(provider_name: str) -> str:
    env_name = PROVIDER_API_KEY_ENV.get(provider_name, "LLM_API_KEY")
    return os.getenv(env_name, "").strip()


def _resolve_fallback_provider_name(primary_name: str, explicit_name: str = "") -> str:
    fallback_name = (explicit_name or "").strip().lower()
    if fallback_name and fallback_name != primary_name:
        return fallback_name

    preferred = ""
    if primary_name == "gemini":
        preferred = "openrouter"
    elif primary_name == "openrouter":
        preferred = "gemini"

    if preferred and os.getenv(PROVIDER_API_KEY_ENV.get(preferred, ""), "").strip():
        return preferred

    for candidate in ("gemini", "openrouter", "openai", "deepseek"):
        if candidate == primary_name:
            continue
        env_name = PROVIDER_API_KEY_ENV.get(candidate, "")
        if env_name and os.getenv(env_name, "").strip():
            return candidate
    return ""

# ---------------------------------------------------------------------------
# Service initialization
# ---------------------------------------------------------------------------

store = create_store(data_dir=DATA_DIR, knowledge_dir=KNOWLEDGE_DIR)
auth_service = create_auth_service(store)
index_store = create_index_store(data_dir=DATA_DIR)

_llm_provider_name = _resolve_provider_name(
    explicit_name=os.getenv("LLM_PROVIDER", ""),
    default="gemini",
)
_llm_api_key = _resolve_provider_api_key(_llm_provider_name)
_llm_provider = create_llm_provider(provider=_llm_provider_name, api_key=_llm_api_key)
_llm_fallback_provider_name = _resolve_fallback_provider_name(
    primary_name=_llm_provider_name,
    explicit_name=os.getenv("LLM_FALLBACK_PROVIDER", ""),
)
_llm_fallback_api_key = _resolve_provider_api_key(_llm_fallback_provider_name)
_llm_fallback_provider = (
    create_llm_provider(provider=_llm_fallback_provider_name, api_key=_llm_fallback_api_key)
    if _llm_fallback_provider_name
    else None
)

_embedding_local_enabled = _env_flag("EMBEDDING_LOCAL_ENABLED", default="1")
_embedding_provider = create_embedding_provider(enabled=_embedding_local_enabled)
_embedding_provider_name = _resolve_provider_name(
    explicit_name=os.getenv("EMBEDDING_PROVIDER", ""),
    default="gemini",
)
_embedding_api_key = _resolve_provider_api_key(_embedding_provider_name)
_embedding_api_provider = None
if not _embedding_provider:
    _embedding_api_provider = create_llm_provider(
        provider=_embedding_provider_name,
        api_key=_embedding_api_key,
    )

_default_gen_models = {
    "gemini": "gemini-2.5-flash",
    "openrouter": "openai/gpt-4.1-mini",
    "openai": "gpt-4.1-mini",
    "deepseek": "deepseek-chat",
}
_default_emb_models = {
    "gemini": "gemini-embedding-001",
    "openrouter": "openai/text-embedding-3-small",
    "openai": "text-embedding-3-small",
    "deepseek": "text-embedding-3-small",
}
_gen_model = os.getenv("LLM_MODEL", _default_gen_models.get(_llm_provider_name, "openai/gpt-4.1-mini"))
_gen_fallback_model = os.getenv(
    "LLM_FALLBACK_MODEL",
    _default_gen_models.get(_llm_fallback_provider_name, "") if _llm_fallback_provider_name else "",
)
_emb_model = os.getenv(
    "EMBEDDING_MODEL",
    _default_emb_models.get(_embedding_provider_name, "text-embedding-3-small"),
)

rag = SimpleRAGEngine(
    api_key=_llm_api_key,
    knowledge_dir=KNOWLEDGE_DIR,
    index_store=index_store,
    generation_model=_gen_model,
    generation_fallback_model=_gen_fallback_model,
    embedding_model=_emb_model,
    llm_provider=_llm_provider,
    generation_fallback_provider=_llm_fallback_provider,
    embedding_api_provider=_embedding_api_provider,
    embedding_provider=_embedding_provider,
)
tide_service = TideService(
    csv_path=os.getenv(
        "TIDE_CSV_PATH",
        os.path.join(BASE_DIR, "resources", "tides", "mares.2026.201.9_setubal_troia.csv"),
    )
)
weather_service = WeatherService(
    api_key=os.getenv("WEATHERAPI_KEY", ""),
    location=os.getenv("WEATHERAPI_LOCATION", "Setubal"),
    language="pt",
)
ais_service = create_ais_service(BASE_DIR)
wave_service = WaveService(
    endpoint=os.getenv(
        "WAVE_API_URL",
        "https://www.hidrografico.pt/hmapi/ondobuoystation/?buoyID=19",
    ),
    station_name=os.getenv("WAVE_STATION_NAME", "Sines"),
)
local_warning_service = LocalWarningService(
    endpoint=os.getenv(
        "LOCAL_WARNING_API_URL",
        "https://anavnetbackend.hidrografico.pt/api/v1/local-warnings?stateId=93&currentPage=1&entityId=27",
    ),
)

# ---------------------------------------------------------------------------
# Populate the services registry (used by helpers + blueprints)
# ---------------------------------------------------------------------------

services.store = store
services.auth_service = auth_service
services.rag = rag
services.tide_service = tide_service
services.weather_service = weather_service
services.ais_service = ais_service
services.wave_service = wave_service
services.local_warning_service = local_warning_service
services.index_store = index_store
services.BASE_DIR = BASE_DIR
services.DATA_DIR = DATA_DIR
services.KNOWLEDGE_DIR = KNOWLEDGE_DIR
services.BERTH_OPTIONS = BERTH_OPTIONS
services.TERMINAL_OPTIONS = TERMINAL_OPTIONS
services.VESSEL_TYPE_OPTIONS = VESSEL_TYPE_OPTIONS
services.CONSTRAINT_OPTIONS = CONSTRAINT_OPTIONS
services.reindex_thread = None
services.reindex_thread_lock = threading.Lock()


def _start_incremental_reindex_from_scheduler() -> None:
    start_reindex_job(force=False)


services.reindex_retry_scheduler = DeferredTaskScheduler(
    name="knowledge-reindex-auto-retry",
    callback=_start_incremental_reindex_from_scheduler,
)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "64")) * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV", "production") == "production"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=int(os.getenv("SESSION_IDLE_MINUTES", "45")))
app.config["SESSION_REFRESH_EACH_REQUEST"] = True


def render_chat_markdown(text: str) -> Markup:
    escaped = str(escape(text or ""))
    escaped = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"(?<!\*)\*(?=\S)(.+?)(?<=\S)\*(?!\*)", r"<em>\1</em>", escaped, flags=re.DOTALL)
    return Markup(escaped)


app.jinja_env.filters["chat_markdown"] = render_chat_markdown

if _embedding_provider:
    app.logger.info(
        "Embeddings locais activos: %s (%d dim)",
        _embedding_provider.model_name, _embedding_provider.dimensions,
    )
elif _embedding_api_provider and _embedding_api_provider.is_available:
    app.logger.info(
        "Embeddings via API activos: %s (%s)",
        _embedding_provider_name, _emb_model,
    )
else:
    app.logger.warning(
        "Embeddings indisponíveis: instala sentence-transformers para uso local "
        "ou configura o provider/API key de embeddings."
    )

# ---------------------------------------------------------------------------
# Register blueprints
# ---------------------------------------------------------------------------

from blueprints.auth import bp as auth_bp
from blueprints.dashboard import bp as dashboard_bp
from blueprints.port_calls import bp as port_calls_bp
from blueprints.admin import bp as admin_bp
from blueprints.chat import bp as chat_bp
from blueprints.api import bp as api_bp

init_csrf(app)


@app.before_request
def refresh_authenticated_session():
    if not session.get("username"):
        return None
    session.permanent = True
    session.modified = True
    return None

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(port_calls_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(api_bp)

# ---------------------------------------------------------------------------
# Global error handler & security headers
# ---------------------------------------------------------------------------

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(_exc):
    from flask import flash, redirect, request, url_for
    flash(
        "Ficheiro demasiado grande para este rascunho local. "
        f"Limite atual: {int(app.config['MAX_CONTENT_LENGTH'] / (1024 * 1024))} MB.",
        "error",
    )
    return redirect(request.referrer or url_for("dashboard_bp.dashboard")), 413


@app.after_request
def apply_security_headers(response):
    if session.get("username") or request.endpoint in {"auth.login", "auth.profile"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if os.getenv("FLASK_ENV", "production") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ---------------------------------------------------------------------------
# Context processor — shared template variables
# ---------------------------------------------------------------------------

@app.context_processor
def inject_globals():
    chatbot_conversation = None
    chatbot_messages = []
    chatbot_conversations = []
    username = session.get("username")
    if username:
        try:
            from flask import request as _req
            requested_conv_id = _req.args.get("conversation_id", "").strip() or None
            chatbot_conversation = store.ensure_conversation(username=username, conversation_id=requested_conv_id)
            chatbot_messages = store.list_messages(username, chatbot_conversation["id"])
            chatbot_conversations = store.list_conversations(username)
        except Exception:
            pass
    return {
        "current_user": username,
        "current_role": session.get("role"),
        "provider": rag.generation_provider_label,
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


# ---------------------------------------------------------------------------
# Startup tasks
# ---------------------------------------------------------------------------

def maybe_run_startup_migration() -> None:
    if getattr(store, "backend_name", "") != "postgres":
        services.startup_migration_status = {"status": "not_applicable", "reason": "backend principal não é postgres"}
        return
    if os.getenv("MIGRATE_LOCAL_DATA_ON_START", "1") != "1":
        services.startup_migration_status = {"status": "disabled", "reason": "MIGRATE_LOCAL_DATA_ON_START=0"}
        return
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        services.startup_migration_status = {"status": "disabled", "reason": "DATABASE_URL em falta"}
        return
    try:
        services.startup_migration_status = migrate_local_json_to_postgres(
            data_dir=DATA_DIR, knowledge_dir=KNOWLEDGE_DIR, database_url=database_url, force=False,
        )
    except Exception as exc:
        services.startup_migration_status = {"status": "error", "reason": str(exc)}
        app.logger.exception("Falha na migração automática inicial")


def maybe_seed_admin() -> None:
    admin_email = os.getenv("ADMIN_EMAIL", "admin@porto.pt").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD", "123456").strip()
    if not admin_email:
        return
    try:
        existing = store.get_user_profile(admin_email)
        if existing:
            if (existing.get("role") or "").lower() != "admin":
                store.set_user_role(admin_email, "admin")
                app.logger.info("[seed] Admin promovido: %s", admin_email)
            try:
                store.reset_user_password(admin_email, admin_password)
            except Exception:
                pass
            app.logger.info("[seed] Admin verificado: %s", admin_email)
            return
        store.create_user(
            username=admin_email, password=admin_password, role="admin",
            full_name="Administrador", organization="APSS",
            email=admin_email, phone="",
        )
        app.logger.info("[seed] Admin criado: %s", admin_email)
    except Exception as exc:
        app.logger.warning("[seed] Falha ao criar admin: %s", exc)


maybe_run_startup_migration()
maybe_seed_admin()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

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
