import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from core import services
from core.helpers import current_reindex_status_payload
from integrations.llm_provider import EmbeddingResult, GenerationResult
from integrations.rag_engine import SimpleRAGEngine
from integrations.vector_store import LocalIndexStore


class _FailingModels:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def embed_content(self, model: str, contents: list[str]):
        self.calls += 1
        raise self.exc


class _FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.models = _FailingModels(exc)


class _EmbeddingResult:
    def __init__(self, total: int) -> None:
        self.embeddings = [{"values": [0.1, 0.2, float(index)]} for index in range(total)]


class _SuccessfulModels:
    def __init__(self) -> None:
        self.calls = 0

    def embed_content(self, model: str, contents: list[str]):
        self.calls += 1
        return _EmbeddingResult(len(contents))


class _SuccessfulClient:
    def __init__(self) -> None:
        self.models = _SuccessfulModels()


class _SequenceModels:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def embed_content(self, model: str, contents: list[str]):
        self.calls += 1
        if not self.outcomes:
            return _EmbeddingResult(len(contents))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _EmbeddingResult(len(contents))


class _SequenceClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.models = _SequenceModels(outcomes)


class _AmbiguousEmbedding:
    def __bool__(self):
        raise ValueError("ambiguous truth value")

    def tolist(self):
        return [0.1, 0.2, 0.3]


class _StubProvider:
    def __init__(
        self,
        provider_name: str,
        *,
        generation_text: str = "",
        generation_exc: Exception | None = None,
        embed_exc: Exception | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.unavailable_reason = ""
        self._generation_text = generation_text
        self._generation_exc = generation_exc
        self._embed_exc = embed_exc
        self.generate_calls = 0
        self.embed_calls = 0

    @property
    def is_available(self) -> bool:
        return True

    def generate(self, prompt: str, model: str, **kwargs) -> GenerationResult:
        self.generate_calls += 1
        if self._generation_exc is not None:
            raise self._generation_exc
        return GenerationResult(text=self._generation_text or f"{self.provider_name}:{model}", model=model, usage={})

    def embed(self, texts: list[str], model: str, **kwargs) -> EmbeddingResult:
        self.embed_calls += 1
        if self._embed_exc is not None:
            raise self._embed_exc
        vectors = [[0.1, 0.2, float(index)] for index, _text in enumerate(texts, start=1)]
        return EmbeddingResult(vectors=vectors, model=model)


class SimpleRAGEngineTests(unittest.TestCase):
    def _make_engine(
        self,
        *,
        document_text: str = "Procedimento de manobra e apoio operacional para o cais.",
        extra_env: dict[str, str] | None = None,
    ) -> tuple[SimpleRAGEngine, str]:
        sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(sandbox.cleanup)
        knowledge_dir = os.path.join(sandbox.name, "knowledge")
        os.makedirs(knowledge_dir, exist_ok=True)
        with open(os.path.join(knowledge_dir, "manual.txt"), "w", encoding="utf-8") as handle:
            handle.write(document_text)

        index_store = LocalIndexStore(index_path=os.path.join(sandbox.name, "rag_index.json"))
        env = {
            "LLM_PROVIDER": "gemini",
            "EMBEDDING_BATCH_SIZE": "4",
            "EMBEDDING_REQUESTS_PER_MINUTE": "0",
            "EMBEDDING_REQUESTS_PER_DAY": "0",
            "EMBEDDING_MAX_RETRIES": "10",
            "EMBEDDING_RETRY_WINDOW_SECONDS": "1",
            "EMBEDDING_MAX_RETRY_DELAY_SECONDS": "0",
            "EMBEDDING_RETRY_PADDING_SECONDS": "0",
        }
        if extra_env:
            env.update(extra_env)
        with patch.dict(
            os.environ,
            env,
            clear=False,
        ):
            engine = SimpleRAGEngine(
                api_key="test-key",
                knowledge_dir=knowledge_dir,
                index_store=index_store,
                generation_model="gemini-test",
                embedding_model="embedding-test",
            )
        return engine, knowledge_dir

    def test_non_retryable_embedding_error_falls_back_immediately(self) -> None:
        engine, _ = self._make_engine()
        failing_client = _FailingClient(RuntimeError("UNAUTHENTICATED: invalid API key"))
        engine.client = failing_client

        engine.rebuild_index(force=True)

        status = engine.get_reindex_status()
        stored_chunks = engine.index_store.load_index()["chunks"]
        self.assertEqual(failing_client.models.calls, 1)
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["phase"], "completed")
        self.assertIn("cobertura semântica parcial", status["message"].lower())
        self.assertTrue(stored_chunks)
        self.assertTrue(all(not chunk.get("embedding") for chunk in stored_chunks))

    def test_quota_exhausted_error_does_not_retry(self) -> None:
        engine, _ = self._make_engine()
        quota_error = RuntimeError(
            "429 RESOURCE_EXHAUSTED. "
            "You exceeded your current quota, please check your plan and billing details. "
            "Quota exceeded for metric: generativelanguage.googleapis.com/embed_content_free_tier_requests. "
            "Please retry in 22s."
        )
        failing_client = _FailingClient(quota_error)
        engine.client = failing_client

        engine.rebuild_index(force=True)

        status = engine.get_reindex_status()
        self.assertEqual(failing_client.models.calls, 1)
        self.assertEqual(status["state"], "completed")
        self.assertIn("cobertura semântica parcial", status["message"].lower())
        self.assertIn("quota de embeddings gemini esgotada", status["error"].lower())

    def test_quota_exhaustion_blocks_future_query_embeddings_without_lexical_fallback(self) -> None:
        engine, _ = self._make_engine()
        quota_error = RuntimeError(
            "429 RESOURCE_EXHAUSTED. "
            "You exceeded your current quota, please check your plan and billing details. "
            "Quota exceeded for metric: generativelanguage.googleapis.com/embed_content_free_tier_requests."
        )
        engine.client = _FailingClient(quota_error)

        engine.rebuild_index(force=True)

        successful_client = _SuccessfulClient()
        engine.client = successful_client
        with self.assertRaises(RuntimeError) as ctx:
            engine.retrieve("procedimento de manobra", top_k=2)
        self.assertEqual(successful_client.models.calls, 0)
        self.assertIn("pesquisa semântica gemini indisponível", str(ctx.exception).lower())

    def test_local_daily_embedding_guard_stops_reindex_before_extra_requests(self) -> None:
        long_text = ("Procedimento de manobra. " * 80).strip()
        engine, _ = self._make_engine(
            document_text=long_text,
            extra_env={
                "EMBEDDING_BATCH_SIZE": "1",
                "EMBEDDING_REQUESTS_PER_DAY": "1",
            },
        )
        successful_client = _SuccessfulClient()
        engine.client = successful_client

        engine.rebuild_index(force=True)

        status = engine.get_reindex_status()
        stored_chunks = engine.index_store.load_index()["chunks"]
        self.assertEqual(successful_client.models.calls, 1)
        self.assertEqual(status["state"], "completed")
        self.assertIn("cobertura semântica parcial", status["message"].lower())
        self.assertIn("quota de embeddings gemini esgotada", status["error"].lower())
        self.assertTrue(stored_chunks)
        embedded_count = sum(1 for chunk in stored_chunks if chunk.get("embedding"))
        self.assertGreater(embedded_count, 0)
        self.assertLess(embedded_count, len(stored_chunks))

    def test_has_pending_reindex_detects_manifest_changes(self) -> None:
        engine, knowledge_dir = self._make_engine()
        engine.client = None

        engine.rebuild_index(force=True)
        self.assertFalse(engine.has_pending_reindex())

        with open(os.path.join(knowledge_dir, "novo-documento.txt"), "w", encoding="utf-8") as handle:
            handle.write("Novo procedimento para reindexacao.")

        self.assertTrue(engine.has_pending_reindex())

    def test_incremental_reindex_backfills_missing_embeddings_without_manifest_changes(self) -> None:
        engine, _ = self._make_engine()
        engine.client = None

        engine.rebuild_index(force=True)
        stored_before = engine.index_store.load_index()["chunks"]
        self.assertTrue(stored_before)
        self.assertTrue(all(not chunk.get("embedding") for chunk in stored_before))

        successful_client = _SuccessfulClient()
        engine.client = successful_client

        self.assertTrue(engine.has_pending_reindex())
        engine.rebuild_index(force=False)

        status = engine.get_reindex_status()
        stored_after = engine.index_store.load_index()["chunks"]
        self.assertEqual(successful_client.models.calls, 1)
        self.assertEqual(status["state"], "completed")
        self.assertIn("embeddings atualizados", status["message"].lower())
        self.assertTrue(all(chunk.get("embedding") for chunk in stored_after))

    def test_incremental_reindex_preserves_partial_embeddings_after_failure(self) -> None:
        long_text = ("Procedimento de manobra. " * 80).strip()
        engine, _ = self._make_engine(
            document_text=long_text,
            extra_env={
                "EMBEDDING_BATCH_SIZE": "1",
                "EMBEDDING_MAX_RETRIES": "1",
            },
        )
        failing_client = _SequenceClient([object(), RuntimeError("UNAVAILABLE: upstream timeout")])
        engine.client = failing_client

        engine.rebuild_index(force=True)

        stored_partial = engine.index_store.load_index()["chunks"]
        partial_embedded = sum(1 for chunk in stored_partial if chunk.get("embedding"))
        self.assertEqual(failing_client.models.calls, 2)
        self.assertGreater(partial_embedded, 0)
        self.assertLess(partial_embedded, len(stored_partial))

        successful_client = _SuccessfulClient()
        engine.client = successful_client
        engine.rebuild_index(force=False)

        stored_final = engine.index_store.load_index()["chunks"]
        self.assertEqual(successful_client.models.calls, len(stored_partial) - partial_embedded)
        self.assertTrue(all(chunk.get("embedding") for chunk in stored_final))

    def test_sync_status_summary_reports_embedding_gap_and_pending_documents(self) -> None:
        engine, _ = self._make_engine()
        engine.client = None

        engine.rebuild_index(force=True)

        summary = engine.get_sync_status_summary()
        self.assertEqual(summary["knowledge_documents"], 1)
        self.assertEqual(summary["indexed_documents"], 1)
        self.assertEqual(summary["documents_with_missing_embeddings"], 1)
        self.assertGreater(summary["missing_embedding_chunks"], 0)
        self.assertEqual(summary["pending_documents_total"], 1)
        self.assertEqual(summary["semantic_chunk_coverage_pct"], 0)
        self.assertEqual(summary["fully_embedded_documents"], 0)
        self.assertEqual(summary["partially_embedded_documents"], 1)
        self.assertIn("manual.txt", summary["pending_summary"])
        self.assertEqual(summary["document_sync_rows"][0]["name"], "manual.txt")
        self.assertEqual(summary["document_sync_rows"][0]["status"], "embedding_pending")

        successful_client = _SuccessfulClient()
        engine.client = successful_client
        engine.rebuild_index(force=False)

        updated_summary = engine.get_sync_status_summary()
        self.assertEqual(updated_summary["documents_with_missing_embeddings"], 0)
        self.assertEqual(updated_summary["missing_embedding_chunks"], 0)
        self.assertEqual(updated_summary["pending_documents_total"], 0)
        self.assertEqual(updated_summary["semantic_chunk_coverage_pct"], 100)
        self.assertEqual(updated_summary["fully_embedded_documents"], 1)
        self.assertEqual(updated_summary["partially_embedded_documents"], 0)
        self.assertIn("alinhados", updated_summary["pending_summary"])
        self.assertEqual(updated_summary["document_sync_rows"][0]["status"], "synced")

    def test_sync_status_summary_accepts_array_like_embeddings_from_pgvector(self) -> None:
        engine, _ = self._make_engine()
        engine.client = None

        engine.rebuild_index(force=True)
        current_index = engine.index_store.load_index()
        current_index["chunks"][0]["embedding"] = _AmbiguousEmbedding()
        with patch.object(engine.index_store, "load_index", return_value=current_index):
            summary = engine.get_sync_status_summary()
        self.assertEqual(summary["embedded_chunks"], 1)
        self.assertEqual(summary["missing_embedding_chunks"], 0)

    def test_stale_has_embedding_flags_do_not_hide_pending_backfill(self) -> None:
        engine, _ = self._make_engine()
        engine.client = None

        engine.rebuild_index(force=True)
        current_index = engine.index_store.load_index()
        for chunk in current_index["chunks"]:
            chunk["has_embedding"] = True
        engine.client = _SuccessfulClient()
        with patch.object(engine.index_store, "load_index", return_value=current_index):
            self.assertTrue(engine.index_has_missing_embeddings())
            self.assertTrue(engine.has_pending_reindex())

    def test_current_reindex_payload_uses_semantic_coverage_when_completion_is_partial(self) -> None:
        long_text = ("Procedimento de manobra. " * 80).strip()
        engine, _ = self._make_engine(
            document_text=long_text,
            extra_env={
                "EMBEDDING_BATCH_SIZE": "1",
                "EMBEDDING_REQUESTS_PER_DAY": "1",
            },
        )
        engine.client = _SuccessfulClient()

        engine.rebuild_index(force=True)

        raw_status = engine.get_reindex_status()
        self.assertEqual(raw_status["state"], "completed")
        self.assertEqual(raw_status["progress_pct"], 100.0)

        with (
            patch.object(services, "rag", engine),
            patch.object(services, "reindex_thread_lock", threading.Lock()),
            patch.object(services, "reindex_thread", None),
            patch.object(services, "reindex_retry_scheduler", None),
        ):
            payload = current_reindex_status_payload()

        self.assertEqual(payload["state"], "completed")
        self.assertLess(payload["semantic_chunk_coverage_pct"], 100)
        self.assertEqual(payload["progress_pct"], payload["semantic_chunk_coverage_pct"])
        self.assertEqual(payload["workflow_progress_pct"], 100.0)

    def test_answer_falls_back_to_secondary_generation_provider(self) -> None:
        engine, _ = self._make_engine()
        engine.client = None
        engine.rebuild_index(force=True)

        primary = _StubProvider(
            "gemini",
            generation_exc=RuntimeError("429 RESOURCE_EXHAUSTED. You exceeded your current quota."),
        )
        fallback = _StubProvider("openrouter", generation_text="Resposta via fallback OpenRouter.")
        engine.provider = primary
        engine.generation_model = "gemini-2.5-flash"
        engine.generation_fallback_provider = fallback
        engine.generation_fallback_model = "openrouter/free"
        engine._generation_candidates = [
            (primary, engine.generation_model),
            (fallback, engine.generation_fallback_model),
        ]
        engine.generation_provider_label = engine._candidate_chain_label(engine._generation_candidates)

        answer = engine.answer(
            question="Resume o procedimento de manobra.",
            role="piloto",
            history=[],
            supplemental_sources=[],
            trusted_answers=[],
        )

        self.assertEqual(primary.generate_calls, 1)
        self.assertEqual(fallback.generate_calls, 1)
        self.assertEqual(answer["answer"], "Resposta via fallback OpenRouter.")


if __name__ == "__main__":
    unittest.main()
