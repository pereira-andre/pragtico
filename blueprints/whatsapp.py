"""WhatsApp webhook blueprint connected to the shared PRAGtico chat runtime."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, request

from core import services
from core.chat_feedback import sync_feedback_correction_eval_case
from core.chat_runtime import handle_chat_turn
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
                send_response = service.send_text_message(number, alert_text, reply_to_message_id="")
                outbound_id = _extract_outbound_message_id(send_response)
                services.store.record_channel_event(
                    channel="whatsapp",
                    event_type=event_type,
                    payload=send_response,
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
    return bool(services.store.get_runtime_state(_processed_inbound_key(message_id)))


def _mark_inbound_processed(message_id: str, *, from_number: str, conversation_id: str, answer: str) -> None:
    if not message_id:
        return
    try:
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
    else:
        send_response = service.send_text_message(
            to_number,
            content,
            reply_to_message_id=reply_to_message_id,
        )
    outbound_message_id = _extract_outbound_message_id(send_response)
    try:
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
                        "review",
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
