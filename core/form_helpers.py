from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import request

from core import services
from domain.berth_layout import (
    canonicalize_berth_label,
    find_occupied_berth_conflict,
    is_known_berth_label,
)
from storage import format_constraint_labels



def parse_local_datetime_input(value: str, label: str = "ETA") -> str:
    """Parse a local datetime string from a form input and return it as a timezone-aware ISO string."""
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        raise ValueError(f"{label} é obrigatória.")
    try:
        dt = datetime.fromisoformat(clean)
    except ValueError as exc:
        dt = None
        relaxed = clean.replace(", ", " ").replace(",", " ")
        for fmt in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(relaxed, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            raise ValueError(f"{label} inválida. Usa data e hora válidas.") from exc
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone().isoformat()


def parse_optional_local_datetime_input(value: str, label: str = "Data e hora") -> str:
    """Parse an optional local datetime string, returning an empty string if the value is blank."""
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        return ""
    return parse_local_datetime_input(clean, label=label)


def require_form_text(value: str, label: str) -> str:
    """Return a cleaned form text value, raising ValueError if it is empty."""
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        raise ValueError(f"{label} é obrigatório.")
    return clean


def normalize_portal_berth(value: str, label: str = "Cais") -> str:
    """Resolve a berth label against the canonical Setubal berth catalog."""
    clean = require_form_text(value, label)
    canonical = canonicalize_berth_label(clean, berth_options=services.BERTH_OPTIONS)
    if not is_known_berth_label(canonical, berth_options=services.BERTH_OPTIONS):
        raise ValueError(f"{label} inválido. Usa um dos cais/fundeadouros conhecidos do porto.")
    return canonical


def occupied_portal_berth_conflict(berth: str, *, current_port_call_id: str = "") -> dict | None:
    """Return the conflicting in-port vessel occupying a quay berth, ignoring anchorages."""
    port_activity = services.store.get_port_activity_snapshot(window_days=3650)
    return find_occupied_berth_conflict(
        berth,
        port_activity.get("in_port", []) or [],
        current_port_call_id=current_port_call_id,
        berth_options=services.BERTH_OPTIONS,
    )


def ensure_portal_berth_is_available(berth: str, *, current_port_call_id: str = "", label: str = "Cais") -> str:
    """Validate a canonical berth and raise when the quay is already occupied by another in-port vessel."""
    canonical = normalize_portal_berth(berth, label=label)
    conflict = occupied_portal_berth_conflict(canonical, current_port_call_id=current_port_call_id)
    if conflict:
        conflict_name = conflict.get("vessel_name") or conflict.get("reference_code") or "outro navio"
        raise ValueError(f"{label} {canonical} já está ocupado por {conflict_name}.")
    return canonical


def format_note_datetime(value: str) -> str:
    """Format an ISO datetime string as a local dd/mm/yyyy HH:MM label for display in notes."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value


def _build_created_port_call_message(port_call: dict) -> str:
    """Return the success message shown after creating a scale from chat."""
    eta_value = port_call.get("eta") or ""
    eta_label = format_note_datetime(eta_value) or port_call.get("eta_label") or "--"
    message = f"Escala criada para {port_call['vessel_name']} com ETA {eta_label}."
    if not eta_value:
        return message
    try:
        eta_dt = datetime.fromisoformat(eta_value)
    except ValueError:
        return message
    if eta_dt.tzinfo is None:
        eta_dt = eta_dt.replace(tzinfo=timezone.utc)
    if eta_dt < datetime.now(eta_dt.tzinfo) - timedelta(days=5):
        message += " Atenção: o ETA ficou no passado e esta escala pode não aparecer na vista operacional atual."
    return message


def _local_iso_to_label(value: str | None) -> str:
    if not value:
        return "Sem hora"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value


def _iso_to_datetime_local_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


def compact_multiline_note(title: str, fields: list[tuple[str, str]]) -> str:
    """Build a multiline note string from a title and a list of (label, value) pairs, omitting blank values."""
    lines = [title]
    for label, value in fields:
        clean = " ".join((value or "").strip().split())
        if clean:
            lines.append(f"{label}: {clean}")
    return "\n".join(lines)


def build_entry_request_note(form_data: dict) -> str:
    """Build the agent entry request note from form data fields."""
    return compact_multiline_note("Registo do agente · Entrada", [
        ("Calado", form_data.get("draft_m", "")),
        ("Rebocadores", form_data.get("tug_count", "")),
        ("Restrições", format_constraint_labels(form_data.get("constraints", []))),
        ("Observações", form_data.get("notes", "")),
    ])


def build_departure_plan_note(form_data: dict) -> str:
    """Build the agent departure plan note from form data fields."""
    return compact_multiline_note("Registo do agente · Saída", [
        ("Origem", form_data.get("origin_berth", "")),
        ("Calado", form_data.get("draft_m", "")),
        ("Rebocadores", form_data.get("tug_count", "")),
        ("Restrições", format_constraint_labels(form_data.get("constraints", []))),
        ("Observações", form_data.get("notes", "")),
    ])


def build_shift_plan_note(form_data: dict) -> str:
    """Build the agent berth-shift plan note from form data fields."""
    return compact_multiline_note("Registo do agente · Mudança", [
        ("Origem", form_data.get("origin_berth", "")),
        ("Destino", form_data.get("destination_berth", "")),
        ("Calado", form_data.get("draft_m", "")),
        ("Rebocadores", form_data.get("tug_count", "")),
        ("Restrições", format_constraint_labels(form_data.get("constraints", []))),
        ("Observações", form_data.get("notes", "")),
    ])


def build_pilot_report_note(form_data: dict, maneuver_label: str, existing_note: str = "") -> str:
    """Build the pilot operational report note, optionally appending to an existing note."""
    report = compact_multiline_note(f"Registo simplificado de pilotagem · {maneuver_label}", [
        ("Início da manobra", format_note_datetime(form_data.get("maneuver_started_at", ""))),
        ("Fim da manobra", format_note_datetime(form_data.get("maneuver_finished_at", ""))),
        ("Calado", form_data.get("draft_m", "")),
        ("Observações", form_data.get("notes", "")),
    ])
    if existing_note.strip():
        return f"{existing_note.strip()}\n\n{report}"
    return report


def get_current_conversation(username: str):
    """Return the current conversation for the user based on the request's conversation_id parameter."""
    requested_id = request.args.get("conversation_id", "").strip() or None
    return services.store.ensure_conversation(username=username, conversation_id=requested_id)
