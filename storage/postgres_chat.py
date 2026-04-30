"""Conversation and channel persistence for the PostgreSQL store."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from .constants import DEFAULT_CONVERSATION_TITLE
from .utils import _conversation_title_from_text, _normalize_username, _utc_iso_to_label


class PostgresChatMixin:
    def list_conversations(self, username: str) -> List[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text AS id, username, title, created_at, updated_at
                    FROM conversations
                    WHERE username = %s
                    ORDER BY updated_at DESC
                    """,
                    (username,),
                )
                rows = cur.fetchall()
        return [
            {
                **row,
                "updated_at": row["updated_at"].isoformat(),
                "created_at": row["created_at"].isoformat(),
                "created_at_label": _utc_iso_to_label(row["created_at"].isoformat()),
                "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
            }
            for row in rows
        ]

    def create_conversation(self, username: str, title: str = DEFAULT_CONVERSATION_TITLE) -> Dict:
        conversation_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversations (id, username, title)
                    VALUES (%s, %s, %s)
                    RETURNING id::text AS id, username, title, created_at, updated_at
                    """,
                    (conversation_id, username, title),
                )
                row = cur.fetchone()
            conn.commit()
        return {
            **row,
            "updated_at": row["updated_at"].isoformat(),
            "created_at": row["created_at"].isoformat(),
            "created_at_label": _utc_iso_to_label(row["created_at"].isoformat()),
            "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
        }

    def rename_conversation(self, username: str, conversation_id: str, title: str) -> Dict:
        clean_title = " ".join(title.strip().split())
        if not clean_title:
            raise ValueError("O título da conversa não pode ficar vazio.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE conversations
                    SET title = %s, updated_at = NOW()
                    WHERE id = %s AND username = %s
                    RETURNING id::text AS id, username, title, created_at, updated_at
                    """,
                    (clean_title, conversation_id, username),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ValueError("Conversa não encontrada.")
        return {
            **row,
            "updated_at": row["updated_at"].isoformat(),
            "created_at": row["created_at"].isoformat(),
            "created_at_label": _utc_iso_to_label(row["created_at"].isoformat()),
            "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
        }

    def clear_conversation(self, username: str, conversation_id: str) -> None:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa não encontrada.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM messages WHERE conversation_id = %s",
                    (conversation_id,),
                )
                cur.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                    (conversation_id,),
                )
            conn.commit()

    def delete_conversation(self, username: str, conversation_id: str) -> Optional[str]:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa não encontrada.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM conversations WHERE id = %s AND username = %s",
                    (conversation_id, username),
                )
            conn.commit()
        remaining = self.list_conversations(username)
        return remaining[0]["id"] if remaining else None

    def ensure_conversation(self, username: str, conversation_id: Optional[str] = None) -> Dict:
        if conversation_id:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id::text AS id, username, title, created_at, updated_at
                        FROM conversations
                        WHERE id = %s AND username = %s
                        """,
                        (conversation_id, username),
                    )
                    row = cur.fetchone()
            if row:
                return {
                    **row,
                    "updated_at": row["updated_at"].isoformat(),
                    "created_at": row["created_at"].isoformat(),
                    "created_at_label": _utc_iso_to_label(row["created_at"].isoformat()),
                    "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
                }

        conversations = self.list_conversations(username)
        if conversations:
            return conversations[0]
        return self.create_conversation(username)

    def list_messages(self, username: str, conversation_id: str) -> List[Dict]:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
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
                        feedback_updated_by,
                        feedback_updated_at,
                        channel,
                        channel_user_id,
                        external_message_id,
                        external_reply_to_id,
                        channel_metadata
                    FROM messages
                    WHERE conversation_id = %s
                    ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                )
                rows = cur.fetchall()
        return [self._row_to_chat_message_record(row) for row in rows if row]

    def append_chat_message(
        self,
        username: str,
        conversation_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict]] = None,
        *,
        channel: str = "web",
        channel_user_id: str = "",
        external_message_id: str = "",
        external_reply_to_id: str = "",
        channel_metadata: Optional[Dict] = None,
    ) -> Dict:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa inválida para este utilizador.")
        message_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO messages (
                        id,
                        conversation_id,
                        role,
                        content,
                        citations,
                        channel,
                        channel_user_id,
                        external_message_id,
                        external_reply_to_id,
                        channel_metadata
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb)
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
                        feedback_updated_by,
                        feedback_updated_at,
                        channel,
                        channel_user_id,
                        external_message_id,
                        external_reply_to_id,
                        channel_metadata
                    """,
                    (
                        message_id,
                        conversation_id,
                        role,
                        content,
                        json.dumps(citations or []),
                        (channel or "web").strip() or "web",
                        (channel_user_id or "").strip(),
                        (external_message_id or "").strip(),
                        (external_reply_to_id or "").strip(),
                        json.dumps(channel_metadata or {}),
                    ),
                )
                row = cur.fetchone()

                title_hint = None
                if role == "user":
                    cur.execute(
                        """
                        SELECT
                            title,
                            COUNT(*) FILTER (WHERE role = 'user') AS user_message_count
                        FROM conversations c
                        LEFT JOIN messages m ON m.conversation_id = c.id
                        WHERE c.id = %s
                        GROUP BY c.title
                        """,
                        (conversation_id,),
                    )
                    stats = cur.fetchone()
                    if stats and (
                        stats["title"] == DEFAULT_CONVERSATION_TITLE
                        or stats["user_message_count"] <= 1
                    ):
                        title_hint = _conversation_title_from_text(content)

                if title_hint:
                    cur.execute(
                        "UPDATE conversations SET title = %s, updated_at = NOW() WHERE id = %s",
                        (title_hint, conversation_id),
                    )
                else:
                    cur.execute(
                        "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                        (conversation_id,),
                    )
            conn.commit()
        message = self._row_to_chat_message_record(row)
        if not message:
            raise ValueError("Falha ao gravar mensagem.")
        return message

    def update_message_channel_metadata(
        self,
        username: str,
        conversation_id: str,
        message_id: str,
        *,
        channel: Optional[str] = None,
        channel_user_id: Optional[str] = None,
        external_message_id: Optional[str] = None,
        external_reply_to_id: Optional[str] = None,
        channel_metadata: Optional[Dict] = None,
    ) -> Dict:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa inválida para este utilizador.")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE messages
                    SET
                        channel = COALESCE(%s, channel),
                        channel_user_id = COALESCE(%s, channel_user_id),
                        external_message_id = COALESCE(%s, external_message_id),
                        external_reply_to_id = COALESCE(%s, external_reply_to_id),
                        channel_metadata = COALESCE(channel_metadata, '{}'::jsonb) || %s::jsonb
                    WHERE id = %s AND conversation_id = %s
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
                        feedback_updated_by,
                        feedback_updated_at,
                        channel,
                        channel_user_id,
                        external_message_id,
                        external_reply_to_id,
                        channel_metadata
                    """,
                    (
                        (channel or "").strip() or None if channel is not None else None,
                        (channel_user_id or "").strip() if channel_user_id is not None else None,
                        (external_message_id or "").strip() if external_message_id is not None else None,
                        (external_reply_to_id or "").strip() if external_reply_to_id is not None else None,
                        json.dumps(channel_metadata or {}),
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

    def find_message_by_channel_message_id(self, channel: str, external_message_id: str) -> Optional[Dict]:
        clean_channel = (channel or "").strip() or "web"
        clean_external_id = (external_message_id or "").strip()
        if not clean_external_id:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        m.id::text AS id,
                        m.conversation_id::text AS conversation_id,
                        c.username,
                        m.role,
                        m.content,
                        m.citations,
                        m.created_at,
                        m.feedback_status,
                        m.feedback_note,
                        m.feedback_correction,
                        m.feedback_correction_document,
                        m.feedback_updated_by,
                        m.feedback_updated_at,
                        m.channel,
                        m.channel_user_id,
                        m.external_message_id,
                        m.external_reply_to_id,
                        m.channel_metadata
                    FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE m.channel = %s
                      AND m.external_message_id = %s
                    LIMIT 1
                    """,
                    (clean_channel, clean_external_id),
                )
                row = cur.fetchone()
        return self._row_to_chat_message_record(row)

    def get_runtime_state(self, key: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT value
                    FROM app_runtime_state
                    WHERE key = %s
                    """,
                    (key,),
                )
                row = cur.fetchone()
        value = row["value"] if row else None
        return value if isinstance(value, dict) else None

    def set_runtime_state(self, key: str, value: Dict) -> Dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_runtime_state (key, value, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    (key, json.dumps(value or {})),
                )
            conn.commit()
        return value

    def delete_runtime_state(self, key: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM app_runtime_state WHERE key = %s", (key,))
            conn.commit()

    def record_channel_event(
        self,
        *,
        channel: str,
        event_type: str,
        payload: Dict,
        username: str = "",
        conversation_id: str = "",
        local_message_id: str = "",
        channel_user_id: str = "",
        external_event_id: str = "",
        external_message_id: str = "",
    ) -> Dict:
        event_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO channel_events (
                        id,
                        channel,
                        event_type,
                        username,
                        conversation_id,
                        local_message_id,
                        channel_user_id,
                        external_event_id,
                        external_message_id,
                        payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING
                        id::text AS id,
                        channel,
                        event_type,
                        username,
                        conversation_id::text AS conversation_id,
                        local_message_id::text AS local_message_id,
                        channel_user_id,
                        external_event_id,
                        external_message_id,
                        payload,
                        created_at
                    """,
                    (
                        event_id,
                        (channel or "").strip() or "unknown",
                        (event_type or "").strip() or "unknown",
                        _normalize_username(username),
                        conversation_id or None,
                        local_message_id or None,
                        (channel_user_id or "").strip(),
                        (external_event_id or "").strip(),
                        (external_message_id or "").strip(),
                        json.dumps(payload or {}),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return {
            **row,
            "created_at": row["created_at"].isoformat(),
        }

    def list_channel_events(
        self,
        *,
        channel: str,
        since: str = "",
        limit: int = 20,
    ) -> List[Dict]:
        clean_channel = (channel or "").strip() or "unknown"
        max_items = max(1, min(int(limit or 20), 100))
        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError:
                since_dt = None

        where_since = ""
        params: list[object] = [clean_channel]
        if since_dt is not None:
            where_since = "AND created_at > %s"
            params.append(since_dt)
        params.append(max_items)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        id::text AS id,
                        channel,
                        event_type,
                        username,
                        conversation_id::text AS conversation_id,
                        local_message_id::text AS local_message_id,
                        channel_user_id,
                        external_event_id,
                        external_message_id,
                        payload,
                        created_at
                    FROM channel_events
                    WHERE channel = %s
                      {where_since}
                    ORDER BY created_at ASC, id ASC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
        return [
            {
                **row,
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]
