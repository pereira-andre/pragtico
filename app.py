"""PRAGtico — Flask application factory with Blueprint registration."""

import logging
import os
import threading
import re
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
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
from integrations.whatsapp_cloud import WhatsAppCloudService
from domain.migration_service import migrate_local_json_to_postgres
from domain.berth_layout import BERTH_OPTIONS, TERMINAL_OPTIONS, dropdown_berth_options
from integrations.rag_engine import SimpleRAGEngine
from core.live_data_refresh import PeriodicTaskScheduler
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


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        return int(raw_value)
    except ValueError:
        return int(default)


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
    "openrouter": "openrouter/free",
    "openai": "o4-mini",
    "deepseek": "deepseek-chat",
}
_default_emb_models = {
    "gemini": "gemini-embedding-001",
    "openrouter": "nvidia/llama-nemotron-embed-vl-1b-v2:free",
    "openai": "text-embedding-3-small",
    "deepseek": "text-embedding-3-small",
}
_gen_model = os.getenv("LLM_MODEL", _default_gen_models.get(_llm_provider_name, "openrouter/free"))
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
_wave_refresh_interval_seconds = _env_int("WAVE_REFRESH_INTERVAL_SECONDS", 3600)
wave_service = WaveService(
    endpoint=os.getenv(
        "WAVE_API_URL",
        "https://www.hidrografico.pt/hmapi/ondobuoystation/?buoyID=19",
    ),
    station_name=os.getenv("WAVE_STATION_NAME", "Sines"),
    cache_ttl_seconds=_env_int("WAVE_CACHE_TTL_SECONDS", _wave_refresh_interval_seconds),
    failure_backoff_seconds=_env_int(
        "WAVE_FAILURE_BACKOFF_SECONDS",
        _env_int("WAVE_CACHE_TTL_SECONDS", _wave_refresh_interval_seconds),
    ),
    snapshot_path=os.path.join(DATA_DIR, "wave_conditions_cache.json"),
)
_local_warning_refresh_interval_seconds = _env_int("LOCAL_WARNING_REFRESH_INTERVAL_SECONDS", 3600)
local_warning_service = LocalWarningService(
    endpoint=os.getenv(
        "LOCAL_WARNING_API_URL",
        "https://anavnetbackend.hidrografico.pt/api/v1/local-warnings?stateId=93&currentPage=1&entityId=27",
    ),
    cache_ttl_seconds=_env_int("LOCAL_WARNING_CACHE_TTL_SECONDS", _local_warning_refresh_interval_seconds),
    failure_backoff_seconds=_env_int(
        "LOCAL_WARNING_FAILURE_BACKOFF_SECONDS",
        _env_int("LOCAL_WARNING_CACHE_TTL_SECONDS", _local_warning_refresh_interval_seconds),
    ),
    snapshot_path=os.path.join(DATA_DIR, "local_warnings_cache.json"),
    allow_insecure_ssl_fallback=_env_flag("LOCAL_WARNING_ALLOW_INSECURE_SSL_FALLBACK", default="1"),
    store=store,
)
whatsapp_service = WhatsAppCloudService.from_env()

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
services.whatsapp_service = whatsapp_service
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
app.config["MANUAL_KNOWLEDGE_AUTHORING_ENABLED"] = _env_flag(
    "MANUAL_KNOWLEDGE_AUTHORING_ENABLED",
    default="0",
)

_external_refresh_lock = threading.Lock()
_external_refresh_started = False


def _refresh_wave_snapshot() -> None:
    if not getattr(services, "wave_service", None) or not services.wave_service.enabled:
        return
    try:
        services.wave_service.probe_current_conditions()
    except Exception as exc:
        app.logger.warning("[external-refresh] Falha ao atualizar ondulação: %s", exc)


def _refresh_local_warning_snapshot() -> None:
    if not getattr(services, "local_warning_service", None) or not services.local_warning_service.enabled:
        return
    try:
        services.local_warning_service.probe_warnings()
    except Exception as exc:
        app.logger.warning("[external-refresh] Falha ao atualizar avisos locais: %s", exc)


services.wave_refresh_scheduler = PeriodicTaskScheduler(
    name="wave-refresh-hourly",
    callback=_refresh_wave_snapshot,
    interval_seconds=_wave_refresh_interval_seconds,
    run_immediately=True,
)
services.local_warning_refresh_scheduler = PeriodicTaskScheduler(
    name="local-warning-refresh-hourly",
    callback=_refresh_local_warning_snapshot,
    interval_seconds=_local_warning_refresh_interval_seconds,
    run_immediately=True,
)


def _ensure_external_refresh_started() -> None:
    global _external_refresh_started

    if app.config.get("TESTING"):
        return
    if not _env_flag("EXTERNAL_DATA_REFRESH_ENABLED", default="1"):
        return

    with _external_refresh_lock:
        if _external_refresh_started:
            return

        started_labels = []
        if services.wave_refresh_scheduler and services.wave_refresh_scheduler.start():
            started_labels.append(f"ondulação/{_wave_refresh_interval_seconds}s")
        if services.local_warning_refresh_scheduler and services.local_warning_refresh_scheduler.start():
            started_labels.append(f"avisos/{_local_warning_refresh_interval_seconds}s")
        _external_refresh_started = True

    if started_labels:
        app.logger.info(
            "Refresh periódico de dados externos ativo: %s",
            ", ".join(started_labels),
        )


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
from blueprints.whatsapp import bp as whatsapp_bp

init_csrf(app)


@app.before_request
def refresh_authenticated_session():
    _ensure_external_refresh_started()
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
app.register_blueprint(whatsapp_bp)

# ---------------------------------------------------------------------------
# Global error handler & security headers
# ---------------------------------------------------------------------------

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(_exc):
    from flask import flash, redirect, request, url_for
    flash(
        "#ERR-8080 Ficheiro demasiado grande. "
        f"Limite atual: {int(app.config['MAX_CONTENT_LENGTH'] / (1024 * 1024))} MB.",
        "error",
    )
    return redirect(request.referrer or url_for("dashboard_bp.dashboard")), 413


def _wants_json():
    return (
        request.path.startswith("/api/")
        or request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") in {"fetch", "XMLHttpRequest"}
    )


@app.errorhandler(403)
def handle_forbidden(_exc):
    if _wants_json():
        return jsonify({"error": "#ERR-2020 Pedido inválido. Recarrega a página e tenta novamente.", "error_code": 2020, "error_ref": "#ERR-2020"}), 403
    return render_template("error.html", error_ref="#ERR-2020", error_title="Acesso negado", error_message="Pedido inválido ou sem permissão. Recarrega a página e tenta novamente."), 403


@app.errorhandler(404)
def handle_not_found(_exc):
    if _wants_json():
        return jsonify({"error": "Recurso não encontrado.", "error_code": 404}), 404
    return render_template("error.html", error_ref="404", error_title="Página não encontrada", error_message="O recurso pedido não existe ou foi removido."), 404


@app.errorhandler(429)
def handle_rate_limited(_exc):
    if _wants_json():
        return jsonify({"error": "#ERR-2021 Demasiados pedidos. Aguarda e tenta novamente.", "error_code": 2021, "error_ref": "#ERR-2021"}), 429
    return render_template("error.html", error_ref="#ERR-2021", error_title="Demasiados pedidos", error_message="Fizeste demasiados pedidos em pouco tempo. Aguarda uns segundos e tenta novamente."), 429


@app.errorhandler(500)
def handle_internal_error(_exc):
    if _wants_json():
        return jsonify({"error": "#ERR-9000 Erro inesperado.", "error_code": 9000, "error_ref": "#ERR-9000"}), 500
    return render_template("error.html", error_ref="#ERR-9000", error_title="Erro interno", error_message="Ocorreu um erro inesperado. Contacta o suporte com este código."), 500


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
        "berth_options": dropdown_berth_options(BERTH_OPTIONS),
        "terminal_options": TERMINAL_OPTIONS,
        "vessel_type_options": VESSEL_TYPE_OPTIONS,
        "constraint_options": CONSTRAINT_OPTIONS,
        "current_profile": current_user_profile(),
        "chatbot_conversation": chatbot_conversation,
        "chatbot_messages": chatbot_messages,
        "chatbot_conversations": chatbot_conversations,
        "chatbot_model": rag.generation_model,
        "manual_knowledge_authoring_enabled": app.config.get("MANUAL_KNOWLEDGE_AUTHORING_ENABLED", False),
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
    _ensure_external_refresh_started()
    refresh_knowledge_state(
        force_reindex=False,
        rebuild_index=os.getenv("RAG_REINDEX_ON_START", "0") == "1",
    )
    app.run(
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
    )
