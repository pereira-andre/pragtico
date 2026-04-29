from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List

from domain.knowledge_chunking import chunk_text_by_structure, structured_chunk_document
from domain.document_processing import extract_text_from_path
from domain.port_entities import detect_port_entities, entity_names_from_matches, specific_entities
from integrations.llm_provider import BaseLLMProvider, create_llm_provider
from core.reindex_scheduler import PACIFIC_TZ, next_provider_quota_reset_utc
from integrations.vector_store import BaseIndexStore


INDEX_FORMAT_VERSION = "structured_chunks_v1"


def chunk_text(text: str, chunk_size: int = 700, overlap: int = 120) -> List[str]:
    chunks = chunk_text_by_structure(text, chunk_size=chunk_size, overlap=overlap)
    if chunks:
        return chunks

    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []

    fallback_chunks = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + chunk_size)
        fallback_chunks.append(clean[start:end])
        if end == len(clean):
            break
        start = max(end - overlap, 0)
    return fallback_chunks


def lexical_score(query: str, text: str) -> float:
    query_tokens = set(re.findall(r"\w+", query.lower()))
    text_tokens = set(re.findall(r"\w+", text.lower()))
    if not query_tokens or not text_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    return len(overlap) / len(query_tokens)


def _looks_like_tug_query(question: str) -> bool:
    clean = _normalize_whitespace(question).lower()
    return any(token in clean for token in ("rebocador", "rebocadores", "reboque", "reboques"))


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_echo_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    normalized = re.sub(r"^(?:pergunta|questao)\s*:\s*", "", normalized)
    normalized = normalized.strip(" \t\n\r\"'“”‘’[]()")
    return re.sub(r"[^\w]+", " ", normalized).strip()


def _strip_leading_question_echo(question: str, answer: str) -> str:
    clean_answer = str(answer or "").strip()
    question_key = _normalize_echo_text(question)
    if not clean_answer or not question_key:
        return clean_answer

    for _attempt in range(3):
        lines = clean_answer.splitlines()
        if not lines:
            break
        first_line = lines[0].strip()
        if _normalize_echo_text(first_line) == question_key:
            clean_answer = "\n".join(lines[1:]).lstrip()
            continue
        candidate_line = re.sub(r"^(?:pergunta|questao)\s*:\s*", "", first_line, flags=re.IGNORECASE).strip()
        if "?" in candidate_line:
            possible_question, remainder = candidate_line.split("?", 1)
            if _normalize_echo_text(possible_question) == question_key:
                next_lines = []
                clean_remainder = remainder.lstrip(" :-")
                if clean_remainder:
                    next_lines.append(clean_remainder)
                next_lines.extend(lines[1:])
                clean_answer = "\n".join(next_lines).lstrip()
                continue
            break
        break

    return clean_answer.strip()


def _format_plan_block(plan: Dict | None) -> str:
    if not plan:
        return "Sem plano estruturado."
    parts = [
        f"intent={plan.get('primary_intent') or '--'}",
        f"live_facets={', '.join(plan.get('live_facets') or []) or '--'}",
        f"weather_mode={plan.get('weather_mode') or '--'}",
        f"requires_live_reasoning={bool(plan.get('requires_live_reasoning'))}",
        f"requires_llm_synthesis={bool(plan.get('requires_llm_synthesis'))}",
        f"needs_history_state={bool(plan.get('needs_history_state'))}",
        f"needs_answer_critic={bool(plan.get('needs_answer_critic'))}",
    ]
    return " | ".join(parts)


class SimpleRAGEngine:
    def __init__(
        self,
        api_key: str,
        knowledge_dir: str,
        index_store: BaseIndexStore,
        generation_model: str,
        embedding_model: str,
        generation_fallback_model: str = "",
        llm_provider: BaseLLMProvider | None = None,
        generation_fallback_provider: BaseLLMProvider | None = None,
        embedding_api_provider: BaseLLMProvider | None = None,
    ) -> None:
        self.api_key = api_key
        self.knowledge_dir = knowledge_dir
        self.index_store = index_store
        self.generation_model = generation_model
        self.generation_fallback_model = generation_fallback_model
        self.embedding_model = embedding_model

        # LLM provider: use injected provider, or create from environment/api_key
        if llm_provider is not None:
            self.provider = llm_provider
        else:
            self.provider = create_llm_provider(api_key=api_key if api_key else None)
        self.generation_fallback_provider = generation_fallback_provider

        # Embeddings are generated through the configured API provider.
        self.embedding_api_provider = embedding_api_provider or self.provider
        self._use_local_embeddings = False

        self._generation_candidates = self._build_provider_candidates(
            (self.provider, self.generation_model),
            (self.generation_fallback_provider, self.generation_fallback_model),
        )
        self._embedding_api_candidates = self._build_provider_candidates(
            (self.embedding_api_provider, self.embedding_model),
        )

        # Backward compatibility: self.client represents the embedding-capable API provider.
        self.client = (
            next(
                (provider for provider, _model in self._embedding_api_candidates if provider.is_available),
                None,
            )
            if self._embedding_api_candidates
            else None
        )
        self.provider_name = self.provider.provider_name
        self.generation_provider_label = self._candidate_chain_label(self._generation_candidates)
        self.embedding_provider_name = self.embedding_api_provider.provider_name
        self.embedding_provider_label = self._provider_label(self.embedding_provider_name)
        self.embedding_api_key_hint = self._provider_api_key_hint(self.embedding_provider_name)
        if self._embedding_api_candidates:
            self.embedding_provider_label = self._candidate_chain_label(self._embedding_api_candidates)

        self.allowed_extensions = {".md", ".txt", ".pdf", ".docx", ".csv"}
        self.last_index_error = ""
        self.embedding_batch_size = max(int(os.getenv("EMBEDDING_BATCH_SIZE", "32")), 1)
        self.embedding_requests_per_minute = max(
            int(os.getenv("EMBEDDING_REQUESTS_PER_MINUTE", "90")), 0
        )
        self.embedding_requests_per_day = max(int(os.getenv("EMBEDDING_REQUESTS_PER_DAY", "900")), 0)
        self.embedding_max_retries = max(int(os.getenv("EMBEDDING_MAX_RETRIES", "60")), 1)
        self.embedding_retry_window_seconds = max(
            float(os.getenv("EMBEDDING_RETRY_WINDOW_SECONDS", "45")), 0.0
        )
        self.embedding_max_retry_delay_seconds = max(
            float(os.getenv("EMBEDDING_MAX_RETRY_DELAY_SECONDS", "12")), 0.0
        )
        self.embedding_retry_padding_seconds = max(
            float(os.getenv("EMBEDDING_RETRY_PADDING_SECONDS", "2")), 0.0
        )
        self._reindex_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._embedding_rate_lock = threading.Lock()
        self._embedding_request_timestamps: deque[float] = deque()
        self._embedding_request_day = self._current_pacific_date()
        self._embedding_request_count_today = 0
        self._embedding_quota_blocked_until = None
        self._embedding_quota_block_reason = ""
        self._embedding_quota_provider_name = self.embedding_provider_name
        self._reindex_started_monotonic: float | None = None
        self._reindex_status = self._build_initial_reindex_status()

    @staticmethod
    def _provider_label(provider_name: str) -> str:
        normalized = (provider_name or "").strip().lower()
        label_map = {
            "gemini": "Gemini",
            "openrouter": "OpenRouter",
            "openai": "OpenAI",
            "deepseek": "DeepSeek",
            "openai_compatible": "API",
            "base": "API",
        }
        return label_map.get(normalized, normalized.replace("_", " ").title() or "API")

    @staticmethod
    def _provider_api_key_hint(provider_name: str) -> str:
        normalized = (provider_name or "").strip().lower()
        hint_map = {
            "gemini": "GEMINI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        return hint_map.get(normalized, "LLM_API_KEY")

    @staticmethod
    def _build_provider_candidates(*pairs) -> List[tuple[BaseLLMProvider, str]]:
        candidates: List[tuple[BaseLLMProvider, str]] = []
        seen = set()
        for provider, model in pairs:
            if provider is None or not model:
                continue
            key = (provider.provider_name, model)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((provider, model))
        return candidates

    def _candidate_chain_label(self, candidates: List[tuple[BaseLLMProvider, str]]) -> str:
        labels = []
        seen = set()
        for provider, _model in candidates:
            label = self._provider_label(getattr(provider, "provider_name", ""))
            if label in seen:
                continue
            seen.add(label)
            labels.append(label)
        return " -> ".join(labels) if labels else "API"

    def can_generate(self) -> bool:
        return any(provider.is_available for provider, _model in self._generation_candidates)

    def generation_unavailable_reason(self) -> str:
        reasons = [
            getattr(provider, "unavailable_reason", "").strip()
            for provider, _model in self._generation_candidates
            if getattr(provider, "unavailable_reason", "").strip()
        ]
        if reasons:
            return " | ".join(dict.fromkeys(reasons))
        return "Provider de geração indisponível."

    def _has_embedding_payload(self, embedding) -> bool:
        if embedding is None:
            return False
        try:
            if isinstance(embedding, dict):
                return len(embedding.get("values", [])) > 0
            if isinstance(embedding, (list, tuple)):
                return len(embedding) > 0
            if hasattr(embedding, "tolist"):
                return len(embedding.tolist()) > 0
            if hasattr(embedding, "embedding"):
                return len(getattr(embedding, "embedding", [])) > 0
            return True
        except Exception:
            return True

    def _count_embedded_chunks(self, chunks: List[Dict]) -> int:
        total = 0
        for item in chunks:
            if self._has_embedding_payload(item.get("embedding")):
                total += 1
        return total

    def _manifest_diff(self, manifest: Dict, previous_manifest: Dict | None) -> Dict:
        previous = previous_manifest or {}
        current_names = set(manifest)
        previous_names = set(previous)
        added = current_names - previous_names
        removed = previous_names - current_names
        common = current_names & previous_names
        changed = {name for name in common if manifest.get(name) != previous.get(name)}
        unchanged = common - changed
        return {
            "new_documents": len(added),
            "changed_documents": len(changed),
            "removed_documents": len(removed),
            "unchanged_documents": len(unchanged),
        }

    def _set_ready_status(self, manifest: Dict, chunks: List[Dict], diff: Dict) -> None:
        self._set_reindex_status(
            state="completed",
            phase="up_to_date",
            message="Índice já sincronizado com a pasta knowledge.",
            progress_pct=100.0,
            processed_documents=len(manifest),
            total_documents=len(manifest),
            total_chunks=len(chunks),
            embedded_chunks=self._count_embedded_chunks(chunks),
            error=self.last_index_error,
            **diff,
        )

    def _build_initial_reindex_status(self) -> Dict:
        return {
            "state": "idle",
            "phase": "idle",
            "message": "Índice pronto.",
            "progress_pct": 0.0,
            "started_at": None,
            "updated_at": None,
            "finished_at": None,
            "elapsed_seconds": 0,
            "eta_seconds": None,
            "total_documents": 0,
            "processed_documents": 0,
            "total_chunks": 0,
            "embedded_chunks": 0,
            "new_documents": 0,
            "changed_documents": 0,
            "removed_documents": 0,
            "unchanged_documents": 0,
            "knowledge_documents": 0,
            "indexed_documents": 0,
            "missing_embedding_chunks": 0,
            "documents_with_missing_embeddings": 0,
            "pending_documents_total": 0,
            "sync_summary": "",
            "pending_summary": "",
            "pending_documents_preview": [],
            "error": "",
        }

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _set_reindex_status(self, **updates) -> None:
        with self._status_lock:
            self._reindex_status.update(updates)
            self._reindex_status["updated_at"] = self._timestamp()
            elapsed_seconds = 0
            if self._reindex_started_monotonic is not None:
                elapsed_seconds = max(int(time.monotonic() - self._reindex_started_monotonic), 0)
            self._reindex_status["elapsed_seconds"] = elapsed_seconds

            progress_pct = float(self._reindex_status.get("progress_pct") or 0.0)
            if self._reindex_status.get("state") == "running" and progress_pct > 0:
                remaining_pct = max(100.0 - progress_pct, 0.0)
                eta_seconds = int((elapsed_seconds / progress_pct) * remaining_pct) if elapsed_seconds else 0
                self._reindex_status["eta_seconds"] = max(eta_seconds, 0)
            elif self._reindex_status.get("state") == "running":
                self._reindex_status["eta_seconds"] = None
            else:
                self._reindex_status["eta_seconds"] = None

            if self._reindex_status.get("state") in {"completed", "error"}:
                self._reindex_status["finished_at"] = self._timestamp()

    def get_reindex_status(self) -> Dict:
        with self._status_lock:
            return dict(self._reindex_status)

    def is_reindex_running(self) -> bool:
        with self._status_lock:
            return self._reindex_status.get("state") == "running"

    def has_active_reindex_worker(self) -> bool:
        return self._reindex_lock.locked()

    def mark_reindex_pending(self) -> None:
        self.last_index_error = ""
        self._set_reindex_status(
            state="running",
            phase="queued",
            message="A iniciar reindexação...",
            progress_pct=1.0,
            started_at=self._timestamp(),
            finished_at=None,
            eta_seconds=None,
            error="",
        )

    def _document_manifest(self) -> Dict:
        manifest = {}
        if not os.path.isdir(self.knowledge_dir):
            return manifest
        for name in sorted(os.listdir(self.knowledge_dir)):
            if os.path.splitext(name)[1].lower() not in self.allowed_extensions:
                continue
            path = os.path.join(self.knowledge_dir, name)
            stat = os.stat(path)
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            manifest[name] = {
                "mtime_ns": int(stat.st_mtime_ns),
                "size": stat.st_size,
                "sha256": digest.hexdigest(),
                "index_format": INDEX_FORMAT_VERSION,
            }
        return manifest

    def _extract_vector(self, embedding) -> List[float]:
        if isinstance(embedding, dict):
            return list(embedding.get("values", []))
        if hasattr(embedding, "values"):
            return list(embedding.values)
        return list(getattr(embedding, "embedding", []))

    def _embed_with_client(self, client, batch: List[str], model: str) -> List[List[float]]:
        if hasattr(client, "embed"):
            embed_result = client.embed(
                texts=batch,
                model=model,
            )
            return embed_result.vectors

        models_api = getattr(client, "models", None)
        if models_api and hasattr(models_api, "embed_content"):
            embed_result = models_api.embed_content(
                model=model,
                contents=batch,
            )
            return [self._extract_vector(item) for item in getattr(embed_result, "embeddings", [])]

        raise RuntimeError("O cliente de embeddings configurado não suporta embeddings.")

    def _api_embedding_vectors(self, batch: List[str]) -> List[List[float]]:
        if not self._embedding_api_candidates and not self.client:
            raise RuntimeError(
                f"Embeddings não disponíveis: configura {self.embedding_api_key_hint}."
            )

        errors: List[str] = []
        attempted_clients = set()
        if self.client is not None and all(id(provider) != id(self.client) for provider, _model in self._embedding_api_candidates):
            try:
                return self._embed_with_client(self.client, batch, self.embedding_model)
            except Exception as exc:
                errors.append(str(exc))

        for provider, model in self._embedding_api_candidates:
            attempted_clients.add(id(provider))
            label = self._provider_label(getattr(provider, "provider_name", ""))
            if not provider.is_available:
                reason = getattr(provider, "unavailable_reason", "").strip()
                if reason:
                    errors.append(f"{label}: {reason}")
                continue
            try:
                self.client = provider
                return self._embed_with_client(provider, batch, model)
            except Exception as exc:
                errors.append(f"{label}: {exc}")

        if self.client is not None and id(self.client) not in attempted_clients:
            try:
                return self._embed_with_client(self.client, batch, self.embedding_model)
            except Exception as exc:
                errors.append(str(exc))

        if errors:
            raise RuntimeError(" | ".join(errors))
        raise RuntimeError("O cliente de embeddings configurado não suporta embeddings.")

    def _current_pacific_date(self) -> str:
        return datetime.now(timezone.utc).astimezone(PACIFIC_TZ).date().isoformat()

    def _sync_embedding_day_locked(self) -> None:
        current_day = self._current_pacific_date()
        if current_day != self._embedding_request_day:
            self._embedding_request_day = current_day
            self._embedding_request_count_today = 0

    def _clear_embedding_quota_block_if_due_locked(self) -> None:
        if (
            self._embedding_quota_blocked_until is not None
            and datetime.now(timezone.utc) >= self._embedding_quota_blocked_until
        ):
            self._embedding_quota_blocked_until = None
            self._embedding_quota_block_reason = ""

    def _mark_embedding_quota_exhausted(self, reason: str, provider_name: str | None = None) -> None:
        with self._embedding_rate_lock:
            normalized_provider = (provider_name or self.embedding_provider_name or "").strip().lower()
            self._embedding_quota_provider_name = normalized_provider or self.embedding_provider_name
            self._embedding_quota_blocked_until = next_provider_quota_reset_utc(
                self._embedding_quota_provider_name
            )
            self._embedding_quota_block_reason = reason

    def _embedding_quota_guard_message(self, marker: str = "") -> str:
        base = (
            f"Quota de embeddings {self.embedding_provider_label} esgotada; "
            "índice guardado com cobertura semântica parcial. "
            "Verifica plano/faturação ou aguarda renovação da quota."
        )
        return f"{base} {marker}".strip()

    def _acquire_embedding_request_slot(self, throttle_callback=None) -> None:
        while True:
            wait_seconds = 0.0
            with self._embedding_rate_lock:
                self._clear_embedding_quota_block_if_due_locked()
                self._sync_embedding_day_locked()

                if (
                    self._embedding_quota_blocked_until is not None
                    and datetime.now(timezone.utc) < self._embedding_quota_blocked_until
                ):
                    raise RuntimeError(self._embedding_quota_block_reason)

                if (
                    self.embedding_requests_per_day > 0
                    and self._embedding_request_count_today >= self.embedding_requests_per_day
                ):
                    reason = self._embedding_quota_guard_message("LOCAL DAILY LIMIT.")
                    self._embedding_quota_provider_name = self.embedding_provider_name
                    self._embedding_quota_blocked_until = next_provider_quota_reset_utc(
                        self._embedding_quota_provider_name
                    )
                    self._embedding_quota_block_reason = reason
                    raise RuntimeError(reason)

                if self.embedding_requests_per_minute > 0:
                    now = time.monotonic()
                    cutoff = now - 60.0
                    while self._embedding_request_timestamps and self._embedding_request_timestamps[0] <= cutoff:
                        self._embedding_request_timestamps.popleft()
                    if len(self._embedding_request_timestamps) >= self.embedding_requests_per_minute:
                        wait_seconds = max(self._embedding_request_timestamps[0] + 60.0 - now, 0.0)
                    else:
                        self._embedding_request_timestamps.append(now)
                        self._embedding_request_count_today += 1
                        return
                else:
                    self._embedding_request_count_today += 1
                    return

            if throttle_callback:
                throttle_callback(wait_seconds)
            time.sleep(min(max(wait_seconds, 0.05), 1.0))

    def _is_rate_limit_error(self, exc: Exception | str) -> bool:
        message = str(exc)
        upper_message = message.upper()
        return "RESOURCE_EXHAUSTED" in upper_message or "429" in upper_message

    def _is_permanent_quota_error(self, exc: Exception | str) -> bool:
        upper_message = str(exc).upper()
        permanent_markers = (
            "EXCEEDED YOUR CURRENT QUOTA",
            "CHECK YOUR PLAN AND BILLING DETAILS",
            "QUOTA EXCEEDED FOR METRIC",
            "FREE_TIER",
            "PERDAY",
            "PER_DAY",
            "BILLING",
            "LOCAL DAILY LIMIT",
            "KEY LIMIT EXCEEDED",
            "DAILY LIMIT",
            "SETTINGS/KEYS",
        )
        return any(marker in upper_message for marker in permanent_markers)

    def is_embedding_quota_exhausted(self, message: str | None = None) -> bool:
        with self._embedding_rate_lock:
            self._clear_embedding_quota_block_if_due_locked()
            if (
                self._embedding_quota_blocked_until is not None
                and datetime.now(timezone.utc) < self._embedding_quota_blocked_until
            ):
                return True
        candidate = self.last_index_error if message is None else message
        upper_candidate = str(candidate or "").upper()
        return (
            "QUOTA DE EMBEDDINGS" in upper_candidate
            or self._is_permanent_quota_error(candidate)
        )

    def _format_embedding_error(self, exc: Exception) -> str:
        if self._is_permanent_quota_error(exc):
            return self._embedding_quota_guard_message()
        return str(exc)

    def _should_retry_embedding_error(self, exc: Exception) -> bool:
        if self._is_permanent_quota_error(exc):
            return False

        if self._is_rate_limit_error(exc):
            return True

        if isinstance(exc, (ConnectionError, TimeoutError)):
            return True

        upper_message = str(exc).upper()
        transient_markers = (
            "UNAVAILABLE",
            "DEADLINE_EXCEEDED",
            "INTERNAL",
            "500",
            "502",
            "503",
            "504",
            "TIMEOUT",
            "TIMED OUT",
            "CONNECTION RESET",
            "SERVER DISCONNECTED",
        )
        return any(marker in upper_message for marker in transient_markers)

    def _retry_delay_seconds(self, exc: Exception, attempt: int) -> float:
        message = str(exc)
        patterns = [
            r"retry in (\d+(?:\.\d+)?)s",
            r"'retryDelay': '(\d+)s'",
            r'"retryDelay":\s*"(\d+)s"',
        ]
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                delay = float(match.group(1)) + self.embedding_retry_padding_seconds
                return min(delay, self.embedding_max_retry_delay_seconds)
        if self._is_rate_limit_error(exc):
            delay = min(60.0, (2 ** min(attempt, 5))) + self.embedding_retry_padding_seconds
            return min(delay, self.embedding_max_retry_delay_seconds)
        delay = min(30.0, (2 ** min(attempt, 4))) + self.embedding_retry_padding_seconds
        return min(delay, self.embedding_max_retry_delay_seconds)

    def _embed_batch(self, batch: List[str], retry_callback=None, throttle_callback=None) -> List[List[float]]:
        last_exc: Exception | None = None
        started_at = time.monotonic()
        for attempt in range(1, self.embedding_max_retries + 1):
            try:
                self._acquire_embedding_request_slot(throttle_callback=throttle_callback)
                return self._api_embedding_vectors(batch)
            except Exception as exc:
                last_exc = exc
                if self._is_permanent_quota_error(exc):
                    self._mark_embedding_quota_exhausted(
                        self._format_embedding_error(exc),
                        provider_name=getattr(self.client, "provider_name", self.embedding_provider_name),
                    )
                retryable = self._should_retry_embedding_error(exc)
                if not retryable or attempt >= self.embedding_max_retries:
                    raise
                delay = self._retry_delay_seconds(exc, attempt)
                elapsed = max(time.monotonic() - started_at, 0.0)
                if (
                    self.embedding_retry_window_seconds > 0
                    and elapsed + delay > self.embedding_retry_window_seconds
                ):
                    raise
                if retry_callback:
                    retry_callback(attempt, self.embedding_max_retries, delay, exc)
                time.sleep(delay)
        if last_exc:
            raise last_exc
        raise RuntimeError("Falha inesperada ao gerar embeddings.")

    def _embed_many(
        self,
        texts: List[str],
        progress_callback=None,
        retry_callback=None,
        throttle_callback=None,
        batch_callback=None,
    ) -> List[List[float]]:
        if not self._use_local_embeddings and not self.client:
            raise RuntimeError(
                f"Embeddings não disponíveis: configura {self.embedding_api_key_hint}."
            )
        vectors = []
        batch_size = self.embedding_batch_size
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            batch_vectors = self._embed_batch(
                batch,
                retry_callback=retry_callback,
                throttle_callback=throttle_callback,
            )
            vectors.extend(batch_vectors)
            if batch_callback:
                batch_callback(start, batch_vectors, len(vectors), len(texts))
            if progress_callback:
                progress_callback(len(vectors), len(texts))
        return vectors

    def has_pending_reindex(self) -> bool:
        manifest = self._document_manifest()
        current_index = self.index_store.load_index()
        previous_manifest = current_index.get("manifest") or {}
        if manifest != previous_manifest:
            return True
        return (self._use_local_embeddings or bool(self.client)) and self.index_has_missing_embeddings()

    def get_sync_status_summary(self) -> Dict:
        manifest = self._document_manifest()
        current_index = self.index_store.load_index()
        previous_manifest = current_index.get("manifest") or {}
        diff = self._manifest_diff(manifest, previous_manifest)
        chunks = current_index.get("chunks", [])
        embedded_chunks = self._count_embedded_chunks(chunks)
        missing_embedding_chunks = max(len(chunks) - embedded_chunks, 0)

        chunk_stats_by_document = {}
        for item in chunks:
            name = item.get("document") or ""
            if not name:
                continue
            stats = chunk_stats_by_document.setdefault(
                name,
                {"total_chunks": 0, "embedded_chunks": 0},
            )
            stats["total_chunks"] += 1
            if self._has_embedding_payload(item.get("embedding")):
                stats["embedded_chunks"] += 1

        docs_missing_embeddings = sorted(
            name
            for name, stats in chunk_stats_by_document.items()
            if stats["embedded_chunks"] < stats["total_chunks"]
        )

        pending_entries = []
        document_sync_rows = []
        all_document_names = sorted(set(manifest) | set(previous_manifest) | set(chunk_stats_by_document))
        for name in sorted(manifest):
            if name not in previous_manifest:
                pending_entries.append(f"{name} (novo)")
            elif previous_manifest.get(name) != manifest.get(name):
                pending_entries.append(f"{name} (alterado)")
            elif name in docs_missing_embeddings:
                stats = chunk_stats_by_document.get(name) or {}
                pending_entries.append(
                    f"{name} ({stats.get('embedded_chunks', 0)}/{stats.get('total_chunks', 0)} chunks com embedding)"
                )
        for name in sorted(previous_manifest):
            if name not in manifest:
                pending_entries.append(f"{name} (removido do knowledge)")

        for name in all_document_names:
            stats = chunk_stats_by_document.get(name) or {"total_chunks": 0, "embedded_chunks": 0}
            total_chunks = stats["total_chunks"]
            embedded_count = stats["embedded_chunks"]
            missing_chunks = max(total_chunks - embedded_count, 0)
            in_knowledge = name in manifest
            in_index = name in previous_manifest
            if not in_knowledge and in_index:
                status = "removed"
            elif in_knowledge and not in_index:
                status = "new"
            elif in_knowledge and in_index and previous_manifest.get(name) != manifest.get(name):
                status = "changed"
            elif missing_chunks > 0:
                status = "embedding_pending"
            else:
                status = "synced"

            if total_chunks > 0:
                coverage_pct = round((embedded_count / total_chunks) * 100)
            else:
                coverage_pct = 100 if status == "removed" else 0

            document_sync_rows.append(
                {
                    "name": name,
                    "status": status,
                    "total_chunks": total_chunks,
                    "embedded_chunks": embedded_count,
                    "missing_chunks": missing_chunks,
                    "coverage_pct": coverage_pct,
                }
            )

        status_priority = {
            "changed": 0,
            "new": 1,
            "embedding_pending": 2,
            "removed": 3,
            "synced": 4,
        }
        document_sync_rows.sort(key=lambda item: (status_priority.get(item["status"], 9), item["name"]))
        fully_embedded_documents = sum(1 for item in document_sync_rows if item["status"] == "synced")
        partially_embedded_documents = sum(
            1 for item in document_sync_rows if item["status"] == "embedding_pending"
        )
        semantic_chunk_coverage_pct = round((embedded_chunks / len(chunks)) * 100) if chunks else 0

        sync_summary = (
            f"knowledge {len(manifest)} docs | "
            f"indice {len(previous_manifest)} docs | "
            f"embeddings {embedded_chunks}/{len(chunks)} chunks"
        )
        if pending_entries:
            pending_summary = "Pendentes: " + " | ".join(pending_entries[:5])
            if len(pending_entries) > 5:
                pending_summary += f" | +{len(pending_entries) - 5} documento(s)"
        else:
            pending_summary = "knowledge e embeddings alinhados."

        return {
            **diff,
            "knowledge_documents": len(manifest),
            "indexed_documents": len(previous_manifest),
            "total_chunks": len(chunks),
            "embedded_chunks": embedded_chunks,
            "missing_embedding_chunks": missing_embedding_chunks,
            "semantic_chunk_coverage_pct": semantic_chunk_coverage_pct,
            "fully_embedded_documents": fully_embedded_documents,
            "partially_embedded_documents": partially_embedded_documents,
            "documents_with_missing_embeddings": len(docs_missing_embeddings),
            "pending_documents_total": len(pending_entries),
            "sync_summary": sync_summary,
            "pending_summary": pending_summary,
            "pending_documents_preview": pending_entries[:5],
            "document_sync_rows": document_sync_rows,
        }

    def index_has_missing_embeddings(self) -> bool:
        current_index = self.index_store.load_index()
        chunks = current_index.get("chunks", [])
        if not chunks:
            return False
        return self._count_embedded_chunks(chunks) < len(chunks)

    def rebuild_index(self, force: bool = False) -> None:
        manifest = self._document_manifest()
        current_index = self.index_store.load_index()
        previous_manifest = current_index.get("manifest") or {}
        manifest_diff = self._manifest_diff(manifest, previous_manifest)
        missing_embeddings = self._count_embedded_chunks(current_index.get("chunks", [])) < len(
            current_index.get("chunks", [])
        )
        if not force and manifest == previous_manifest and not missing_embeddings:
            self.last_index_error = ""
            self._set_ready_status(manifest, current_index.get("chunks", []), manifest_diff)
            return

        if not self._reindex_lock.acquire(blocking=False):
            return
        self._reindex_started_monotonic = time.monotonic()
        total_documents = len(manifest)
        self._set_reindex_status(
            state="running",
            phase="preparing",
            message="A preparar reindexação...",
            progress_pct=2.0,
            started_at=self._timestamp(),
            finished_at=None,
            total_documents=total_documents,
            processed_documents=0,
            total_chunks=0,
            embedded_chunks=0,
            **manifest_diff,
            error="",
        )

        try:
            previous_chunks_by_document = {}
            for item in current_index.get("chunks", []):
                previous_chunks_by_document.setdefault(item.get("document"), []).append(item)
            for items in previous_chunks_by_document.values():
                items.sort(key=lambda item: item.get("chunk_id", 0))

            chunks = []
            extraction_errors = []
            chunks_missing_embedding = []
            for index, name in enumerate(manifest, start=1):
                self._set_reindex_status(
                    phase="extracting",
                    message=f"A extrair {name}...",
                    progress_pct=(20.0 * (index - 1) / max(total_documents, 1)) if total_documents else 20.0,
                    processed_documents=index - 1,
                    total_chunks=len(chunks),
                )
                if not force and previous_manifest.get(name) == manifest.get(name):
                    reused_chunks = [
                        {
                            key: value
                            for key, value in item.items()
                            if key not in {"score", "retrieval_mode", "has_embedding"}
                        }
                        for item in previous_chunks_by_document.get(name, [])
                    ]
                    chunks.extend(reused_chunks)
                    for item in reused_chunks:
                        if not self._has_embedding_payload(item.get("embedding")):
                            chunks_missing_embedding.append(item)
                    self._set_reindex_status(
                        phase="reusing",
                        message=f"A reutilizar {name} sem alterações...",
                        processed_documents=index,
                        total_chunks=len(chunks),
                        progress_pct=(20.0 * index / max(total_documents, 1)) if total_documents else 20.0,
                    )
                    continue
                path = os.path.join(self.knowledge_dir, name)
                try:
                    text = extract_text_from_path(path)
                except Exception as exc:
                    extraction_errors.append(f"{name}: {exc}")
                    self._set_reindex_status(
                        processed_documents=index,
                        progress_pct=(20.0 * index / max(total_documents, 1)) if total_documents else 20.0,
                    )
                    continue
                structured_chunks = structured_chunk_document(text, document_name=name)
                for chunk_id, chunk in enumerate(structured_chunks, start=1):
                    item = {
                        "id": f"{name}:{chunk_id}",
                        "document": name,
                        "chunk_id": chunk_id,
                        **chunk,
                    }
                    chunks.append(item)
                    chunks_missing_embedding.append(item)
                self._set_reindex_status(
                    processed_documents=index,
                    total_chunks=len(chunks),
                    progress_pct=(20.0 * index / max(total_documents, 1)) if total_documents else 20.0,
                )

            if chunks_missing_embedding and (self._use_local_embeddings or self.client):
                try:
                    embedding_progress = {
                        "done": 0,
                        "total": len(chunks_missing_embedding),
                    }

                    def progress_callback(done: int, total: int) -> None:
                        embedding_progress["done"] = done
                        progress = 20.0 + (75.0 * done / max(total, 1))
                        self._set_reindex_status(
                            phase="embedding",
                            message=f"A gerar embeddings ({done}/{total} chunks)...",
                            progress_pct=progress,
                            total_chunks=total,
                            embedded_chunks=done,
                        )

                    def retry_callback(attempt: int, total_attempts: int, delay: float, exc: Exception) -> None:
                        done = embedding_progress["done"]
                        total = embedding_progress["total"]
                        progress = 20.0 + (75.0 * done / max(total, 1))
                        self._set_reindex_status(
                            phase="embedding_retry",
                            message=(
                                "Falha temporária nos embeddings. "
                                f"Nova tentativa em {int(round(delay))}s "
                                f"({attempt}/{total_attempts})."
                            ),
                            progress_pct=progress,
                            total_chunks=total,
                            embedded_chunks=done,
                            error=str(exc),
                        )

                    def throttle_callback(delay: float) -> None:
                        done = embedding_progress["done"]
                        total = embedding_progress["total"]
                        progress = 20.0 + (75.0 * done / max(total, 1))
                        self._set_reindex_status(
                            phase="embedding_throttle",
                            message=(
                                "A respeitar o limite configurado de embeddings. "
                                f"Retoma em {int(round(delay))}s."
                            ),
                            progress_pct=progress,
                            total_chunks=total,
                            embedded_chunks=done,
                            error="",
                        )

                    def batch_callback(start: int, batch_vectors: List[List[float]], _done: int, _total: int) -> None:
                        for offset, vector in enumerate(batch_vectors):
                            chunks_missing_embedding[start + offset]["embedding"] = vector

                    self._embed_many(
                        [item["text"] for item in chunks_missing_embedding],
                        progress_callback=progress_callback,
                        retry_callback=retry_callback,
                        throttle_callback=throttle_callback,
                        batch_callback=batch_callback,
                    )
                    self.last_index_error = ""
                except Exception as exc:
                    self.last_index_error = self._format_embedding_error(exc)
            elif chunks_missing_embedding and not self._use_local_embeddings and not self.client:
                self.last_index_error = (
                    "API key de embeddings não configurada "
                    f"(define {self.embedding_api_key_hint})."
                )
            else:
                self.last_index_error = ""

            if extraction_errors:
                extraction_note = "Documentos ignorados na indexação: " + " | ".join(extraction_errors[:5])
                if self.last_index_error:
                    self.last_index_error = f"{self.last_index_error} | {extraction_note}"
                else:
                    self.last_index_error = extraction_note

            embedded_chunks = self._count_embedded_chunks(chunks)
            self._set_reindex_status(
                phase="saving",
                message="A guardar índice...",
                progress_pct=96.0,
                total_chunks=len(chunks),
                embedded_chunks=embedded_chunks,
                **manifest_diff,
                error=self.last_index_error,
            )
            self.index_store.replace_index(manifest=manifest, chunks=chunks)
            self._set_reindex_status(
                state="completed",
                phase="completed",
                message=(
                    "Reindexação concluída com embeddings atualizados."
                    if not self.last_index_error
                    else "Reindexação concluída com cobertura semântica parcial."
                ),
                progress_pct=100.0,
                processed_documents=total_documents,
                total_chunks=len(chunks),
                embedded_chunks=embedded_chunks,
                **manifest_diff,
                error=self.last_index_error,
            )
        except Exception as exc:
            self.last_index_error = str(exc)
            self._set_reindex_status(
                state="error",
                phase="error",
                message="A reindexação falhou.",
                progress_pct=self.get_reindex_status().get("progress_pct", 0.0),
                **manifest_diff,
                error=self.last_index_error,
            )
            raise
        finally:
            self._reindex_started_monotonic = None
            self._reindex_lock.release()

    @staticmethod
    def _query_entities(question: str) -> List[Dict]:
        matches = detect_port_entities(question)
        return specific_entities(matches) or matches

    @staticmethod
    def _item_entity_names(item: Dict) -> List[str]:
        names = item.get("entity_names") or []
        if isinstance(names, str):
            names = [names]
        names = [str(name).strip() for name in names if str(name).strip()]
        if names:
            return names
        inferred = detect_port_entities(
            " ".join(
                str(item.get(key) or "")
                for key in ("document", "document_title", "section", "primary_entity", "snippet", "text")
            )
        )
        return entity_names_from_matches(inferred)

    def _entity_match_score(self, item: Dict, query_entities: List[Dict]) -> float:
        if not query_entities:
            return 0.0
        item_names = set(self._item_entity_names(item))
        if not item_names:
            return 0.0
        query_names = {entity["name"] for entity in query_entities}
        if item_names & query_names:
            return 1.0
        query_channels = {str(entity.get("channel") or "") for entity in query_entities if entity.get("channel")}
        item_channel = str(item.get("channel") or "")
        if item_channel and item_channel in query_channels:
            return 0.25
        return 0.0

    def _has_entity_conflict(self, item: Dict, query_entities: List[Dict]) -> bool:
        if not query_entities:
            return False
        item_names = set(self._item_entity_names(item))
        if not item_names:
            return False
        for entity in query_entities:
            if entity.get("generic"):
                continue
            if item_names & set(entity.get("must_not_mix_with") or []):
                return True
        return False

    @staticmethod
    def _candidate_key(item: Dict) -> str:
        return str(item.get("id") or f"{item.get('document')}:{item.get('chunk_id')}")

    def _merge_retrieval_candidates(self, *candidate_groups: List[Dict]) -> List[Dict]:
        merged: Dict[str, Dict] = {}
        for group in candidate_groups:
            for item in group:
                key = self._candidate_key(item)
                existing = merged.get(key)
                if not existing:
                    mode = str(item.get("retrieval_mode") or "unknown")
                    merged[key] = {
                        **item,
                        "_retrieval_modes": [mode],
                        "_base_score": float(item.get("score") or 0.0),
                    }
                    continue
                existing["_base_score"] = max(
                    float(existing.get("_base_score") or 0.0),
                    float(item.get("score") or 0.0),
                )
                mode = str(item.get("retrieval_mode") or "unknown")
                if mode not in existing["_retrieval_modes"]:
                    existing["_retrieval_modes"].append(mode)
                for key_name, value in item.items():
                    if key_name not in existing or existing.get(key_name) in (None, "", []):
                        existing[key_name] = value
        return list(merged.values())

    def _rerank_candidates(self, question: str, candidates: List[Dict], query_entities: List[Dict], top_k: int) -> List[Dict]:
        if not candidates:
            return []

        strict_entities = [entity for entity in query_entities if not entity.get("generic")]
        entity_matched = [
            item for item in candidates if self._entity_match_score(item, strict_entities) >= 1.0
        ]
        pure_entity_matched = [
            item for item in entity_matched if not self._has_entity_conflict(item, strict_entities)
        ]
        ranking_pool = pure_entity_matched or (entity_matched if strict_entities and entity_matched else candidates)

        ranked = []
        for item in ranking_pool:
            lexical = lexical_score(question, " ".join([
                str(item.get("document") or ""),
                str(item.get("document_title") or ""),
                str(item.get("section") or ""),
                " ".join(self._item_entity_names(item)),
                str(item.get("text") or ""),
            ]))
            entity_score = self._entity_match_score(item, query_entities)
            conflict_penalty = 0.35 if self._has_entity_conflict(item, query_entities) else 0.0
            final_score = max(float(item.get("_base_score") or item.get("score") or 0.0), lexical)
            final_score = final_score + (0.45 * entity_score) + (0.20 * lexical) - conflict_penalty
            retrieval_modes = item.get("_retrieval_modes") or [item.get("retrieval_mode", "semantic")]
            ranked.append(
                {
                    **item,
                    "score": max(final_score, 0.0),
                    "retrieval_mode": "+".join(sorted(set(retrieval_modes))),
                    "entity_match": entity_score >= 1.0 if query_entities else None,
                    "query_entities": entity_names_from_matches(query_entities),
                    "rerank_reason": {
                        "lexical_score": round(lexical, 3),
                        "entity_score": round(entity_score, 3),
                        "entity_conflict": conflict_penalty > 0,
                    },
                }
            )

        ranked.sort(key=lambda item: item.get("score", 0), reverse=True)
        return ranked[:top_k]

    def retrieve(self, question: str, top_k: int = 4) -> List[Dict]:
        self.rebuild_index()
        index = self.index_store.load_index()
        chunks = index.get("chunks", [])
        if not chunks:
            return []

        query_entities = self._query_entities(question)
        lexical_results = self._lexical_search(question, chunks, max(top_k * 4, top_k), query_entities=query_entities)

        if not self._use_local_embeddings and not self.client:
            return self._rerank_candidates(question, lexical_results, query_entities, top_k)
        if not self._use_local_embeddings and self.is_embedding_quota_exhausted():
            raise RuntimeError(
                "Pesquisa semântica "
                f"{self.embedding_provider_label} indisponível enquanto a quota de embeddings não renovar."
            )

        try:
            query_vector = self._embed_many([question])[0]
            semantic_results = self.index_store.semantic_search(query_vector, max(top_k * 4, top_k))
            candidates = self._merge_retrieval_candidates(
                [item for item in semantic_results if item.get("score", 0) > 0],
                lexical_results,
            )
            if candidates:
                return self._rerank_candidates(question, candidates, query_entities, top_k)
        except Exception as exc:
            self.last_index_error = self._format_embedding_error(exc)
            if self.is_embedding_quota_exhausted(self.last_index_error):
                raise RuntimeError(
                    "Pesquisa semântica "
                    f"{self.embedding_provider_label} indisponível enquanto a quota de embeddings não renovar."
                ) from exc
            return self._rerank_candidates(question, lexical_results, query_entities, top_k)

        return []

    def _lexical_search(
        self,
        question: str,
        chunks: List[Dict],
        top_k: int,
        query_entities: List[Dict] | None = None,
    ) -> List[Dict]:
        query_entities = query_entities or []
        scored = []
        tug_query = _looks_like_tug_query(question)
        for item in chunks:
            search_text = " ".join(
                [
                    str(item.get("document") or ""),
                    str(item.get("document_title") or ""),
                    str(item.get("section") or ""),
                    " ".join(self._item_entity_names(item)),
                    str(item.get("content_type") or ""),
                    str(item.get("text") or ""),
                ]
            )
            score = lexical_score(question, search_text)
            entity_score = self._entity_match_score(item, query_entities)
            if entity_score:
                score += 0.35 * entity_score
            if tug_query:
                document_name = str(item.get("document") or "").lower()
                section = str(item.get("section") or "").lower()
                if "it-016" in document_name:
                    score += 1.1
                elif "rebocador" in section or "rebocadores" in section:
                    score += 0.45
            if self._has_entity_conflict(item, query_entities):
                score -= 0.25
            if score <= 0:
                continue
            scored.append({**item, "score": score, "retrieval_mode": "lexical"})
        scored.sort(key=lambda item: item.get("score", 0), reverse=True)
        return scored[:top_k]

    def index_summary(self) -> Dict:
        self.rebuild_index()
        index = self.index_store.load_index()
        chunks = index.get("chunks", [])
        documents = index.get("manifest", {})
        embedded_chunks = self._count_embedded_chunks(chunks)
        return {
            "document_count": len(documents),
            "chunk_count": len(chunks),
            "embedded_chunks": embedded_chunks,
            "index_backend": getattr(self.index_store, "backend_name", "unknown"),
            "index_error": self.last_index_error,
        }

    def _build_source_payload(self, contexts: List[Dict]) -> List[Dict]:
        sources = []
        for index, item in enumerate(contexts, start=1):
            entity_names = item.get("entity_names") or []
            if isinstance(entity_names, str):
                entity_names = [entity_names]
            sources.append(
                {
                    "source_id": f"S{index}",
                    "document": item["document"],
                    "chunk_id": item["chunk_id"],
                    "score": round(item["score"], 3),
                    "retrieval_mode": item.get("retrieval_mode", "semantic"),
                    "snippet": item["text"][:260],
                    "section": item.get("section") or "",
                    "page": item.get("page"),
                    "entity_names": entity_names,
                    "primary_entity": item.get("primary_entity") or "",
                    "channel": item.get("channel") or "",
                    "content_type": item.get("content_type") or "",
                    "content_scope": item.get("content_scope") or "",
                    "source": item.get("source") or "",
                    "entity_match": item.get("entity_match"),
                    "query_entities": item.get("query_entities") or [],
                    "rerank_reason": item.get("rerank_reason") or {},
                }
            )
        return sources

    def _build_fallback_answer(self, sources: List[Dict], error_message: str) -> str:
        if not sources:
            return (
                "Não consegui contactar o modelo e também não encontrei contexto documental "
                "suficiente para responder com segurança."
            )

        return (
            "Não consegui contactar o modelo neste momento. "
            "Existem dados de suporte disponíveis, mas a resposta automática falhou. "
            f"Detalhe técnico: {error_message}"
        )

    def _targeted_document_sources(self, sources: List[Dict]) -> List[Dict]:
        return [item for item in sources if item.get("retrieval_mode") == "document_target" and item.get("snippet")]

    def _looks_like_document_evasion(self, answer_text: str) -> bool:
        clean = _normalize_whitespace(answer_text).lower()
        if not clean:
            return True
        patterns = (
            r"não (?:tenho|tinha) .*acesso",
            r"não (?:está|esta) disponível no contexto",
            r"não .*disponível .*consulta direta",
            r"não consigo .*consultar .*document",
            r"não posso fornecer .*document",
            r"recomendo consultar diretamente",
            r"consulta diretamente o documento",
        )
        return any(re.search(pattern, clean) for pattern in patterns)

    def _build_targeted_document_answer(self, question: str, sources: List[Dict]) -> str:
        targeted_sources = self._targeted_document_sources(sources)
        if not targeted_sources:
            return ""

        document_name = str(targeted_sources[0].get("document") or "documento").replace("_", " ")
        document_name = re.sub(r"\.[a-z0-9]+$", "", document_name, flags=re.IGNORECASE).strip()
        document_name = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý])", " ", document_name)
        snippets: list[str] = []
        for item in targeted_sources[:2]:
            snippet = _normalize_whitespace(item.get("snippet", ""))
            if snippet and snippet not in snippets:
                snippets.append(snippet)
        if not snippets:
            return ""

        lead = f"Segundo o {document_name},"
        question_clean = _normalize_whitespace(question).lower()
        if re.search(r"\b(resume|resumo|o que diz|diz me|qual e|qual é|regra)\b", question_clean):
            return f"{lead} {' '.join(snippets)}"
        return " ".join(snippets)

    def _validate_retrieval_support(self, question: str, sources: List[Dict]) -> Dict:
        detected = detect_port_entities(question)
        strict_entities = specific_entities(detected)
        if not strict_entities:
            return {
                "can_answer": True,
                "reason": "Pergunta sem entidade portuária específica.",
                "entity_match": None,
                "source_quality": "media" if sources else "baixa",
                "missing_information": [],
            }

        matched_sources = [
            source for source in sources if self._entity_match_score(source, strict_entities) >= 1.0
        ]
        if matched_sources:
            return {
                "can_answer": True,
                "reason": "Foram recuperadas fontes da entidade pedida.",
                "entity_match": True,
                "source_quality": "alta" if len(matched_sources) >= 2 else "media",
                "missing_information": [],
            }

        entity_names = entity_names_from_matches(strict_entities)
        return {
            "can_answer": False,
            "reason": "Nenhuma fonte final confirma a entidade específica pedida.",
            "entity_match": False,
            "source_quality": "baixa",
            "missing_information": [
                "fonte documental com entidade: " + ", ".join(entity_names),
            ],
        }

    @staticmethod
    def _build_insufficient_document_answer(validation: Dict, question: str) -> str:
        missing = validation.get("missing_information") or []
        requested = ", ".join(missing).replace("fonte documental com entidade: ", "") or "a entidade pedida"
        return (
            "A documentação recuperada não permite responder com segurança. "
            "Foram encontrados excertos relacionados, mas nenhum confirma especificamente "
            f"a informação pedida sobre {requested}. "
            "Recomendo rever os documentos de origem ou refazer a pesquisa com termos mais específicos."
        )

    def generate_text(self, prompt: str):
        if not self.can_generate():
            raise RuntimeError(
                "Define a API key do provider antes de usar o chatbot."
            )

        errors: List[str] = []
        for provider, model in self._generation_candidates:
            label = self._provider_label(getattr(provider, "provider_name", ""))
            if not provider.is_available:
                reason = getattr(provider, "unavailable_reason", "").strip()
                if reason:
                    errors.append(f"{label}: {reason}")
                continue
            try:
                return provider.generate(
                    prompt=prompt,
                    model=model,
                )
            except Exception as exc:
                errors.append(f"{label}: {exc}")

        raise RuntimeError(" | ".join(errors) if errors else self.generation_unavailable_reason())

    def answer(
        self,
        question: str,
        role: str,
        history: List[Dict],
        supplemental_sources: List[Dict] | None = None,
        trusted_answers: List[Dict] | None = None,
        reviewed_answers: List[Dict] | None = None,
        retrieval_question: str | None = None,
        execution_plan: Dict | None = None,
        conversation_state: Dict | None = None,
    ) -> Dict:
        try:
            retrieval_top_k = 8 if (execution_plan or {}).get("requires_llm_synthesis") else 4
            contexts = self.retrieve((retrieval_question or question or "").strip(), top_k=retrieval_top_k)
        except Exception as exc:
            return {
                "answer": (
                    "A pesquisa documental não está disponível neste momento. "
                    f"Detalhe: {exc}"
                ),
                "sources": supplemental_sources or [],
                "retrieval_error": str(exc),
            }
        sources = self._build_source_payload(contexts)
        if supplemental_sources:
            sources.extend(supplemental_sources)

        retrieval_validation = self._validate_retrieval_support(question, sources)
        if not retrieval_validation.get("can_answer"):
            return {
                "answer": self._build_insufficient_document_answer(retrieval_validation, question),
                "sources": sources,
                "retrieval_validation": retrieval_validation,
            }

        trusted_answers = trusted_answers or []
        reviewed_answers = reviewed_answers or []
        trusted_block = "\n\n".join(
            (
                f"Pergunta validada: {item['question']}\n"
                f"Resposta anterior aprovada, a sintetizar e não copiar literalmente: {item['answer']}\n"
                f"Nota do operador: {item.get('feedback_note') or 'Sem nota.'}\n"
                f"Semelhança: {item.get('similarity', 0)}"
            )
            for item in trusted_answers[:3]
        )
        reviewed_block = "\n\n".join(
            (
                f"Pergunta em revisão: {item['question']}\n"
                f"Resposta anterior a não repetir: {item['answer']}\n"
                f"Nota do operador: {item.get('feedback_note') or 'Sem nota.'}\n"
                f"Resposta corrigida sugerida: {item.get('feedback_correction') or 'Sem resposta corrigida.'}\n"
                f"Documento base sugerido: {item.get('feedback_correction_document') or 'Sem documento indicado.'}\n"
                f"Semelhança: {item.get('similarity', 0)}"
            )
            for item in reviewed_answers[:3]
        )

        history_block = "\n".join(
            f"{entry['role']}: {entry['content']}" for entry in history[-10:]
        )
        plan_block = _format_plan_block(execution_plan)
        conversation_state_block = (
            str((conversation_state or {}).get("summary") or "").strip()
            or "Sem estado conversacional estruturado."
        )
        context_block = "\n\n".join(
            (
                f"[{source['source_id']}] Documento: {source['document']} | "
                f"secção {source.get('section') or '--'} | "
                f"página {source.get('page') or '--'} | "
                f"entidade {', '.join(source.get('entity_names') or []) or source.get('primary_entity') or '--'} | "
                f"tipo {source.get('content_type') or '--'} | "
                f"escopo {source.get('content_scope') or '--'} | "
                f"score {source['score']} | modo {source['retrieval_mode']}\n"
                f"Excerto: {source['snippet']}"
            )
            for source in sources
        )

        prompt = f"""
És um assistente operacional para um portal marítimo com perfis admin, agente e piloto.
Perfil do utilizador atual: {role}

Regras:
- Responde em português europeu.
- Usa primeiro o contexto recuperado.
- Para perguntas documentais, a tua função é explicar o que está suportado nas fontes recuperadas, não tomar uma decisão operacional final sem confirmação humana.
- Não inventes regras, limites, exceções, horários, valores, calados, comprimentos, rebocadores ou condições que não apareçam nas fontes disponíveis.
- Não mistures informação de cais, terminais, fundeadouros ou documentos diferentes. Se a pergunta mencionar uma entidade específica, responde apenas com fontes dessa entidade.
- Se os excertos forem insuficientes ou contraditórios, diz isso claramente em vez de preencher a lacuna.
- Quando a fonte tiver documento, secção ou página, podes mencionar essa referência em linguagem natural; não mostres ids técnicos, chunks ou scores ao utilizador.
- As fontes com prefixo operacional (por exemplo OPS1, OPS2, OPS3) representam dados vivos do portal: escalas, planeamento e arquivo de manobras.
- Fontes com modo `document_companion` são factos curados pelo admin; usa-as como balizas fortes, mas não as copies como resposta pronta.
- Fontes com modo `berth_profile` são perfis estruturados de cais/terminal; usa-as para normalizar dimensões, calados, janelas de manobra, restrições e orientação de rebocadores antes de sintetizar.
- Fontes com modo `operational_safety_limits` são limites de suspensão de manobras; se indicarem suspensão por nevoeiro ou vento, começa pela conclusão de que as manobras ficam suspensas.
- Fontes com modo `operational_tug_guidance` são regra prática operacional para rebocadores; usa-as como baseline da recomendação. A IT-016 confirma ou agrava mínimos legais/DWT/carga perigosa, mas não deve reduzir essa regra prática.
- Fontes com modo `operational_feedback_memory` são memória operacional revista por operadores; usa-as como sinal forte, mas reconcilia com documentos, perfis de cais e dados live. Nunca copies literalmente essa memória.
- Fontes com modo `message_analysis` separam mensagens compostas em contexto e perguntas; responde a cada pergunta explícita e usa os factos declarativos como premissas.
- Se existir uma resposta anteriormente aprovada para a mesma pergunta ou para uma pergunta muito parecida, usa-a como referência forte de factos e decisão, mas reformula-a no contexto atual.
- Não copies literalmente feedback ou respostas vindas do chat/WhatsApp; extrai os princípios operacionais, cruza-os com as fontes disponíveis e responde com síntese própria.
- Se existir uma resposta semelhante marcada para revisão, não repitas a resposta anterior como validada.
- Trata a nota do operador associada à revisão como sinal prioritário de correção ou dúvida.
- Se existir uma resposta corrigida sugerida pelo operador para uma pergunta muito semelhante, usa-a como referência forte e reconcilia-a com os documentos disponíveis.
- Se a revisão pendente não puder ser reconciliada com as fontes disponíveis, diz explicitamente que a resposta anterior ficou em revisão.
- Se existir fonte com modo `document_target`, assume que o utilizador quer esse documento/regra em concreto e prioriza-a sobre contexto genérico.
- Se existirem excertos de um documento-alvo, nunca digas que o documento não está disponível ou que não tens acesso a ele.
- Em perguntas amplas de inventário, como cais, terminais, docas ou instalações do Porto de Setúbal, cruza várias fontes RAG e não respondas pelo todo com uma fonte parcial de outro tipo, por exemplo fundeadouros.
- Em inventário do Porto de Setúbal, não uses "Terminal Multiusos Norte" nem "Terminal Multiusos Sul"; usa TMS1 e TMS2. Não dupliques aliases: TMS1 = Cais das Fontainhas, TMS2 = Terminal de Contentores / Multiusos 2, Autoeuropa = Ro-Ro.
- Para contagens de cais/terminais, declara o critério de contagem quando houver ambiguidade entre terminal, cais físico, face, rampa, doca, plataforma ou duque d'alba.
- Em perguntas amplas de inventário, não comeces por repetir a pergunta. Começa pela resposta direta, usa uma lista curta quando houver vários itens, e deixa notas complementares como fundeadouros só no fim.
- Se o contexto for insuficiente, diz claramente o que falta.
- Sê objetivo e útil.
- Se a pergunta tiver várias frases ou várias perguntas, não respondas só à última: organiza a resposta pelos pontos pedidos, sem repetir a mensagem do utilizador.
- Não mostres ids de fontes, chunks, scores ou secções "Fontes usadas".
- Integra a informação de forma natural, como resposta operacional fluida.
- Quando falares de ocupação portuária, usa lógica de slots de cais.
- Fundeadouro Norte e Fundeadouro Sul / Tróia são quadros/fundeadouros: podem ter vários navios e não contam como slots de cais ocupados.
- Não digas que um navio "não cabe" num cais só porque a extensão nominal do cais é menor do que o LOA do navio.
- Se a pergunta for sobre dimensões do cais ou do navio, limita-te aos factos documentais disponíveis e evita conclusões automáticas de incompatibilidade.
- Exceção: quando a fonte indicar um limite dimensional rígido de acesso, como boca máxima, largura máxima, calado máximo, LOA máximo absoluto ou limite do Hidrolift/eclusa, compara o valor dado pelo utilizador com esse limite. Se exceder, começa pela conclusão prática de que a manobra não deve seguir assim.
- Quando o plano interno indicar `live_reasoning`, usa os dados live como evidência para responder a uma decisão operacional.
- Em perguntas de avaliação ou suficiência, começa pela conclusão prática e só depois justifica.
- Não respondas a uma pergunta de avaliação com um dump de meteorologia, marés, ondulação ou avisos sem concluir algo operacional.
- Com nevoeiro em porto, todas as manobras ficam suspensas até a visibilidade ser restaurada. Com vento superior a 30 kt, todas as manobras ficam suspensas; depois de suspensão por vento, só retomar abaixo de 25 kt.
- Quando a pergunta pedir quantidade/recomendação de reboques/rebocadores, usa primeiro a fonte `operational_tug_guidance` como baseline, cruza com histórico/live e usa a IT-016 para confirmar ou agravar mínimos legais, DWT, carga perigosa, estado carregado/vazio e thrusters.
- Se faltarem dados críticos para rebocadores (DWT, carga perigosa, carregado/vazio, bow/stern thruster), não inventes; dá a recomendação condicionada por cenários claros e diz exatamente o que falta confirmar.
- Se a pergunta juntar vento/marés/ondulação live com "quantos", "recomendas", "aconselhas" ou "suficiente", trata o live feed como input da decisão e não como resposta final.

Histórico recente:
{history_block or "Sem histórico anterior."}

Plano interno:
{plan_block}

Estado conversacional extraído:
{conversation_state_block}

Fontes disponíveis:
{context_block or "Sem contexto recuperado."}

Memória operacional validada por feedback:
{trusted_block or "Sem respostas aprovadas semelhantes."}

Respostas anteriores marcadas para revisão:
{reviewed_block or "Sem revisões pendentes semelhantes."}

Pergunta:
{question}
""".strip()

        if not self.can_generate():
            raise RuntimeError("Define a API key do provider antes de usar o chatbot.")

        try:
            gen_result = self.generate_text(prompt)
            answer_text = gen_result.text or "Sem resposta do modelo."
        except Exception as exc:
            answer_text = self._build_fallback_answer(sources, str(exc))

        if self._needs_answer_critic(execution_plan) and self._looks_like_unresolved_decision_answer(question, answer_text):
            retry_prompt = f"""
És o mesmo assistente operacional e a tua primeira resposta não fechou a decisão pedida.

Pergunta original:
{question}

Plano interno:
{plan_block}

Estado conversacional:
{conversation_state_block}

Fontes disponíveis:
{context_block or "Sem contexto recuperado."}

Resposta preliminar a corrigir:
{answer_text}

Reformula para responder diretamente à decisão operacional pedida.
Regras:
- Começa por uma conclusão curta e explícita.
- Usa os dados live apenas como base da conclusão.
- Se a pergunta fala de meios já propostos (por exemplo, dois rebocadores), diz claramente se parecem suficientes, insuficientes ou se a resposta fica condicionada.
- Evita despejar previsões ou listas de observação sem concluir.
""".strip()
            try:
                retry_result = self.generate_text(retry_prompt)
                retry_text = (retry_result.text or "").strip()
                if retry_text:
                    answer_text = retry_text
            except Exception:
                pass

        if self._targeted_document_sources(sources) and self._looks_like_document_evasion(answer_text):
            extractive_answer = self._build_targeted_document_answer(question, sources)
            if extractive_answer:
                answer_text = extractive_answer
        answer_text = _strip_leading_question_echo(question, answer_text)

        return {
            "answer": answer_text,
            "sources": sources,
            "retrieval_validation": retrieval_validation,
        }

    @staticmethod
    def _needs_answer_critic(execution_plan: Dict | None) -> bool:
        return bool((execution_plan or {}).get("needs_answer_critic"))

    @staticmethod
    def _looks_like_unresolved_decision_answer(question: str, answer_text: str) -> bool:
        clean_question = _normalize_whitespace(question).lower()
        clean_answer = _normalize_whitespace(answer_text).lower()
        if not clean_answer:
            return True
        asks_decision = any(
            token in clean_question
            for token in (
                "suficiente",
                "suficientes",
                "recomend",
                "aconselh",
                "quantos",
                "quantas",
                "avalia",
                "avaliar",
                "achas",
                "parece",
                "basta",
                "chega",
            )
        )
        if not asks_decision:
            return False
        has_conclusion = any(
            token in clean_answer
            for token in (
                "recomendo",
                "recomendaria",
                "aconselho",
                "parece suficiente",
                "parecem suficientes",
                "não parecem suficientes",
                "nao parecem suficientes",
                "são suficientes",
                "sao suficientes",
                "não chegam",
                "nao chegam",
                "diria que",
                "manteria",
            )
        )
        looks_like_data_dump = clean_answer.startswith("meteorologia para ") or (
            "condições meteorológicas atuais" in clean_answer and not has_conclusion
        )
        return looks_like_data_dump or not has_conclusion
