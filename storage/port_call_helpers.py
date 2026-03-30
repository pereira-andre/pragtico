"""Port call normalization, decoration, activity snapshot, and maneuver helpers."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from domain.cost_engine import ManoeuvreInput, ManoeuvreType, calculate_scale_cost
from domain.document_processing import iso_now

from .constants import (
    ALLOWED_PORT_CALL_APPROVAL_STATUSES,
    PORT_CALL_APPROVAL_ABORTED,
    PORT_CALL_APPROVAL_APPROVED,
    PORT_CALL_APPROVAL_PENDING,
    PORT_CALL_STATUS_DEPARTED,
    PORT_CALL_STATUS_IN_PORT,
    PORT_CALL_STATUS_SCHEDULED,
    _lookup_key,
)
from .utils import (
    _actor_meta,
    _build_actor_snapshot,
    _clean_text,
    _constraint_badges,
    _iso_to_datetime_local_value,
    _local_date_label,
    _local_iso_to_label,
    _local_time_label,
    _maneuver_action_label,
    _maneuver_state_meta,
    _maneuver_type_label,
    _normalize_actor_label,
    _normalize_maneuver_type,
    _normalize_username,
    _parse_iso_datetime,
    _resolve_vessel_type_meta,
    normalize_constraint_codes,
)


def _normalize_maneuver_record(record: Dict, fallback_created_by: str = "system") -> Dict:
    maneuver_type = _normalize_maneuver_type(record.get("type"))
    state = (record.get("state") or PORT_CALL_APPROVAL_PENDING).strip().lower()
    if state not in ALLOWED_PORT_CALL_APPROVAL_STATUSES | {"completed"}:
        state = PORT_CALL_APPROVAL_PENDING
    created_by = _normalize_username(record.get("created_by") or fallback_created_by or "system")
    decided_by = _normalize_username(record.get("decided_by"))
    reported_by = _normalize_username(record.get("reported_by"))
    created_at = record.get("created_at") or iso_now()
    updated_at = record.get("updated_at") or created_at
    return {
        "id": record.get("id") or str(uuid.uuid4()),
        "type": maneuver_type,
        "state": state,
        "planned_at": record.get("planned_at"),
        "completed_at": record.get("completed_at"),
        "execution_started_at": record.get("execution_started_at"),
        "execution_finished_at": record.get("execution_finished_at"),
        "planned_draft_m": record.get("planned_draft_m", "") or _extract_compact_note_value(record.get("plan_note", ""), "Calado"),
        "tug_count": record.get("tug_count", "") or _extract_compact_note_value(record.get("plan_note", ""), "Rebocadores"),
        "plan_observations": record.get("plan_observations", "") or _extract_compact_note_value(record.get("plan_note", ""), "Observações"),
        "reported_draft_m": record.get("reported_draft_m", "") or "",
        "origin": " ".join((record.get("origin") or "").strip().split()),
        "destination": " ".join((record.get("destination") or "").strip().split()),
        "plan_note": record.get("plan_note", "") or "",
        "approval_note": record.get("approval_note", "") or "",
        "aborted_reason": record.get("aborted_reason", "") or "",
        "constraints": normalize_constraint_codes(record.get("constraints")),
        "decided_by": decided_by or None,
        "decided_by_profile": _build_actor_snapshot(record.get("decided_by_profile"), username=decided_by),
        "decided_at": record.get("decided_at"),
        "report_note": record.get("report_note", "") or "",
        "reported_by": reported_by or None,
        "reported_by_profile": _build_actor_snapshot(record.get("reported_by_profile"), username=reported_by),
        "reported_at": record.get("reported_at"),
        "change_log": list(record.get("change_log") or []),
        "created_by": created_by,
        "created_by_profile": _build_actor_snapshot(record.get("created_by_profile"), username=created_by),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _maneuver_sort_key(record: Dict) -> tuple[float, str]:
    dt = (
        _parse_iso_datetime(record.get("planned_at"))
        or _parse_iso_datetime(record.get("completed_at"))
        or _parse_iso_datetime(record.get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )
    return (dt.timestamp(), record.get("id", ""))


def _latest_maneuver(history: List[Dict], maneuver_type: str, states: Optional[set[str]] = None) -> Optional[Dict]:
    clean_type = _normalize_maneuver_type(maneuver_type)
    filtered = [item for item in history if _normalize_maneuver_type(item.get("type")) == clean_type]
    if states is not None:
        filtered = [item for item in filtered if item.get("state") in states]
    if not filtered:
        return None
    filtered.sort(key=_maneuver_sort_key)
    return filtered[-1]


def _latest_reportable_maneuver(history: List[Dict], maneuver_type: str) -> Optional[Dict]:
    clean_type = _normalize_maneuver_type(maneuver_type)
    filtered = [
        item
        for item in history
        if _normalize_maneuver_type(item.get("type")) == clean_type
        and item.get("state") in ("completed", "approved")
        and not (item.get("report_note") or "").strip()
    ]
    if not filtered:
        return None
    filtered.sort(key=_maneuver_sort_key)
    return filtered[-1]


def _build_maneuver_change_log_entry(
    *,
    actor_username: str,
    actor_profile: Optional[Dict],
    reason: str,
    summary: str,
) -> Dict:
    return {
        "changed_at": iso_now(),
        "changed_by": _normalize_username(actor_username),
        "changed_by_profile": _build_actor_snapshot(actor_profile, username=actor_username),
        "reason": " ".join((reason or "").strip().split()),
        "summary": " ".join((summary or "").strip().split()),
    }


def _append_maneuver_change_log(
    maneuver: Dict,
    *,
    actor_username: str,
    actor_profile: Optional[Dict],
    reason: str,
    summary: str,
) -> None:
    if not " ".join((reason or "").strip().split()):
        raise ValueError("O motivo da alteração é obrigatório.")
    maneuver.setdefault("change_log", [])
    maneuver["change_log"].append(
        _build_maneuver_change_log_entry(
            actor_username=actor_username,
            actor_profile=actor_profile,
            reason=reason,
            summary=summary,
        )
    )


def _extract_compact_note_value(note: str, label: str) -> str:
    prefix = f"{label.strip()}:".casefold()
    for raw_line in (note or "").splitlines():
        line = raw_line.strip()
        if line.casefold().startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def _effective_maneuver_time(record: Dict) -> Optional[str]:
    return record.get("execution_finished_at") or record.get("completed_at") or record.get("planned_at")


def _can_edit_maneuver_plan(maneuver: Dict, actor_role: str) -> bool:
    clean_role = (actor_role or "").strip().lower()
    if clean_role == "admin":
        return True
    if clean_role == "piloto":
        return True
    if clean_role == "agente":
        return maneuver.get("state") == PORT_CALL_APPROVAL_PENDING
    return False


def _extract_labeled_report(note: str, label: str) -> str:
    clean = (note or "").strip()
    marker = f"Registo simplificado de pilotagem · {label}"
    index = clean.find(marker)
    if index >= 0:
        return clean[index:].strip()
    if label == "Entrada":
        legacy_marker = "Registo simplificado de pilotagem"
        index = clean.find(legacy_marker)
        if index >= 0 and "· Saída" not in clean[index:index + 40] and "· Mudança" not in clean[index:index + 40]:
            return clean[index:].strip()
    return ""


def _compose_abort_note(base_note: str, prefix: str, reason: str) -> str:
    clean_note = (base_note or "").strip()
    clean_reason = " ".join((reason or "").strip().split())
    if not clean_reason:
        return clean_note
    return f"{clean_note} | {prefix}: {clean_reason}".strip(" |")


def _build_legacy_maneuver_history(record: Dict) -> List[Dict]:
    history: List[Dict] = []
    created_at = record.get("created_at") or iso_now()
    created_by = record.get("created_by", "system") or "system"

    if record.get("eta") or record.get("ata") or record.get("approval_note") or record.get("aborted_reason"):
        entry_state = PORT_CALL_APPROVAL_PENDING
        if record.get("ata"):
            entry_state = "completed"
        elif record.get("approval_status") == PORT_CALL_APPROVAL_ABORTED:
            entry_state = PORT_CALL_APPROVAL_ABORTED
        elif record.get("approval_status") == PORT_CALL_APPROVAL_APPROVED:
            entry_state = PORT_CALL_APPROVAL_APPROVED
        history.append(
            _normalize_maneuver_record(
                {
                    "type": "entry",
                    "state": entry_state,
                    "planned_at": record.get("eta"),
                    "completed_at": record.get("ata"),
                    "origin": record.get("last_port", ""),
                    "destination": record.get("berth", ""),
                    "plan_note": record.get("notes", ""),
                    "approval_note": record.get("approval_note", ""),
                    "aborted_reason": record.get("aborted_reason", ""),
                    "decided_by": record.get("decided_by"),
                    "decided_at": record.get("decided_at"),
                    "report_note": _extract_labeled_report(record.get("notes", ""), "Entrada"),
                    "created_by": created_by,
                    "created_at": created_at,
                    "updated_at": record.get("updated_at") or created_at,
                },
                fallback_created_by=created_by,
            )
        )

    departure_abort_reason = _extract_departure_abort_reason(record.get("departure_plan_note"))
    if record.get("planned_departure_at") or record.get("departure_at") or record.get("departure_plan_note"):
        departure_state = PORT_CALL_APPROVAL_PENDING
        if record.get("departure_at"):
            departure_state = "completed"
        elif departure_abort_reason:
            departure_state = PORT_CALL_APPROVAL_ABORTED
        elif record.get("planned_departure_at") and record.get("approval_status") == PORT_CALL_APPROVAL_APPROVED:
            departure_state = PORT_CALL_APPROVAL_APPROVED
        history.append(
            _normalize_maneuver_record(
                {
                    "type": "departure",
                    "state": departure_state,
                    "planned_at": record.get("planned_departure_at"),
                    "completed_at": record.get("departure_at"),
                    "origin": record.get("berth", ""),
                    "destination": record.get("next_port", ""),
                    "plan_note": record.get("departure_plan_note", ""),
                    "approval_note": record.get("approval_note", "") if record.get("planned_departure_at") else "",
                    "aborted_reason": departure_abort_reason,
                    "decided_by": record.get("decided_by") if record.get("planned_departure_at") else None,
                    "decided_at": record.get("decided_at") if record.get("planned_departure_at") else None,
                    "report_note": _extract_labeled_report(record.get("notes", ""), "Saída"),
                    "created_by": created_by,
                    "created_at": record.get("updated_at") or created_at,
                    "updated_at": record.get("updated_at") or created_at,
                },
                fallback_created_by=created_by,
            )
        )

    shift_abort_reason = _extract_shift_abort_reason(record.get("shift_plan_note"))
    if record.get("planned_shift_at") or record.get("shift_at") or record.get("shift_plan_note"):
        shift_state = PORT_CALL_APPROVAL_PENDING
        if record.get("shift_at"):
            shift_state = "completed"
        elif shift_abort_reason:
            shift_state = PORT_CALL_APPROVAL_ABORTED
        elif record.get("shift_approval_status") == PORT_CALL_APPROVAL_APPROVED:
            shift_state = PORT_CALL_APPROVAL_APPROVED
        history.append(
            _normalize_maneuver_record(
                {
                    "type": "shift",
                    "state": shift_state,
                    "planned_at": record.get("planned_shift_at"),
                    "completed_at": record.get("shift_at"),
                    "origin": record.get("shift_origin_berth", "") or record.get("berth", ""),
                    "destination": record.get("shift_destination_berth", ""),
                    "plan_note": record.get("shift_plan_note", ""),
                    "approval_note": record.get("shift_approval_note", ""),
                    "aborted_reason": shift_abort_reason or record.get("shift_aborted_reason", ""),
                    "decided_by": record.get("shift_decided_by"),
                    "decided_at": record.get("shift_decided_at"),
                    "report_note": _extract_labeled_report(record.get("notes", ""), "Mudança"),
                    "created_by": created_by,
                    "created_at": record.get("updated_at") or created_at,
                    "updated_at": record.get("updated_at") or created_at,
                },
                fallback_created_by=created_by,
            )
        )

    history.sort(key=_maneuver_sort_key)
    return history


def _sync_port_call_from_history(record: Dict) -> Dict:
    synced = dict(record)
    history = [_normalize_maneuver_record(item, fallback_created_by=synced.get("created_by", "system")) for item in synced.get("maneuver_history", [])]
    history.sort(key=_maneuver_sort_key)
    synced["maneuver_history"] = history

    entry = _latest_maneuver(history, "entry")
    active_departure = _latest_maneuver(history, "departure", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
    latest_departure = _latest_maneuver(history, "departure")
    completed_departure = _latest_maneuver(history, "departure", {"completed"})
    active_shift = _latest_maneuver(history, "shift", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
    latest_shift = _latest_maneuver(history, "shift")
    completed_shift = _latest_maneuver(history, "shift", {"completed"})

    if entry:
        synced["eta"] = entry.get("planned_at")
        synced["ata"] = entry.get("completed_at") if entry.get("state") == "completed" else None
        synced["approval_status"] = (
            entry.get("state")
            if entry.get("state") in ALLOWED_PORT_CALL_APPROVAL_STATUSES
            else PORT_CALL_APPROVAL_APPROVED if entry.get("state") == "completed" else PORT_CALL_APPROVAL_PENDING
        )
        synced["approval_note"] = entry.get("approval_note", "")
        synced["aborted_reason"] = entry.get("aborted_reason", "")
        synced["decided_by"] = entry.get("decided_by")
        synced["decided_by_profile"] = entry.get("decided_by_profile") or _build_actor_snapshot(
            None,
            username=entry.get("decided_by"),
        )
        synced["decided_at"] = entry.get("decided_at")
        synced["created_by"] = entry.get("created_by") or synced.get("created_by", "system")
        synced["created_by_profile"] = entry.get("created_by_profile") or _build_actor_snapshot(
            None,
            username=synced.get("created_by"),
        )
        if entry.get("origin"):
            synced["last_port"] = entry["origin"]
    else:
        synced["eta"] = synced.get("eta")

    current_berth = synced.get("berth", "")
    if completed_shift and completed_shift.get("destination"):
        current_berth = completed_shift["destination"]
    elif entry and entry.get("destination"):
        current_berth = entry["destination"]
    synced["berth"] = current_berth

    current_departure = active_departure or latest_departure
    synced["planned_departure_at"] = active_departure.get("planned_at") if active_departure else None
    synced["departure_at"] = completed_departure.get("completed_at") if completed_departure else None
    if current_departure:
        synced["departure_plan_note"] = _compose_abort_note(
            current_departure.get("plan_note", ""),
            "Saída abortada",
            current_departure.get("aborted_reason", ""),
        )
        if current_departure.get("destination"):
            synced["next_port"] = current_departure["destination"]
    else:
        synced["departure_plan_note"] = ""

    current_shift = active_shift or latest_shift
    synced["planned_shift_at"] = active_shift.get("planned_at") if active_shift else None
    synced["shift_at"] = completed_shift.get("completed_at") if completed_shift else None
    if current_shift:
        synced["shift_plan_note"] = _compose_abort_note(
            current_shift.get("plan_note", ""),
            "Mudança abortada",
            current_shift.get("aborted_reason", ""),
        )
        synced["shift_origin_berth"] = current_shift.get("origin", "")
        synced["shift_destination_berth"] = current_shift.get("destination", "")
        synced["shift_approval_status"] = (
            current_shift.get("state")
            if current_shift.get("state") in ALLOWED_PORT_CALL_APPROVAL_STATUSES
            else PORT_CALL_APPROVAL_APPROVED if current_shift.get("state") == "completed" else PORT_CALL_APPROVAL_PENDING
        )
        synced["shift_approval_note"] = current_shift.get("approval_note", "")
        synced["shift_aborted_reason"] = current_shift.get("aborted_reason", "")
        synced["shift_decided_by"] = current_shift.get("decided_by")
        synced["shift_decided_by_profile"] = current_shift.get("decided_by_profile") or _build_actor_snapshot(
            None,
            username=current_shift.get("decided_by"),
        )
        synced["shift_decided_at"] = current_shift.get("decided_at")
    else:
        synced["shift_plan_note"] = ""
        synced["shift_origin_berth"] = ""
        synced["shift_destination_berth"] = ""
        synced["shift_approval_status"] = PORT_CALL_APPROVAL_PENDING
        synced["shift_approval_note"] = ""
        synced["shift_aborted_reason"] = ""
        synced["shift_decided_by"] = None
        synced["shift_decided_by_profile"] = _build_actor_snapshot(None)
        synced["shift_decided_at"] = None

    if completed_departure:
        synced["status"] = PORT_CALL_STATUS_DEPARTED
    elif entry and entry.get("state") == "completed":
        synced["status"] = PORT_CALL_STATUS_IN_PORT
    else:
        synced["status"] = PORT_CALL_STATUS_SCHEDULED
    return synced


def _normalize_port_call_record(record: Dict) -> Dict:
    created_at = record.get("created_at") or iso_now()
    updated_at = record.get("updated_at") or created_at
    approval_status = record.get("approval_status") or PORT_CALL_APPROVAL_PENDING
    status = record.get("status") or PORT_CALL_STATUS_SCHEDULED
    if (
        "approval_status" not in record
        and status in {PORT_CALL_STATUS_IN_PORT, PORT_CALL_STATUS_DEPARTED}
    ):
        approval_status = PORT_CALL_APPROVAL_APPROVED
    if approval_status not in ALLOWED_PORT_CALL_APPROVAL_STATUSES:
        approval_status = PORT_CALL_APPROVAL_PENDING
    shift_approval_status = record.get("shift_approval_status") or PORT_CALL_APPROVAL_PENDING
    if shift_approval_status not in ALLOWED_PORT_CALL_APPROVAL_STATUSES:
        shift_approval_status = PORT_CALL_APPROVAL_PENDING
    normalized = {
        "id": record.get("id") or str(uuid.uuid4()),
        "vessel_name": record.get("vessel_name") or "Navio",
        "vessel_short_name": record.get("vessel_short_name", "") or "",
        "vessel_imo": record.get("vessel_imo", "") or "",
        "vessel_call_sign": record.get("vessel_call_sign", "") or "",
        "vessel_flag": record.get("vessel_flag", "") or "",
        "vessel_type": record.get("vessel_type", "") or "",
        "vessel_loa_m": record.get("vessel_loa_m", "") or "",
        "vessel_beam_m": record.get("vessel_beam_m", "") or "",
        "vessel_gt_t": record.get("vessel_gt_t", "") or "",
        "vessel_max_draft_m": record.get("vessel_max_draft_m", "") or "",
        "vessel_dwt_t": record.get("vessel_dwt_t", "") or "",
        "status": status,
        "approval_status": approval_status,
        "approval_note": record.get("approval_note", "") or "",
        "aborted_reason": record.get("aborted_reason", "") or "",
        "decided_by": _normalize_username(record.get("decided_by")),
        "decided_by_profile": _build_actor_snapshot(
            record.get("decided_by_profile"),
            username=record.get("decided_by"),
        ),
        "decided_at": record.get("decided_at"),
        "eta": record.get("eta"),
        "ata": record.get("ata"),
        "planned_departure_at": record.get("planned_departure_at"),
        "departure_plan_note": record.get("departure_plan_note", "") or "",
        "departure_at": record.get("departure_at"),
        "planned_shift_at": record.get("planned_shift_at"),
        "shift_plan_note": record.get("shift_plan_note", "") or "",
        "shift_at": record.get("shift_at"),
        "shift_origin_berth": record.get("shift_origin_berth", "") or "",
        "shift_destination_berth": record.get("shift_destination_berth", "") or "",
        "shift_approval_status": shift_approval_status,
        "shift_approval_note": record.get("shift_approval_note", "") or "",
        "shift_aborted_reason": record.get("shift_aborted_reason", "") or "",
        "shift_decided_by": _normalize_username(record.get("shift_decided_by")),
        "shift_decided_by_profile": _build_actor_snapshot(
            record.get("shift_decided_by_profile"),
            username=record.get("shift_decided_by"),
        ),
        "shift_decided_at": record.get("shift_decided_at"),
        "berth": record.get("berth", "") or "",
        "last_port": record.get("last_port", "") or "",
        "next_port": record.get("next_port", "") or "",
        "created_by": _normalize_username(record.get("created_by", "system") or "system"),
        "created_by_profile": _build_actor_snapshot(
            record.get("created_by_profile"),
            username=record.get("created_by", "system") or "system",
        ),
        "notes": record.get("notes", "") or "",
        "created_at": created_at,
        "updated_at": updated_at,
    }
    raw_history = record.get("maneuver_history")
    if isinstance(raw_history, list) and raw_history:
        normalized["maneuver_history"] = [
            _normalize_maneuver_record(item, fallback_created_by=normalized["created_by"])
            for item in raw_history
        ]
    else:
        normalized["maneuver_history"] = _build_legacy_maneuver_history({**record, **normalized})
    return _sync_port_call_from_history(normalized)


def _can_abort_port_call(record: Dict) -> bool:
    eta_dt = _parse_iso_datetime(record.get("eta"))
    if not eta_dt:
        return False
    return datetime.now(timezone.utc) <= eta_dt - timedelta(hours=2)


def _can_abort_departure_plan(record: Dict) -> bool:
    planned_dt = _parse_iso_datetime(record.get("planned_departure_at"))
    if not planned_dt:
        return False
    return datetime.now(timezone.utc) <= planned_dt - timedelta(hours=1)


def _can_abort_shift_plan(record: Dict) -> bool:
    planned_dt = _parse_iso_datetime(record.get("planned_shift_at"))
    if not planned_dt:
        return False
    return datetime.now(timezone.utc) <= planned_dt - timedelta(hours=1)


def _extract_departure_abort_reason(note: Optional[str]) -> str:
    if not note:
        return ""
    match = re.search(r"Saída abortada:\s*([^|]+)", note, flags=re.IGNORECASE)
    if not match:
        return ""
    return " ".join(match.group(1).strip().split())


def _extract_shift_abort_reason(note: Optional[str]) -> str:
    if not note:
        return ""
    match = re.search(r"Mudança abortada:\s*([^|]+)", note, flags=re.IGNORECASE)
    if not match:
        return ""
    return " ".join(match.group(1).strip().split())


def _build_maneuver_event(
    *,
    event_type: str,
    title: str,
    when_value: str,
    vessel_name: str,
    summary: str,
    detail: str = "",
    berth_label: str = "",
) -> Dict:
    return {
        "event_type": event_type,
        "title": title,
        "when": when_value,
        "when_label": _local_iso_to_label(when_value),
        "vessel_name": vessel_name,
        "summary": summary,
        "detail": detail,
        "berth_label": berth_label or "Sem cais atribuído",
    }


def _build_port_call_reference(record: Dict) -> str:
    custom_reference = " ".join((record.get("reference_code") or "").strip().split())
    if custom_reference:
        return custom_reference
    created_dt = _parse_iso_datetime(record.get("created_at")) or datetime.now(timezone.utc)
    year_code = created_dt.astimezone().strftime("%y")
    vessel_code = re.sub(r"[^A-Z0-9]", "", (record.get("vessel_name") or "").upper())[:4] or "NAV"
    unique_code = re.sub(r"[^A-Z0-9]", "", (record.get("id") or "").upper())[:6] or "000000"
    return f"PTSET{year_code}{vessel_code}{unique_code}"


def _default_port_calls() -> List[Dict]:
    return []


def _format_number_pt(value: float, decimals: int = 2) -> str:
    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", "#").replace(".", ",").replace("#", ".")


def _format_currency_pt(value: Optional[float]) -> str:
    if value is None:
        return "--"
    return f"{_format_number_pt(value, 2)} €"


def _format_days_pt(value: float) -> str:
    if value <= 0:
        return "--"
    return f"{_format_number_pt(value, 1)} d"


def _format_hours_pt(value: float) -> str:
    if value <= 0:
        return ""
    return f"{_format_number_pt(value, 1)} h"


def _parse_gt_for_cost(value: Optional[str]) -> float:
    raw = (value or "").strip()
    if not raw:
        return 0.0
    clean = raw.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return 0.0


def _cost_vessel_type_key(value: Optional[str]) -> str:
    label_key = _lookup_key(_resolve_vessel_type_meta(value).get("label"))
    if label_key in {"contentores", "porta contentores"}:
        return "contentores"
    if label_key == "roll on roll off":
        return "roll-on_roll-off"
    if label_key in {"passageiros", "cruzeiros"}:
        return "passageiros"
    if label_key == "graneis liquidos":
        return "tanque"
    return "restantes"


def _maneuver_type_to_cost_engine(value: Optional[str]) -> ManoeuvreType:
    clean_type = _normalize_maneuver_type(value)
    if clean_type == "departure":
        return ManoeuvreType.DEPARTURE
    if clean_type == "shift":
        return ManoeuvreType.SHIFT
    return ManoeuvreType.ENTRY


def _archive_row_reference_value(item: Dict) -> Optional[str]:
    return item.get("actual_value") or item.get("planned_value") or item.get("date_value")


def _archive_row_reference_dt(item: Dict) -> Optional[datetime]:
    return _parse_iso_datetime(_archive_row_reference_value(item))


def _derive_standby_hours(started_at: Optional[str], finished_at: Optional[str]) -> float:
    started_dt = _parse_iso_datetime(started_at)
    finished_dt = _parse_iso_datetime(finished_at)
    if not started_dt or not finished_dt or finished_dt <= started_dt:
        return 0.0
    duration_hours = (finished_dt - started_dt).total_seconds() / 3600
    return round(max(duration_hours - 3.0, 0.0), 2)


def _estimate_scale_stay_days(port_call: Dict, archived_rows: List[Dict], now: datetime) -> float:
    status = port_call.get("status")
    if status not in {PORT_CALL_STATUS_IN_PORT, PORT_CALL_STATUS_DEPARTED}:
        return 0.0

    started_at = _parse_iso_datetime(port_call.get("ata")) or _parse_iso_datetime(port_call.get("eta"))
    if not started_at:
        for row in archived_rows:
            if row.get("maneuver_type") == "entry":
                started_at = _archive_row_reference_dt(row)
                if started_at:
                    break
    if not started_at:
        started_at = min(
            (_archive_row_reference_dt(row) for row in archived_rows if _archive_row_reference_dt(row)),
            default=None,
        )
    if not started_at:
        return 0.5

    ended_at = _parse_iso_datetime(port_call.get("departure_at"))
    if not ended_at:
        if status == PORT_CALL_STATUS_IN_PORT:
            ended_at = now
        else:
            ended_at = max(
                (_archive_row_reference_dt(row) for row in archived_rows if _archive_row_reference_dt(row)),
                default=started_at,
            )
    if not ended_at or ended_at <= started_at:
        return 0.5
    return max((ended_at - started_at).total_seconds() / 86400, 0.5)


def _build_archived_scale_summary(port_call: Dict, archived_rows: List[Dict], now: datetime) -> Dict:
    ordered_rows = sorted(
        archived_rows,
        key=lambda item: _archive_row_reference_dt(item) or datetime.min.replace(tzinfo=timezone.utc),
    )
    gt = _parse_gt_for_cost(port_call.get("vessel_gt_t") or ordered_rows[0].get("vessel_gt"))
    stay_days = _estimate_scale_stay_days(port_call, ordered_rows, now)
    include_tup = gt > 0 and stay_days > 0 and port_call.get("status") in {PORT_CALL_STATUS_IN_PORT, PORT_CALL_STATUS_DEPARTED}

    estimated_rows: List[Dict] = []
    pilotage_total = None
    tup_estimate = None
    grand_total = None
    notes: List[str] = []

    if gt > 0 and ordered_rows:
        estimate = calculate_scale_cost(
            vessel_name=port_call.get("vessel_name", "Navio"),
            gt=gt,
            vessel_type=_cost_vessel_type_key(port_call.get("vessel_type")),
            manoeuvres=[
                ManoeuvreInput(
                    manoeuvre_type=_maneuver_type_to_cost_engine(row.get("maneuver_type")),
                    gt=gt,
                    standby_hours=row.get("derived_standby_hours") or 0.0,
                )
                for row in ordered_rows
            ],
            stay_days=stay_days or 1.0,
            include_tup=include_tup,
        )
        pilotage_total = estimate.pilotage_total
        tup_estimate = estimate.tup_estimate if include_tup else 0.0
        grand_total = estimate.grand_total
        notes = estimate.notes
        for row, result in zip(ordered_rows, estimate.manoeuvres):
            estimated_rows.append(
                    {
                        **row,
                        "derived_standby_hours_label": _format_hours_pt(row.get("derived_standby_hours") or 0.0),
                        "estimated_cost": result.total_cost,
                        "estimated_cost_label": _format_currency_pt(result.total_cost),
                    }
            )
    else:
        estimated_rows = [{**row, "estimated_cost": None, "estimated_cost_label": "--"} for row in ordered_rows]

    latest_row = estimated_rows[-1] if estimated_rows else {}
    latest_value = _archive_row_reference_value(latest_row)
    latest_dt = _archive_row_reference_dt(latest_row)
    return {
        "maneuvers": estimated_rows,
        "maneuver_count": len(estimated_rows),
        "estimated_pilotage_total": pilotage_total,
        "estimated_pilotage_label": _format_currency_pt(pilotage_total),
        "estimated_tup": tup_estimate,
        "estimated_tup_label": _format_currency_pt(tup_estimate),
        "estimated_grand_total": grand_total,
        "estimated_grand_total_label": _format_currency_pt(grand_total),
        "stay_days": round(stay_days, 2) if stay_days else 0.0,
        "stay_days_label": _format_days_pt(stay_days),
        "cost_notes": notes,
        "latest_activity_value": latest_value or "",
        "latest_activity_label": _local_iso_to_label(latest_value),
        "latest_date_label": _local_date_label(latest_value) if latest_value else "Sem data",
        "latest_execution_label": (
            latest_row.get("execution_window_label")
            or latest_row.get("actual_label")
            or latest_row.get("planned_label")
            or "--"
        ),
        "latest_activity_ts": latest_dt.timestamp() if latest_dt else 0.0,
    }


def _decorate_port_call(record: Dict) -> Dict:
    normalized = _normalize_port_call_record(record)
    decorated_history = []
    for maneuver in normalized.get("maneuver_history", []):
        state_label, state_class = _maneuver_state_meta(maneuver.get("type"), maneuver.get("state"))
        agent_profile = _actor_meta(maneuver.get("created_by"), maneuver.get("created_by_profile"))
        pilot_profile = _actor_meta(maneuver.get("decided_by"), maneuver.get("decided_by_profile"))
        reported_by_profile = _actor_meta(maneuver.get("reported_by"), maneuver.get("reported_by_profile"))
        decorated_history.append(
            {
                **maneuver,
                "type_label": _maneuver_type_label(maneuver.get("type")),
                "action_label": _maneuver_action_label(maneuver.get("type")),
                "state_label": state_label,
                "state_class": state_class,
                "planned_label": _local_iso_to_label(maneuver.get("planned_at")),
                "planned_input_value": _iso_to_datetime_local_value(maneuver.get("planned_at")),
                "completed_label": _local_iso_to_label(maneuver.get("completed_at")),
                "execution_started_label": _local_iso_to_label(maneuver.get("execution_started_at")),
                "execution_started_input_value": _iso_to_datetime_local_value(maneuver.get("execution_started_at")),
                "execution_finished_label": _local_iso_to_label(maneuver.get("execution_finished_at")),
                "execution_finished_input_value": _iso_to_datetime_local_value(maneuver.get("execution_finished_at")),
                "effective_time_label": _local_iso_to_label(_effective_maneuver_time(maneuver)),
                "reported_draft_m": maneuver.get("reported_draft_m", "") or "",
                "decided_label": _local_iso_to_label(maneuver.get("decided_at")),
                "reported_label": _local_iso_to_label(maneuver.get("reported_at")),
                "agent_profile": agent_profile,
                "pilot_profile": pilot_profile,
                "reported_by_profile": reported_by_profile,
                "agent_label": agent_profile["label"],
                "pilot_label": pilot_profile["label"],
                "reported_by_label": reported_by_profile["label"],
                "constraint_badges": _constraint_badges(maneuver.get("constraints", [])),
                "change_count": len(maneuver.get("change_log") or []),
                "has_changes": bool(maneuver.get("change_log")),
            }
        )
    agent_profile = _actor_meta(normalized.get("created_by"), normalized.get("created_by_profile"))
    pilot_profile = _actor_meta(normalized.get("decided_by"), normalized.get("decided_by_profile"))
    shift_pilot_profile = _actor_meta(normalized.get("shift_decided_by"), normalized.get("shift_decided_by_profile"))
    ship_type_meta = _resolve_vessel_type_meta(normalized.get("vessel_type"))
    return {
        **normalized,
        "maneuver_history": decorated_history,
        "eta_label": _local_iso_to_label(normalized.get("eta")),
        "ata_label": _local_iso_to_label(normalized.get("ata")),
        "planned_departure_label": _local_iso_to_label(normalized.get("planned_departure_at")),
        "departure_label": _local_iso_to_label(normalized.get("departure_at")),
        "planned_shift_label": _local_iso_to_label(normalized.get("planned_shift_at")),
        "shift_label": _local_iso_to_label(normalized.get("shift_at")),
        "decided_at_label": _local_iso_to_label(normalized.get("decided_at")),
        "shift_decided_at_label": _local_iso_to_label(normalized.get("shift_decided_at")),
        "berth_label": normalized.get("berth") or "Sem cais atribuído",
        "reference_code": _build_port_call_reference(normalized),
        "agent_profile": agent_profile,
        "pilot_profile": pilot_profile,
        "shift_pilot_profile": shift_pilot_profile,
        "agent_label": agent_profile["label"],
        "pilot_label": pilot_profile["label"],
        "shift_pilot_label": shift_pilot_profile["label"],
        "shift_origin_label": normalized.get("shift_origin_berth") or normalized.get("berth") or "Sem cais atribuído",
        "shift_destination_label": normalized.get("shift_destination_berth") or "Sem cais atribuído",
        "ship_short_name_label": normalized.get("vessel_short_name") or normalized.get("vessel_name") or "Navio",
        "ship_imo_label": normalized.get("vessel_imo") or "--",
        "ship_call_sign_label": normalized.get("vessel_call_sign") or "--",
        "ship_flag_label": normalized.get("vessel_flag") or "--",
        "ship_type_label": ship_type_meta["label"],
        "ship_type_icon": ship_type_meta["icon"],
        "ship_loa_label": normalized.get("vessel_loa_m") or "--",
        "ship_beam_label": normalized.get("vessel_beam_m") or "--",
        "ship_gt_label": normalized.get("vessel_gt_t") or "--",
        "ship_max_draft_label": normalized.get("vessel_max_draft_m") or "--",
        "ship_dwt_label": normalized.get("vessel_dwt_t") or "--",
        "can_abort": _can_abort_port_call(normalized),
        "can_abort_departure_plan": _can_abort_departure_plan(normalized),
        "can_abort_shift_plan": _can_abort_shift_plan(normalized),
    }


def _build_port_activity_snapshot(records: List[Dict], window_days: int = 5) -> Dict:
    now = datetime.now(timezone.utc)
    future_limit = now + timedelta(days=window_days)
    past_limit = now - timedelta(days=window_days)

    arrivals = []
    in_port = []
    departed = []
    aborted = []
    maneuvers = []
    planned_rows = []
    archived_scale_sources: Dict[str, Dict] = {}
    departure_candidates = []

    def build_activity_row(
        *,
        row_id: str,
        port_call: Dict,
        maneuver: Dict,
        maneuver_label: str,
        situation_label: str,
        situation_class: str,
        date_value: Optional[str],
        planned_value: Optional[str],
        actual_value: Optional[str],
        local_origin: str,
        local_destination: str,
        detail_note: str = "",
    ) -> Optional[Dict]:
        date_dt = _parse_iso_datetime(date_value)
        if not date_dt:
            return None
        return {
            "id": row_id,
            "maneuver_id": maneuver.get("id") or "",
            "maneuver_type": _normalize_maneuver_type(maneuver.get("type")),
            "port_call_id": port_call["id"],
            "reference_code": port_call["reference_code"],
            "vessel_name": port_call.get("vessel_name", "Navio"),
            "vessel_gt": port_call.get("vessel_gt_t") or "",
            "vessel_type": port_call.get("ship_type_label") or "Navio",
            "vessel_type_icon": port_call.get("ship_type_icon"),
            "date_value": date_dt.isoformat(),
            "date_key": date_dt.astimezone().strftime("%Y-%m-%d"),
            "date_label": _local_date_label(date_dt.isoformat()),
            "planned_value": planned_value,
            "planned_label": _local_time_label(planned_value),
            "actual_value": actual_value,
            "actual_label": _local_time_label(actual_value),
            "execution_started_label": maneuver.get("execution_started_label", "Sem hora"),
            "execution_finished_label": maneuver.get("execution_finished_label", "Sem hora"),
            "execution_started_at": maneuver.get("execution_started_at"),
            "execution_finished_at": maneuver.get("execution_finished_at"),
            "execution_window_label": (
                f"{_local_time_label(maneuver.get('execution_started_at'))} -> {_local_time_label(maneuver.get('execution_finished_at'))}"
                if maneuver.get("execution_started_at") and maneuver.get("execution_finished_at")
                else ""
            ),
            "derived_standby_hours": _derive_standby_hours(
                maneuver.get("execution_started_at"),
                maneuver.get("execution_finished_at"),
            ),
            "situation_label": situation_label,
            "situation_class": situation_class,
            "maneuver_label": maneuver_label,
            "local_origin": local_origin or "--",
            "origin_cabin_label": "--- | ---",
            "local_destination": local_destination or "--",
            "destination_cabin_label": "--- | ---",
            "loa_label": port_call.get("ship_loa_label") or "--",
            "beam_label": port_call.get("ship_beam_label") or "--",
            "draft_label": maneuver.get("reported_draft_m") or maneuver.get("planned_draft_m") or port_call.get("ship_max_draft_label") or "--",
            "tug_count_label": maneuver.get("tug_count") or "--",
            "agent_label": maneuver.get("agent_label") or port_call["agent_label"],
            "agent_profile": maneuver.get("agent_profile") or port_call.get("agent_profile"),
            "pilot_label": maneuver.get("pilot_label") or port_call["pilot_label"],
            "pilot_profile": maneuver.get("pilot_profile") or port_call.get("pilot_profile"),
            "validated_by_label": maneuver.get("pilot_label") or port_call["pilot_label"],
            "validated_by_profile": maneuver.get("pilot_profile") or port_call.get("pilot_profile"),
            "reported_by_label": maneuver.get("reported_by_label", "--"),
            "reported_by_profile": maneuver.get("reported_by_profile"),
            "executed_by_label": maneuver.get("reported_by_label", "--"),
            "executed_by_profile": maneuver.get("reported_by_profile"),
            "report_completed": bool((maneuver.get("report_note") or "").strip()),
            "constraint_badges": maneuver.get("constraint_badges", []),
            "change_count": maneuver.get("change_count", 0),
            "has_changes": maneuver.get("has_changes", False),
            "detail_note": detail_note,
            "approval_note": port_call.get("approval_note", ""),
            "aborted_reason": port_call.get("aborted_reason", ""),
            "show_approve": (
                maneuver_label == "Entrar"
                and port_call["status"] == PORT_CALL_STATUS_SCHEDULED
                and port_call.get("approval_status") == PORT_CALL_APPROVAL_PENDING
            )
            or (
                maneuver_label == "Sair"
                and port_call["status"] == PORT_CALL_STATUS_IN_PORT
                and bool(port_call.get("planned_departure_at"))
                and port_call.get("approval_status") == PORT_CALL_APPROVAL_PENDING
            ),
            "show_abort_entry": maneuver_label == "Entrar"
            and port_call["status"] == PORT_CALL_STATUS_SCHEDULED
            and port_call.get("approval_status") != PORT_CALL_APPROVAL_ABORTED
            and port_call.get("can_abort"),
            "show_mark_arrived": maneuver_label == "Entrar"
            and port_call["status"] == PORT_CALL_STATUS_SCHEDULED
            and port_call.get("approval_status") == PORT_CALL_APPROVAL_APPROVED,
            "show_abort_departure": maneuver_label == "Sair"
            and port_call["status"] == PORT_CALL_STATUS_IN_PORT
            and bool(port_call.get("planned_departure_at"))
            and port_call.get("can_abort_departure_plan"),
            "show_mark_departed": maneuver_label == "Sair"
            and port_call["status"] == PORT_CALL_STATUS_IN_PORT
            and bool(port_call.get("planned_departure_at"))
            and port_call.get("approval_status") == PORT_CALL_APPROVAL_APPROVED,
        }

    for raw in records:
        item = _decorate_port_call(raw)
        scale_archived_rows: List[Dict] = []
        scale_has_visible_archive = False
        eta_dt = _parse_iso_datetime(item.get("eta"))
        departure_dt = _parse_iso_datetime(item.get("departure_at"))
        decided_dt = _parse_iso_datetime(item.get("decided_at"))
        status = item.get("status")
        approval_status = item.get("approval_status", PORT_CALL_APPROVAL_PENDING)
        if (
            status == PORT_CALL_STATUS_SCHEDULED
            and approval_status != PORT_CALL_APPROVAL_ABORTED
            and eta_dt
            and now <= eta_dt <= future_limit
        ):
            arrivals.append(item)
        elif status == PORT_CALL_STATUS_IN_PORT:
            in_port.append(item)
        elif status == PORT_CALL_STATUS_DEPARTED and departure_dt and past_limit <= departure_dt <= now:
            departed.append(item)
        elif (
            approval_status == PORT_CALL_APPROVAL_ABORTED
            and decided_dt
            and past_limit <= decided_dt <= now
        ):
            aborted.append(item)
        for maneuver in item.get("maneuver_history", []):
            maneuver_type = _normalize_maneuver_type(maneuver.get("type"))
            maneuver_label = maneuver.get("action_label") or _maneuver_action_label(maneuver_type)
            planned_dt = _parse_iso_datetime(maneuver.get("planned_at"))
            completed_dt = _parse_iso_datetime(maneuver.get("completed_at"))
            maneuver_decided_dt = _parse_iso_datetime(maneuver.get("decided_at"))
            state = maneuver.get("state")
            detail_note = (
                maneuver.get("report_note")
                or maneuver.get("plan_note")
                or maneuver.get("approval_note")
                or maneuver.get("aborted_reason")
                or ""
            )
            summary = f"{maneuver.get('origin') or '--'} -> {maneuver.get('destination') or '--'}"

            if planned_dt and state in {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED} and now <= planned_dt <= future_limit:
                maneuvers.append(
                    _build_maneuver_event(
                        event_type=f"{maneuver_type}-planned",
                        title=f"{maneuver_label} planeada",
                        when_value=maneuver["planned_at"],
                        vessel_name=item.get("vessel_name", "Navio"),
                        summary=summary,
                        detail=detail_note,
                        berth_label=maneuver.get("destination") or item["berth_label"],
                    )
                )

            if state == PORT_CALL_APPROVAL_ABORTED and maneuver_decided_dt and past_limit <= maneuver_decided_dt <= now:
                maneuvers.append(
                    _build_maneuver_event(
                        event_type=f"{maneuver_type}-aborted",
                        title=f"{maneuver_label} abortada",
                        when_value=maneuver["decided_at"],
                        vessel_name=item.get("vessel_name", "Navio"),
                        summary=summary,
                        detail=detail_note,
                        berth_label=maneuver.get("origin") or item["berth_label"],
                    )
                )

            if completed_dt and past_limit <= completed_dt <= now:
                maneuvers.append(
                    _build_maneuver_event(
                        event_type=f"{maneuver_type}-completed",
                        title=f"{maneuver_label} concluída",
                        when_value=_effective_maneuver_time(maneuver),
                        vessel_name=item.get("vessel_name", "Navio"),
                        summary=summary,
                        detail=detail_note,
                        berth_label=maneuver.get("destination") or item["berth_label"],
                    )
                )

            effective_completed_dt = _parse_iso_datetime(_effective_maneuver_time(maneuver))
            if state == "completed":
                reference_dt = effective_completed_dt
            elif state == PORT_CALL_APPROVAL_ABORTED:
                reference_dt = maneuver_decided_dt or planned_dt
            else:
                reference_dt = planned_dt or maneuver_decided_dt
            if not reference_dt or not (past_limit <= reference_dt <= future_limit):
                continue
            situation_label, situation_class = _maneuver_state_meta(maneuver_type, state)
            actual_value = (
                _effective_maneuver_time(maneuver)
                if state == "completed"
                else maneuver.get("decided_at") if state == PORT_CALL_APPROVAL_ABORTED else None
            )
            row_payload = build_activity_row(
                row_id=f"{maneuver_type}-{item['id']}-{maneuver.get('id')}",
                port_call={
                    **item,
                    "pilot_label": _normalize_actor_label(maneuver.get("decided_by"), item["pilot_label"]),
                    "approval_note": maneuver.get("approval_note", ""),
                    "aborted_reason": maneuver.get("aborted_reason", ""),
                },
                maneuver=maneuver,
                maneuver_label=maneuver_label,
                situation_label=situation_label,
                situation_class=situation_class,
                date_value=reference_dt.isoformat(),
                planned_value=maneuver.get("planned_at"),
                actual_value=actual_value,
                local_origin=maneuver.get("origin") or "--",
                local_destination=maneuver.get("destination") or "--",
                detail_note=detail_note,
            )
            if not row_payload:
                continue
            within_window = past_limit <= reference_dt <= future_limit
            if within_window:
                planned_rows.append(row_payload)
            if state in {"completed", PORT_CALL_APPROVAL_ABORTED}:
                scale_archived_rows.append(row_payload)
                if within_window:
                    scale_has_visible_archive = True
        if scale_has_visible_archive and scale_archived_rows:
            archived_scale_sources[item["id"]] = {
                "port_call": item,
                "rows": scale_archived_rows,
            }

    arrivals.sort(
        key=lambda item: _parse_iso_datetime(item.get("eta")) or datetime.max.replace(tzinfo=timezone.utc)
    )
    in_port.sort(
        key=lambda item: (
            item.get("berth_label", ""),
            _parse_iso_datetime(item.get("ata")) or datetime.max.replace(tzinfo=timezone.utc),
        )
    )
    departed.sort(
        key=lambda item: _parse_iso_datetime(item.get("departure_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    aborted.sort(
        key=lambda item: _parse_iso_datetime(item.get("decided_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    def _snapshot_maneuver_sort_key(item: Dict) -> tuple[int, float]:
        when_dt = _parse_iso_datetime(item.get("when")) or now
        timestamp = when_dt.timestamp()
        if when_dt >= now:
            return (0, timestamp)
        return (1, -timestamp)

    maneuvers.sort(key=_snapshot_maneuver_sort_key)

    berth_map: Dict[str, List[Dict]] = {}
    for item in in_port:
        berth_map.setdefault(item["berth_label"], []).append(item)

    _BERTH_GEO_ORDER = [
        "Secil", "Fundeadouro Norte", "Cais Palmeiras",
        "TMS 1", "TMS 2", "Autoeuropa", "Cais 10", "Cais 11",
        "Praias do Sado", "Pirites", "SAPEC",
        "ALSTOM", "PAN", "Tróia", "Fundeadouro Sul",
        "Tanquisado", "Eco-Oil",
        "Lisnave", "Teporset",
    ]

    def _berth_geo_sort_key(pair):
        name = pair[0]
        for idx, prefix in enumerate(_BERTH_GEO_ORDER):
            if prefix.lower() in name.lower():
                return idx
        return 9999

    berthed = [
        {
            "berth": berth,
            "count": len(vessels),
            "vessels": vessels,
        }
        for berth, vessels in sorted(berth_map.items(), key=_berth_geo_sort_key)
    ]

    for item in in_port:
        active_departure = _latest_maneuver(
            item.get("maneuver_history", []),
            "departure",
            {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED},
        )
        if active_departure:
            continue
        departure_candidates.append(
            {
                "id": item["id"],
                "vessel_name": item["vessel_name"],
                "berth_label": item["berth_label"],
                "next_port": item.get("next_port", ""),
                "notes": item.get("notes", ""),
                "agent_label": item["agent_label"],
            }
        )

    planned_rows.sort(
        key=lambda item: (
            _parse_iso_datetime(item.get("planned_value") or item.get("actual_value") or item.get("date_value"))
            or datetime.max.replace(tzinfo=timezone.utc),
            item.get("maneuver_label", ""),
            item.get("vessel_name", ""),
        )
    )

    def _is_archived_row(item: Dict) -> bool:
        return item.get("situation_class") == "aborted" or bool(item.get("report_completed"))

    archived_rows = [
        item
        for item in planned_rows
        if _is_archived_row(item)
    ]
    archived_scales = []
    for source in archived_scale_sources.values():
        summary = _build_archived_scale_summary(source["port_call"], source["rows"], now)
        maneuver_search_tokens = [
            " ".join(
                [
                    row.get("maneuver_label", ""),
                    row.get("local_origin", ""),
                    row.get("local_destination", ""),
                    row.get("detail_note", ""),
                    row.get("situation_label", ""),
                ]
            )
            for row in summary["maneuvers"]
        ]
        archived_scales.append(
            {
                "port_call_id": source["port_call"]["id"],
                "reference_code": source["port_call"]["reference_code"],
                "vessel_name": source["port_call"].get("vessel_name", "Navio"),
                "vessel_gt": source["port_call"].get("vessel_gt_t") or "",
                "vessel_type": source["port_call"].get("ship_type_label") or "Navio",
                "vessel_type_icon": source["port_call"].get("ship_type_icon"),
                "agent_label": source["port_call"].get("agent_label", "--"),
                "agent_profile": source["port_call"].get("agent_profile"),
                "pilot_label": source["port_call"].get("pilot_label", "--"),
                "pilot_profile": source["port_call"].get("pilot_profile"),
                "status": source["port_call"].get("status"),
                "status_label": source["port_call"].get("status", "").replace("_", " "),
                "search_blob": " ".join(
                    [
                        source["port_call"].get("reference_code", ""),
                        source["port_call"].get("vessel_name", ""),
                        source["port_call"].get("ship_type_label", ""),
                        source["port_call"].get("vessel_gt_t", ""),
                        source["port_call"].get("agent_label", ""),
                        *maneuver_search_tokens,
                    ]
                ).lower(),
                **summary,
            }
        )
    archived_scales.sort(key=lambda item: item.get("latest_activity_ts", 0.0), reverse=True)
    active_planned_rows = [
        item
        for item in planned_rows
        if not _is_archived_row(item)
    ]
    planned_groups_map: Dict[str, Dict] = {}
    for item in active_planned_rows:
        group = planned_groups_map.setdefault(
            item["date_key"],
            {
                "date_key": item["date_key"],
                "date_label": item["date_label"],
                "total": 0,
            },
        )
        group["total"] += 1
    planned_groups = [
        planned_groups_map[key]
        for key in sorted(planned_groups_map.keys())
    ]

    return {
        "arrivals": arrivals,
        "in_port": in_port,
        "berthed": berthed,
        "departed": departed,
        "aborted": aborted,
        "maneuvers": maneuvers,
        "planned_maneuvers": active_planned_rows,
        "archived_maneuvers": archived_rows,
        "archived_scales": archived_scales,
        "planned_groups": planned_groups,
        "departure_candidates": departure_candidates,
        "stats": {
            "scheduled_count": len(arrivals),
            "in_port_count": len(in_port),
            "departed_count": len(departed),
            "berth_count": len(berthed),
            "aborted_count": len(aborted),
            "maneuver_count": len(maneuvers),
            "planned_count": len(active_planned_rows),
            "archive_count": len(archived_rows),
            "archive_scale_count": len(archived_scales),
            "pending_count": sum(
                1 for item in active_planned_rows if item.get("situation_class") == "pending"
            ),
        },
        "generated_at_label": _local_iso_to_label(now.isoformat()),
        "window_days": window_days,
    }
