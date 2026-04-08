"""Shared helpers for user-facing WhatsApp opt-in and admin diagnostics."""

from __future__ import annotations

from typing import Any

from domain.document_processing import iso_now
from storage import (
    WHATSAPP_STATUS_DISABLED,
    WHATSAPP_STATUS_NOT_OPTED_IN,
    WHATSAPP_STATUS_PENDING,
    WHATSAPP_STATUS_UNKNOWN,
)
from storage.utils import _local_iso_to_label


def _status_key(service, number: str) -> str:
    if service is None:
        return ""
    return getattr(service, "status_state_key", lambda value: "")(number)


def load_whatsapp_status(store, service, number: str) -> dict[str, Any]:
    key = _status_key(service, number)
    if not key:
        return {}
    return dict(store.get_runtime_state(key) or {})


def save_whatsapp_status(store, service, number: str, payload: dict[str, Any]) -> dict[str, Any]:
    key = _status_key(service, number)
    if key:
        store.set_runtime_state(key, payload)
    return payload


def build_user_whatsapp_view(user: dict[str, Any], service, store) -> dict[str, Any]:
    whatsapp_number = str(user.get("whatsapp_number") or "").strip()
    normalized_number = getattr(service, "normalize_phone_number", lambda value: str(value or "").strip())(whatsapp_number)
    local_allowed = bool(normalized_number) and bool(service) and service.is_allowed_number(normalized_number)
    status_record = load_whatsapp_status(store, service, normalized_number)
    if not normalized_number:
        status = WHATSAPP_STATUS_UNKNOWN
    elif not user.get("whatsapp_opt_in"):
        status = WHATSAPP_STATUS_NOT_OPTED_IN
    else:
        status = str(status_record.get("category") or "").strip() or WHATSAPP_STATUS_PENDING

    summary = str(status_record.get("summary") or "").strip()
    details = str(status_record.get("details") or "").strip()
    if not normalized_number:
        summary = "Sem número WhatsApp no perfil."
        details = ""
    elif not user.get("whatsapp_opt_in"):
        summary = "Número guardado sem consentimento ativo."
        details = "Ativa o opt-in antes de verificar na Meta ou enviar mensagens automáticas."
    elif not summary:
        if normalized_number:
            summary = "Pronto para validação ou envio inicial."
            details = details or "Ainda não existe um resultado remoto guardado para este número."
    checked_at = str(status_record.get("checked_at") or "").strip()
    return {
        **user,
        "whatsapp_number": normalized_number,
        "whatsapp_local_allowed": local_allowed,
        "whatsapp_status": status,
        "whatsapp_status_summary": summary,
        "whatsapp_status_details": details,
        "whatsapp_status_checked_at": checked_at,
        "whatsapp_status_checked_at_label": _local_iso_to_label(checked_at) if checked_at else "Nunca",
        "whatsapp_status_ok": bool(status_record.get("ok")),
        "whatsapp_status_message_id": str(status_record.get("message_id") or "").strip(),
    }


def verify_user_whatsapp(user: dict[str, Any], service, store, *, source: str) -> dict[str, Any]:
    whatsapp_number = str(user.get("whatsapp_number") or "").strip()
    normalized_number = getattr(service, "normalize_phone_number", lambda value: str(value or "").strip())(whatsapp_number)

    if not normalized_number:
        result = {
            "checked_at": iso_now(),
            "to_number": "",
            "source": source,
            "ok": False,
            "category": WHATSAPP_STATUS_UNKNOWN,
            "summary": "Utilizador sem número WhatsApp configurado.",
            "details": "Preenche o número WhatsApp no perfil antes de verificar.",
        }
        return result

    if not user.get("whatsapp_opt_in"):
        result = {
            "checked_at": iso_now(),
            "to_number": normalized_number,
            "source": source,
            "ok": False,
            "category": WHATSAPP_STATUS_NOT_OPTED_IN,
            "summary": "Consentimento WhatsApp em falta.",
            "details": "A verificação remota fica bloqueada até existir opt-in explícito no perfil.",
        }
        return save_whatsapp_status(store, service, normalized_number, result)

    if service is None:
        result = {
            "checked_at": iso_now(),
            "to_number": normalized_number,
            "source": source,
            "ok": False,
            "category": WHATSAPP_STATUS_DISABLED,
            "summary": "Serviço WhatsApp indisponível neste arranque.",
            "details": "Confirma a configuração da integração WhatsApp no backend.",
        }
        return save_whatsapp_status(store, service, normalized_number, result)

    template_name = getattr(service, "welcome_template_name", "").strip() if service else ""
    template_language = getattr(service, "welcome_template_language", "pt_PT").strip() if service else "pt_PT"
    result = service.attempt_template_message(
        normalized_number,
        template_name=template_name,
        language_code=template_language,
        source=source,
    )
    return save_whatsapp_status(store, service, normalized_number, result)
