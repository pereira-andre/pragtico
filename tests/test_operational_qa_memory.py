from __future__ import annotations

from types import SimpleNamespace

from core.chat_runtime import _build_supplemental_sources
from domain.chat_response_formatting import add_contextual_response_emojis
from domain.operational_qa_memory import (
    build_qa_memory_sources,
    find_qa_memory_matches,
    load_qa_memory_audit_report,
    load_qa_memory_records,
    qa_memory_supported_questions,
)
from integrations.rag_engine import SimpleRAGEngine


def clear_qa_memory_caches() -> None:
    load_qa_memory_records.cache_clear()
    load_qa_memory_audit_report.cache_clear()
    qa_memory_supported_questions.cache_clear()


def test_qa_memory_matches_paraphrased_critical_tug_case() -> None:
    clear_qa_memory_caches()

    matches = find_qa_memory_matches("RORO 230 metros com vento norte forte: quantos rebocadores?")

    assert matches
    record, score = matches[0]
    assert score >= 0.6
    assert "RORO" in record.question.upper()
    assert any("4 rebocadores grandes" in item for item in record.expected)


def test_qa_memory_avoids_noise_for_short_navigation_conversion() -> None:
    clear_qa_memory_caches()

    matches = find_qa_memory_matches("100 jardas sao quantos metros?")

    assert [record.question for record, _score in matches] == ["100 jardas são quantos metros?"]


def test_qa_memory_source_is_guidance_not_ready_answer() -> None:
    source = build_qa_memory_sources("100 jardas sao quantos metros?")[0]

    assert source["retrieval_mode"] == "operational_qa_memory"
    assert "Nunca copiar literalmente" in source["snippet"]
    assert "fundamentação mínima" in source["snippet"]
    assert "91,44 metros" in source["snippet"]
    assert "Resposta validada anterior" not in source["snippet"]


def test_qa_memory_audit_blocks_known_fundeadouro_norte_one_hour_regression() -> None:
    clear_qa_memory_caches()
    old_question = "Navio do Fundeadouro Norte para a Lisnave deve sair quando para chegar ao reponto das 20:03?"
    report = load_qa_memory_audit_report()
    record = next(item for item in report["records"] if item["question"] == old_question)

    assert record["status"] == "review"
    assert "1h30" in record["reason"]
    assert all(match.question != old_question for match, _score in find_qa_memory_matches(old_question))


def test_qa_memory_keeps_correct_fundeadouro_norte_lisnave_lead_time() -> None:
    clear_qa_memory_caches()

    matches = find_qa_memory_matches(
        "Do Fundeadouro Norte para a LISNAVE, para chegar à próxima preia-mar, quando marco piloto?"
    )

    assert any(
        "1 hora e 30 minutos antes" in " | ".join(record.expected)
        for record, _score in matches
    )


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
