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
    inbound_messages = service.parse_inbound_messages(payload)
    delivered = 0
    ignored = 0
    duplicates = 0

    for inbound in inbound_messages:
        from_number = inbound.get("from_number", "")
        message_id = (inbound.get("message_id") or "").strip()
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
                inbound.get("profile_name", ""),
                getattr(service, "default_role", "piloto"),
            )
            result = handle_chat_turn(
                username=profile["username"],
                role=profile.get("role", getattr(service, "default_role", "piloto")),
                question=inbound.get("text", ""),
                channel="whatsapp",
                allow_mutations=False,
            )
            service.send_text_message(
                from_number,
                result["answer"],
                reply_to_message_id=message_id,
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
        "received": len(inbound_messages),
        "delivered": delivered,
        "ignored": ignored,
        "duplicates": duplicates,
    }), 200
