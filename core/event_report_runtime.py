from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core import services
from domain.event_reports import (
    build_event_report_template,
    format_event_report_answer,
    is_cancel_reply,
    is_no_photo_reply,
    normalize_event_description,
    parse_event_report_command,
    pending_event_report_key,
    register_event_report,
)


logger = logging.getLogger(__name__)


def _user_label(username: str) -> str:
    profile = services.store.get_user_profile(username) or {}
    return (
        str(profile.get("full_name") or "").strip()
        or str(profile.get("organization") or "").strip()
        or str(profile.get("email") or "").strip()
        or username
    )


def _pending_key(
    *,
    channel: str,
    username: str,
    conversation_id: str,
    channel_user_id: str = "",
) -> str:
    return pending_event_report_key(
        channel=channel,
        username=username,
        conversation_id=conversation_id,
        channel_user_id=channel_user_id,
    )


def load_pending_event_report(
    *,
    channel: str,
    username: str,
    conversation_id: str,
    channel_user_id: str = "",
) -> dict[str, Any] | None:
    key = _pending_key(
        channel=channel,
        username=username,
        conversation_id=conversation_id,
        channel_user_id=channel_user_id,
    )
    payload = services.store.get_runtime_state(key)
    if not payload:
        return None
    if str(payload.get("username") or "") != username:
        return None
    if str(payload.get("conversation_id") or "") != conversation_id:
        return None
    return payload


def save_pending_event_report(
    *,
    username: str,
    role: str,
    conversation_id: str,
    channel: str,
    channel_user_id: str = "",
    inbound_message_id: str = "",
    draft: dict[str, Any],
) -> dict[str, Any]:
    key = _pending_key(
        channel=channel,
        username=username,
        conversation_id=conversation_id,
        channel_user_id=channel_user_id,
    )
    payload = {
        "username": username,
        "role": role,
        "conversation_id": conversation_id,
        "channel": channel,
        "channel_user_id": channel_user_id,
        "inbound_message_id": inbound_message_id,
        "draft": draft,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    services.store.set_runtime_state(key, payload)
    return payload


def clear_pending_event_report(
    *,
    channel: str,
    username: str,
    conversation_id: str,
    channel_user_id: str = "",
) -> None:
    services.store.delete_runtime_state(
        _pending_key(
            channel=channel,
            username=username,
            conversation_id=conversation_id,
            channel_user_id=channel_user_id,
        )
    )


def build_event_report_photo_prompt(draft: dict[str, Any]) -> str:
    return (
        "Reporte preparado.\n\n"
        f"Tipo: {draft.get('tag', '--')}\n"
        f"Local: {draft.get('local', '--')}\n\n"
        "Queres anexar uma foto? Envia a foto agora ou responde `não` para arquivar sem anexo."
    )


def _polish_description(description: str) -> str:
    fallback = normalize_event_description(description)
    rag = getattr(services, "rag", None)
    if not rag or not getattr(rag, "can_generate", lambda: False)():
        return fallback
    prompt = (
        "Reescreve a descricao operacional abaixo em portugues europeu, com clareza, "
        "sem inventar factos, sem acrescentar notificacoes ou conclusoes, e devolve apenas "
        "um paragrafo curto.\n\n"
        f"Descricao original:\n{description}"
    )
    try:
        result = rag.generate_text(prompt)
    except Exception:
        logger.exception("Falha ao formatar reporte de evento com LLM.")
        return fallback
    candidate = str(getattr(result, "text", result) or "").strip()
    candidate = " ".join(candidate.split())
    if 5 <= len(candidate) <= 1200:
        return candidate
    return fallback


def finalize_pending_event_report(
    pending: dict[str, Any],
    *,
    attachment_bytes: bytes | None = None,
    attachment_mime_type: str = "",
    attachment_filename: str = "",
    media_id: str = "",
) -> dict[str, Any]:
    draft = dict(pending.get("draft") or {})
    original_description = str(draft.get("description_original") or "")
    event = register_event_report(
        draft,
        username=str(pending.get("username") or ""),
        role=str(pending.get("role") or ""),
        user_label=_user_label(str(pending.get("username") or "")),
        description_processed=_polish_description(original_description),
        attachment_bytes=attachment_bytes,
        attachment_mime_type=attachment_mime_type,
        attachment_filename=attachment_filename,
        media_id=media_id,
    )
    clear_pending_event_report(
        channel=str(pending.get("channel") or ""),
        username=str(pending.get("username") or ""),
        conversation_id=str(pending.get("conversation_id") or ""),
        channel_user_id=str(pending.get("channel_user_id") or ""),
    )
    return event


__all__ = [
    "build_event_report_photo_prompt",
    "build_event_report_template",
    "clear_pending_event_report",
    "finalize_pending_event_report",
    "format_event_report_answer",
    "is_cancel_reply",
    "is_no_photo_reply",
    "load_pending_event_report",
    "parse_event_report_command",
    "save_pending_event_report",
]
