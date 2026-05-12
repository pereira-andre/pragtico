from __future__ import annotations

from types import SimpleNamespace

from core.chat_runtime import _build_supplemental_sources
from domain.chat_response_formatting import add_contextual_response_emojis
from domain.operational_qa_memory import build_qa_memory_sources, find_qa_memory_matches, load_qa_memory_records
from integrations.rag_engine import SimpleRAGEngine


def test_qa_memory_matches_paraphrased_critical_tug_case() -> None:
    load_qa_memory_records.cache_clear()

    matches = find_qa_memory_matches("RORO 230 metros com vento norte forte: quantos rebocadores?")

    assert matches
    record, score = matches[0]
    assert score >= 0.6
    assert "RORO" in record.question.upper()
    assert any("4 rebocadores grandes" in item for item in record.expected)


def test_qa_memory_avoids_noise_for_short_navigation_conversion() -> None:
    load_qa_memory_records.cache_clear()

    matches = find_qa_memory_matches("100 jardas sao quantos metros?")

    assert [record.question for record, _score in matches] == ["100 jardas são quantos metros?"]


def test_qa_memory_source_is_guidance_not_ready_answer() -> None:
    source = build_qa_memory_sources("100 jardas sao quantos metros?")[0]

    assert source["retrieval_mode"] == "operational_qa_memory"
    assert "Nunca copiar literalmente" in source["snippet"]
    assert "fundamentação mínima" in source["snippet"]
    assert "91,44 metros" in source["snippet"]


def test_supplemental_sources_include_qa_memory_without_bypassing_direct_rules() -> None:
    sources = _build_supplemental_sources("100 jardas sao quantos metros?")

    assert any(source.get("retrieval_mode") == "operational_qa_memory" for source in sources)


def test_qa_memory_llm_answers_get_single_contextual_emoji_prefix() -> None:
    payload = {
        "answer_origin": "llm",
        "answer": "100 jardas correspondem a 91,44 metros, porque 1 jarda equivale a 0,9144 m.",
        "sources": [{"retrieval_mode": "operational_qa_memory"}],
    }

    decorated = add_contextual_response_emojis(payload, "100 jardas sao quantos metros?")

    assert decorated["answer"].startswith("📋 ")
    assert decorated["answer"].count("📋") == 1


class PromptCaptureRAG(SimpleRAGEngine):
    def __init__(self) -> None:
        self.prompt = ""

    def retrieve(self, _question: str, top_k: int = 4):  # noqa: ANN001
        return []

    def can_generate(self) -> bool:
        return True

    def generate_text(self, prompt: str):  # noqa: ANN001
        self.prompt = prompt
        return SimpleNamespace(text="Resposta sintetizada com fundamento.")


def test_rag_prompt_treats_qa_memory_as_non_copy_training_signal() -> None:
    engine = PromptCaptureRAG()
    source = build_qa_memory_sources("100 jardas sao quantos metros?")[0]

    engine.answer(
        question="100 jardas sao quantos metros?",
        retrieval_question="100 jardas sao quantos metros?",
        role="admin",
        history=[],
        supplemental_sources=[source],
        execution_plan={},
        conversation_state={},
    )

    assert "operational_qa_memory" in engine.prompt
    assert "nunca como texto final pronto" in engine.prompt
    assert "pelo menos uma frase de fundamentação" in engine.prompt
