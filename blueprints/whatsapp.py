"""WhatsApp webhook blueprint connected to the shared PRAGtico chat runtime."""

from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, current_app, jsonify, request

from core import services
from core.chat_feedback import sync_feedback_correction_eval_case
from core.chat_runtime import handle_chat_turn
from core.operational_diagnostics import build_operational_diagnostic, format_operational_diagnostic
from core.rule_catalog import _active_knowledge_dir
from core.event_report_runtime import (
    finalize_pending_event_report,
    format_event_report_answer,
    load_pending_event_report,
)
from domain.error_catalog import error_ref, log_error_event, user_error_message
from domain.sos_alerts import (
    build_sos_admin_alert,
    build_sos_admin_cancel_alert,
    build_sos_cancelled_reply,
    build_sos_dispatched_cancelled_reply,
    build_sos_disabled_reply,
    build_sos_event_id,
    build_sos_expired_reply,
    build_sos_location_prompt,
    build_sos_no_pending_cancel_reply,
    build_sos_no_pending_location_reply,
    build_sos_user_confirmation,
    configured_sos_numbers,
    is_sos_cancel,
    is_sos_trigger,
    local_datetime_label,
    normalize_location,
    sos_admin_recipients,
    sos_alerts_enabled,
    sos_last_event_key,
    sos_pending_expired,
    sos_event_key,
    sos_pending_key,
    utc_now_iso,
)

bp = Blueprint("whatsapp", __name__)

WHATSAPP_TEXT_CHUNK_LIMIT = 3600
WHATSAPP_INBOUND_PROCESSING_TTL = timedelta(minutes=15)


def _normalize_whatsapp_role(value: str | None) -> str:
    role = (value or "").strip().lower()
    return role if role in {"admin", "agente", "piloto"} else "piloto"


def _whatsapp_username(from_number: str) -> str:
    return f"whatsapp-{from_number}@pragtico.local"


def _processed_inbound_key(message_id: str) -> str:
    return f"whatsapp:processed:{message_id}"


def _welcome_sent_key(from_number: str) -> str:
    return f"whatsapp:welcome:{from_number}"


def _pending_feedback_correction_key(from_number: str) -> str:
    return f"whatsapp:feedback-correction:{from_number}"


def _outbound_message_alias_key(external_message_id: str) -> str:
    return f"whatsapp:outbound:{external_message_id}"


def _sos_user_label(profile: dict | None, fallback_name: str = "") -> str:
    profile = profile or {}
    return (
        str(profile.get("full_name") or "").strip()
        or str(fallback_name or "").strip()
        or str(profile.get("username") or "").strip()
        or "Utilizador WhatsApp"
    )


def _save_pending_sos(
    *,
    from_number: str,
    profile: dict,
    conversation_id: str,
    message_id: str,
    text: str,
) -> dict:
    number_suffix = "".join(char for char in from_number if char.isdigit())[-4:]
    event_id = build_sos_event_id()
    if number_suffix:
        event_id = f"{event_id}-{number_suffix}"
    payload = {
        "event_id": event_id,
        "from_number": from_number,
        "username": profile.get("username", ""),
        "user_label": _sos_user_label(profile),
        "conversation_id": conversation_id,
        "message_id": message_id,
        "initial_text": text,
        "requested_at": utc_now_iso(),
    }
    services.store.set_runtime_state(sos_pending_key(from_number), payload)
    return payload


def _load_pending_sos(from_number: str) -> dict:
    return services.store.get_runtime_state(sos_pending_key(from_number)) or {}


def _clear_pending_sos(from_number: str) -> None:
    services.store.delete_runtime_state(sos_pending_key(from_number))


def _send_sos_alerts(
    service,
    *,
    requester_username: str,
    requester_conversation_id: str,
    requester_message_id: str,
    alert_text: str,
    recipients: list[dict],
    sos_event_id: str,
    event_type: str = "outgoing_sos_alert",
    message_kind: str = "sos_admin_alert",
) -> tuple[int, list[str]]:
    sent = 0
    failed: list[str] = []
    for recipient in recipients:
        number = recipient.get("number", "")
        username = recipient.get("username", "")
        try:
            if username:
                conversation = services.store.ensure_conversation(username=username)
                local_message = services.store.append_chat_message(
                    username=username,
                    conversation_id=conversation["id"],
                    role="assistant",
                    content=alert_text,
                    channel="whatsapp",
                    channel_user_id=number,
                    external_reply_to_id="",
                    channel_metadata={
                        "message_kind": message_kind,
                        "sos_event_id": sos_event_id,
                        "requester_message_id": requester_message_id,
                    },
                )
                _send_and_record_outbound_message(
                    service,
                    username=username,
                    conversation_id=conversation["id"],
                    local_message_id=local_message["id"],
                    content=alert_text,
                    to_number=number,
                    reply_to_message_id="",
                    event_type=event_type,
                )
            else:
                text_parts = _split_whatsapp_text(alert_text)
                for index, text_part in enumerate(text_parts, start=1):
                    send_response = service.send_text_message(number, text_part, reply_to_message_id="")
                    outbound_id = _extract_outbound_message_id(send_response)
                    event_payload = send_response
                    if len(text_parts) > 1:
                        event_payload = {
                            **send_response,
                            "part_index": index,
                            "part_total": len(text_parts),
                        }
                    services.store.record_channel_event(
                        channel="whatsapp",
                        event_type=event_type,
                        payload=event_payload,
                        username=requester_username,
                        conversation_id=requester_conversation_id,
                        local_message_id="",
                        channel_user_id=number,
                        external_event_id=outbound_id,
                        external_message_id=outbound_id,
                    )
            sent += 1
        except Exception as exc:
            current_app.logger.exception("Falha ao enviar alerta SOS para %s.", number)
            failed.append(f"{number}: {exc}")
    return sent, failed


def _reaction_feedback_status(emoji: str | None) -> str:
    if (emoji or "").strip() == "👍":
        return "approved"
    if (emoji or "").strip() == "👎":
        return "review"
    return ""


def _extract_outbound_message_id(payload: dict | None) -> str:
    messages = (payload or {}).get("messages") or []
    if not messages:
        return ""
    first = messages[0] or {}
    return str(first.get("id") or "").strip()


def _send_whatsapp_error_reply(
    service,
    *,
    from_number: str,
    message_id: str,
    profile: dict | None,
) -> bool:
    error_text = user_error_message("CHAT_RUNTIME_FAILED", channel="whatsapp")
    username = str((profile or {}).get("username") or _whatsapp_username(from_number)).strip()
    try:
        conversation = services.store.ensure_conversation(username=username)
        assistant_message = services.store.append_chat_message(
            username=username,
            conversation_id=conversation["id"],
            role="assistant",
            content=error_text,
            channel="whatsapp",
            channel_user_id=from_number,
            external_reply_to_id=message_id,
            channel_metadata={
                "message_kind": "error",
                "error_ref": error_ref("CHAT_RUNTIME_FAILED"),
            },
        )
        _send_and_record_outbound_message(
            service,
            username=username,
            conversation_id=conversation["id"],
            local_message_id=assistant_message["id"],
            content=error_text,
            to_number=from_number,
            reply_to_message_id=message_id,
            event_type="outgoing_error",
        )
        _mark_inbound_processed(
            message_id,
            from_number=from_number,
            conversation_id=conversation["id"],
            answer=error_text,
        )
        return True
    except Exception as send_exc:
        current_app.logger.exception(
            "Falha ao enviar resposta de erro WhatsApp (from=%s, msg=%s): %s",
            from_number,
            message_id,
            send_exc,
        )
        return False


def _is_duplicate_inbound(message_id: str) -> bool:
    if not message_id:
        return False
    state = services.store.get_runtime_state(_processed_inbound_key(message_id))
    return _inbound_state_is_active(state)


def _inbound_state_is_active(state: dict | None) -> bool:
    if not isinstance(state, dict) or not state:
        return False
    status = str(state.get("status") or "").strip().lower()
    if status == "processing":
        started_raw = str(state.get("processing_started_at") or "").strip()
        try:
            started_at = datetime.fromisoformat(started_raw)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return datetime.now(timezone.utc) - started_at <= WHATSAPP_INBOUND_PROCESSING_TTL
    return True


def _claim_inbound_processing(message_id: str, *, from_number: str) -> bool:
    if not message_id:
        return True
    key = _processed_inbound_key(message_id)
    try:
        if _inbound_state_is_active(services.store.get_runtime_state(key)):
            return False
        services.store.set_runtime_state(
            key,
            {
                "status": "processing",
                "message_id": message_id,
                "from_number": from_number,
                "processing_started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return True
    except Exception:
        current_app.logger.exception(
            "Falha não bloqueante ao marcar inbound WhatsApp como em processamento (msg=%s).",
            message_id,
        )
        return True


def _mark_inbound_processed(message_id: str, *, from_number: str, conversation_id: str, answer: str) -> None:
    if not message_id:
        return
    try:
        services.store.set_runtime_state(
            _processed_inbound_key(message_id),
            {
                "status": "processed",
                "message_id": message_id,
                "from_number": from_number,
                "conversation_id": conversation_id,
                "answer_preview": str(answer or "")[:240],
                "processed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        current_app.logger.exception(
            "Falha não bloqueante ao marcar inbound WhatsApp como processado (msg=%s).",
            message_id,
        )


def _welcome_already_sent(from_number: str) -> bool:
    if not from_number:
        return False
    return bool(services.store.get_runtime_state(_welcome_sent_key(from_number)))


def _mark_welcome_sent(
    from_number: str,
    *,
    conversation_id: str,
    local_message_id: str,
    external_message_id: str,
) -> None:
    if not from_number:
        return
    services.store.set_runtime_state(
        _welcome_sent_key(from_number),
        {
            "from_number": from_number,
            "conversation_id": conversation_id,
            "local_message_id": local_message_id,
            "external_message_id": external_message_id,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _feedback_correction_skip_requested(text: str) -> bool:
    clean = (text or "").strip().lower()
    return clean in {
        "ignorar",
        "cancelar",
        "saltar",
        "sem correcao",
        "sem correção",
        "sem resposta",
    }


_NON_REVISABLE_ASSISTANT_MESSAGE_KINDS = {
    "error",
    "feedback_correction_ack",
    "feedback_correction_prompt",
    "answer_retry_without_context",
    "answer_diagnostic",
    "sos_cancelled",
    "sos_cancel_without_pending",
    "sos_disabled",
    "sos_dispatched_cancelled",
    "sos_location_invalid",
    "sos_location_prompt",
    "welcome",
}


def _normalize_command_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.lower()
    return re.sub(r"\s+", " ", normalized).strip()


def _whatsapp_answer_retry_requested(text: str) -> bool:
    clean = _normalize_command_text(text)
    if not clean:
        return False
    exact_commands = {
        "reve",
        "rever",
        "revisa",
        "revisao",
        "reformula",
        "reformular",
        "nova tentativa",
        "tenta outra vez",
        "tenta de novo",
        "responde outra vez",
        "pensa melhor",
        "nao era isso",
    }
    if clean in exact_commands:
        return True
    command_prefixes = (
        "reve ",
        "rever ",
        "revisa ",
        "reformula ",
        "tenta outra vez",
        "tenta de novo",
        "responde outra vez",
        "confirma melhor",
        "verifica melhor",
        "corrige a resposta",
    )
    if any(clean.startswith(prefix) for prefix in command_prefixes):
        return True
    return any(
        phrase in clean
        for phrase in (
            "podes rever",
            "podes revisar",
            "podes reformular",
            "nova tentativa",
            "nao era isso",
            "pensa melhor",
            "responde de novo",
        )
    )


def _whatsapp_diagnostic_requested(text: str) -> bool:
    clean = _normalize_command_text(text).strip(" ?!.")
    if not clean:
        return False
    exact_commands = {
        "/diagnostico",
        "diagnostico",
        "mostra diagnostico",
        "explica diagnostico",
        "/porque",
        "/por que",
        "/porque?",
        "porque",
        "por que",
    }
    if clean in exact_commands:
        return True
    return clean.startswith("/diagnostico ") or clean.startswith("diagnostico da resposta")


def _assistant_message_is_revisable(message: dict) -> bool:
    if message.get("role") != "assistant":
        return False
    metadata = message.get("channel_metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    message_kind = str(metadata.get("message_kind") or "").strip()
    return message_kind not in _NON_REVISABLE_ASSISTANT_MESSAGE_KINDS


def _latest_assistant_diagnostic(
    username: str,
    conversation_id: str,
) -> dict:
    conversation = services.store.ensure_conversation(username, conversation_id)
    if conversation["id"] != conversation_id:
        raise ValueError("Conversa não encontrada.")
    messages = services.store.list_messages(username, conversation_id)
    for target_index in range(len(messages) - 1, -1, -1):
        target = messages[target_index]
        if not _assistant_message_is_revisable(target):
            continue
        metadata = target.get("channel_metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        diagnostic = metadata.get("operational_diagnostic")
        if not isinstance(diagnostic, dict) or not diagnostic.get("present"):
            previous_user = next(
                (
                    item
                    for item in reversed(messages[:target_index])
                    if item.get("role") == "user" and str(item.get("content") or "").strip()
                ),
                None,
            )
            if not previous_user:
                raise ValueError("Não encontrei a pergunta original dessa resposta.")
            diagnostic = build_operational_diagnostic(
                str(previous_user.get("content") or ""),
                history=messages[:target_index],
                answer={"answer": str(target.get("content") or "")},
                knowledge_dir=_active_knowledge_dir() or "knowledge",
            )
        return diagnostic
    raise ValueError("Não encontrei uma resposta anterior nesta conversa para diagnosticar.")


def _assistant_revision_context_from_messages(
    messages: list[dict],
    target_index: int,
) -> dict:
    previous_user = next(
        (
            item
            for item in reversed(messages[:target_index])
            if item.get("role") == "user"
        ),
        None,
    )
    if not previous_user:
        raise ValueError("Não encontrei a pergunta original dessa resposta.")
    target_message = messages[target_index]
    return {
        "original_question": str(previous_user.get("content") or "").strip(),
        "previous_answer": str(target_message.get("content") or "").strip(),
        "target_message_id": str(target_message.get("id") or ""),
    }


def _assistant_message_revision_context(
    username: str,
    conversation_id: str,
    message_id: str,
) -> dict:
    conversation = services.store.ensure_conversation(username, conversation_id)
    if conversation["id"] != conversation_id:
        raise ValueError("Conversa não encontrada.")
    messages = services.store.list_messages(username, conversation_id)
    target_index = next(
        (
            index
            for index, item in enumerate(messages)
            if str(item.get("id") or "") == str(message_id)
            and _assistant_message_is_revisable(item)
        ),
        None,
    )
    if target_index is None:
        raise ValueError("Resposta não encontrada.")
    return _assistant_revision_context_from_messages(messages, target_index)


def _latest_assistant_revision_context(username: str, conversation_id: str) -> dict:
    conversation = services.store.ensure_conversation(username, conversation_id)
    if conversation["id"] != conversation_id:
        raise ValueError("Conversa não encontrada.")
    messages = services.store.list_messages(username, conversation_id)
    for target_index in range(len(messages) - 1, -1, -1):
        if _assistant_message_is_revisable(messages[target_index]):
            return _assistant_revision_context_from_messages(messages, target_index)
    raise ValueError("Não encontrei uma resposta anterior nesta conversa para rever.")


def _build_answer_retry_prompt(user_note: str) -> str:
    retry_prompt = "Revê a tua resposta anterior e tenta responder de novo sem repetir a mesma síntese."
    clean_note = (user_note or "").strip()
    if clean_note:
        retry_prompt += f" Observação: {clean_note}"
    return retry_prompt


def _find_whatsapp_message_by_external_id(external_message_id: str) -> dict | None:
    clean_external_id = (external_message_id or "").strip()
    if not clean_external_id:
        return None
    matched_message = services.store.find_message_by_channel_message_id("whatsapp", clean_external_id)
    if matched_message:
        return matched_message

    alias = services.store.get_runtime_state(_outbound_message_alias_key(clean_external_id)) or {}
    username = str(alias.get("username") or "").strip()
    conversation_id = str(alias.get("conversation_id") or "").strip()
    local_message_id = str(alias.get("local_message_id") or "").strip()
    if not username or not conversation_id or not local_message_id:
        return None
    try:
        messages = services.store.list_messages(username, conversation_id)
    except Exception:
        current_app.logger.exception(
            "Falha ao resolver alias de outbound WhatsApp (wamid=%s).",
            clean_external_id,
        )
        return None
    for message in messages:
        if message.get("id") == local_message_id:
            resolved = dict(message)
            resolved.setdefault("username", username)
            resolved.setdefault("conversation_id", conversation_id)
            return resolved
    return None


def _take_text_chunk(text: str, limit: int) -> tuple[str, str]:
    if len(text) <= limit:
        return text.strip(), ""

    split_at = max(
        text.rfind(delimiter, 0, limit + 1) + len(delimiter)
        for delimiter in ("\n\n", "\n", ". ", "; ", ", ", " ")
    )
    if split_at < max(1, int(limit * 0.45)):
        split_at = limit

    chunk = text[:split_at].strip()
    remainder = text[split_at:].strip()
    return chunk, remainder


def _split_whatsapp_text(text: str, *, limit: int = WHATSAPP_TEXT_CHUNK_LIMIT) -> list[str]:
    clean = str(text or "").strip()
    if not clean:
        return [""]
    if limit < 32:
        raise ValueError("Limite WhatsApp demasiado baixo para dividir mensagens.")
    if len(clean) <= limit:
        return [clean]

    part_limit = limit - 12
    chunks: list[str] = []
    remaining = clean
    while remaining:
        chunk, remaining = _take_text_chunk(remaining, part_limit)
        if not chunk and remaining:
            chunk = remaining[:part_limit].strip()
            remaining = remaining[part_limit:].strip()
        if chunk:
            chunks.append(chunk)

    if len(chunks) <= 1:
        return chunks or [clean[:limit]]
    total = len(chunks)
    return [f"({index}/{total}) {chunk}" for index, chunk in enumerate(chunks, start=1)]


def _ensure_whatsapp_user(from_number: str, profile_name: str, default_role: str) -> dict:
    username = _whatsapp_username(from_number)
    profile = services.store.get_user_profile(username)
    if profile:
        return profile
    return services.store.create_user(
        username=username,
        password=secrets.token_urlsafe(24),
        role=_normalize_whatsapp_role(default_role),
        full_name=(profile_name or f"WhatsApp {from_number}").strip(),
        organization="WhatsApp",
        email=username,
        phone=f"+{from_number}",
        whatsapp_number=from_number,
        whatsapp_opt_in=True,
    )


def _send_and_record_outbound_message(
    service,
    *,
    username: str,
    conversation_id: str,
    local_message_id: str,
    content: str,
    to_number: str,
    reply_to_message_id: str,
    event_type: str,
    template_name: str = "",
    template_language: str = "",
) -> tuple[dict, str]:
    if template_name.strip():
        send_response = service.send_template_message(
            to_number,
            template_name=template_name.strip(),
            language_code=template_language.strip() or "pt_PT",
            reply_to_message_id=reply_to_message_id,
        )
        send_responses = [send_response]
    else:
        text_parts = _split_whatsapp_text(content)
        send_responses = []
        for index, text_part in enumerate(text_parts):
            send_responses.append(
                service.send_text_message(
                    to_number,
                    text_part,
                    reply_to_message_id=reply_to_message_id if index == 0 else "",
                )
            )
        send_response = (
            send_responses[0]
            if len(send_responses) == 1
            else {
                "messages": [
                    message
                    for response in send_responses
                    for message in list(response.get("messages") or [])
                ],
                "message_count": len(send_responses),
                "responses": send_responses,
            }
        )
    outbound_message_ids = [
        message_id
        for response in send_responses
        for message_id in [_extract_outbound_message_id(response)]
        if message_id
    ]
    outbound_message_id = outbound_message_ids[0] if outbound_message_ids else ""
    try:
        services.store.update_message_channel_metadata(
            username,
            conversation_id,
            local_message_id,
            external_message_id=outbound_message_id or None,
            channel_metadata={
                "send_response": send_response,
                "external_message_ids": outbound_message_ids,
                "last_status": "accepted",
                "message_count": len(send_responses),
            },
        )
        for message_id in outbound_message_ids[1:]:
            services.store.set_runtime_state(
                _outbound_message_alias_key(message_id),
                {
                    "username": username,
                    "conversation_id": conversation_id,
                    "local_message_id": local_message_id,
                    "external_message_id": message_id,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        for index, response in enumerate(send_responses, start=1):
            part_message_id = _extract_outbound_message_id(response)
            event_payload = response
            if len(send_responses) > 1:
                event_payload = {
                    **response,
                    "part_index": index,
                    "part_total": len(send_responses),
                }
            services.store.record_channel_event(
                channel="whatsapp",
                event_type=event_type,
                payload=event_payload,
                username=username,
                conversation_id=conversation_id,
                local_message_id=local_message_id,
                channel_user_id=to_number,
                external_event_id=part_message_id,
                external_message_id=part_message_id,
            )
    except Exception:
        current_app.logger.exception(
            "Resposta WhatsApp enviada, mas falhou o registo local do outbound (msg=%s, wamid=%s).",
            local_message_id,
            outbound_message_id,
        )
    return send_response, outbound_message_id


def _append_send_and_mark_reply(
    service,
    *,
    username: str,
    conversation_id: str,
    from_number: str,
    inbound_message_id: str,
    content: str,
    event_type: str,
    metadata: dict | None = None,
) -> None:
    reply_message = services.store.append_chat_message(
        username=username,
        conversation_id=conversation_id,
        role="assistant",
        content=content,
        channel="whatsapp",
        channel_user_id=from_number,
        external_reply_to_id=inbound_message_id,
        channel_metadata=metadata or {},
    )
    _send_and_record_outbound_message(
        service,
        username=username,
        conversation_id=conversation_id,
        local_message_id=reply_message["id"],
        content=content,
        to_number=from_number,
        reply_to_message_id=inbound_message_id,
        event_type=event_type,
    )
    _mark_inbound_processed(
        inbound_message_id,
        from_number=from_number,
        conversation_id=conversation_id,
        answer=content,
    )


def _process_whatsapp_answer_retry(
    service,
    *,
    username: str,
    role: str,
    conversation_id: str,
    from_number: str,
    inbound_message_id: str,
    event: dict,
    request_text: str,
    revision_context: dict,
    incoming_event_type: str,
    requested_from_feedback: bool = False,
) -> dict:
    retry_context = dict(revision_context or {})
    retry_context["user_note"] = request_text
    retry_prompt = _build_answer_retry_prompt(request_text)
    inbound_metadata = {
        "profile_name": event.get("profile_name", ""),
        "timestamp": event.get("timestamp", ""),
        "message_type": "text",
        "message_kind": "answer_retry_request",
        "original_request": request_text,
    }
    if requested_from_feedback:
        inbound_metadata["requested_from_feedback"] = True

    result = handle_chat_turn(
        username=username,
        role=role,
        question=retry_prompt,
        conversation_id=conversation_id,
        channel="whatsapp",
        allow_mutations=True,
        channel_user_id=from_number,
        inbound_message_id=inbound_message_id,
        inbound_message_metadata=inbound_metadata,
        revision_context=retry_context,
    )
    services.store.record_channel_event(
        channel="whatsapp",
        event_type=incoming_event_type,
        payload=event.get("raw") or {},
        username=username,
        conversation_id=result["conversation_id"],
        local_message_id=result.get("user_message_id", ""),
        channel_user_id=from_number,
        external_event_id=inbound_message_id,
        external_message_id=inbound_message_id,
    )
    _send_and_record_outbound_message(
        service,
        username=username,
        conversation_id=result["conversation_id"],
        local_message_id=result["message_id"],
        content=result["answer"],
        to_number=from_number,
        reply_to_message_id=inbound_message_id,
        event_type="outgoing_answer_retry",
    )
    _mark_inbound_processed(
        inbound_message_id,
        from_number=from_number,
        conversation_id=result["conversation_id"],
        answer=result["answer"],
    )
    return result


@bp.route("/webhooks/whatsapp", methods=["GET"])
def whatsapp_webhook_verify():
    service = getattr(services, "whatsapp_service", None)
    if not service or not service.webhook_ready:
        return jsonify({"error": "WhatsApp webhook indisponível."}), 503

    mode = request.args.get("hub.mode", "")
    token = request.args.get("hub.verify_token", "")
    challenge = request.args.get("hub.challenge", "")
    if service.verify_webhook(mode, token):
        return Response(challenge, mimetype="text/plain")
    return jsonify({"error": "Verificação inválida."}), 403


@bp.route("/webhooks/whatsapp", methods=["POST"])
def whatsapp_webhook_receive():
    service = getattr(services, "whatsapp_service", None)
    if not service or not service.enabled:
        return jsonify({"status": "disabled"}), 200

    payload = request.get_json(silent=True) or {}
    webhook_events = service.parse_webhook_events(payload)
    delivered = 0
    ignored = 0
    duplicates = 0
    feedback_applied = 0
    status_events = 0

    for event in webhook_events:
        event_type = (event.get("event_type") or "").strip().lower()
        if event_type == "message_status":
            message_id = (event.get("message_id") or "").strip()
            matched_message = _find_whatsapp_message_by_external_id(message_id)
            services.store.record_channel_event(
                channel="whatsapp",
                event_type="message_status",
                payload=event.get("raw") or {},
                username=(matched_message or {}).get("username", ""),
                conversation_id=(matched_message or {}).get("conversation_id", ""),
                local_message_id=(matched_message or {}).get("id", ""),
                channel_user_id=event.get("recipient_id", ""),
                external_event_id=(event.get("event_id") or message_id),
                external_message_id=message_id,
            )
            if matched_message:
                services.store.update_message_channel_metadata(
                    matched_message["username"],
                    matched_message["conversation_id"],
                    matched_message["id"],
                    channel_metadata={
                        "latest_status": event.get("status", ""),
                        "latest_status_at": event.get("timestamp", ""),
                        "last_status_payload": event.get("raw") or {},
                    },
                )
            status_events += 1
            continue

        from_number = event.get("from_number", "")
        message_id = (event.get("message_id") or event.get("event_id") or "").strip()
        if not service.is_allowed_number(from_number):
            ignored += 1
            current_app.logger.info("WhatsApp webhook ignorado para número não autorizado: %s", from_number)
            continue
        if not _claim_inbound_processing(message_id, from_number=from_number):
            duplicates += 1
            current_app.logger.info("WhatsApp webhook duplicado/em processamento ignorado: %s", message_id)
            continue

        try:
            current_app.logger.info(
                "WhatsApp webhook: processando %s de %s (tipo=%s)",
                message_id, from_number, event_type,
            )
            profile = _ensure_whatsapp_user(
                from_number,
                event.get("profile_name", ""),
                getattr(service, "default_role", "piloto"),
            )

            if event_type == "message_reaction":
                target_message = _find_whatsapp_message_by_external_id(event.get("target_message_id", ""))
                services.store.record_channel_event(
                    channel="whatsapp",
                    event_type="incoming_reaction",
                    payload=event.get("raw") or {},
                    username=profile["username"],
                    conversation_id=(target_message or {}).get("conversation_id", ""),
                    local_message_id=(target_message or {}).get("id", ""),
                    channel_user_id=from_number,
                    external_event_id=message_id,
                    external_message_id=event.get("target_message_id", ""),
                )
                feedback_status = _reaction_feedback_status(event.get("emoji", ""))
                if feedback_status and target_message and target_message.get("role") == "assistant":
                    services.store.update_message_feedback(
                        target_message["username"],
                        target_message["conversation_id"],
                        target_message["id"],
                        feedback_status,
                        f"Feedback via reação WhatsApp: {event.get('emoji', '')}",
                        feedback_updated_by=profile["username"],
                    )
                    sync_feedback_correction_eval_case(
                        services.store,
                        target_message["username"],
                        target_message["conversation_id"],
                        target_message["id"],
                        source="whatsapp",
                    )
                    feedback_applied += 1
                    if feedback_status == "review":
                        services.store.set_runtime_state(
                            _pending_feedback_correction_key(from_number),
                            {
                                "username": target_message["username"],
                                "conversation_id": target_message["conversation_id"],
                                "message_id": target_message["id"],
                                "target_external_message_id": event.get("target_message_id", ""),
                                "feedback_note": f"Feedback via reação WhatsApp: {event.get('emoji', '')}",
                                "requested_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        correction_prompt = (
                            "Registei esta resposta para revisão. "
                            "Qual seria a resposta correta? "
                            "Se não quiseres guardar correção, responde `ignorar`."
                        )
                        prompt_message = services.store.append_chat_message(
                            username=profile["username"],
                            conversation_id=target_message["conversation_id"],
                            role="assistant",
                            content=correction_prompt,
                            channel="whatsapp",
                            channel_user_id=from_number,
                            external_reply_to_id=event.get("target_message_id", ""),
                            channel_metadata={"message_kind": "feedback_correction_prompt"},
                        )
                        try:
                            _send_and_record_outbound_message(
                                service,
                                username=profile["username"],
                                conversation_id=target_message["conversation_id"],
                                local_message_id=prompt_message["id"],
                                content=correction_prompt,
                                to_number=from_number,
                                reply_to_message_id=event.get("target_message_id", ""),
                                event_type="outgoing_feedback_correction_prompt",
                            )
                            delivered += 1
                        except Exception:
                            current_app.logger.exception(
                                "Falha ao enviar prompt de correção WhatsApp para %s.",
                                from_number,
                            )
                    else:
                        services.store.delete_runtime_state(_pending_feedback_correction_key(from_number))
                _mark_inbound_processed(
                    message_id,
                    from_number=from_number,
                    conversation_id=(target_message or {}).get("conversation_id", ""),
                    answer=f"reaction:{event.get('emoji', '')}",
                )
                continue

            if event_type == "message_location":
                conversation = services.store.ensure_conversation(username=profile["username"])
                try:
                    latitude, longitude = normalize_location(event.get("latitude"), event.get("longitude"))
                except ValueError as exc:
                    reply_text = f"🛟⚠️ Recebi uma localização, mas as coordenadas não são válidas: {exc}"
                    _append_send_and_mark_reply(
                        service,
                        username=profile["username"],
                        conversation_id=conversation["id"],
                        from_number=from_number,
                        inbound_message_id=message_id,
                        content=reply_text,
                        event_type="outgoing_sos_location_invalid",
                        metadata={"message_kind": "sos_location_invalid"},
                    )
                    delivered += 1
                    continue

                location_message = services.store.append_chat_message(
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    role="user",
                    content=f"[localização recebida: {latitude:.6f}, {longitude:.6f}]",
                    channel="whatsapp",
                    channel_user_id=from_number,
                    external_message_id=message_id,
                    channel_metadata={
                        "message_kind": "sos_location",
                        "latitude": latitude,
                        "longitude": longitude,
                        "location_name": event.get("location_name", ""),
                        "location_address": event.get("location_address", ""),
                    },
                )
                services.store.record_channel_event(
                    channel="whatsapp",
                    event_type="incoming_sos_location",
                    payload=event.get("raw") or {},
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    local_message_id=location_message["id"],
                    channel_user_id=from_number,
                    external_event_id=message_id,
                    external_message_id=message_id,
                )

                pending_sos = _load_pending_sos(from_number)
                if not pending_sos:
                    _append_send_and_mark_reply(
                        service,
                        username=profile["username"],
                        conversation_id=conversation["id"],
                        from_number=from_number,
                        inbound_message_id=message_id,
                        content=build_sos_no_pending_location_reply(),
                        event_type="outgoing_sos_location_without_pending",
                        metadata={"message_kind": "sos_location_without_pending"},
                    )
                    delivered += 1
                    continue

                if sos_pending_expired(pending_sos):
                    _clear_pending_sos(from_number)
                    _append_send_and_mark_reply(
                        service,
                        username=profile["username"],
                        conversation_id=conversation["id"],
                        from_number=from_number,
                        inbound_message_id=message_id,
                        content=build_sos_expired_reply(),
                        event_type="outgoing_sos_expired",
                        metadata={"message_kind": "sos_expired"},
                    )
                    delivered += 1
                    continue

                created_at = utc_now_iso()
                sos_event_id = str(pending_sos.get("event_id") or build_sos_event_id())
                alert_payload = {
                    **pending_sos,
                    "event_id": sos_event_id,
                    "latitude": latitude,
                    "longitude": longitude,
                    "created_at": created_at,
                    "created_at_label": local_datetime_label(created_at),
                    "location_name": event.get("location_name", ""),
                    "location_address": event.get("location_address", ""),
                }
                alert_text = build_sos_admin_alert(alert_payload)
                recipients = sos_admin_recipients(
                    services.store.list_users(),
                    configured_numbers=configured_sos_numbers(),
                    exclude_number=from_number,
                )
                sent_count, failed = _send_sos_alerts(
                    service,
                    requester_username=profile["username"],
                    requester_conversation_id=conversation["id"],
                    requester_message_id=message_id,
                    alert_text=alert_text,
                    recipients=recipients,
                    sos_event_id=sos_event_id,
                )
                services.store.set_runtime_state(
                    sos_event_key(sos_event_id),
                    {
                        **alert_payload,
                        "status": "alert_sent",
                        "recipient_count": len(recipients),
                        "sent_count": sent_count,
                        "failed": failed,
                    },
                )
                services.store.set_runtime_state(
                    sos_last_event_key(from_number),
                    {
                        "event_id": sos_event_id,
                        "status": "alert_sent",
                        "created_at": created_at,
                    },
                )
                _clear_pending_sos(from_number)
                _append_send_and_mark_reply(
                    service,
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    from_number=from_number,
                    inbound_message_id=message_id,
                    content=build_sos_user_confirmation(sent_count, len(failed)),
                    event_type="outgoing_sos_confirmation",
                    metadata={
                        "message_kind": "sos_confirmation",
                        "sos_event_id": sos_event_id,
                        "sent_count": sent_count,
                        "failed_count": len(failed),
                    },
                )
                delivered += sent_count + 1
                continue

            if event_type == "message_media":
                conversation = services.store.ensure_conversation(username=profile["username"])
                media_kind = (event.get("media_kind") or "").strip().lower()
                media_id = (event.get("media_id") or "").strip()
                media_message = services.store.append_chat_message(
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    role="user",
                    content=f"[{media_kind or 'media'} recebida para reporte]",
                    channel="whatsapp",
                    channel_user_id=from_number,
                    external_message_id=message_id,
                    channel_metadata={
                        "message_kind": "event_report_media",
                        "media_kind": media_kind,
                        "media_id": media_id,
                        "mime_type": event.get("mime_type", ""),
                        "caption": event.get("caption", ""),
                    },
                )
                services.store.record_channel_event(
                    channel="whatsapp",
                    event_type="incoming_media",
                    payload=event.get("raw") or {},
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    local_message_id=media_message["id"],
                    channel_user_id=from_number,
                    external_event_id=message_id,
                    external_message_id=message_id,
                )
                pending_report = load_pending_event_report(
                    channel="whatsapp",
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    channel_user_id=from_number,
                )
                if not pending_report:
                    reply_text = (
                        "Recebi a imagem, mas não tenho um reporte de evento pendente. "
                        "Envia primeiro `/reportar_evento TAG. LOCAL. DESCRIPTION`."
                    )
                    _append_send_and_mark_reply(
                        service,
                        username=profile["username"],
                        conversation_id=conversation["id"],
                        from_number=from_number,
                        inbound_message_id=message_id,
                        content=reply_text,
                        event_type="outgoing_event_report_media_help",
                        metadata={"message_kind": "event_report_media_help"},
                    )
                    delivered += 1
                    continue
                if media_kind != "image":
                    reply_text = "Para este reporte, envia uma foto ou responde `não` para arquivar sem anexo."
                    _append_send_and_mark_reply(
                        service,
                        username=profile["username"],
                        conversation_id=conversation["id"],
                        from_number=from_number,
                        inbound_message_id=message_id,
                        content=reply_text,
                        event_type="outgoing_event_report_media_rejected",
                        metadata={"message_kind": "event_report_media_rejected"},
                    )
                    delivered += 1
                    continue
                try:
                    media_payload = service.download_media(media_id)
                except Exception:
                    current_app.logger.exception(
                        "Falha ao descarregar foto WhatsApp para reporte (media=%s).",
                        media_id,
                    )
                    reply_text = (
                        "Recebi a foto, mas não consegui descarregá-la da WhatsApp Cloud API. "
                        "Tenta enviar novamente ou responde `não` para arquivar sem anexo."
                    )
                    _append_send_and_mark_reply(
                        service,
                        username=profile["username"],
                        conversation_id=conversation["id"],
                        from_number=from_number,
                        inbound_message_id=message_id,
                        content=reply_text,
                        event_type="outgoing_event_report_media_error",
                        metadata={"message_kind": "event_report_media_error"},
                    )
                    delivered += 1
                    continue

                event_report = finalize_pending_event_report(
                    pending_report,
                    attachment_bytes=media_payload.get("bytes") or b"",
                    attachment_mime_type=media_payload.get("mime_type") or event.get("mime_type", ""),
                    attachment_filename=media_payload.get("filename") or event.get("filename", ""),
                    media_id=media_id,
                )
                reply_text = format_event_report_answer(event_report)
                _append_send_and_mark_reply(
                    service,
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    from_number=from_number,
                    inbound_message_id=message_id,
                    content=reply_text,
                    event_type="outgoing_event_report_registered",
                    metadata={
                        "message_kind": "event_report_registered",
                        "event_report_id": event_report.get("id", ""),
                    },
                )
                delivered += 1
                continue

            text = (event.get("text") or "").strip()
            if not text:
                ignored += 1
                continue

            pending_sos = _load_pending_sos(from_number)
            last_sos_event = {}
            if not pending_sos:
                last_sos_ref = services.store.get_runtime_state(sos_last_event_key(from_number)) or {}
                last_event_id = str(last_sos_ref.get("event_id") or "").strip()
                if last_event_id:
                    candidate_event = services.store.get_runtime_state(sos_event_key(last_event_id)) or {}
                    if (
                        candidate_event
                        and not candidate_event.get("cancelled_at")
                        and not sos_pending_expired({"requested_at": candidate_event.get("created_at")})
                    ):
                        last_sos_event = candidate_event
            if is_sos_cancel(text, pending_sos=bool(pending_sos or last_sos_event)):
                conversation_id = str(pending_sos.get("conversation_id") or "").strip()
                if not conversation_id and last_sos_event:
                    conversation_id = str(last_sos_event.get("conversation_id") or "").strip()
                if not conversation_id:
                    conversation = services.store.ensure_conversation(username=profile["username"])
                    conversation_id = conversation["id"]
                sos_event_id = str(
                    pending_sos.get("event_id") or last_sos_event.get("event_id") or ""
                ).strip()
                user_message = services.store.append_chat_message(
                    username=profile["username"],
                    conversation_id=conversation_id,
                    role="user",
                    content=text,
                    channel="whatsapp",
                    channel_user_id=from_number,
                    external_message_id=message_id,
                    channel_metadata={
                        "message_kind": "sos_cancel",
                        "sos_event_id": sos_event_id,
                    },
                )
                services.store.record_channel_event(
                    channel="whatsapp",
                    event_type="incoming_sos_cancel",
                    payload=event.get("raw") or {},
                    username=profile["username"],
                    conversation_id=conversation_id,
                    local_message_id=user_message["id"],
                    channel_user_id=from_number,
                    external_event_id=message_id,
                    external_message_id=message_id,
                )
                if pending_sos:
                    _clear_pending_sos(from_number)
                    reply_text = build_sos_cancelled_reply()
                    event_type_reply = "outgoing_sos_cancelled"
                    message_kind = "sos_cancelled"
                    extra_delivered = 0
                elif last_sos_event:
                    cancelled_at = utc_now_iso()
                    cancel_payload = {
                        **last_sos_event,
                        "cancelled_at": cancelled_at,
                        "cancelled_at_label": local_datetime_label(cancelled_at),
                        "cancel_message_id": message_id,
                    }
                    cancel_alert_text = build_sos_admin_cancel_alert(cancel_payload)
                    recipients = sos_admin_recipients(
                        services.store.list_users(),
                        configured_numbers=configured_sos_numbers(),
                        exclude_number=from_number,
                    )
                    sent_count, failed = _send_sos_alerts(
                        service,
                        requester_username=profile["username"],
                        requester_conversation_id=conversation_id,
                        requester_message_id=message_id,
                        alert_text=cancel_alert_text,
                        recipients=recipients,
                        sos_event_id=sos_event_id,
                        event_type="outgoing_sos_cancel_alert",
                        message_kind="sos_admin_cancel_alert",
                    )
                    services.store.set_runtime_state(
                        sos_event_key(sos_event_id),
                        {
                            **cancel_payload,
                            "status": "cancelled",
                            "cancel_sent_count": sent_count,
                            "cancel_failed": failed,
                        },
                    )
                    services.store.set_runtime_state(
                        sos_last_event_key(from_number),
                        {
                            "event_id": sos_event_id,
                            "status": "cancelled",
                            "cancelled_at": cancelled_at,
                        },
                    )
                    reply_text = build_sos_dispatched_cancelled_reply(sent_count, len(failed))
                    event_type_reply = "outgoing_sos_dispatched_cancelled"
                    message_kind = "sos_dispatched_cancelled"
                    extra_delivered = sent_count
                else:
                    reply_text = build_sos_no_pending_cancel_reply()
                    event_type_reply = "outgoing_sos_cancel_without_pending"
                    message_kind = "sos_cancel_without_pending"
                    extra_delivered = 0
                _append_send_and_mark_reply(
                    service,
                    username=profile["username"],
                    conversation_id=conversation_id,
                    from_number=from_number,
                    inbound_message_id=message_id,
                    content=reply_text,
                    event_type=event_type_reply,
                    metadata={
                        "message_kind": message_kind,
                        "sos_event_id": sos_event_id,
                    },
                )
                delivered += extra_delivered + 1
                continue

            if is_sos_trigger(text):
                conversation = services.store.ensure_conversation(username=profile["username"])
                user_message = services.store.append_chat_message(
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    role="user",
                    content=text,
                    channel="whatsapp",
                    channel_user_id=from_number,
                    external_message_id=message_id,
                    channel_metadata={
                        "message_kind": "sos_request",
                        "profile_name": event.get("profile_name", ""),
                        "timestamp": event.get("timestamp", ""),
                    },
                )
                services.store.record_channel_event(
                    channel="whatsapp",
                    event_type="incoming_sos_request",
                    payload=event.get("raw") or {},
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    local_message_id=user_message["id"],
                    channel_user_id=from_number,
                    external_event_id=message_id,
                    external_message_id=message_id,
                )

                if sos_alerts_enabled():
                    _save_pending_sos(
                        from_number=from_number,
                        profile=profile,
                        conversation_id=conversation["id"],
                        message_id=message_id,
                        text=text,
                    )
                    reply_text = build_sos_location_prompt()
                    event_type_reply = "outgoing_sos_location_prompt"
                    message_kind = "sos_location_prompt"
                else:
                    reply_text = build_sos_disabled_reply()
                    event_type_reply = "outgoing_sos_disabled"
                    message_kind = "sos_disabled"

                _append_send_and_mark_reply(
                    service,
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    from_number=from_number,
                    inbound_message_id=message_id,
                    content=reply_text,
                    event_type=event_type_reply,
                    metadata={"message_kind": message_kind},
                )
                delivered += 1
                continue

            pending_feedback_correction = services.store.get_runtime_state(
                _pending_feedback_correction_key(from_number)
            ) or {}
            if pending_feedback_correction:
                correction_conversation_id = str(
                    pending_feedback_correction.get("conversation_id") or ""
                ).strip()
                correction_username = str(
                    pending_feedback_correction.get("username") or profile["username"] or ""
                ).strip()
                correction_message_id = str(
                    pending_feedback_correction.get("message_id") or ""
                ).strip()
                if _whatsapp_answer_retry_requested(text):
                    services.store.delete_runtime_state(_pending_feedback_correction_key(from_number))
                    revision_context = _assistant_message_revision_context(
                        correction_username,
                        correction_conversation_id,
                        correction_message_id,
                    )
                    _process_whatsapp_answer_retry(
                        service,
                        username=correction_username,
                        role=profile.get("role", getattr(service, "default_role", "piloto")),
                        conversation_id=correction_conversation_id,
                        from_number=from_number,
                        inbound_message_id=message_id,
                        event=event,
                        request_text=text,
                        revision_context=revision_context,
                        incoming_event_type="incoming_feedback_retry_request",
                        requested_from_feedback=True,
                    )
                    delivered += 1
                    continue
                user_correction_message = services.store.append_chat_message(
                    username=correction_username,
                    conversation_id=correction_conversation_id,
                    role="user",
                    content=text,
                    channel="whatsapp",
                    channel_user_id=from_number,
                    external_message_id=message_id,
                    channel_metadata={
                        "message_kind": "feedback_correction",
                        "feedback_target_message_id": correction_message_id,
                    },
                )
                services.store.record_channel_event(
                    channel="whatsapp",
                    event_type="incoming_feedback_correction",
                    payload=event.get("raw") or {},
                    username=correction_username,
                    conversation_id=correction_conversation_id,
                    local_message_id=user_correction_message["id"],
                    channel_user_id=from_number,
                    external_event_id=message_id,
                    external_message_id=message_id,
                )

                if _feedback_correction_skip_requested(text):
                    correction_reply = (
                        "Mantive a resposta em revisão sem correção adicional."
                    )
                else:
                    updated_message = services.store.update_message_feedback(
                        correction_username,
                        correction_conversation_id,
                        correction_message_id,
                        "corrected",
                        str(pending_feedback_correction.get("feedback_note") or "").strip(),
                        feedback_correction=text,
                        feedback_updated_by=profile["username"],
                    )
                    sync_feedback_correction_eval_case(
                        services.store,
                        correction_username,
                        correction_conversation_id,
                        correction_message_id,
                        source="whatsapp",
                    )
                    if str(updated_message.get("feedback_correction") or "").strip():
                        correction_reply = (
                            "Correção guardada. Vou usá-la como referência forte em perguntas semelhantes, "
                            "conciliando-a com os documentos disponíveis."
                        )
                    else:
                        correction_reply = (
                            "Registei a tua nota de revisão, mas não a vou reutilizar como resposta final "
                            "sem uma formulação canónica."
                        )

                services.store.delete_runtime_state(_pending_feedback_correction_key(from_number))
                reply_message = services.store.append_chat_message(
                    username=correction_username,
                    conversation_id=correction_conversation_id,
                    role="assistant",
                    content=correction_reply,
                    channel="whatsapp",
                    channel_user_id=from_number,
                    external_reply_to_id=message_id,
                    channel_metadata={"message_kind": "feedback_correction_ack"},
                )
                try:
                    _send_and_record_outbound_message(
                        service,
                        username=correction_username,
                        conversation_id=correction_conversation_id,
                        local_message_id=reply_message["id"],
                        content=correction_reply,
                        to_number=from_number,
                        reply_to_message_id=message_id,
                        event_type="outgoing_feedback_correction_ack",
                    )
                    _mark_inbound_processed(
                        message_id,
                        from_number=from_number,
                        conversation_id=correction_conversation_id,
                        answer=correction_reply,
                    )
                    delivered += 1
                    continue
                except Exception:
                    current_app.logger.exception(
                        "Falha ao responder ao fluxo de correção WhatsApp (from=%s, msg=%s).",
                        from_number,
                        message_id,
                    )
                    continue

            if _whatsapp_diagnostic_requested(text):
                conversation = services.store.ensure_conversation(username=profile["username"])
                user_message = services.store.append_chat_message(
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    role="user",
                    content=text,
                    channel="whatsapp",
                    channel_user_id=from_number,
                    external_message_id=message_id,
                    channel_metadata={
                        "message_kind": "answer_diagnostic_request",
                        "profile_name": event.get("profile_name", ""),
                        "timestamp": event.get("timestamp", ""),
                    },
                )
                services.store.record_channel_event(
                    channel="whatsapp",
                    event_type="incoming_answer_diagnostic_request",
                    payload=event.get("raw") or {},
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    local_message_id=user_message["id"],
                    channel_user_id=from_number,
                    external_event_id=message_id,
                    external_message_id=message_id,
                )
                try:
                    diagnostic = _latest_assistant_diagnostic(profile["username"], conversation["id"])
                    reply_text = format_operational_diagnostic(diagnostic)
                except ValueError:
                    reply_text = (
                        "Não encontrei uma resposta anterior nesta conversa para diagnosticar. "
                        "Faz a pergunta de novo com navio, cais/doca, hora e decisão pretendida."
                    )
                reply_message = services.store.append_chat_message(
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    role="assistant",
                    content=reply_text,
                    channel="whatsapp",
                    channel_user_id=from_number,
                    external_reply_to_id=message_id,
                    channel_metadata={"message_kind": "answer_diagnostic"},
                )
                _send_and_record_outbound_message(
                    service,
                    username=profile["username"],
                    conversation_id=conversation["id"],
                    local_message_id=reply_message["id"],
                    content=reply_text,
                    to_number=from_number,
                    reply_to_message_id=message_id,
                    event_type="outgoing_answer_diagnostic",
                )
                _mark_inbound_processed(
                    message_id,
                    from_number=from_number,
                    conversation_id=conversation["id"],
                    answer=reply_text,
                )
                delivered += 1
                continue

            if _whatsapp_answer_retry_requested(text):
                conversation = services.store.ensure_conversation(username=profile["username"])
                try:
                    revision_context = _latest_assistant_revision_context(
                        profile["username"],
                        conversation["id"],
                    )
                except ValueError:
                    user_message = services.store.append_chat_message(
                        username=profile["username"],
                        conversation_id=conversation["id"],
                        role="user",
                        content=text,
                        channel="whatsapp",
                        channel_user_id=from_number,
                        external_message_id=message_id,
                        channel_metadata={
                            "message_kind": "answer_retry_request_without_context",
                            "profile_name": event.get("profile_name", ""),
                            "timestamp": event.get("timestamp", ""),
                        },
                    )
                    services.store.record_channel_event(
                        channel="whatsapp",
                        event_type="incoming_answer_retry_without_context",
                        payload=event.get("raw") or {},
                        username=profile["username"],
                        conversation_id=conversation["id"],
                        local_message_id=user_message["id"],
                        channel_user_id=from_number,
                        external_event_id=message_id,
                        external_message_id=message_id,
                    )
                    reply_text = (
                        "Não encontrei uma resposta anterior nesta conversa para rever. "
                        "Faz a pergunta de novo com os dados relevantes e eu reanaliso."
                    )
                    _append_send_and_mark_reply(
                        service,
                        username=profile["username"],
                        conversation_id=conversation["id"],
                        from_number=from_number,
                        inbound_message_id=message_id,
                        content=reply_text,
                        event_type="outgoing_answer_retry_without_context",
                        metadata={"message_kind": "answer_retry_without_context"},
                    )
                    delivered += 1
                    continue

                _process_whatsapp_answer_retry(
                    service,
                    username=profile["username"],
                    role=profile.get("role", getattr(service, "default_role", "piloto")),
                    conversation_id=conversation["id"],
                    from_number=from_number,
                    inbound_message_id=message_id,
                    event=event,
                    request_text=text,
                    revision_context=revision_context,
                    incoming_event_type="incoming_answer_retry_request",
                )
                delivered += 1
                continue

            pre_response_messages = []
            if getattr(service, "welcome_enabled", False) and not _welcome_already_sent(from_number):
                welcome_message = service.build_welcome_message(event)
                if welcome_message:
                    welcome_metadata = {
                        "message_kind": "welcome",
                        "welcome_auto": True,
                    }
                    if getattr(service, "should_send_welcome_template", lambda: False)():
                        welcome_metadata.update(
                            {
                                "welcome_template_name": getattr(service, "welcome_template_name", ""),
                                "welcome_template_language": getattr(service, "welcome_template_language", "pt_PT"),
                            }
                        )
                    pre_response_messages.append(
                        {
                            "content": welcome_message,
                            "channel_metadata": welcome_metadata,
                        }
                    )

            current_app.logger.info(
                "WhatsApp: chat_turn para %s, welcome_pre=%d",
                from_number, len(pre_response_messages),
            )
            result = handle_chat_turn(
                username=profile["username"],
                role=profile.get("role", getattr(service, "default_role", "piloto")),
                question=text,
                channel="whatsapp",
                allow_mutations=False,
                channel_user_id=from_number,
                inbound_message_id=message_id,
                inbound_message_metadata={
                    "profile_name": event.get("profile_name", ""),
                    "timestamp": event.get("timestamp", ""),
                    "message_type": "text",
                },
                pre_response_messages=pre_response_messages,
            )
            current_app.logger.info(
                "WhatsApp: chat_turn OK, answer_len=%d, pre_msgs=%d",
                len(result.get("answer") or ""),
                len(result.get("pre_response_messages") or []),
            )
            services.store.record_channel_event(
                channel="whatsapp",
                event_type="incoming_text",
                payload=event.get("raw") or {},
                username=profile["username"],
                conversation_id=result["conversation_id"],
                local_message_id=result.get("user_message_id", ""),
                channel_user_id=from_number,
                external_event_id=message_id,
                external_message_id=message_id,
            )

            for pre_message in result.get("pre_response_messages") or []:
                is_welcome = (pre_message.get("channel_metadata") or {}).get("message_kind") == "welcome"
                pre_message_id = ""
                try:
                    _, pre_message_id = _send_and_record_outbound_message(
                        service,
                        username=profile["username"],
                        conversation_id=result["conversation_id"],
                        local_message_id=pre_message["message_id"],
                        content=pre_message["content"],
                        to_number=from_number,
                        reply_to_message_id=message_id,
                        event_type="outgoing_welcome",
                        template_name=(pre_message.get("channel_metadata") or {}).get("welcome_template_name", ""),
                        template_language=(pre_message.get("channel_metadata") or {}).get("welcome_template_language", ""),
                    )
                except Exception:
                    current_app.logger.exception("Falha ao enviar welcome WhatsApp para %s.", from_number)
                if is_welcome:
                    _mark_welcome_sent(
                        from_number,
                        conversation_id=result["conversation_id"],
                        local_message_id=pre_message["message_id"],
                        external_message_id=pre_message_id,
                    )

            current_app.logger.info("WhatsApp: a enviar resposta para %s", from_number)
            _, outbound_message_id = _send_and_record_outbound_message(
                service,
                username=profile["username"],
                conversation_id=result["conversation_id"],
                local_message_id=result["message_id"],
                content=result["answer"],
                to_number=from_number,
                reply_to_message_id=message_id,
                event_type="outgoing_text",
            )
            current_app.logger.info(
                "WhatsApp: resposta enviada, wamid=%s", outbound_message_id,
            )
            _mark_inbound_processed(
                message_id,
                from_number=from_number,
                conversation_id=result["conversation_id"],
                answer=result["answer"],
            )
            delivered += 1
        except Exception as exc:
            log_error_event(
                current_app.logger,
                "CHAT_RUNTIME_FAILED",
                detail=str(exc),
                channel="whatsapp",
                from_number=from_number,
                message_id=message_id,
                event_type=event_type,
            )
            current_app.logger.exception(
                "Falha ao responder ao webhook WhatsApp (from=%s, msg=%s).",
                from_number,
                message_id,
            )
            if _send_whatsapp_error_reply(
                service,
                from_number=from_number,
                message_id=message_id,
                profile=locals().get("profile"),
            ):
                delivered += 1

    return jsonify({
        "status": "ok",
        "received": len(webhook_events),
        "delivered": delivered,
        "ignored": ignored,
        "duplicates": duplicates,
        "feedback_applied": feedback_applied,
        "status_events": status_events,
    }), 200
