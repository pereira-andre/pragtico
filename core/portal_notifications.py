"""Helpers for concise live operational notifications in the portal UI."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Dict, Optional

from flask import url_for

from core import services

PORTAL_NOTIFICATION_CHANNEL = "portal_live"


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _parse_iso(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _time_label(value: str | None) -> str:
    parsed = _parse_iso(value)
    if not parsed:
        return "--"
    return parsed.astimezone().strftime("%H:%M")


def _maneuver_type_label(value: str | None) -> str:
    mapping = {
        "entry": "entrada",
        "departure": "saida",
        "shift": "mudanca",
    }
    return mapping.get(_clean_text(value).lower(), "manobra")


def _role_actor_label(role: str | None) -> str:
    mapping = {
        "admin": "APSS",
        "agente": "Agencia",
        "piloto": "Pilotagem",
    }
    return mapping.get(_clean_text(role).lower(), "Sistema")


def _actor_label(actor_profile: Optional[Dict], *, fallback_role: str = "") -> str:
    organization = _clean_text((actor_profile or {}).get("organization"))
    if organization:
        return organization
    role = _clean_text((actor_profile or {}).get("role")) or fallback_role
    return _role_actor_label(role)


def _scope_key(value: str | None) -> str:
    clean = _clean_text(value)
    if not clean:
        return ""
    normalized = unicodedata.normalize("NFKD", clean)
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    return ascii_only.casefold()


def maneuver_by_id(port_call: Dict, maneuver_id: str) -> Optional[Dict]:
    target_id = _clean_text(maneuver_id)
    if not target_id:
        return None
    return next(
        (item for item in port_call.get("maneuver_history", []) if _clean_text(item.get("id")) == target_id),
        None,
    )


def latest_maneuver_by_type(port_call: Dict, maneuver_type: str) -> Optional[Dict]:
    clean_type = _clean_text(maneuver_type).lower()
    matches = [
        item
        for item in port_call.get("maneuver_history", [])
        if _clean_text(item.get("type")).lower() == clean_type
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda item: (
            item.get("planned_at") or item.get("completed_at") or item.get("created_at") or "",
            item.get("id") or "",
        )
    )
    return matches[-1]


def _short_maneuver_id(maneuver: Dict) -> str:
    raw_id = _clean_text((maneuver or {}).get("id"))
    if not raw_id:
        return "--"
    return raw_id.split("-", 1)[0].upper()


def _short_label(value: str, *, limit: int = 26) -> str:
    clean = _clean_text(value)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _port_call_scope_organization(port_call: Dict) -> str:
    for key in ("agent_profile", "created_by_profile", "reported_by_profile"):
        profile = port_call.get(key) or {}
        organization = _clean_text(profile.get("organization"))
        if organization:
            return organization
    return ""


def _plan_update_summary(before: Optional[Dict], after: Dict) -> str:
    previous = before or {}
    before_time = _time_label(previous.get("planned_at"))
    after_time = _time_label(after.get("planned_at"))
    if before_time != "--" and after_time != "--" and before_time != after_time:
        return f"hora alterada {before_time} -> {after_time}"

    before_route = (
        _clean_text(previous.get("origin")),
        _clean_text(previous.get("destination")),
    )
    after_route = (
        _clean_text(after.get("origin")),
        _clean_text(after.get("destination")),
    )
    if before_route != after_route:
        return "trajeto alterado"

    return "planeamento atualizado"


def _build_message(
    *,
    event_type: str,
    port_call: Dict,
    maneuver: Dict,
    actor_label: str,
    previous_maneuver: Optional[Dict] = None,
) -> str:
    vessel_name = _short_label(_clean_text(port_call.get("vessel_name")) or "Navio")
    maneuver_id = _short_maneuver_id(maneuver)
    maneuver_type = _maneuver_type_label(maneuver.get("type"))
    if event_type == "created":
        planned_time = _time_label(maneuver.get("planned_at"))
        suffix = f"{maneuver_type} {planned_time}" if planned_time != "--" else f"{maneuver_type} criada"
        return f"🟡 {maneuver_id} · {vessel_name} · {suffix} · {actor_label}"
    if event_type == "approved":
        return f"✅ {maneuver_id} · {vessel_name} · {maneuver_type} aprovada · {actor_label}"
    if event_type == "completed":
        finished_time = _time_label(maneuver.get("execution_finished_at") or maneuver.get("completed_at"))
        suffix = f"{maneuver_type} concluida {finished_time}" if finished_time != "--" else f"{maneuver_type} concluida"
        return f"✅ {maneuver_id} · {vessel_name} · {suffix} · {actor_label}"
    if event_type == "report_updated":
        return f"📝 {maneuver_id} · {vessel_name} · registo revisto · {actor_label}"
    summary = _plan_update_summary(previous_maneuver, maneuver)
    return f"✏️ {maneuver_id} · {vessel_name} · {maneuver_type} {summary} · {actor_label}"


def record_maneuver_notification(
    *,
    port_call: Dict,
    maneuver: Optional[Dict],
    event_type: str,
    actor_username: str,
    previous_maneuver: Optional[Dict] = None,
) -> Optional[Dict]:
    target = maneuver or {}
    if not target.get("id") or not port_call.get("id"):
        return None

    actor_profile = services.store.get_user_profile(actor_username) or {}
    actor_label = _actor_label(actor_profile, fallback_role=actor_profile.get("role", ""))
    payload = {
        "kind": "maneuver",
        "event_type": _clean_text(event_type) or "updated",
        "message": _build_message(
            event_type=event_type,
            port_call=port_call,
            maneuver=target,
            actor_label=actor_label,
            previous_maneuver=previous_maneuver,
        ),
        "port_call_id": _clean_text(port_call.get("id")),
        "maneuver_id": _clean_text(target.get("id")),
        "reference_code": _clean_text(port_call.get("reference_code")),
        "vessel_name": _clean_text(port_call.get("vessel_name")),
        "actor_label": actor_label,
        "scope_organization_key": _scope_key(_port_call_scope_organization(port_call)),
        "url": url_for(
            "port_calls.maneuver_detail",
            port_call_id=port_call["id"],
            maneuver_id=target["id"],
        ),
    }
    return services.store.record_channel_event(
        channel=PORTAL_NOTIFICATION_CHANNEL,
        event_type=f"maneuver_{payload['event_type']}",
        payload=payload,
        username=actor_username,
    )
