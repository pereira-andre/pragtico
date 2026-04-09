"""Helpers for supervised chat feedback and derived evaluation cases."""

from __future__ import annotations

from domain.knowledge_evals import _extract_expected_substrings
from storage.utils import _question_for_assistant_message, normalize_feedback_correction


def _infer_feedback_document(message: dict) -> str:
    explicit_document = str(message.get("feedback_correction_document") or "").strip()
    if explicit_document:
        return explicit_document
    cited_documents: list[str] = []
    for citation in message.get("citations") or []:
        document_name = str((citation or {}).get("document") or "").strip()
        if document_name and document_name not in cited_documents:
            cited_documents.append(document_name)
    if len(cited_documents) == 1:
        return cited_documents[0]
    return ""
def sync_feedback_correction_eval_case(store, username: str, conversation_id: str, message_id: str, *, source: str) -> dict | None:
    if not hasattr(store, "upsert_feedback_eval_case") or not hasattr(store, "delete_feedback_eval_case"):
        return None

    messages = store.list_messages(username, conversation_id)
    target_message = next(
        (item for item in messages if str(item.get("id") or "") == str(message_id)),
        None,
    )
    if not target_message:
        return None

    document_name = _infer_feedback_document(target_message)
    question = _question_for_assistant_message(messages, message_id)
    corrected_answer = normalize_feedback_correction(
        question,
        str(target_message.get("feedback_correction") or "").strip(),
    )
    should_register = (
        (target_message.get("feedback_status") or "").strip().lower() == "review"
        and bool(corrected_answer)
        and bool(document_name)
        and bool(question)
    )
    if should_register:
        return store.upsert_feedback_eval_case(
            source_message_id=message_id,
            document=document_name,
            question=question,
            expected_answer=corrected_answer,
            expected_substrings=_extract_expected_substrings(corrected_answer),
            feedback_note=str(target_message.get("feedback_note") or "").strip(),
            updated_by=str(target_message.get("feedback_updated_by") or "").strip(),
            source=source,
        )

    store.delete_feedback_eval_case(
        source_message_id=message_id,
        document=document_name,
        question=question,
    )
    return None
