from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any


SOS_TRIGGER_WORDS = {
    "sos",
    "emergencia",
    "emergência",
    "ajuda",
    "socorro",
    "mayday",
}

SOS_TRIGGER_PHRASES = {
    "homem ao mar",
    "cai ao mar",
    "cai na agua",
    "cai na água",
    "queda ao mar",
    "preciso de ajuda urgente",
}

SOS_CANCEL_WORDS = {
    "cancelar",
    "cancela",
    "anular",
    "anula",
}

SOS_CANCEL_PHRASES = {
    "cancelar sos",
    "cancela sos",
    "anular sos",
    "anula sos",
    "falso alarme",
    "sem efeito",
    "cancelar ajuda",
    "pedido cancelado",
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _lookup_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_only.casefold()).strip()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sos_alerts_enabled() -> bool:
    raw = os.getenv("WHATSAPP_SOS_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def sos_pending_ttl_minutes() -> int:
    raw = os.getenv("WHATSAPP_SOS_PENDING_TTL_MINUTES", "20").strip()
    try:
        minutes = int(raw)
    except ValueError:
        return 20
    return max(1, minutes)


def sos_pending_key(from_number: str) -> str:
    number = re.sub(r"\D+", "", str(from_number or ""))
    return f"whatsapp:sos:pending:{number}"


def sos_event_key(event_id: str) -> str:
    return f"whatsapp:sos:event:{_clean_text(event_id)}"


def sos_last_event_key(from_number: str) -> str:
    number = re.sub(r"\D+", "", str(from_number or ""))
    return f"whatsapp:sos:last:{number}"


def build_sos_event_id(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return f"SOS-{current.strftime('%Y%m%d-%H%M%S')}"


def is_sos_trigger(text: str) -> bool:
    clean = _clean_text(text)
    lookup = _lookup_key(clean)
    if lookup in SOS_TRIGGER_WORDS:
        return True
    return any(phrase in lookup for phrase in SOS_TRIGGER_PHRASES)


def is_sos_cancel(text: str, *, pending_sos: bool = False) -> bool:
    clean = _clean_text(text)
    lookup = _lookup_key(clean)
    if pending_sos and lookup in SOS_CANCEL_WORDS:
        return True
    return any(phrase in lookup for phrase in SOS_CANCEL_PHRASES)


def sos_pending_expired(payload: dict[str, Any], *, now: datetime | None = None) -> bool:
    requested_at = str(payload.get("requested_at") or "").strip()
    if not requested_at:
        return False
    try:
        requested = datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current - requested > timedelta(minutes=sos_pending_ttl_minutes())


def normalize_location(latitude: Any, longitude: Any) -> tuple[float, float]:
    try:
        lat = float(str(latitude).strip().replace(",", "."))
        lon = float(str(longitude).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("Coordenadas de localização inválidas.")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Coordenadas de localização fora dos limites válidos.")
    return lat, lon


def maps_link(latitude: float, longitude: float) -> str:
    return f"https://maps.google.com/?q={latitude:.6f},{longitude:.6f}"


def build_sos_location_prompt() -> str:
    return (
        "🛟⚠️ SOS recebido.\n\n"
        "Partilha já a tua localização atual pelo WhatsApp para eu avisar imediatamente o contacto de emergência.\n\n"
        "No WhatsApp: + > Localização > Enviar localização atual.\n\n"
        "Se foi engano, responde `CANCELAR SOS`."
    )


def build_sos_disabled_reply() -> str:
    return (
        "🛟⚠️ SOS recebido, mas o modo SOS não está ativo neste ambiente.\n\n"
        "Contacta de imediato os meios de emergência locais ou a coordenação operacional por canal direto."
    )


def build_sos_no_pending_location_reply() -> str:
    return (
        "🛟 Localização recebida, mas não há pedido SOS ativo.\n\n"
        "Se precisares de ajuda urgente, envia primeiro `SOS` e volta a partilhar a localização atual."
    )


def build_sos_cancelled_reply() -> str:
    return (
        "✅🛟 Pedido SOS cancelado.\n\n"
        "Não enviei alerta ao contacto de emergência. Se precisares de ajuda, envia novamente `SOS`."
    )


def build_sos_no_pending_cancel_reply() -> str:
    return (
        "🛟 Não há pedido SOS ativo para cancelar.\n\n"
        "Se precisares de ajuda urgente, envia `SOS` e partilha a localização atual."
    )


def build_sos_expired_reply() -> str:
    return (
        "🛟⚠️ A localização chegou, mas o pedido SOS anterior já expirou por segurança.\n\n"
        "Se ainda precisares de ajuda, envia novamente `SOS` e partilha a localização atual."
    )


def build_sos_admin_alert(payload: dict[str, Any]) -> str:
    lat = float(payload["latitude"])
    lon = float(payload["longitude"])
    return (
        "🛟⚠️ ALERTA SOS\n\n"
        f"{payload.get('user_label') or 'Utilizador'} enviou um pedido de ajuda.\n"
        f"Telefone: +{payload.get('from_number', '--')}\n"
        f"Hora: {payload.get('created_at_label') or payload.get('created_at') or '--'}\n"
        f"Localização: {lat:.6f}, {lon:.6f}\n"
        f"Mapa: {maps_link(lat, lon)}\n\n"
        f"Mensagem inicial: {payload.get('initial_text') or 'SOS'}"
    )


def build_sos_admin_cancel_alert(payload: dict[str, Any]) -> str:
    lat = payload.get("latitude")
    lon = payload.get("longitude")
    location_lines = ""
    if lat is not None and lon is not None:
        latitude = float(lat)
        longitude = float(lon)
        location_lines = (
            f"\nLocalização do pedido: {latitude:.6f}, {longitude:.6f}"
            f"\nMapa: {maps_link(latitude, longitude)}"
        )
    return (
        "✅🛟 CANCELAMENTO SOS\n\n"
        f"{payload.get('user_label') or 'Utilizador'} cancelou o pedido SOS.\n"
        f"Telefone: +{payload.get('from_number', '--')}\n"
        f"Hora do cancelamento: {payload.get('cancelled_at_label') or payload.get('cancelled_at') or '--'}\n"
        f"Evento: {payload.get('event_id') or '--'}"
        f"{location_lines}"
    )


def build_sos_user_confirmation(sent_count: int, failed_count: int = 0) -> str:
    if sent_count > 0:
        return (
            "✅🛟 Localização recebida.\n\n"
            f"O alerta SOS foi enviado ao contacto de emergência ({sent_count} envio(s)). "
            "Mantém-te em segurança e segue instruções da coordenação.\n\n"
            "Se foi falso alarme, responde `CANCELAR SOS`."
        )
    if failed_count == 0:
        return (
            "🛟⚠️ Localização recebida, mas não encontrei contacto de emergência externo para avisar.\n\n"
            "Contacta de imediato os meios de emergência locais ou a coordenação operacional por canal direto."
        )
    return (
        "🛟⚠️ Localização recebida, mas não consegui enviar o alerta por WhatsApp.\n\n"
        "Contacta de imediato os meios de emergência locais ou a coordenação operacional por canal direto. "
        f"Falhas registadas: {failed_count}."
    )


def build_sos_dispatched_cancelled_reply(sent_count: int, failed_count: int = 0) -> str:
    if sent_count > 0:
        return (
            "✅🛟 Pedido SOS cancelado.\n\n"
            f"Avisei o contacto de emergência do cancelamento ({sent_count} envio(s))."
        )
    return (
        "✅🛟 Pedido SOS marcado como cancelado.\n\n"
        "Não consegui avisar o contacto de emergência por WhatsApp. "
        f"Falhas registadas: {failed_count}."
    )


def configured_sos_numbers() -> list[str]:
    numbers = []
    for raw in os.getenv("WHATSAPP_SOS_ALERT_NUMBERS", "").split(","):
        clean = re.sub(r"\D+", "", raw)
        if clean and clean not in numbers:
            numbers.append(clean)
    return numbers


def sos_admin_recipients(
    users: list[dict[str, Any]],
    *,
    configured_numbers: list[str] | None = None,
    exclude_number: str = "",
) -> list[dict[str, str]]:
    recipients: list[dict[str, str]] = []
    seen: set[str] = set()
    excluded = re.sub(r"\D+", "", exclude_number)
    for user in users:
        if _lookup_key(user.get("role")) != "admin":
            continue
        number = re.sub(r"\D+", "", str(user.get("whatsapp_number") or ""))
        if excluded and number == excluded:
            continue
        if not number or not bool(user.get("whatsapp_opt_in")):
            continue
        seen.add(number)
        recipients.append(
            {
                "number": number,
                "username": _clean_text(user.get("username")),
                "label": _clean_text(user.get("full_name")) or _clean_text(user.get("username")) or number,
            }
        )
    for number in configured_numbers or []:
        clean = re.sub(r"\D+", "", number)
        if excluded and clean == excluded:
            continue
        if clean and clean not in seen:
            seen.add(clean)
            recipients.append({"number": clean, "username": "", "label": clean})
    return recipients


def local_datetime_label(value: str) -> str:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return _clean_text(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d/%m/%Y %H:%M")
