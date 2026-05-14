from __future__ import annotations

from core.chat_runtime import _build_supplemental_sources
from domain.answer_contract import (
    answer_contract_trace,
    build_response_contract_source,
    is_critical_operational_question,
)


def test_response_contract_marks_critical_operational_questions() -> None:
    assert is_critical_operational_question("Quantos rebocadores uso para um RORO com vento?")
    assert not is_critical_operational_question("Bom dia")


def test_response_contract_source_is_first_supplemental_source() -> None:
    sources = _build_supplemental_sources("100 jardas sao quantos metros?")

    assert sources[0]["retrieval_mode"] == "response_contract"
    assert sources[0]["source_id"] == "CONTRACT1"
    assert "Nunca responder só com um número" in sources[0]["snippet"]


def test_response_contract_trace_documents_feedback_boundary() -> None:
    trace = answer_contract_trace("mares hoje")

    assert trace["version"]
    assert trace["feedback_reuse"] == "only destination=memory with approved/corrected status"


def test_response_contract_source_explains_qa_memory_boundary() -> None:
    source = build_response_contract_source("revê a resposta da maré")

    assert source["retrieval_mode"] == "response_contract"
    assert "Memória QA curada" in source["snippet"]
    assert "nunca texto final pronto" in source["snippet"]
