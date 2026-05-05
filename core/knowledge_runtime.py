from __future__ import annotations

import logging
import threading
from datetime import datetime

from core import services
from core.reindex_scheduler import next_provider_quota_reset_utc

logger = logging.getLogger(__name__)


def safe_rebuild_index(force: bool = False) -> bool:
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
    with services.reindex_thread_lock:
        if services.reindex_thread and services.reindex_thread.is_alive():
            return False

        def worker():
            safe_rebuild_index(force=force)

        services.reindex_thread = threading.Thread(
            target=worker,
            name="knowledge-reindex",
            daemon=True,
        )
        services.rag.mark_reindex_pending()
        if services.reindex_retry_scheduler is not None:
            services.reindex_retry_scheduler.cancel()
        services.reindex_thread.start()
        return True


def sync_reindex_retry_schedule() -> None:
    if services.reindex_retry_scheduler is None or services.rag.has_active_reindex_worker():
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
    status_payload = services.rag.get_reindex_status()
    workflow_progress_pct = float(status_payload.get("progress_pct") or 0.0)
    try:
        sync_summary = services.rag.get_sync_status_summary()
    except Exception as exc:
        logger.exception("Falha ao gerar resumo de sincronização do índice")
        services.rag.last_index_error = str(exc)
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
    retry_status = services.reindex_retry_scheduler.status() if services.reindex_retry_scheduler is not None else {}
    with services.reindex_thread_lock:
        thread_alive = bool(services.reindex_thread and services.reindex_thread.is_alive())
    worker_active = thread_alive or services.rag.has_active_reindex_worker()
    status_payload = {
        **status_payload,
        **sync_summary,
        "embedding_provider": services.rag.embedding_provider_label if services.rag.client else "indisponivel",
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
            else "Pesquisa semântica em modo lexical: motor de embeddings indisponível."
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
    stale_running = bool(
        updated_dt is not None
        and (datetime.now(updated_dt.tzinfo) - updated_dt).total_seconds() >= 180
    )
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
