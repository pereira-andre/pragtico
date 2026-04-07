"""WhatsApp webhook blueprint connected to the shared PRAGtico chat runtime."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, request

from core import services
from core.chat_runtime import handle_chat_turn

bp = Blueprint("whatsapp", __name__)


def _normalize_whatsapp_role(value: str | None) -> str:
    role = (value or "").strip().lower()
    return role if role in {"admin", "agente", "piloto"} else "piloto"


def _whatsapp_username(from_number: str) -> str:
    return f"whatsapp-{from_number}@pragtico.local"


def _processed_inbound_key(message_id: str) -> str:
    return f"whatsapp:processed:{message_id}"


def _welcome_sent_key(from_number: str) -> str:
    return f"whatsapp:welcome:{from_number}"


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


def _is_duplicate_inbound(message_id: str) -> bool:
    if not message_id:
        return False
    return bool(services.store.get_runtime_state(_processed_inbound_key(message_id)))


def _mark_inbound_processed(message_id: str, *, from_number: str, conversation_id: str, answer: str) -> None:
    if not message_id:
        return
    services.store.set_runtime_state(
        _processed_inbound_key(message_id),
        {
            "message_id": message_id,
            "from_number": from_number,
            "conversation_id": conversation_id,
            "answer_preview": str(answer or "")[:240],
            "processed_at": datetime.now(timezone.utc).isoformat(),
        },
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
    else:
        send_response = service.send_text_message(
            to_number,
            content,
            reply_to_message_id=reply_to_message_id,
        )
    outbound_message_id = _extract_outbound_message_id(send_response)
    services.store.update_message_channel_metadata(
        username,
        conversation_id,
        local_message_id,
        external_message_id=outbound_message_id or None,
        channel_metadata={
            "send_response": send_response,
            "last_status": "accepted",
        },
    )
    services.store.record_channel_event(
        channel="whatsapp",
        event_type=event_type,
        payload=send_response,
        username=username,
        conversation_id=conversation_id,
        local_message_id=local_message_id,
        channel_user_id=to_number,
        external_event_id=outbound_message_id,
        external_message_id=outbound_message_id,
    )
    return send_response, outbound_message_id


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
            matched_message = services.store.find_message_by_channel_message_id("whatsapp", message_id)
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
        if _is_duplicate_inbound(message_id):
            duplicates += 1
            current_app.logger.info("WhatsApp webhook duplicado ignorado: %s", message_id)
            continue

        try:
            profile = _ensure_whatsapp_user(
                from_number,
                event.get("profile_name", ""),
                getattr(service, "default_role", "piloto"),
            )

            if event_type == "message_reaction":
                target_message = services.store.find_message_by_channel_message_id(
                    "whatsapp",
                    event.get("target_message_id", ""),
                )
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
                    )
                    feedback_applied += 1
                _mark_inbound_processed(
                    message_id,
                    from_number=from_number,
                    conversation_id=(target_message or {}).get("conversation_id", ""),
                    answer=f"reaction:{event.get('emoji', '')}",
                )
                continue

            text = (event.get("text") or "").strip()
            if not text:
                ignored += 1
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
            _mark_inbound_processed(
                message_id,
                from_number=from_number,
                conversation_id=result["conversation_id"],
                answer=result["answer"],
            )
            delivered += 1
        except Exception:
            current_app.logger.exception("Falha ao responder ao webhook WhatsApp.")

    return jsonify({
        "status": "ok",
        "received": len(webhook_events),
        "delivered": delivered,
        "ignored": ignored,
        "duplicates": duplicates,
        "feedback_applied": feedback_applied,
        "status_events": status_events,
    }), 200
