from __future__ import annotations

from core.chat_feedback import sync_feedback_correction_eval_case
from core.feedback_governance import (
    feedback_allows_memory_reuse,
    feedback_governance_state,
    normalize_feedback_governance,
)
from domain.operational_memory import filter_feedback_for_synthesis


class FakeFeedbackStore:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages
        self.upserted: list[dict] = []
        self.deleted: list[dict] = []

    def list_messages(self, username: str, conversation_id: str) -> list[dict]:
        return self.messages

    def upsert_feedback_eval_case(self, **kwargs) -> dict:
        self.upserted.append(kwargs)
        return kwargs

    def delete_feedback_eval_case(self, **kwargs) -> int:
        self.deleted.append(kwargs)
        return 1


def _conversation_with_assistant(**overrides) -> list[dict]:
    assistant = {
        "id": "a1",
        "role": "assistant",
        "content": "Resposta antiga.",
        "citations": [{"document": "IT-016_Rebocadores.txt"}],
        "feedback_status": "corrected",
        "feedback_correction": "Usa 4 rebocadores em vento norte forte para este caso.",
        "feedback_correction_document": "IT-016_Rebocadores.txt",
        "feedback_note": "",
        "feedback_error_type": "operational_rule_issue",
        "feedback_scope": "tug_guidance",
        "feedback_destination": "eval",
        "feedback_criticality": "high",
    }
    assistant.update(overrides)
    return [
        {"id": "u1", "role": "user", "content": "Quantos rebocadores uso?"},
        assistant,
    ]


def test_corrected_feedback_without_governance_defaults_to_triage() -> None:
    governance = normalize_feedback_governance(feedback_status="corrected")

    assert governance["feedback_destination"] == "triage"
    assert governance["feedback_criticality"] == "medium"


def test_triage_feedback_does_not_reuse_as_memory() -> None:
    item = {
        "feedback_status": "corrected",
        "feedback_destination": "triage",
        "feedback_correction": "Resposta ainda por validar.",
        "similarity": 0.99,
    }

    assert not feedback_allows_memory_reuse(item)
    trusted, _reviewed = filter_feedback_for_synthesis([item], [])
    assert trusted == []


def test_source_update_feedback_is_not_promoted_to_eval(monkeypatch) -> None:
    monkeypatch.setattr("core.chat_feedback.load_bot_settings", lambda: {"auto_promote_corrections": True})
    store = FakeFeedbackStore(
        _conversation_with_assistant(
            feedback_destination="source_update",
            feedback_scope="document",
        )
    )

    result = sync_feedback_correction_eval_case(
        store,
        "piloto",
        "conv1",
        "a1",
        source="web",
    )

    assert result is None
    assert store.upserted == []
    assert store.deleted


def test_fully_governed_feedback_promotes_to_eval(monkeypatch) -> None:
    monkeypatch.setattr("core.chat_feedback.load_bot_settings", lambda: {"auto_promote_corrections": True})
    store = FakeFeedbackStore(_conversation_with_assistant())

    result = sync_feedback_correction_eval_case(
        store,
        "piloto",
        "conv1",
        "a1",
        source="web",
    )

    assert result
    assert store.deleted == []
    assert store.upserted[0]["document"] == "IT-016_Rebocadores.txt"
    assert store.upserted[0]["question"] == "Quantos rebocadores uso?"
    assert "4 rebocadores" in store.upserted[0]["expected_answer"]


def test_governance_state_flags_missing_document_for_document_scope() -> None:
    state = feedback_governance_state(
        {
            "feedback_status": "corrected",
            "feedback_correction": "Resposta corrigida.",
            "feedback_error_type": "wrong_source",
            "feedback_scope": "document",
            "feedback_destination": "eval",
            "feedback_criticality": "medium",
        }
    )

    assert state["state"] == "needs_triage"
    assert "documento" in state["missing"]
