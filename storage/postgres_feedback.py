"""Feedback persistence for chat answers and evaluation cases."""

from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional

from core.feedback_governance import normalize_feedback_governance

from .constants import ALLOWED_FEEDBACK_STATUSES, DEFAULT_CONVERSATION_TITLE, FEEDBACK_APPROVED
from .utils import (
    _clean_text,
    _question_for_assistant_message,
    _text_similarity,
    normalize_feedback_correction,
)


def _unique_citation_documents(message: Dict) -> list[str]:
    documents: list[str] = []
    for citation in message.get("citations") or []:
        document_name = _clean_text((citation or {}).get("document"))
        if document_name and document_name not in documents:
            documents.append(document_name)
    return documents


def _infer_feedback_correction_document(message: Dict, explicit_document: str = "") -> str:
    clean_document = _clean_text(explicit_document)
    if clean_document:
        return clean_document
    cited_documents = _unique_citation_documents(message)
    if len(cited_documents) == 1:
        return cited_documents[0]
    return ""


def _feedback_eval_identity_key(document: str, question: str) -> str:
    return f"{_clean_text(document).lower()}::{_clean_text(question).lower()}"


class PostgresFeedbackMixin:
    def update_message_feedback(
        self,
        username: str,
        conversation_id: str,
        message_id: str,
        feedback_status: str,
        feedback_note: str = "",
        feedback_correction: str = "",
        feedback_correction_document: str = "",
        feedback_error_type: str = "",
        feedback_scope: str = "",
        feedback_destination: str = "",
        feedback_criticality: str = "",
        feedback_updated_by: str = "",
    ) -> Dict:
        if feedback_status not in ALLOWED_FEEDBACK_STATUSES:
            raise ValueError("Estado de feedback inválido.")
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa inválida para este utilizador.")

        updated_by = feedback_updated_by.strip()

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text AS id, role, content, citations
                    FROM messages
                    WHERE conversation_id = %s
                    ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                )
                message_rows = cur.fetchall()
                message_row = next(
                    (
                        row
                        for row in message_rows
                        if row.get("id") == message_id and row.get("role") == "assistant"
                    ),
                    None,
                )
                if not message_row:
                    raise ValueError("Mensagem não encontrada.")
                feedback_question = _question_for_assistant_message(message_rows, message_id)
                correction_text = (
                    normalize_feedback_correction(feedback_question, feedback_correction)
                    if feedback_status == "corrected"
                    else ""
                )
                correction_document = (
                    _infer_feedback_correction_document(
                        {"citations": message_row.get("citations") or []},
                        feedback_correction_document,
                    )
                    if correction_text
                    else ""
                )
                governance = normalize_feedback_governance(
                    feedback_status=feedback_status,
                    feedback_error_type=feedback_error_type,
                    feedback_scope=feedback_scope,
                    feedback_destination=feedback_destination,
                    feedback_criticality=feedback_criticality,
                    feedback_correction_document=correction_document,
                )
                cur.execute(
                    """
                    UPDATE messages
                    SET
                        feedback_status = %s,
                        feedback_note = %s,
                        feedback_correction = %s,
                        feedback_correction_document = %s,
                        feedback_error_type = %s,
                        feedback_scope = %s,
                        feedback_destination = %s,
                        feedback_criticality = %s,
                        feedback_updated_by = %s,
                        feedback_updated_at = NOW()
                    WHERE id = %s AND conversation_id = %s AND role = 'assistant'
                    RETURNING
                        id::text AS id,
                        conversation_id::text AS conversation_id,
                        role,
                        content,
                        citations,
                        created_at,
                        feedback_status,
                        feedback_note,
                        feedback_correction,
                        feedback_correction_document,
                        feedback_error_type,
                        feedback_scope,
                        feedback_destination,
                        feedback_criticality,
                        feedback_updated_by,
                        feedback_updated_at,
                        channel,
                        channel_user_id,
                        external_message_id,
                        external_reply_to_id,
                        channel_metadata
                    """,
                    (
                        feedback_status,
                        feedback_note.strip(),
                        correction_text,
                        correction_document,
                        governance["feedback_error_type"],
                        governance["feedback_scope"],
                        governance["feedback_destination"],
                        governance["feedback_criticality"],
                        updated_by,
                        message_id,
                        conversation_id,
                    ),
                )
                row = cur.fetchone()
                cur.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                    (conversation_id,),
                )
            conn.commit()
        message = self._row_to_chat_message_record(row)
        if not message:
            raise ValueError("Mensagem não encontrada.")
        return message

    def find_feedback_matches(
        self,
        username: str,
        question: str,
        limit: int = 3,
        feedback_statuses: Optional[set[str]] = None,
    ) -> List[Dict]:
        allowed_statuses = {
            (status or "").strip().lower()
            for status in (feedback_statuses or {FEEDBACK_APPROVED})
            if (status or "").strip()
        }
        allowed_statuses &= ALLOWED_FEEDBACK_STATUSES
        if not allowed_statuses:
            return []

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        assistant.id::text AS message_id,
                        assistant.conversation_id::text AS conversation_id,
                        assistant.content AS answer,
                        assistant.citations,
                        assistant.feedback_status,
                        assistant.feedback_note,
                        assistant.feedback_correction,
                        assistant.feedback_correction_document,
                        assistant.feedback_error_type,
                        assistant.feedback_scope,
                        assistant.feedback_destination,
                        assistant.feedback_criticality,
                        assistant.feedback_updated_by,
                        assistant.feedback_updated_at,
                        user_msg.content AS question,
                        c.username
                    FROM messages assistant
                    JOIN conversations c ON c.id = assistant.conversation_id
                    JOIN LATERAL (
                        SELECT content
                        FROM messages
                        WHERE conversation_id = assistant.conversation_id
                          AND role = 'user'
                          AND created_at <= assistant.created_at
                        ORDER BY created_at DESC
                        LIMIT 1
                    ) user_msg ON TRUE
                    WHERE assistant.role = 'assistant'
                      AND assistant.feedback_status = ANY(%s)
                    ORDER BY assistant.feedback_updated_at DESC NULLS LAST, assistant.created_at DESC
                    """,
                    (sorted(allowed_statuses),),
                )
                rows = cur.fetchall()

        matches = []
        for row in rows:
            score = _text_similarity(question, row.get("question", ""))
            if score < 0.35:
                continue
            matches.append(
                {
                    **row,
                    "similarity": round(score, 3),
                    "feedback_updated_at": (
                        row["feedback_updated_at"].isoformat() if row["feedback_updated_at"] else None
                    ),
                }
            )
        matches.sort(
            key=lambda item: (
                item["similarity"],
                item.get("feedback_updated_at") or "",
            ),
            reverse=True,
        )
        return matches[:limit]

    def list_reviewable_chat_messages(
        self,
        *,
        limit: int = 100,
        feedback_status: Optional[str] = None,
    ) -> List[Dict]:
        clean_feedback_status = (feedback_status or "").strip().lower()
        where_feedback = ""
        params: list[object] = []
        if clean_feedback_status == "pending":
            where_feedback = "AND COALESCE(m.feedback_status, '') = ''"
        elif clean_feedback_status in ALLOWED_FEEDBACK_STATUSES:
            where_feedback = "AND m.feedback_status = %s"
            params.append(clean_feedback_status)

        params.append(max(limit, 0))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        m.id::text AS id,
                        m.conversation_id::text AS conversation_id,
                        c.username,
                        c.title AS conversation_title,
                        m.role,
                        m.content,
                        m.citations,
                        m.created_at,
                        m.feedback_status,
                        m.feedback_note,
                        m.feedback_correction,
                        m.feedback_correction_document,
                        m.feedback_error_type,
                        m.feedback_scope,
                        m.feedback_destination,
                        m.feedback_criticality,
                        m.feedback_updated_by,
                        m.feedback_updated_at,
                        m.channel,
                        m.channel_user_id,
                        m.external_message_id,
                        m.external_reply_to_id,
                        m.channel_metadata,
                        (
                            SELECT prev.content
                            FROM messages prev
                            WHERE prev.conversation_id = m.conversation_id
                              AND prev.role = 'user'
                              AND prev.created_at <= m.created_at
                            ORDER BY prev.created_at DESC
                            LIMIT 1
                        ) AS question
                    FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE m.role = 'assistant'
                      {where_feedback}
                    ORDER BY COALESCE(m.feedback_updated_at, m.created_at) DESC, m.created_at DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()

        items = []
        for row in rows:
            message = self._row_to_chat_message_record(row)
            if not message:
                continue
            message["feedback_status"] = (message.get("feedback_status") or "").strip().lower()
            message["question"] = row.get("question") or ""
            message["conversation_title"] = row.get("conversation_title") or DEFAULT_CONVERSATION_TITLE
            message["citation_documents"] = _unique_citation_documents(message)
            items.append(message)
        return items

    def list_feedback_eval_cases(self, *, source: str = "") -> List[Dict]:
        params: list[object] = []
        where_clause = ""
        clean_source = _clean_text(source)
        if clean_source:
            where_clause = "WHERE source = %s"
            params.append(clean_source)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        id::text AS id,
                        source_message_id,
                        document,
                        question,
                        expected_answer,
                        expected_substrings,
                        feedback_note,
                        updated_by,
                        source,
                        created_at,
                        updated_at
                    FROM feedback_eval_cases
                    {where_clause}
                    ORDER BY document ASC, question ASC, updated_at ASC
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
        return [self._row_to_feedback_eval_case_record(row) for row in rows if row]

    def upsert_feedback_eval_case(
        self,
        *,
        source_message_id: str,
        document: str,
        question: str,
        expected_answer: str,
        expected_substrings: List[str],
        feedback_note: str = "",
        updated_by: str = "",
        source: str = "",
    ) -> Dict:
        clean_source_message_id = _clean_text(source_message_id)
        clean_document = _clean_text(document)
        clean_question = _clean_text(question)
        clean_answer = str(expected_answer or "").strip()
        if not clean_document or not clean_question or not clean_answer:
            raise ValueError("Documento, pergunta e resposta esperada são obrigatórios.")

        with self._connect() as conn:
            with conn.cursor() as cur:
                existing_row = None
                if clean_source_message_id:
                    cur.execute(
                        """
                        SELECT id::text AS id, created_at
                        FROM feedback_eval_cases
                        WHERE source_message_id = %s
                        LIMIT 1
                        """,
                        (clean_source_message_id,),
                    )
                    existing_row = cur.fetchone()
                if not existing_row:
                    cur.execute(
                        """
                        SELECT id::text AS id, created_at
                        FROM feedback_eval_cases
                        WHERE document = %s AND question = %s
                        LIMIT 1
                        """,
                        (clean_document, clean_question),
                    )
                    existing_row = cur.fetchone()

                if existing_row:
                    cur.execute(
                        """
                        UPDATE feedback_eval_cases
                        SET
                            source_message_id = %s,
                            document = %s,
                            question = %s,
                            expected_answer = %s,
                            expected_substrings = %s::jsonb,
                            feedback_note = %s,
                            updated_by = %s,
                            source = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING
                            id::text AS id,
                            source_message_id,
                            document,
                            question,
                            expected_answer,
                            expected_substrings,
                            feedback_note,
                            updated_by,
                            source,
                            created_at,
                            updated_at
                        """,
                        (
                            clean_source_message_id,
                            clean_document,
                            clean_question,
                            clean_answer,
                            json.dumps([str(item).strip() for item in expected_substrings if str(item).strip()]),
                            str(feedback_note or "").strip(),
                            str(updated_by or "").strip(),
                            _clean_text(source),
                            existing_row["id"],
                        ),
                    )
                    row = cur.fetchone()
                else:
                    record_id = str(uuid.uuid4())
                    cur.execute(
                        """
                        INSERT INTO feedback_eval_cases (
                            id,
                            source_message_id,
                            document,
                            question,
                            expected_answer,
                            expected_substrings,
                            feedback_note,
                            updated_by,
                            source
                        )
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                        RETURNING
                            id::text AS id,
                            source_message_id,
                            document,
                            question,
                            expected_answer,
                            expected_substrings,
                            feedback_note,
                            updated_by,
                            source,
                            created_at,
                            updated_at
                        """,
                        (
                            record_id,
                            clean_source_message_id,
                            clean_document,
                            clean_question,
                            clean_answer,
                            json.dumps([str(item).strip() for item in expected_substrings if str(item).strip()]),
                            str(feedback_note or "").strip(),
                            str(updated_by or "").strip(),
                            _clean_text(source),
                        ),
                    )
                    row = cur.fetchone()
            conn.commit()
        record = self._row_to_feedback_eval_case_record(row)
        if not record:
            raise ValueError("Falha ao guardar caso de avaliação.")
        return record

    def delete_feedback_eval_case(
        self,
        *,
        source_message_id: str = "",
        document: str = "",
        question: str = "",
    ) -> int:
        clean_source_message_id = _clean_text(source_message_id)
        clean_document = _clean_text(document)
        clean_question = _clean_text(question)
        where_clauses: list[str] = []
        params: list[object] = []
        if clean_source_message_id:
            where_clauses.append("source_message_id = %s")
            params.append(clean_source_message_id)
        if clean_document and clean_question:
            where_clauses.append("(document = %s AND question = %s)")
            params.extend([clean_document, clean_question])
        if not where_clauses:
            return 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM feedback_eval_cases WHERE {' OR '.join(where_clauses)}",
                    tuple(params),
                )
                removed = cur.rowcount or 0
            conn.commit()
        return int(removed)
