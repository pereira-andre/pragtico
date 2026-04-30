from __future__ import annotations

import os

from flask import session

from core import services
from core.knowledge_runtime import current_reindex_status_payload
from domain.database_runtime import get_database_runtime_status
from storage import PASSWORD_HASH_METHOD


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
            "internal": "Interno",
            "postgres": "PostgreSQL",
            "pgvector": "pgvector",
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
    embeddings_ready = bool(services.rag.client)
    weather_ready = bool(getattr(services, "weather_service", None) and services.weather_service.enabled)
    storage_backend = getattr(services.store, "backend_name", "unknown")
    storage_label = _backend_label(storage_backend)
    db_runtime_ok = bool(db_runtime)
    db_degraded = bool(db_runtime_error)

    storage_detail = (
        f"{db_runtime.get('database_name', '--')} · utilizador {db_runtime.get('database_user', '--')}"
        if db_runtime
        else db_runtime_error or "Runtime PostgreSQL indisponível"
    )
    storage_technical = (
        "pgvector ativo" if db_runtime and db_runtime.get("vector_installed")
        else "pgvector em falta" if db_runtime
        else "Sem ligação runtime PostgreSQL"
    )
    embedding_detail = (
        services.rag.embedding_model
        if embeddings_ready
        else f"Configurar {services.rag.embedding_api_key_hint}"
    )

    service_health = [
        _build_service_item(
            "Geração",
            ok=llm_ready,
            headline=services.rag.generation_provider_label if llm_ready else "Sem geração disponível",
            detail=services.rag.generation_model if llm_ready else services.rag.generation_unavailable_reason(),
            technical="cadeia de providers ativa",
        ),
        _build_service_item(
            "Embeddings",
            ok=embeddings_ready,
            headline=services.rag.embedding_provider_label if embeddings_ready else "Indisponíveis",
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
            ok=db_runtime_ok,
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
        alerts.append("Geração indisponível.")

    overall_state = "online"
    if alerts:
        overall_state = "degraded"
    if not llm_ready or not db_runtime_ok:
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
            "embeddings_api": embeddings_ready,
            "embedding_model": services.rag.embedding_model,
            "weather_ready": weather_ready,
            "wave_ready": wave_enabled,
            "warnings_ready": warning_enabled,
            "ais_ready": bool(ais_status.get("configured")),
            "database_url_ready": bool(database_url),
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

