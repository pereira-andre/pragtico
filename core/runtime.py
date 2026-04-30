from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from core import services
from core.reindex_scheduler import DeferredTaskScheduler
from domain.berth_layout import BERTH_OPTIONS, TERMINAL_OPTIONS
from integrations.ais_service import create_ais_service
from integrations.auth_service import create_auth_service
from integrations.llm_provider import BaseLLMProvider, create_llm_provider
from integrations.local_warning_service import LocalWarningService
from integrations.rag_engine import SimpleRAGEngine
from integrations.tide_service import TideService
from integrations.vector_store import create_index_store
from integrations.weather_service import WeatherService
from integrations.wave_service import WaveService
from integrations.whatsapp_cloud import WhatsAppCloudService
from storage import create_store, get_constraint_options, get_vessel_type_options

logger = logging.getLogger(__name__)

PROVIDER_API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

DEFAULT_GENERATION_MODELS = {
    "gemini": "gemini-2.5-flash",
    "openrouter": "openrouter/free",
    "openai": "o4-mini",
    "deepseek": "deepseek-chat",
}

DEFAULT_EMBEDDING_MODELS = {
    "gemini": "gemini-embedding-001",
    "openrouter": "nvidia/llama-nemotron-embed-vl-1b-v2:free",
    "openai": "text-embedding-3-small",
    "deepseek": "text-embedding-3-small",
}


@dataclass
class Runtime:
    base_dir: str
    data_dir: str
    knowledge_dir: str
    store: object
    auth_service: object
    index_store: object
    rag: SimpleRAGEngine
    tide_service: TideService
    weather_service: WeatherService
    ais_service: object
    wave_service: WaveService
    local_warning_service: LocalWarningService
    whatsapp_service: WhatsAppCloudService
    berth_options: list
    terminal_options: list
    vessel_type_options: list
    constraint_options: list
    embedding_provider_name: str
    embedding_model: str
    embedding_api_provider: BaseLLMProvider
    wave_refresh_interval_seconds: int
    local_warning_refresh_interval_seconds: int


def env_flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def env_int(name: str, default: int) -> int:
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

    preferred = "openrouter" if primary_name == "gemini" else "gemini" if primary_name == "openrouter" else ""
    if preferred and os.getenv(PROVIDER_API_KEY_ENV.get(preferred, ""), "").strip():
        return preferred

    for candidate in ("gemini", "openrouter", "openai", "deepseek"):
        if candidate == primary_name:
            continue
        env_name = PROVIDER_API_KEY_ENV.get(candidate, "")
        if env_name and os.getenv(env_name, "").strip():
            return candidate
    return ""


def _build_rag(knowledge_dir: str, index_store: object) -> tuple[SimpleRAGEngine, str, str, BaseLLMProvider]:
    generation_provider_name = _resolve_provider_name(os.getenv("LLM_PROVIDER", ""), default="gemini")
    generation_api_key = _resolve_provider_api_key(generation_provider_name)
    generation_provider = create_llm_provider(provider=generation_provider_name, api_key=generation_api_key)

    fallback_provider_name = _resolve_fallback_provider_name(
        primary_name=generation_provider_name,
        explicit_name=os.getenv("LLM_FALLBACK_PROVIDER", ""),
    )
    fallback_api_key = _resolve_provider_api_key(fallback_provider_name)
    fallback_provider = (
        create_llm_provider(provider=fallback_provider_name, api_key=fallback_api_key)
        if fallback_provider_name
        else None
    )

    embedding_provider_name = _resolve_provider_name(os.getenv("EMBEDDING_PROVIDER", ""), default="gemini")
    embedding_provider = create_llm_provider(
        provider=embedding_provider_name,
        api_key=_resolve_provider_api_key(embedding_provider_name),
    )
    generation_model = os.getenv(
        "LLM_MODEL",
        DEFAULT_GENERATION_MODELS.get(generation_provider_name, "openrouter/free"),
    )
    fallback_model = os.getenv(
        "LLM_FALLBACK_MODEL",
        DEFAULT_GENERATION_MODELS.get(fallback_provider_name, "") if fallback_provider_name else "",
    )
    embedding_model = os.getenv(
        "EMBEDDING_MODEL",
        DEFAULT_EMBEDDING_MODELS.get(embedding_provider_name, "text-embedding-3-small"),
    )

    rag = SimpleRAGEngine(
        api_key=generation_api_key,
        knowledge_dir=knowledge_dir,
        index_store=index_store,
        generation_model=generation_model,
        generation_fallback_model=fallback_model,
        embedding_model=embedding_model,
        llm_provider=generation_provider,
        generation_fallback_provider=fallback_provider,
        embedding_api_provider=embedding_provider,
    )
    return rag, embedding_provider_name, embedding_model, embedding_provider


def initialize_runtime() -> Runtime:
    base_dir = str(Path(__file__).resolve().parents[1])
    data_dir = os.path.join(base_dir, "data")
    knowledge_dir = os.path.join(base_dir, "knowledge")

    store = create_store(data_dir=data_dir, knowledge_dir=knowledge_dir)
    auth_service = create_auth_service(store)
    index_store = create_index_store(data_dir=data_dir)
    rag, embedding_provider_name, embedding_model, embedding_provider = _build_rag(
        knowledge_dir=knowledge_dir,
        index_store=index_store,
    )

    wave_refresh_seconds = env_int("WAVE_REFRESH_INTERVAL_SECONDS", 3600)
    local_warning_refresh_seconds = env_int("LOCAL_WARNING_REFRESH_INTERVAL_SECONDS", 3600)
    tide_service = TideService(
        csv_path=os.getenv(
            "TIDE_CSV_PATH",
            os.path.join(base_dir, "resources", "tides", "mares.2026.201.9_setubal_troia.csv"),
        )
    )
    weather_service = WeatherService(
        api_key=os.getenv("WEATHERAPI_KEY", ""),
        location=os.getenv("WEATHERAPI_LOCATION", "Setubal"),
        language="pt",
    )
    wave_service = WaveService(
        endpoint=os.getenv(
            "WAVE_API_URL",
            "https://www.hidrografico.pt/hmapi/ondobuoystation/?buoyID=19",
        ),
        station_name=os.getenv("WAVE_STATION_NAME", "Sines"),
        cache_ttl_seconds=env_int("WAVE_CACHE_TTL_SECONDS", wave_refresh_seconds),
        failure_backoff_seconds=env_int(
            "WAVE_FAILURE_BACKOFF_SECONDS",
            env_int("WAVE_CACHE_TTL_SECONDS", wave_refresh_seconds),
        ),
        snapshot_path=os.path.join(data_dir, "wave_conditions_cache.json"),
    )
    local_warning_service = LocalWarningService(
        endpoint=os.getenv(
            "LOCAL_WARNING_API_URL",
            "https://anavnetbackend.hidrografico.pt/api/v1/local-warnings?stateId=93&currentPage=1&entityId=27",
        ),
        cache_ttl_seconds=env_int("LOCAL_WARNING_CACHE_TTL_SECONDS", local_warning_refresh_seconds),
        failure_backoff_seconds=env_int(
            "LOCAL_WARNING_FAILURE_BACKOFF_SECONDS",
            env_int("LOCAL_WARNING_CACHE_TTL_SECONDS", local_warning_refresh_seconds),
        ),
        snapshot_path=os.path.join(data_dir, "local_warnings_cache.json"),
        allow_insecure_ssl_fallback=env_flag("LOCAL_WARNING_ALLOW_INSECURE_SSL_FALLBACK", default="1"),
        store=store,
    )

    runtime = Runtime(
        base_dir=base_dir,
        data_dir=data_dir,
        knowledge_dir=knowledge_dir,
        store=store,
        auth_service=auth_service,
        index_store=index_store,
        rag=rag,
        tide_service=tide_service,
        weather_service=weather_service,
        ais_service=create_ais_service(base_dir),
        wave_service=wave_service,
        local_warning_service=local_warning_service,
        whatsapp_service=WhatsAppCloudService.from_env(),
        berth_options=BERTH_OPTIONS,
        terminal_options=TERMINAL_OPTIONS,
        vessel_type_options=get_vessel_type_options(),
        constraint_options=get_constraint_options(),
        embedding_provider_name=embedding_provider_name,
        embedding_model=embedding_model,
        embedding_api_provider=embedding_provider,
        wave_refresh_interval_seconds=wave_refresh_seconds,
        local_warning_refresh_interval_seconds=local_warning_refresh_seconds,
    )
    populate_services(runtime)
    return runtime


def populate_services(runtime: Runtime) -> None:
    services.store = runtime.store
    services.auth_service = runtime.auth_service
    services.rag = runtime.rag
    services.tide_service = runtime.tide_service
    services.weather_service = runtime.weather_service
    services.ais_service = runtime.ais_service
    services.wave_service = runtime.wave_service
    services.local_warning_service = runtime.local_warning_service
    services.whatsapp_service = runtime.whatsapp_service
    services.index_store = runtime.index_store
    services.BASE_DIR = runtime.base_dir
    services.DATA_DIR = runtime.data_dir
    services.KNOWLEDGE_DIR = runtime.knowledge_dir
    services.BERTH_OPTIONS = runtime.berth_options
    services.TERMINAL_OPTIONS = runtime.terminal_options
    services.VESSEL_TYPE_OPTIONS = runtime.vessel_type_options
    services.CONSTRAINT_OPTIONS = runtime.constraint_options
    services.reindex_thread = None
    services.reindex_thread_lock = threading.Lock()

    from core.knowledge_runtime import start_reindex_job

    services.reindex_retry_scheduler = DeferredTaskScheduler(
        name="knowledge-reindex-auto-retry",
        callback=lambda: start_reindex_job(force=False),
    )


def log_embedding_status(runtime: Runtime, app_logger: logging.Logger) -> None:
    if runtime.embedding_api_provider and runtime.embedding_api_provider.is_available:
        app_logger.info(
            "Embeddings via API activos: %s (%s)",
            runtime.embedding_provider_name,
            runtime.embedding_model,
        )
    else:
        app_logger.warning("Embeddings indisponíveis: configura o provider/API key de embeddings.")


def seed_admin(store: object, app_logger: logging.Logger) -> None:
    admin_email = os.getenv("ADMIN_EMAIL", "admin@porto.pt").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD", "123456").strip()
    if not admin_email:
        return
    try:
        existing = store.get_user_profile(admin_email)
        if existing:
            if (existing.get("role") or "").lower() != "admin":
                store.set_user_role(admin_email, "admin")
                app_logger.info("[seed] Admin promovido: %s", admin_email)
            try:
                store.reset_user_password(admin_email, admin_password)
            except Exception:
                pass
            app_logger.info("[seed] Admin verificado: %s", admin_email)
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
        app_logger.info("[seed] Admin criado: %s", admin_email)
    except Exception as exc:
        app_logger.warning("[seed] Falha ao criar admin: %s", exc)
