from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from werkzeug.datastructures import FileStorage
from werkzeug.security import check_password_hash, generate_password_hash

from document_processing import (
    build_preview,
    ensure_unique_filename,
    extract_text_from_path,
    file_metadata,
    format_bytes,
    infer_document_type,
    is_allowed_document,
    is_text_editable,
    iso_now,
    sanitize_upload_filename,
    slugify,
)

PASSWORD_HASH_METHOD = "scrypt"


DEFAULT_CONVERSATION_TITLE = "Nova conversa"
FEEDBACK_APPROVED = "approved"
FEEDBACK_REVIEW = "review"
ALLOWED_FEEDBACK_STATUSES = {FEEDBACK_APPROVED, FEEDBACK_REVIEW}
PORT_CALL_STATUS_SCHEDULED = "scheduled"
PORT_CALL_STATUS_IN_PORT = "in_port"
PORT_CALL_STATUS_DEPARTED = "departed"
ALLOWED_PORT_CALL_STATUSES = {
    PORT_CALL_STATUS_SCHEDULED,
    PORT_CALL_STATUS_IN_PORT,
    PORT_CALL_STATUS_DEPARTED,
}
PORT_CALL_APPROVAL_PENDING = "pending"
PORT_CALL_APPROVAL_APPROVED = "approved"
PORT_CALL_APPROVAL_ABORTED = "aborted"
ALLOWED_PORT_CALL_APPROVAL_STATUSES = {
    PORT_CALL_APPROVAL_PENDING,
    PORT_CALL_APPROVAL_APPROVED,
    PORT_CALL_APPROVAL_ABORTED,
}
PT_MONTH_NAMES = (
    "",
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)
USER_PROFILE_REQUIRED_ROLES = {"agente", "piloto"}
USER_PROFILE_REQUIRED_FIELDS = ("full_name", "organization", "email", "phone")
VESSEL_TYPE_META = (
    {
        "label": "Atividades ao largo",
        "icon": "atividades-ao-largo.png",
        "aliases": ("atividades ao largo", "offshore", "offshore apoio", "apoio offshore"),
    },
    {
        "label": "Batelão s/ propulsão",
        "icon": "batelao-s-propulsao.png",
        "aliases": ("batelao", "batelao s propulsao", "batelao sem propulsao"),
    },
    {
        "label": "Carga geral",
        "icon": "carga-geral.png",
        "aliases": ("carga geral", "general cargo"),
    },
    {
        "label": "Contentores",
        "icon": "contentores.png",
        "aliases": ("contentores", "container", "containers"),
    },
    {
        "label": "Cruzeiros",
        "icon": "cruzeiros.png",
        "aliases": ("cruzeiros", "cruzeiro", "cruise"),
    },
    {
        "label": "Diversos",
        "icon": "diversos.png",
        "aliases": ("diversos", "misc", "multiusos"),
    },
    {
        "label": "Estruturas diversas",
        "icon": "estruturas-diversas.png",
        "aliases": ("estruturas diversas", "estrutura"),
    },
    {
        "label": "Frigorífico",
        "icon": "frigorifico.png",
        "aliases": ("frigorifico", "reefer"),
    },
    {
        "label": "Graneis líquidos",
        "icon": "graneis-liquidos.png",
        "aliases": ("graneis liquidos", "petroleiro", "tanque", "tanker", "gas"),
    },
    {
        "label": "Graneis sólidos",
        "icon": "graneis-solidos.png",
        "aliases": ("graneis solidos", "graneleiro", "bulk carrier", "bulk"),
    },
    {
        "label": "Navios de guerra",
        "icon": "navios-de-guerra.png",
        "aliases": ("navios de guerra", "guerra", "militar", "naval"),
    },
    {
        "label": "Passageiros",
        "icon": "passageiros.png",
        "aliases": ("passageiros", "passenger"),
    },
    {
        "label": "Pesca",
        "icon": "pesca.png",
        "aliases": ("pesca", "fishing"),
    },
    {
        "label": "Porta-contentores",
        "icon": "porta-contentores.png",
        "aliases": ("porta contentores", "porta-contentores"),
    },
    {
        "label": "Propulsão",
        "icon": "propulsao.png",
        "aliases": ("propulsao", "propulsão"),
    },
    {
        "label": "Rebocadores",
        "icon": "rebocadores.png",
        "aliases": ("rebocadores", "rebocador", "tug", "tugs"),
    },
    {
        "label": "Restantes",
        "icon": "restantes.png",
        "aliases": ("restantes", "restante", "navio", "outros", "outro"),
    },
    {
        "label": "Roll-on/Roll-off",
        "icon": "roll-on-roll-off.png",
        "aliases": ("roll on roll off", "ro ro", "ro-ro", "ro ro pcc", "ro-ro / pcc", "pcc"),
    },
    {
        "label": "Transporte especializado carga seca",
        "icon": "transporte-especializado-carga-seca.png",
        "aliases": ("transporte especializado carga seca", "carga seca especializada"),
    },
)
CONSTRAINT_META = (
    {
        "code": "daylight",
        "label": "Day-light",
        "icon": "constraint-daylight-21-alt.svg",
        "aliases": ("daylight", "day light", "day-light", "dia"),
    },
    {
        "code": "gas",
        "label": "Gás / carga perigosa",
        "icon": "constraint-gas-21-alt.svg",
        "aliases": ("gas", "gás", "carga perigosa", "perigosa", "dangerous cargo"),
    },
    {
        "code": "estrategico",
        "label": "Estratégico",
        "icon": "constraint-estrategico-21-alt.svg",
        "aliases": ("estrategico", "estratégico", "strategic"),
    },
)


def _lookup_key(value: Optional[str]) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


VESSEL_TYPE_LOOKUP = {
    _lookup_key(alias): item
    for item in VESSEL_TYPE_META
    for alias in (item["label"], *item.get("aliases", ()))
}
CONSTRAINT_LOOKUP = {
    _lookup_key(alias): item
    for item in CONSTRAINT_META
    for alias in (item["code"], item["label"], *item.get("aliases", ()))
}


def get_vessel_type_options() -> List[Dict]:
    return [{"label": item["label"], "icon": item["icon"]} for item in VESSEL_TYPE_META]


def get_constraint_options() -> List[Dict]:
    return [{"code": item["code"], "label": item["label"], "icon": item["icon"]} for item in CONSTRAINT_META]


def _utc_iso_to_label(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _local_iso_to_label(value: Optional[str]) -> str:
    dt = _parse_iso_datetime(value)
    if not dt:
        return "Sem hora"
    return dt.astimezone().strftime("%d %b %H:%M")


def _iso_to_datetime_local_value(value: Optional[str]) -> str:
    dt = _parse_iso_datetime(value)
    if not dt:
        return ""
    return dt.astimezone().strftime("%Y-%m-%dT%H:%M")


def _local_date_label(value: Optional[str]) -> str:
    dt = _parse_iso_datetime(value)
    if not dt:
        return "Sem data"
    local_dt = dt.astimezone()
    return f"{local_dt.day} {PT_MONTH_NAMES[local_dt.month]} {local_dt.year}"


def _local_time_label(value: Optional[str]) -> str:
    dt = _parse_iso_datetime(value)
    if not dt:
        return "--"
    return dt.astimezone().strftime("%H:%M")


def _normalize_actor_label(value: Optional[str], fallback: str = "--") -> str:
    clean = " ".join((value or "").strip().split())
    if not clean:
        return fallback
    return clean


def _clean_text(value: Optional[str]) -> str:
    return " ".join((value or "").strip().split())


def _normalize_username(value: Optional[str]) -> str:
    return _clean_text(value).lower()


def _normalize_email(value: Optional[str]) -> str:
    return _clean_text(value).lower()


def _normalize_phone(value: Optional[str]) -> str:
    return _clean_text(value)


def _normalize_user_profile_payload(
    record: Optional[Dict],
    *,
    username: Optional[str] = None,
    role: Optional[str] = None,
) -> Dict:
    payload = record or {}
    normalized_role = (payload.get("role") or role or "piloto").strip().lower()
    if normalized_role not in {"admin", "agente", "piloto"}:
        normalized_role = "piloto"
    profile = {
        "username": _normalize_username(payload.get("username") or username),
        "role": normalized_role,
        "full_name": _clean_text(payload.get("full_name")),
        "organization": _clean_text(payload.get("organization")),
        "email": _normalize_email(payload.get("email")),
        "phone": _normalize_phone(payload.get("phone")),
        "profile_completed_at": payload.get("profile_completed_at"),
    }
    if is_user_profile_complete(profile):
        profile["profile_completed_at"] = payload.get("profile_completed_at") or iso_now()
    else:
        profile["profile_completed_at"] = payload.get("profile_completed_at")
    return profile


def is_user_profile_complete(profile: Optional[Dict]) -> bool:
    payload = profile or {}
    role = (payload.get("role") or "").strip().lower()
    if role not in USER_PROFILE_REQUIRED_ROLES:
        return True
    return all(_clean_text(payload.get(field, "")) for field in USER_PROFILE_REQUIRED_FIELDS)


def _build_actor_snapshot(profile: Optional[Dict], *, username: Optional[str] = None, role: Optional[str] = None) -> Dict:
    normalized = _normalize_user_profile_payload(profile, username=username, role=role)
    return {
        "username": normalized.get("username", ""),
        "role": normalized.get("role", ""),
        "full_name": normalized.get("full_name", ""),
        "organization": normalized.get("organization", ""),
        "email": normalized.get("email", ""),
        "phone": normalized.get("phone", ""),
    }


def _actor_meta(username: Optional[str], snapshot: Optional[Dict], fallback: str = "--") -> Dict:
    normalized = _build_actor_snapshot(snapshot, username=username)
    label = normalized.get("full_name") or _normalize_actor_label(normalized.get("username"), fallback)
    details = [value for value in (normalized.get("organization"), normalized.get("email"), normalized.get("phone")) if value]
    return {
        **normalized,
        "label": label,
        "contact_label": " · ".join(details) if details else "--",
    }


def _resolve_vessel_type_meta(value: Optional[str]) -> Dict:
    meta = VESSEL_TYPE_LOOKUP.get(_lookup_key(value))
    if meta:
        return meta
    return VESSEL_TYPE_LOOKUP[_lookup_key("Restantes")]


def _split_text_values(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = re.split(r"[,;|]", str(value))
    return [_clean_text(item) for item in items if _clean_text(item)]


def normalize_constraint_codes(value) -> List[str]:
    codes: List[str] = []
    for item in _split_text_values(value):
        meta = CONSTRAINT_LOOKUP.get(_lookup_key(item))
        if not meta:
            continue
        code = meta["code"]
        if code not in codes:
            codes.append(code)
    return codes


def _constraint_badges(codes: List[str]) -> List[Dict]:
    badges = []
    for code in normalize_constraint_codes(codes):
        meta = CONSTRAINT_LOOKUP.get(_lookup_key(code))
        if not meta:
            continue
        badges.append(
            {
                "code": meta["code"],
                "label": meta["label"],
                "icon": meta["icon"],
            }
        )
    return badges


def format_constraint_labels(value) -> str:
    labels = [item["label"] for item in _constraint_badges(normalize_constraint_codes(value))]
    return ", ".join(labels)


def _validate_required_vessel_profile(record: Dict[str, str]) -> None:
    required_fields = (
        ("vessel_imo", "IMO"),
        ("vessel_call_sign", "indicativo"),
        ("vessel_flag", "bandeira"),
        ("vessel_type", "tipo de navio"),
        ("vessel_loa_m", "LOA"),
        ("vessel_beam_m", "boca"),
        ("vessel_gt_t", "GT"),
        ("vessel_max_draft_m", "calado"),
        ("vessel_dwt_t", "DWT"),
    )
    missing = [label for field, label in required_fields if not _clean_text(record.get(field, ""))]
    if missing:
        raise ValueError(f"Faltam dados obrigatórios do navio: {', '.join(missing)}.")


def _validate_required_operational_profile(record: Dict[str, str], fields: tuple[tuple[str, str], ...]) -> None:
    missing = [label for field, label in fields if not _clean_text(record.get(field, ""))]
    if missing:
        raise ValueError(f"Faltam dados operacionais obrigatórios: {', '.join(missing)}.")


def _build_port_call_reference(record: Dict) -> str:
    custom_reference = " ".join((record.get("reference_code") or "").strip().split())
    if custom_reference:
        return custom_reference
    created_dt = _parse_iso_datetime(record.get("created_at")) or datetime.now(timezone.utc)
    year_code = created_dt.astimezone().strftime("%y")
    vessel_code = re.sub(r"[^A-Z0-9]", "", (record.get("vessel_name") or "").upper())[:4] or "NAV"
    unique_code = re.sub(r"[^A-Z0-9]", "", (record.get("id") or "").upper())[:6] or "000000"
    return f"PTSET{year_code}{vessel_code}{unique_code}"


def _normalize_maneuver_type(value: Optional[str]) -> str:
    clean = re.sub(r"[^a-z0-9_\s-]", "", (value or "").strip().lower())
    clean = re.sub(r"[\s-]+", "_", clean).strip("_")
    return clean or "entry"


def _maneuver_type_label(maneuver_type: Optional[str]) -> str:
    clean_type = _normalize_maneuver_type(maneuver_type)
    return {
        "entry": "Entrada",
        "departure": "Saída",
        "shift": "Mudança",
    }.get(clean_type, clean_type.replace("_", " ").strip().title() or "Manobra")


def _maneuver_action_label(maneuver_type: Optional[str]) -> str:
    clean_type = _normalize_maneuver_type(maneuver_type)
    return {
        "entry": "Entrar",
        "departure": "Sair",
        "shift": "Mudança",
    }.get(clean_type, _maneuver_type_label(clean_type))


def _maneuver_state_meta(maneuver_type: Optional[str], state: Optional[str]) -> tuple[str, str]:
    clean_type = _normalize_maneuver_type(maneuver_type)
    clean_state = (state or PORT_CALL_APPROVAL_PENDING).strip().lower()
    label = {
        PORT_CALL_APPROVAL_PENDING: "Planeada" if clean_type != "entry" else "Pendente",
        PORT_CALL_APPROVAL_APPROVED: "Aprovada",
        PORT_CALL_APPROVAL_ABORTED: "Abortada",
        "completed": "Realizada",
    }.get(clean_state, "Planeada")
    css_class = {
        PORT_CALL_APPROVAL_PENDING: "pending",
        PORT_CALL_APPROVAL_APPROVED: "approved",
        PORT_CALL_APPROVAL_ABORTED: "aborted",
        "completed": "completed",
    }.get(clean_state, "pending")
    return label, css_class


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
        and item.get("state") == "completed"
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


def _default_port_calls() -> List[Dict]:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [
        {
            "id": str(uuid.uuid4()),
            "vessel_name": "Atlantic Navigator",
            "vessel_short_name": "ATL NAV",
            "vessel_imo": "9723345",
            "vessel_call_sign": "CQAN7",
            "vessel_flag": "Madeira",
            "vessel_type": "Graneleiro",
            "vessel_loa_m": "189.9",
            "vessel_beam_m": "32.2",
            "vessel_gt_t": "32.540",
            "vessel_max_draft_m": "11.8",
            "vessel_dwt_t": "38.600",
            "status": PORT_CALL_STATUS_SCHEDULED,
            "approval_status": PORT_CALL_APPROVAL_PENDING,
            "approval_note": "",
            "aborted_reason": "",
            "decided_by": None,
            "decided_at": None,
            "eta": (now + timedelta(hours=3)).isoformat(),
            "ata": None,
            "planned_departure_at": None,
            "departure_plan_note": "",
            "departure_at": None,
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": "Secil W",
            "last_port": "Sines",
            "next_port": "Setubal",
            "created_by": "system",
            "notes": "Entrada prevista pela barra com destino a Secil W.",
            "created_at": iso_now(),
            "updated_at": iso_now(),
        },
        {
            "id": str(uuid.uuid4()),
            "vessel_name": "Setubal Carrier",
            "vessel_short_name": "SET CAR",
            "vessel_imo": "9812456",
            "vessel_call_sign": "CSBD9",
            "vessel_flag": "Portugal",
            "vessel_type": "Carga geral",
            "vessel_loa_m": "143.2",
            "vessel_beam_m": "22.4",
            "vessel_gt_t": "11.860",
            "vessel_max_draft_m": "8.4",
            "vessel_dwt_t": "12.100",
            "status": PORT_CALL_STATUS_SCHEDULED,
            "approval_status": PORT_CALL_APPROVAL_PENDING,
            "approval_note": "",
            "aborted_reason": "",
            "decided_by": None,
            "decided_at": None,
            "eta": (now + timedelta(hours=11)).isoformat(),
            "ata": None,
            "planned_departure_at": None,
            "departure_plan_note": "",
            "departure_at": None,
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": "TMS 1 - Cais 5",
            "last_port": "Huelva",
            "next_port": "Setubal",
            "created_by": "system",
            "notes": "Escala prevista no TMS 1 com janela de maré favorável.",
            "created_at": iso_now(),
            "updated_at": iso_now(),
        },
        {
            "id": str(uuid.uuid4()),
            "vessel_name": "Lusitano Tanker",
            "vessel_short_name": "LUS TANK",
            "vessel_imo": "9567812",
            "vessel_call_sign": "CQUZ3",
            "vessel_flag": "Malta",
            "vessel_type": "Petroleiro",
            "vessel_loa_m": "228.4",
            "vessel_beam_m": "36.0",
            "vessel_gt_t": "48.220",
            "vessel_max_draft_m": "12.6",
            "vessel_dwt_t": "74.800",
            "status": PORT_CALL_STATUS_SCHEDULED,
            "approval_status": PORT_CALL_APPROVAL_PENDING,
            "approval_note": "",
            "aborted_reason": "",
            "decided_by": None,
            "decided_at": None,
            "eta": (now + timedelta(days=1, hours=5)).isoformat(),
            "ata": None,
            "planned_departure_at": None,
            "departure_plan_note": "",
            "departure_at": None,
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": "Tanquisado (lado jusante)",
            "last_port": "Cartagena",
            "next_port": "Setubal",
            "created_by": "system",
            "notes": "Navio tanque previsto para Tanquisado, sujeito a reponto de maré.",
            "created_at": iso_now(),
            "updated_at": iso_now(),
        },
        {
            "id": str(uuid.uuid4()),
            "vessel_name": "Yard Supporter",
            "vessel_short_name": "Y SUPPORT",
            "vessel_imo": "9675523",
            "vessel_call_sign": "CQYS4",
            "vessel_flag": "Bahamas",
            "vessel_type": "Offshore / Apoio",
            "vessel_loa_m": "121.0",
            "vessel_beam_m": "22.0",
            "vessel_gt_t": "9.870",
            "vessel_max_draft_m": "6.9",
            "vessel_dwt_t": "8.450",
            "status": PORT_CALL_STATUS_IN_PORT,
            "approval_status": PORT_CALL_APPROVAL_APPROVED,
            "approval_note": "Aprovada para operação em cais.",
            "aborted_reason": "",
            "decided_by": "piloto",
            "decided_at": (now - timedelta(hours=8)).isoformat(),
            "eta": (now - timedelta(hours=9)).isoformat(),
            "ata": (now - timedelta(hours=7, minutes=30)).isoformat(),
            "planned_departure_at": (now + timedelta(hours=9)).isoformat(),
            "departure_plan_note": "Saída prevista após conclusão da operação de carga.",
            "departure_at": None,
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": "Lisnave - Doca 21",
            "last_port": "Leixões",
            "next_port": "Valência",
            "created_by": "system",
            "notes": "Escala de estaleiro em curso na Lisnave.",
            "created_at": iso_now(),
            "updated_at": iso_now(),
        },
        {
            "id": str(uuid.uuid4()),
            "vessel_name": "Sado Mineral",
            "vessel_short_name": "S MINERAL",
            "vessel_imo": "9641188",
            "vessel_call_sign": "CQSM8",
            "vessel_flag": "Liberia",
            "vessel_type": "Graneleiro",
            "vessel_loa_m": "199.8",
            "vessel_beam_m": "32.2",
            "vessel_gt_t": "35.410",
            "vessel_max_draft_m": "11.2",
            "vessel_dwt_t": "39.900",
            "status": PORT_CALL_STATUS_IN_PORT,
            "approval_status": PORT_CALL_APPROVAL_APPROVED,
            "approval_note": "Aprovada com acompanhamento de rebocador.",
            "aborted_reason": "",
            "decided_by": "piloto",
            "decided_at": (now - timedelta(hours=15)).isoformat(),
            "eta": (now - timedelta(hours=16)).isoformat(),
            "ata": (now - timedelta(hours=15, minutes=10)).isoformat(),
            "planned_departure_at": None,
            "departure_plan_note": "",
            "departure_at": None,
            "planned_shift_at": (now + timedelta(hours=2)).isoformat(),
            "shift_plan_note": "Mudança prevista de TMS 1 - Cais 8 para TMS 2 após libertação do posto.",
            "shift_at": None,
            "shift_origin_berth": "TMS 1 - Cais 8",
            "shift_destination_berth": "TMS 2",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": "TMS 1 - Cais 8",
            "last_port": "Casablanca",
            "next_port": "Bilbau",
            "created_by": "system",
            "notes": "Aguarda mudança interna entre TMS 1 e TMS 2.",
            "created_at": iso_now(),
            "updated_at": iso_now(),
        },
        {
            "id": str(uuid.uuid4()),
            "vessel_name": "Auto Carrier One",
            "vessel_short_name": "AUTO ONE",
            "vessel_imo": "9774012",
            "vessel_call_sign": "3EAX9",
            "vessel_flag": "Panama",
            "vessel_type": "Ro-Ro / PCC",
            "vessel_loa_m": "199.9",
            "vessel_beam_m": "32.3",
            "vessel_gt_t": "58.600",
            "vessel_max_draft_m": "9.7",
            "vessel_dwt_t": "18.400",
            "status": PORT_CALL_STATUS_IN_PORT,
            "approval_status": PORT_CALL_APPROVAL_APPROVED,
            "approval_note": "Aprovada para escala curta.",
            "aborted_reason": "",
            "decided_by": "piloto",
            "decided_at": (now - timedelta(days=1, hours=2)).isoformat(),
            "eta": (now - timedelta(days=1, hours=1)).isoformat(),
            "ata": (now - timedelta(days=1)).isoformat(),
            "planned_departure_at": (now + timedelta(hours=4)).isoformat(),
            "departure_plan_note": "Saída planeada em janela curta.",
            "departure_at": None,
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": "Cais 10 / Autoeuropa",
            "last_port": "Tânger",
            "next_port": "Setubal",
            "created_by": "system",
            "notes": "Operação automóvel em curso no cais 10 / Autoeuropa.",
            "created_at": iso_now(),
            "updated_at": iso_now(),
        },
        {
            "id": str(uuid.uuid4()),
            "vessel_name": "Pirites Trader",
            "vessel_short_name": "PIR TRDR",
            "vessel_imo": "9586648",
            "vessel_call_sign": "CQPT2",
            "vessel_flag": "Cyprus",
            "vessel_type": "Carga geral",
            "vessel_loa_m": "154.7",
            "vessel_beam_m": "24.8",
            "vessel_gt_t": "16.420",
            "vessel_max_draft_m": "8.8",
            "vessel_dwt_t": "21.300",
            "status": PORT_CALL_STATUS_DEPARTED,
            "approval_status": PORT_CALL_APPROVAL_APPROVED,
            "approval_note": "Aprovada e concluída.",
            "aborted_reason": "",
            "decided_by": "piloto",
            "decided_at": (now - timedelta(days=1, hours=8)).isoformat(),
            "eta": (now - timedelta(days=1, hours=8)).isoformat(),
            "ata": (now - timedelta(days=1, hours=7)).isoformat(),
            "planned_departure_at": (now - timedelta(hours=6)).isoformat(),
            "departure_plan_note": "Planeamento concluído.",
            "departure_at": (now - timedelta(hours=5)).isoformat(),
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": "Praias do Sado / Pirites Alentejanas",
            "last_port": "Setubal",
            "next_port": "Vigo",
            "created_by": "system",
            "notes": "Saída concluída do terminal de Praias do Sado / Pirites Alentejanas.",
            "created_at": iso_now(),
            "updated_at": iso_now(),
        },
        {
            "id": str(uuid.uuid4()),
            "vessel_name": "Sado Bulk",
            "vessel_short_name": "S BULK",
            "vessel_imo": "9657804",
            "vessel_call_sign": "CQSB6",
            "vessel_flag": "Marshall Islands",
            "vessel_type": "Graneleiro",
            "vessel_loa_m": "181.5",
            "vessel_beam_m": "30.0",
            "vessel_gt_t": "28.930",
            "vessel_max_draft_m": "10.6",
            "vessel_dwt_t": "34.750",
            "status": PORT_CALL_STATUS_DEPARTED,
            "approval_status": PORT_CALL_APPROVAL_APPROVED,
            "approval_note": "Aprovada e concluída.",
            "aborted_reason": "",
            "decided_by": "piloto",
            "decided_at": (now - timedelta(days=2, hours=6)).isoformat(),
            "eta": (now - timedelta(days=2, hours=6)).isoformat(),
            "ata": (now - timedelta(days=2, hours=5, minutes=30)).isoformat(),
            "planned_departure_at": (now - timedelta(days=1, hours=4)).isoformat(),
            "departure_plan_note": "Planeamento concluído.",
            "departure_at": (now - timedelta(days=1, hours=3)).isoformat(),
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": "SAPEC Sólidos",
            "last_port": "Setubal",
            "next_port": "Antuérpia",
            "created_by": "system",
            "notes": "Operação encerrada no terminal SAPEC Sólidos.",
            "created_at": iso_now(),
            "updated_at": iso_now(),
        },
    ]


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
    departure_candidates = []

    def add_planned_row(
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
    ) -> None:
        date_dt = _parse_iso_datetime(date_value)
        if not date_dt:
            return
        planned_rows.append(
            {
                "id": row_id,
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
                "execution_window_label": (
                    f"{_local_time_label(maneuver.get('execution_started_at'))} -> {_local_time_label(maneuver.get('execution_finished_at'))}"
                    if maneuver.get("execution_started_at") and maneuver.get("execution_finished_at")
                    else ""
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
        )

    for raw in records:
        item = _decorate_port_call(raw)
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
            add_planned_row(
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
    def _maneuver_sort_key(item: Dict) -> tuple[int, float]:
        when_dt = _parse_iso_datetime(item.get("when")) or now
        timestamp = when_dt.timestamp()
        if when_dt >= now:
            return (0, timestamp)
        return (1, -timestamp)

    maneuvers.sort(key=_maneuver_sort_key)

    berth_map: Dict[str, List[Dict]] = {}
    for item in in_port:
        berth_map.setdefault(item["berth_label"], []).append(item)

    # Geographic berth order: Secil → Teporset (matching operational sequence)
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
    archived_rows = [
        item
        for item in planned_rows
        if item.get("situation_class") == "completed" and item.get("report_completed")
    ]
    active_planned_rows = [
        item
        for item in planned_rows
        if item.get("situation_class") != "completed" or not item.get("report_completed")
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
            "pending_count": sum(
                1 for item in active_planned_rows if item.get("situation_class") == "pending"
            ),
        },
        "generated_at_label": _local_iso_to_label(now.isoformat()),
        "window_days": window_days,
    }


def _conversation_title_from_text(text: str) -> str:
    collapsed = " ".join(text.strip().split())
    if not collapsed:
        return DEFAULT_CONVERSATION_TITLE
    if len(collapsed) <= 52:
        return collapsed
    return collapsed[:51].rstrip() + "…"


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _text_similarity(left: str, right: str) -> float:
    normalized_left = _normalize_text(left)
    normalized_right = _normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    left_tokens = set(re.findall(r"\w+", normalized_left))
    right_tokens = set(re.findall(r"\w+", normalized_right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))


class BaseStore(ABC):
    backend_name = "base"

    @abstractmethod
    def list_users(self) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        full_name: str = "",
        organization: str = "",
        email: str = "",
        phone: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        raise NotImplementedError

    @abstractmethod
    def get_user_profile(self, username: str) -> Optional[Dict]:
        raise NotImplementedError

    @abstractmethod
    def set_user_role(self, username: str, role: str) -> Dict:
        raise NotImplementedError

    def reset_user_password(self, username: str, new_password: str) -> bool:
        """Reset a user's password. Returns True if successful."""
        raise NotImplementedError

    @abstractmethod
    def update_user_profile(
        self,
        username: str,
        *,
        full_name: str,
        organization: str,
        email: str,
        phone: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def delete_user(self, username: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_document(self, title: str, content: str, created_by: str = "manual") -> str:
        raise NotImplementedError

    @abstractmethod
    def save_uploaded_document(self, uploaded_file: FileStorage, created_by: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def list_documents(self) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def get_document(self, name: str) -> Optional[Dict]:
        raise NotImplementedError

    @abstractmethod
    def get_document_text(self, name: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_document_file_path(self, name: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def update_document_text(self, name: str, content: str, updated_by: str) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def delete_document(self, name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_conversations(self, username: str) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def create_conversation(self, username: str, title: str = DEFAULT_CONVERSATION_TITLE) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def rename_conversation(self, username: str, conversation_id: str, title: str) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def clear_conversation(self, username: str, conversation_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_conversation(self, username: str, conversation_id: str) -> Optional[str]:
        raise NotImplementedError

    @abstractmethod
    def ensure_conversation(self, username: str, conversation_id: Optional[str] = None) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def list_messages(self, username: str, conversation_id: str) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def append_chat_message(
        self,
        username: str,
        conversation_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict]] = None,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def get_runtime_state(self, key: str) -> Optional[Dict]:
        raise NotImplementedError

    @abstractmethod
    def set_runtime_state(self, key: str, value: Dict) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def delete_runtime_state(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def update_message_feedback(
        self,
        username: str,
        conversation_id: str,
        message_id: str,
        feedback_status: str,
        feedback_note: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def find_feedback_matches(self, username: str, question: str, limit: int = 3) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def get_port_activity_snapshot(self, window_days: int = 5) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def get_port_call(self, port_call_id: str) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def create_port_call(
        self,
        vessel_name: str,
        eta: str,
        created_by: str,
        constraints: Optional[List[str]] = None,
        berth: str = "",
        last_port: str = "",
        next_port: str = "",
        notes: str = "",
        vessel_short_name: str = "",
        vessel_imo: str = "",
        vessel_call_sign: str = "",
        vessel_flag: str = "",
        vessel_type: str = "",
        vessel_loa_m: str = "",
        vessel_beam_m: str = "",
        vessel_gt_t: str = "",
        vessel_max_draft_m: str = "",
        vessel_dwt_t: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def mark_port_call_arrived(
        self,
        port_call_id: str,
        arrived_at: str,
        updated_by: str,
        berth: str = "",
        notes: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def mark_port_call_departed(
        self,
        port_call_id: str,
        departed_at: str,
        updated_by: str,
        next_port: str = "",
        notes: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def approve_port_call(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        raise NotImplementedError

    @abstractmethod
    def attach_entry_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def attach_departure_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def schedule_shift_plan(
        self,
        port_call_id: str,
        planned_shift_at: str,
        updated_by: str,
        destination_berth: str,
        constraints: Optional[List[str]] = None,
        shift_plan_note: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def approve_shift_plan(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        raise NotImplementedError

    @abstractmethod
    def abort_shift_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def mark_shift_completed(
        self,
        port_call_id: str,
        shifted_at: str,
        updated_by: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def attach_shift_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def edit_maneuver_plan(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        actor_role: str,
        planned_at: str,
        origin: str,
        destination: str,
        draft_m: str,
        tug_count: str,
        constraints: Optional[List[str]] = None,
        plan_note: str = "",
        change_reason: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def edit_maneuver_report(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        change_reason: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def abort_port_call(
        self,
        port_call_id: str,
        decided_by: str,
        aborted_reason: str,
        approval_note: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def schedule_departure_plan(
        self,
        port_call_id: str,
        planned_departure_at: str,
        updated_by: str,
        next_port: str = "",
        constraints: Optional[List[str]] = None,
        departure_plan_note: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def abort_departure_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        raise NotImplementedError


class LocalStore(BaseStore):
    backend_name = "local"

    def __init__(self, data_dir: str, knowledge_dir: str) -> None:
        self.data_dir = data_dir
        self.knowledge_dir = knowledge_dir
        self.users_path = os.path.join(data_dir, "users.json")
        self.documents_path = os.path.join(data_dir, "documents.json")
        self.conversations_path = os.path.join(data_dir, "conversations.json")
        self.messages_path = os.path.join(data_dir, "messages.json")
        self.runtime_state_path = os.path.join(data_dir, "runtime_state.json")
        self.port_calls_path = os.path.join(data_dir, "port_calls.json")
        self.legacy_chats_path = os.path.join(data_dir, "chats.json")
        self._ensure_dirs()
        self._seed_defaults()
        self._migrate_legacy_chats()
        self._sync_document_records()

    def _ensure_dirs(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.knowledge_dir, exist_ok=True)

    def _read_json(self, path: str, fallback):
        if not os.path.exists(path):
            return fallback
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_json(self, path: str, payload) -> None:
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)

    def _seed_defaults(self) -> None:
        users = self._read_json(self.users_path, [])
        if not users:
            users = [
                self._build_user("admin", "admin123", "admin"),
                self._build_user("agente", "agente123", "agente"),
                self._build_user("piloto", "piloto123", "piloto"),
            ]
            self._write_json(self.users_path, users)

        for path, fallback in (
            (self.documents_path, []),
            (self.conversations_path, []),
            (self.messages_path, []),
            (self.runtime_state_path, {}),
            (self.port_calls_path, _default_port_calls()),
        ):
            if not os.path.exists(path):
                self._write_json(path, fallback)

    def _migrate_legacy_chats(self) -> None:
        legacy = self._read_json(self.legacy_chats_path, {})
        conversations = self._read_json(self.conversations_path, [])
        messages = self._read_json(self.messages_path, [])
        if not legacy or conversations or messages:
            return

        migrated_conversations = []
        migrated_messages = []
        for username, history in legacy.items():
            conversation = self._build_conversation_record(
                username=username,
                title="Conversa importada",
            )
            migrated_conversations.append(conversation)
            for entry in history:
                migrated_messages.append(
                    self._build_message_record(
                        conversation_id=conversation["id"],
                        role=entry.get("role", "assistant"),
                        content=entry.get("content", ""),
                        citations=entry.get("citations", []),
                    )
                )

        self._write_json(self.conversations_path, migrated_conversations)
        self._write_json(self.messages_path, migrated_messages)

    def _build_user(
        self,
        username: str,
        password: str,
        role: str,
        *,
        full_name: str = "",
        organization: str = "",
        email: str = "",
        phone: str = "",
    ) -> Dict:
        return {
            "password_hash": generate_password_hash(password, method=PASSWORD_HASH_METHOD),
            **_normalize_user_profile_payload(
                {
                    "username": username,
                    "role": role,
                    "full_name": full_name,
                    "organization": organization,
                    "email": email,
                    "phone": phone,
                }
            ),
        }

    def _build_conversation_record(
        self,
        username: str,
        title: str = DEFAULT_CONVERSATION_TITLE,
        created_at: Optional[str] = None,
    ) -> Dict:
        stamp = created_at or iso_now()
        return {
            "id": str(uuid.uuid4()),
            "username": username,
            "title": title,
            "created_at": stamp,
            "updated_at": stamp,
        }

    def _build_message_record(
        self,
        conversation_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict]] = None,
        feedback_status: Optional[str] = None,
        feedback_note: str = "",
        feedback_updated_at: Optional[str] = None,
    ) -> Dict:
        return {
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "citations": citations or [],
            "created_at": iso_now(),
            "feedback_status": feedback_status,
            "feedback_note": feedback_note,
            "feedback_updated_at": feedback_updated_at,
        }

    def _read_document_records(self) -> List[Dict]:
        return self._read_json(self.documents_path, [])

    def _write_document_records(self, records: List[Dict]) -> None:
        self._write_json(self.documents_path, records)

    def _read_conversations(self) -> List[Dict]:
        return self._read_json(self.conversations_path, [])

    def _write_conversations(self, records: List[Dict]) -> None:
        self._write_json(self.conversations_path, records)

    def _read_messages(self) -> List[Dict]:
        return self._read_json(self.messages_path, [])

    def _write_messages(self, records: List[Dict]) -> None:
        self._write_json(self.messages_path, records)

    def _read_runtime_state(self) -> Dict:
        return self._read_json(self.runtime_state_path, {})

    def _write_runtime_state(self, payload: Dict) -> None:
        self._write_json(self.runtime_state_path, payload)

    def _read_users(self) -> List[Dict]:
        records = self._read_json(self.users_path, [])
        normalized = []
        changed = False
        for record in records:
            normalized_record = {
                "password_hash": record.get("password_hash", ""),
                **_normalize_user_profile_payload(record),
            }
            normalized.append(normalized_record)
            if normalized_record != record:
                changed = True
        if changed:
            self._write_json(self.users_path, normalized)
        return normalized

    def _write_users(self, records: List[Dict]) -> None:
        self._write_json(self.users_path, records)

    def _read_port_calls(self) -> List[Dict]:
        records = self._read_json(self.port_calls_path, [])
        normalized_records = [_normalize_port_call_record(item) for item in records]
        if normalized_records != records:
            self._write_port_calls(normalized_records)
        return normalized_records

    def _write_port_calls(self, records: List[Dict]) -> None:
        self._write_json(self.port_calls_path, records)

    def _find_port_call_index(self, port_call_id: str) -> int:
        for index, item in enumerate(self._read_port_calls()):
            if item["id"] == port_call_id:
                return index
        raise ValueError("Manobra não encontrada.")

    def _message_owned_by_user(
        self, username: str, conversation_id: str, message_id: str
    ) -> Optional[Dict]:
        if not self._conversation_owned_by_user(username, conversation_id):
            return None
        for message in self._read_messages():
            if message["id"] == message_id and message["conversation_id"] == conversation_id:
                return message
        return None

    def _upsert_document_record(self, record: Dict) -> None:
        records = self._read_document_records()
        replaced = False
        for index, current in enumerate(records):
            if current["name"] == record["name"]:
                records[index] = record
                replaced = True
                break
        if not replaced:
            records.append(record)
        records.sort(key=lambda item: item["name"])
        self._write_document_records(records)

    def _sync_document_records(self) -> None:
        records_by_name = {record["name"]: record for record in self._read_document_records()}
        synced = []
        for name in sorted(os.listdir(self.knowledge_dir)):
            path = os.path.join(self.knowledge_dir, name)
            if not os.path.isfile(path) or not is_allowed_document(name):
                continue

            meta = file_metadata(path)
            previous = records_by_name.get(name, {})
            preview = previous.get("preview", "")
            if (
                previous.get("size_bytes") != meta["size_bytes"]
                or previous.get("updated_at") != meta["updated_at"]
                or not preview
            ):
                try:
                    text = extract_text_from_path(path)
                    preview = build_preview(text)
                except Exception as exc:
                    preview = f"Erro ao extrair conteúdo: {exc}"
            synced.append(
                {
                    "name": name,
                    "original_name": previous.get("original_name", name),
                    "doc_type": infer_document_type(name),
                    "size_bytes": meta["size_bytes"],
                    "size_label": format_bytes(meta["size_bytes"]),
                    "updated_at": meta["updated_at"],
                    "updated_at_label": _utc_iso_to_label(meta["updated_at"]),
                    "created_at": previous.get("created_at", meta["updated_at"]),
                    "uploaded_by": previous.get("uploaded_by", "system"),
                    "preview": preview,
                    "editable": is_text_editable(name),
                }
            )

        self._write_document_records(synced)

    def _conversation_owned_by_user(self, username: str, conversation_id: str) -> Optional[Dict]:
        for conversation in self._read_conversations():
            if conversation["id"] == conversation_id and conversation["username"] == username:
                return conversation
        return None

    def _touch_conversation(self, conversation_id: str, title_hint: Optional[str] = None) -> None:
        conversations = self._read_conversations()
        messages = self._read_messages()
        user_message_count = sum(
            1
            for item in messages
            if item["conversation_id"] == conversation_id and item["role"] == "user"
        )
        for conversation in conversations:
            if conversation["id"] != conversation_id:
                continue
            conversation["updated_at"] = iso_now()
            if title_hint and (
                conversation["title"] == DEFAULT_CONVERSATION_TITLE or user_message_count <= 1
            ):
                conversation["title"] = _conversation_title_from_text(title_hint)
        self._write_conversations(conversations)

    def list_users(self) -> List[Dict]:
        return self._read_users()

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        full_name: str = "",
        organization: str = "",
        email: str = "",
        phone: str = "",
    ) -> Dict:
        username = _normalize_username(username)
        if len(username) < 3:
            raise ValueError("O email deve ter pelo menos 3 caracteres.")
        if len(password) < 6:
            raise ValueError("A password deve ter pelo menos 6 caracteres.")
        if role not in {"admin", "agente", "piloto"}:
            raise ValueError("Role invalido.")

        users = self._read_users()
        if any(user["username"] == username for user in users):
            raise ValueError("Esse utilizador ja existe.")

        user = self._build_user(
            username,
            password,
            role,
            full_name=full_name,
            organization=organization,
            email=email,
            phone=phone,
        )
        users.append(user)
        self._write_users(users)
        return {key: user[key] for key in user if key != "password_hash"}

    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        users = self._read_users()
        for user in users:
            if user["username"] == _normalize_username(username) and check_password_hash(
                user["password_hash"], password
            ):
                return {key: user[key] for key in user if key != "password_hash"}
        return None

    def get_user_profile(self, username: str) -> Optional[Dict]:
        users = self._read_users()
        for user in users:
            if user["username"] == _normalize_username(username):
                return {key: user[key] for key in user if key != "password_hash"}
        return None

    def set_user_role(self, username: str, role: str) -> Dict:
        username = _normalize_username(username)
        if role not in {"admin", "agente", "piloto"}:
            raise ValueError("Role invalido.")
        users = self._read_users()
        updated = None
        for user in users:
            if user["username"] == username:
                user["role"] = role
                if is_user_profile_complete(user):
                    user["profile_completed_at"] = user.get("profile_completed_at") or iso_now()
                updated = user
                break
        if not updated:
            raise ValueError("Utilizador não encontrado.")
        self._write_users(users)
        return {key: updated[key] for key in updated if key != "password_hash"}

    def reset_user_password(self, username: str, new_password: str) -> bool:
        username = _normalize_username(username)
        if len(new_password) < 6:
            raise ValueError("A password deve ter pelo menos 6 caracteres.")
        users = self._read_users()
        for user in users:
            if user["username"] == username:
                user["password_hash"] = generate_password_hash(new_password, method=PASSWORD_HASH_METHOD)
                self._write_users(users)
                return True
        return False

    def delete_user(self, username: str) -> None:
        normalized_username = _normalize_username(username)
        users = self._read_users()
        target = next((user for user in users if user["username"] == normalized_username), None)
        if not target:
            raise ValueError("Utilizador não encontrado.")
        if target.get("role") == "admin":
            admin_count = sum(1 for user in users if user.get("role") == "admin")
            if admin_count <= 1:
                raise ValueError("Não podes apagar o último admin.")

        remaining_users = [user for user in users if user["username"] != normalized_username]
        self._write_users(remaining_users)

        conversations = [item for item in self._read_conversations() if item["username"] != normalized_username]
        conversation_ids = {item["id"] for item in self._read_conversations() if item["username"] == normalized_username}
        self._write_conversations(conversations)

        if conversation_ids:
            messages = [item for item in self._read_messages() if item["conversation_id"] not in conversation_ids]
            self._write_messages(messages)

    def update_user_profile(
        self,
        username: str,
        *,
        full_name: str,
        organization: str,
        email: str,
        phone: str,
    ) -> Dict:
        users = self._read_users()
        updated = None
        for user in users:
            if user["username"] != _normalize_username(username):
                continue
            user["full_name"] = _clean_text(full_name)
            user["organization"] = _clean_text(organization)
            user["email"] = _normalize_email(email)
            user["phone"] = _normalize_phone(phone)
            user["profile_completed_at"] = iso_now() if is_user_profile_complete(user) else None
            updated = user
            break
        if not updated:
            raise ValueError("Utilizador não encontrado.")
        self._write_users(users)
        return {key: updated[key] for key in updated if key != "password_hash"}

    def save_document(self, title: str, content: str, created_by: str = "manual") -> str:
        filename = ensure_unique_filename(self.knowledge_dir, f"{slugify(title)}.md")
        path = os.path.join(self.knowledge_dir, filename)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content.strip() + "\n")

        meta = file_metadata(path)
        self._upsert_document_record(
            {
                "name": filename,
                "original_name": filename,
                "doc_type": infer_document_type(filename),
                "size_bytes": meta["size_bytes"],
                "size_label": format_bytes(meta["size_bytes"]),
                "updated_at": meta["updated_at"],
                "updated_at_label": _utc_iso_to_label(meta["updated_at"]),
                "created_at": iso_now(),
                "uploaded_by": created_by,
                "preview": build_preview(content),
                "editable": is_text_editable(filename),
            }
        )
        return filename

    def save_uploaded_document(self, uploaded_file: FileStorage, created_by: str) -> str:
        filename = sanitize_upload_filename(uploaded_file.filename or "")
        if not is_allowed_document(filename):
            raise ValueError("Formato não suportado. Usa .pdf, .md, .txt, .docx ou .csv.")

        path = os.path.join(self.knowledge_dir, filename)
        stem, suffix = os.path.splitext(path)
        temp_path = f"{stem}.upload-{uuid.uuid4().hex}{suffix}"
        uploaded_file.save(temp_path)

        try:
            text = extract_text_from_path(temp_path)
        except Exception as exc:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise ValueError(f"Falha ao processar ficheiro: {exc}") from exc
        if not text.strip():
            os.remove(temp_path)
            raise ValueError("Não foi possível extrair texto útil do ficheiro.")

        os.replace(temp_path, path)

        meta = file_metadata(path)
        self._upsert_document_record(
            {
                "name": filename,
                "original_name": uploaded_file.filename or filename,
                "doc_type": infer_document_type(filename),
                "size_bytes": meta["size_bytes"],
                "size_label": format_bytes(meta["size_bytes"]),
                "updated_at": meta["updated_at"],
                "updated_at_label": _utc_iso_to_label(meta["updated_at"]),
                "created_at": iso_now(),
                "uploaded_by": created_by,
                "preview": build_preview(text),
                "editable": is_text_editable(filename),
            }
        )
        return filename

    def list_documents(self) -> List[Dict]:
        self._sync_document_records()
        return self._read_document_records()

    def get_document(self, name: str) -> Optional[Dict]:
        self._sync_document_records()
        for record in self._read_document_records():
            if record["name"] == name:
                return record
        return None

    def get_document_text(self, name: str) -> str:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        path = os.path.join(self.knowledge_dir, record["name"])
        return extract_text_from_path(path)

    def get_document_file_path(self, name: str) -> str:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        return os.path.join(self.knowledge_dir, record["name"])

    def update_document_text(self, name: str, content: str, updated_by: str) -> Dict:
        if not content.strip():
            raise ValueError("O conteúdo não pode estar vazio.")
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        if not is_text_editable(name):
            raise ValueError("Este tipo de ficheiro não pode ser editado no browser.")

        path = os.path.join(self.knowledge_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content.strip() + "\n")

        meta = file_metadata(path)
        updated = {
            **record,
            "size_bytes": meta["size_bytes"],
            "size_label": format_bytes(meta["size_bytes"]),
            "updated_at": meta["updated_at"],
            "updated_at_label": _utc_iso_to_label(meta["updated_at"]),
            "uploaded_by": updated_by,
            "preview": build_preview(content),
            "editable": True,
        }
        self._upsert_document_record(updated)
        return updated

    def delete_document(self, name: str) -> None:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        path = os.path.join(self.knowledge_dir, name)
        if os.path.exists(path):
            os.remove(path)
        records = [item for item in self._read_document_records() if item["name"] != name]
        self._write_document_records(records)

    def list_conversations(self, username: str) -> List[Dict]:
        conversations = [item for item in self._read_conversations() if item["username"] == username]
        conversations.sort(key=lambda item: item["updated_at"], reverse=True)
        return [
            {
                **item,
                "updated_at_label": _utc_iso_to_label(item["updated_at"]),
            }
            for item in conversations
        ]

    def create_conversation(self, username: str, title: str = DEFAULT_CONVERSATION_TITLE) -> Dict:
        conversations = self._read_conversations()
        conversation = self._build_conversation_record(username=username, title=title)
        conversations.append(conversation)
        self._write_conversations(conversations)
        return {
            **conversation,
            "updated_at_label": _utc_iso_to_label(conversation["updated_at"]),
        }

    def rename_conversation(self, username: str, conversation_id: str, title: str) -> Dict:
        clean_title = " ".join(title.strip().split())
        if not clean_title:
            raise ValueError("O título da conversa não pode ficar vazio.")

        conversations = self._read_conversations()
        updated = None
        for conversation in conversations:
            if conversation["id"] == conversation_id and conversation["username"] == username:
                conversation["title"] = clean_title
                conversation["updated_at"] = iso_now()
                updated = conversation
                break

        if not updated:
            raise ValueError("Conversa não encontrada.")

        self._write_conversations(conversations)
        return {
            **updated,
            "updated_at_label": _utc_iso_to_label(updated["updated_at"]),
        }

    def clear_conversation(self, username: str, conversation_id: str) -> None:
        conversation = self._conversation_owned_by_user(username, conversation_id)
        if not conversation:
            raise ValueError("Conversa não encontrada.")
        messages = [
            item for item in self._read_messages() if item["conversation_id"] != conversation_id
        ]
        self._write_messages(messages)
        self._touch_conversation(conversation_id)

    def delete_conversation(self, username: str, conversation_id: str) -> Optional[str]:
        conversation = self._conversation_owned_by_user(username, conversation_id)
        if not conversation:
            raise ValueError("Conversa não encontrada.")

        conversations = [
            item
            for item in self._read_conversations()
            if not (item["id"] == conversation_id and item["username"] == username)
        ]
        self._write_conversations(conversations)
        messages = [
            item for item in self._read_messages() if item["conversation_id"] != conversation_id
        ]
        self._write_messages(messages)

        remaining = self.list_conversations(username)
        return remaining[0]["id"] if remaining else None

    def ensure_conversation(self, username: str, conversation_id: Optional[str] = None) -> Dict:
        if conversation_id:
            existing = self._conversation_owned_by_user(username, conversation_id)
            if existing:
                return {
                    **existing,
                    "updated_at_label": _utc_iso_to_label(existing["updated_at"]),
                }
        conversations = self.list_conversations(username)
        if conversations:
            return conversations[0]
        return self.create_conversation(username)

    def list_messages(self, username: str, conversation_id: str) -> List[Dict]:
        conversation = self._conversation_owned_by_user(username, conversation_id)
        if not conversation:
            return []

        messages = [
            item
            for item in self._read_messages()
            if item["conversation_id"] == conversation_id
        ]
        messages.sort(key=lambda item: item["created_at"])
        for message in messages:
            message.setdefault("feedback_status", None)
            message.setdefault("feedback_note", "")
            message.setdefault("feedback_updated_at", None)
        return messages

    def append_chat_message(
        self,
        username: str,
        conversation_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict]] = None,
    ) -> Dict:
        if not self._conversation_owned_by_user(username, conversation_id):
            raise ValueError("Conversa inválida para este utilizador.")

        messages = self._read_messages()
        message = self._build_message_record(
            conversation_id=conversation_id,
            role=role,
            content=content,
            citations=citations,
        )
        messages.append(message)
        self._write_messages(messages)
        if role == "user":
            self._touch_conversation(conversation_id, title_hint=content)
        else:
            self._touch_conversation(conversation_id)
        return message

    def get_runtime_state(self, key: str) -> Optional[Dict]:
        payload = self._read_runtime_state()
        value = payload.get(key)
        return value if isinstance(value, dict) else None

    def set_runtime_state(self, key: str, value: Dict) -> Dict:
        payload = self._read_runtime_state()
        payload[key] = value
        self._write_runtime_state(payload)
        return value

    def delete_runtime_state(self, key: str) -> None:
        payload = self._read_runtime_state()
        if key in payload:
            payload.pop(key, None)
            self._write_runtime_state(payload)

    def update_message_feedback(
        self,
        username: str,
        conversation_id: str,
        message_id: str,
        feedback_status: str,
        feedback_note: str = "",
    ) -> Dict:
        if feedback_status not in ALLOWED_FEEDBACK_STATUSES:
            raise ValueError("Estado de feedback inválido.")
        if not self._message_owned_by_user(username, conversation_id, message_id):
            raise ValueError("Mensagem não encontrada.")

        messages = self._read_messages()
        updated = None
        for message in messages:
            if message["id"] != message_id or message["conversation_id"] != conversation_id:
                continue
            if message["role"] != "assistant":
                raise ValueError("Só podes classificar respostas do assistente.")
            message["feedback_status"] = feedback_status
            message["feedback_note"] = feedback_note.strip()
            message["feedback_updated_at"] = iso_now()
            updated = message
            break

        if not updated:
            raise ValueError("Mensagem não encontrada.")

        self._write_messages(messages)
        self._touch_conversation(conversation_id)
        return updated

    def find_feedback_matches(self, username: str, question: str, limit: int = 3) -> List[Dict]:
        conversations = {
            item["id"]: item
            for item in self._read_conversations()
            if item["username"] == username
        }
        if not conversations:
            return []

        conversation_messages: Dict[str, List[Dict]] = {}
        for message in self._read_messages():
            if message["conversation_id"] in conversations:
                conversation_messages.setdefault(message["conversation_id"], []).append(message)

        matches = []
        for conversation_id, messages in conversation_messages.items():
            messages.sort(key=lambda item: item["created_at"])
            previous_user = None
            for message in messages:
                if message["role"] == "user":
                    previous_user = message
                    continue
                if message["role"] != "assistant":
                    continue
                if message.get("feedback_status") != FEEDBACK_APPROVED:
                    continue
                if not previous_user:
                    continue
                score = _text_similarity(question, previous_user.get("content", ""))
                if score < 0.35:
                    continue
                matches.append(
                    {
                        "message_id": message["id"],
                        "conversation_id": conversation_id,
                        "question": previous_user.get("content", ""),
                        "answer": message.get("content", ""),
                        "citations": message.get("citations", []),
                        "feedback_note": message.get("feedback_note", ""),
                        "feedback_updated_at": message.get("feedback_updated_at"),
                        "similarity": round(score, 3),
                    }
                )

        matches.sort(
            key=lambda item: (
                item["similarity"],
                item.get("feedback_updated_at") or "",
            ),
            reverse=True,
        )
        return matches[:limit]

    def get_port_activity_snapshot(self, window_days: int = 5) -> Dict:
        return _build_port_activity_snapshot(self._read_port_calls(), window_days=window_days)

    def get_port_call(self, port_call_id: str) -> Dict:
        for item in self._read_port_calls():
            if item["id"] == port_call_id:
                return _decorate_port_call(item)
        raise ValueError("Escala não encontrada.")

    def create_port_call(
        self,
        vessel_name: str,
        eta: str,
        created_by: str,
        constraints: Optional[List[str]] = None,
        berth: str = "",
        last_port: str = "",
        next_port: str = "",
        notes: str = "",
        vessel_short_name: str = "",
        vessel_imo: str = "",
        vessel_call_sign: str = "",
        vessel_flag: str = "",
        vessel_type: str = "",
        vessel_loa_m: str = "",
        vessel_beam_m: str = "",
        vessel_gt_t: str = "",
        vessel_max_draft_m: str = "",
        vessel_dwt_t: str = "",
    ) -> Dict:
        clean_name = _clean_text(vessel_name)
        creator_username = _normalize_username(created_by) or "system"
        creator_profile = self.get_user_profile(creator_username)
        if len(clean_name) < 2:
            raise ValueError("Indica o nome do navio.")
        if not eta.strip():
            raise ValueError("O ETA é obrigatório.")
        vessel_profile = {
            "vessel_short_name": _clean_text(vessel_short_name),
            "vessel_imo": _clean_text(vessel_imo),
            "vessel_call_sign": _clean_text(vessel_call_sign),
            "vessel_flag": _clean_text(vessel_flag),
            "vessel_type": _clean_text(vessel_type),
            "vessel_loa_m": _clean_text(vessel_loa_m),
            "vessel_beam_m": _clean_text(vessel_beam_m),
            "vessel_gt_t": _clean_text(vessel_gt_t),
            "vessel_max_draft_m": _clean_text(vessel_max_draft_m),
            "vessel_dwt_t": _clean_text(vessel_dwt_t),
        }
        _validate_required_vessel_profile(vessel_profile)
        _validate_required_operational_profile(
            {
                "berth": berth,
                "last_port": last_port,
                "next_port": next_port,
            },
            (
                ("berth", "cais previsto"),
                ("last_port", "porto anterior"),
                ("next_port", "próximo destino"),
            ),
        )

        record = {
            "id": str(uuid.uuid4()),
            "vessel_name": clean_name,
            **vessel_profile,
            "status": PORT_CALL_STATUS_SCHEDULED,
            "approval_status": PORT_CALL_APPROVAL_PENDING,
            "approval_note": "",
            "aborted_reason": "",
            "decided_by": None,
            "decided_at": None,
            "eta": eta,
            "ata": None,
            "planned_departure_at": None,
            "departure_plan_note": "",
            "departure_at": None,
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": _clean_text(berth),
            "last_port": _clean_text(last_port),
            "next_port": _clean_text(next_port),
            "created_by": creator_username,
            "created_by_profile": _build_actor_snapshot(creator_profile, username=creator_username),
            "notes": notes.strip(),
            "created_at": iso_now(),
            "updated_at": iso_now(),
        }
        record["maneuver_history"] = [
            _normalize_maneuver_record(
                {
                    "type": "entry",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": eta,
                    "completed_at": None,
                    "origin": record["last_port"],
                    "destination": record["berth"],
                    "plan_note": notes.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": record["created_by"],
                    "created_by_profile": record["created_by_profile"],
                    "created_at": record["created_at"],
                    "updated_at": record["updated_at"],
                },
                fallback_created_by=record["created_by"],
            )
        ]
        record = _sync_port_call_from_history(record)
        records = self._read_port_calls()
        records.append(record)
        records.sort(key=lambda item: item.get("eta") or "")
        self._write_port_calls(records)
        return _decorate_port_call(record)

    def mark_port_call_arrived(
        self,
        port_call_id: str,
        arrived_at: str,
        updated_by: str,
        berth: str = "",
        notes: str = "",
    ) -> Dict:
        if not arrived_at.strip():
            raise ValueError("A hora real de chegada é obrigatória.")
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_SCHEDULED or not entry:
                raise ValueError("Só podes confirmar entrada de manobras previstas.")
            entry["state"] = "completed"
            entry["completed_at"] = arrived_at
            if berth.strip():
                entry["destination"] = " ".join(berth.strip().split())
            entry["updated_at"] = iso_now()
            current["maneuver_history"] = current["maneuver_history"]
            if berth.strip():
                current["berth"] = " ".join(berth.strip().split())
            if notes.strip():
                existing = current.get("notes", "").strip()
                current["notes"] = f"{existing} | {notes.strip()}".strip(" |")
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def schedule_departure_plan(
        self,
        port_call_id: str,
        planned_departure_at: str,
        updated_by: str,
        next_port: str = "",
        constraints: Optional[List[str]] = None,
        departure_plan_note: str = "",
    ) -> Dict:
        if not planned_departure_at.strip():
            raise ValueError("A hora prevista de saída é obrigatória.")
        destination = " ".join(next_port.strip().split())
        if not destination:
            raise ValueError("Indica o próximo destino da saída.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            if current["status"] != PORT_CALL_STATUS_IN_PORT:
                raise ValueError("Só podes planear saída para navios que estão em porto.")
            if _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED}):
                raise ValueError("Já existe uma saída ativa para esta escala.")
            departure = _normalize_maneuver_record(
                {
                    "type": "departure",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": planned_departure_at,
                    "completed_at": None,
                    "origin": current.get("berth", ""),
                    "destination": destination,
                    "plan_note": departure_plan_note.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": actor_username or current.get("created_by", "system"),
                    "created_by_profile": _build_actor_snapshot(actor_profile, username=actor_username),
                    "created_at": iso_now(),
                    "updated_at": iso_now(),
                },
                fallback_created_by=actor_username or current.get("created_by", "system"),
            )
            current["maneuver_history"].append(departure)
            current["next_port"] = destination
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def abort_departure_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de aborto da saída é obrigatório.")
        actor_username = _normalize_username(updated_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            departure = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not departure:
                raise ValueError("Não existe manobra de saída planeada para este navio.")
            if not _can_abort_departure_plan({"planned_departure_at": departure.get("planned_at")}):
                raise ValueError("A saída só pode ser abortada com pelo menos 1 hora de antecedência.")
            departure["state"] = PORT_CALL_APPROVAL_ABORTED
            departure["aborted_reason"] = reason
            departure["decided_by"] = actor_username
            departure["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            departure["decided_at"] = iso_now()
            departure["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def mark_port_call_departed(
        self,
        port_call_id: str,
        departed_at: str,
        updated_by: str,
        next_port: str = "",
        notes: str = "",
    ) -> Dict:
        if not departed_at.strip():
            raise ValueError("A hora de saída é obrigatória.")
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            departure = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not departure:
                raise ValueError("Só podes registar saída de navios que estão em porto.")
            departure["state"] = "completed"
            departure["completed_at"] = departed_at
            if next_port.strip():
                departure["destination"] = " ".join(next_port.strip().split())
                current["next_port"] = " ".join(next_port.strip().split())
            if notes.strip():
                existing = current.get("notes", "").strip()
                current["notes"] = f"{existing} | {notes.strip()}".strip(" |")
            departure["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def approve_port_call(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        actor_username = _normalize_username(decided_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            target = None
            if current["status"] == PORT_CALL_STATUS_SCHEDULED:
                target = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_PENDING})
            elif current["status"] == PORT_CALL_STATUS_IN_PORT:
                target = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING})
            else:
                raise ValueError("Só podes aprovar manobras ainda não executadas.")
            if not target:
                raise ValueError("Não existe manobra pendente para aprovar.")
            target["state"] = PORT_CALL_APPROVAL_APPROVED
            target["approval_note"] = approval_note.strip()
            target["aborted_reason"] = ""
            target["decided_by"] = actor_username
            target["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            target["decided_at"] = iso_now()
            target["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)


    def attach_entry_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            entry = _latest_reportable_maneuver(current.get("maneuver_history", []), "entry")
            if not entry:
                raise ValueError("Só podes registar a entrada depois da manobra estar concluída.")
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            entry["report_note"] = note
            entry["execution_started_at"] = maneuver_started_at
            entry["execution_finished_at"] = maneuver_finished_at
            entry["reported_draft_m"] = draft_m.strip()
            entry["reported_by"] = actor_username
            entry["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            entry["reported_at"] = iso_now()
            entry["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def attach_departure_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            departure = _latest_reportable_maneuver(current.get("maneuver_history", []), "departure")
            if not departure:
                raise ValueError("Só podes registar a saída depois da manobra estar concluída.")
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            departure["report_note"] = note
            departure["execution_started_at"] = maneuver_started_at
            departure["execution_finished_at"] = maneuver_finished_at
            departure["reported_draft_m"] = draft_m.strip()
            departure["reported_by"] = actor_username
            departure["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            departure["reported_at"] = iso_now()
            departure["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def schedule_shift_plan(
        self,
        port_call_id: str,
        planned_shift_at: str,
        updated_by: str,
        destination_berth: str,
        constraints: Optional[List[str]] = None,
        shift_plan_note: str = "",
    ) -> Dict:
        if not planned_shift_at.strip():
            raise ValueError("A hora prevista da mudança é obrigatória.")
        destination = " ".join(destination_berth.strip().split())
        if not destination:
            raise ValueError("Indica o cais de destino da mudança.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            if current["status"] != PORT_CALL_STATUS_IN_PORT:
                raise ValueError("Só podes planear mudança para navios em porto.")
            if _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED}):
                raise ValueError("Já existe uma mudança ativa para esta escala.")
            shift = _normalize_maneuver_record(
                {
                    "type": "shift",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": planned_shift_at,
                    "completed_at": None,
                    "origin": current.get("berth", ""),
                    "destination": destination,
                    "plan_note": shift_plan_note.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": actor_username or current.get("created_by", "system"),
                    "created_by_profile": _build_actor_snapshot(actor_profile, username=actor_username),
                    "created_at": iso_now(),
                    "updated_at": iso_now(),
                },
                fallback_created_by=actor_username or current.get("created_by", "system"),
            )
            current["maneuver_history"].append(shift)
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def approve_shift_plan(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        actor_username = _normalize_username(decided_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("Só podes aprovar mudanças ainda não executadas.")
            shift["state"] = PORT_CALL_APPROVAL_APPROVED
            shift["approval_note"] = approval_note.strip()
            shift["aborted_reason"] = ""
            shift["decided_by"] = actor_username
            shift["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["decided_at"] = iso_now()
            shift["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)


    def abort_shift_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de aborto da mudança é obrigatório.")
        actor_username = _normalize_username(updated_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("Não existe manobra de mudança planeada para este navio.")
            if not _can_abort_shift_plan({"planned_shift_at": shift.get("planned_at")}):
                raise ValueError("A mudança só pode ser abortada com pelo menos 1 hora de antecedência.")
            shift["state"] = PORT_CALL_APPROVAL_ABORTED
            shift["aborted_reason"] = reason
            shift["decided_by"] = actor_username
            shift["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["decided_at"] = iso_now()
            shift["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def mark_shift_completed(
        self,
        port_call_id: str,
        shifted_at: str,
        updated_by: str,
    ) -> Dict:
        if not shifted_at.strip():
            raise ValueError("A hora real da mudança é obrigatória.")
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("Só podes concluir mudanças planeadas de navios em porto.")
            shift["state"] = "completed"
            shift["completed_at"] = shifted_at
            shift["updated_at"] = iso_now()
            if shift.get("destination"):
                current["berth"] = shift["destination"]
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def attach_shift_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            shift = _latest_reportable_maneuver(current.get("maneuver_history", []), "shift")
            if not shift:
                raise ValueError("Só podes registar a mudança depois da manobra estar concluída.")
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            shift["report_note"] = note
            shift["execution_started_at"] = maneuver_started_at
            shift["execution_finished_at"] = maneuver_finished_at
            shift["reported_draft_m"] = draft_m.strip()
            shift["reported_by"] = actor_username
            shift["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["reported_at"] = iso_now()
            shift["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def edit_maneuver_plan(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        actor_role: str,
        planned_at: str,
        origin: str,
        destination: str,
        draft_m: str,
        tug_count: str,
        constraints: Optional[List[str]] = None,
        plan_note: str = "",
        change_reason: str,
    ) -> Dict:
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada na escala.")
            if target.get("state") == "completed":
                raise ValueError("A manobra concluída já só pode ser ajustada no registo.")
            if not _can_edit_maneuver_plan(target, actor_role):
                raise ValueError("Depois de validada, esta manobra só pode ser editada por piloto.")
            target["planned_at"] = planned_at
            target["origin"] = _clean_text(origin)
            target["destination"] = _clean_text(destination)
            target["planned_draft_m"] = (draft_m or "").strip()
            target["tug_count"] = (tug_count or "").strip()
            target["plan_observations"] = (plan_note or "").strip()
            target["constraints"] = normalize_constraint_codes(constraints)
            target["plan_note"] = (plan_note or "").strip()
            target["updated_at"] = iso_now()
            _append_maneuver_change_log(
                target,
                actor_username=actor_username,
                actor_profile=actor_profile,
                reason=change_reason,
                summary="Planeamento atualizado.",
            )
            if target.get("type") == "entry":
                current["last_port"] = target["origin"]
                current["berth"] = target["destination"]
            elif target.get("type") == "departure":
                current["next_port"] = target["destination"]
            elif target.get("type") == "shift":
                current["shift_origin_berth"] = target["origin"]
                current["shift_destination_berth"] = target["destination"]
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def edit_maneuver_report(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        change_reason: str,
    ) -> Dict:
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada na escala.")
            if target.get("state") != "completed":
                raise ValueError("Só podes editar o registo de manobras já concluídas.")
            target["report_note"] = (notes or "").strip()
            target["execution_started_at"] = maneuver_started_at
            target["execution_finished_at"] = maneuver_finished_at
            target["reported_draft_m"] = draft_m.strip()
            target["reported_by"] = target.get("reported_by") or actor_username
            target["reported_by_profile"] = target.get("reported_by_profile") or _build_actor_snapshot(actor_profile, username=actor_username)
            target["reported_at"] = target.get("reported_at") or iso_now()
            target["updated_at"] = iso_now()
            _append_maneuver_change_log(
                target,
                actor_username=actor_username,
                actor_profile=actor_profile,
                reason=change_reason,
                summary=f"Registo revisto. Calado: {draft_m}.",
            )
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def abort_port_call(
        self,
        port_call_id: str,
        decided_by: str,
        aborted_reason: str,
        approval_note: str = "",
    ) -> Dict:
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de manobra abortada é obrigatório.")
        actor_username = _normalize_username(decided_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_SCHEDULED or not entry:
                raise ValueError("Só podes abortar manobras ainda não executadas.")
            if not _can_abort_port_call({"eta": entry.get("planned_at")}):
                raise ValueError("A manobra só pode ser abortada com pelo menos 2 horas de antecedência.")
            entry["state"] = PORT_CALL_APPROVAL_ABORTED
            entry["approval_note"] = approval_note.strip()
            entry["aborted_reason"] = reason
            entry["decided_by"] = actor_username
            entry["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            entry["decided_at"] = iso_now()
            entry["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)


class PostgresStore(BaseStore):
    backend_name = "postgres"

    def __init__(self, database_url: str, knowledge_dir: str) -> None:
        self.database_url = database_url
        self.knowledge_dir = knowledge_dir
        os.makedirs(self.knowledge_dir, exist_ok=True)
        self._ensure_schema()
        self._seed_defaults()
        self._sync_document_records()

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Instala `psycopg[binary]` para usar o backend postgres.") from exc
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _port_call_select_clause(self) -> str:
        return """
            SELECT
                id::text AS id,
                vessel_name,
                vessel_short_name,
                vessel_imo,
                vessel_call_sign,
                vessel_flag,
                vessel_type,
                vessel_loa_m,
                vessel_beam_m,
                vessel_gt_t,
                vessel_max_draft_m,
                vessel_dwt_t,
                status,
                approval_status,
                approval_note,
                aborted_reason,
                decided_by,
                decided_at,
                eta,
                ata,
                planned_departure_at,
                departure_plan_note,
                departure_at,
                planned_shift_at,
                shift_plan_note,
                shift_at,
                shift_origin_berth,
                shift_destination_berth,
                shift_approval_status,
                shift_approval_note,
                shift_aborted_reason,
                shift_decided_by,
                shift_decided_at,
                maneuver_history,
                berth,
                last_port,
                next_port,
                created_by,
                notes,
                created_at,
                updated_at
            FROM port_calls
        """

    def _row_to_port_call_record(self, row: Optional[Dict]) -> Optional[Dict]:
        if not row:
            return None
        return {
            **row,
            "eta": row["eta"].isoformat() if row.get("eta") else None,
            "ata": row["ata"].isoformat() if row.get("ata") else None,
            "planned_departure_at": row["planned_departure_at"].isoformat() if row.get("planned_departure_at") else None,
            "departure_at": row["departure_at"].isoformat() if row.get("departure_at") else None,
            "planned_shift_at": row["planned_shift_at"].isoformat() if row.get("planned_shift_at") else None,
            "shift_at": row["shift_at"].isoformat() if row.get("shift_at") else None,
            "shift_decided_at": row["shift_decided_at"].isoformat() if row.get("shift_decided_at") else None,
            "maneuver_history": row.get("maneuver_history") or [],
            "decided_at": row["decided_at"].isoformat() if row.get("decided_at") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _fetch_port_call_record(self, conn, port_call_id: str, for_update: bool = False) -> Optional[Dict]:
        query = f"{self._port_call_select_clause()} WHERE id = %s"
        if for_update:
            query += " FOR UPDATE"
        with conn.cursor() as cur:
            cur.execute(query, (port_call_id,))
            row = cur.fetchone()
        payload = self._row_to_port_call_record(row)
        if not payload:
            return None
        return _normalize_port_call_record(payload)

    def _save_port_call_record(self, conn, record: Dict) -> Dict:
        payload = _sync_port_call_from_history(_normalize_port_call_record(record))
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE port_calls
                SET
                    vessel_name = %s,
                    vessel_short_name = %s,
                    vessel_imo = %s,
                    vessel_call_sign = %s,
                    vessel_flag = %s,
                    vessel_type = %s,
                    vessel_loa_m = %s,
                    vessel_beam_m = %s,
                    vessel_gt_t = %s,
                    vessel_max_draft_m = %s,
                    vessel_dwt_t = %s,
                    status = %s,
                    approval_status = %s,
                    approval_note = %s,
                    aborted_reason = %s,
                    decided_by = %s,
                    decided_at = %s,
                    eta = %s,
                    ata = %s,
                    planned_departure_at = %s,
                    departure_plan_note = %s,
                    departure_at = %s,
                    planned_shift_at = %s,
                    shift_plan_note = %s,
                    shift_at = %s,
                    shift_origin_berth = %s,
                    shift_destination_berth = %s,
                    shift_approval_status = %s,
                    shift_approval_note = %s,
                    shift_aborted_reason = %s,
                    shift_decided_by = %s,
                    shift_decided_at = %s,
                    maneuver_history = %s::jsonb,
                    berth = %s,
                    last_port = %s,
                    next_port = %s,
                    created_by = %s,
                    notes = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING
                    id::text AS id,
                    vessel_name,
                    vessel_short_name,
                    vessel_imo,
                    vessel_call_sign,
                    vessel_flag,
                    vessel_type,
                    vessel_loa_m,
                    vessel_beam_m,
                    vessel_gt_t,
                    vessel_max_draft_m,
                    vessel_dwt_t,
                    status,
                    approval_status,
                    approval_note,
                    aborted_reason,
                    decided_by,
                    decided_at,
                    eta,
                    ata,
                    planned_departure_at,
                    departure_plan_note,
                    departure_at,
                    planned_shift_at,
                    shift_plan_note,
                    shift_at,
                    shift_origin_berth,
                    shift_destination_berth,
                    shift_approval_status,
                    shift_approval_note,
                    shift_aborted_reason,
                    shift_decided_by,
                    shift_decided_at,
                    maneuver_history,
                    berth,
                    last_port,
                    next_port,
                    created_by,
                    notes,
                    created_at,
                    updated_at
                """,
                (
                    payload.get("vessel_name"),
                    payload.get("vessel_short_name", ""),
                    payload.get("vessel_imo", ""),
                    payload.get("vessel_call_sign", ""),
                    payload.get("vessel_flag", ""),
                    payload.get("vessel_type", ""),
                    payload.get("vessel_loa_m", ""),
                    payload.get("vessel_beam_m", ""),
                    payload.get("vessel_gt_t", ""),
                    payload.get("vessel_max_draft_m", ""),
                    payload.get("vessel_dwt_t", ""),
                    payload.get("status"),
                    payload.get("approval_status"),
                    payload.get("approval_note", ""),
                    payload.get("aborted_reason", ""),
                    payload.get("decided_by"),
                    payload.get("decided_at"),
                    payload.get("eta"),
                    payload.get("ata"),
                    payload.get("planned_departure_at"),
                    payload.get("departure_plan_note", ""),
                    payload.get("departure_at"),
                    payload.get("planned_shift_at"),
                    payload.get("shift_plan_note", ""),
                    payload.get("shift_at"),
                    payload.get("shift_origin_berth", ""),
                    payload.get("shift_destination_berth", ""),
                    payload.get("shift_approval_status"),
                    payload.get("shift_approval_note", ""),
                    payload.get("shift_aborted_reason", ""),
                    payload.get("shift_decided_by"),
                    payload.get("shift_decided_at"),
                    json.dumps(payload.get("maneuver_history", [])),
                    payload.get("berth", ""),
                    payload.get("last_port", ""),
                    payload.get("next_port", ""),
                    payload.get("created_by", "system"),
                    payload.get("notes", ""),
                    payload["id"],
                ),
            )
            row = cur.fetchone()
        if not row:
            raise ValueError("Manobra não encontrada.")
        saved = self._row_to_port_call_record(row)
        if not saved:
            raise ValueError("Manobra não encontrada.")
        return saved

    def _mutate_port_call(self, port_call_id: str, mutator) -> Dict:
        with self._connect() as conn:
            current = self._fetch_port_call_record(conn, port_call_id, for_update=True)
            if not current:
                raise ValueError("Manobra não encontrada.")
            updated = mutator(current)
            updated["updated_at"] = iso_now()
            saved = self._save_port_call_record(conn, updated)
            conn.commit()
        return _decorate_port_call(saved)

    def _ensure_schema(self) -> None:
        schema_path = os.path.join(os.path.dirname(__file__), "sql", "postgres_schema.sql")
        with open(schema_path, "r", encoding="utf-8") as handle:
            schema_sql = handle.read()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
            conn.commit()

    def _seed_defaults(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                for username, password, role in (
                    ("admin", "admin123", "admin"),
                    ("agente", "agente123", "agente"),
                    ("piloto", "piloto123", "piloto"),
                ):
                    cur.execute(
                        """
                        INSERT INTO app_users (username, password_hash, role, full_name, organization, email, phone)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (username) DO NOTHING
                        """,
                        (username, generate_password_hash(password, method=PASSWORD_HASH_METHOD), role, "", "", "", ""),
                    )
                cur.execute("SELECT COUNT(*) AS total FROM port_calls")
                port_calls_count = int(cur.fetchone()["total"])
            conn.commit()

        if not os.listdir(self.knowledge_dir):
            self.save_document(
                "Manual de Manobra",
                """Checklist de aproximação:
1. Confirmar vento, maré e calado.
2. Validar disponibilidade dos rebocadores.
3. Rever canal VHF ativo.

Procedimento de atracação:
- Reduzir velocidade antes da bacia.
- Confirmar ordens do piloto e do comandante.
- Registar incidentes e tempos no histórico operacional.
""",
                created_by="system",
            )
        if port_calls_count == 0:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    for item in _default_port_calls():
                        cur.execute(
                            """
                            INSERT INTO port_calls (
                                id, vessel_name, status, approval_status, approval_note, aborted_reason,
                                decided_by, decided_at, eta, ata, planned_departure_at, departure_plan_note, departure_at,
                                planned_shift_at, shift_plan_note, shift_at, shift_origin_berth, shift_destination_berth,
                                shift_approval_status, shift_approval_note, shift_aborted_reason, shift_decided_by, shift_decided_at, berth,
                                last_port, next_port, created_by, notes, created_at, updated_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            (
                                item["id"],
                                item["vessel_name"],
                                item["status"],
                                item.get("approval_status", PORT_CALL_APPROVAL_PENDING),
                                item.get("approval_note", ""),
                                item.get("aborted_reason", ""),
                                item.get("decided_by"),
                                item.get("decided_at"),
                                item.get("eta"),
                                item.get("ata"),
                                item.get("planned_departure_at"),
                                item.get("departure_plan_note", ""),
                                item.get("departure_at"),
                                item.get("planned_shift_at"),
                                item.get("shift_plan_note", ""),
                                item.get("shift_at"),
                                item.get("shift_origin_berth", ""),
                                item.get("shift_destination_berth", ""),
                                item.get("shift_approval_status", PORT_CALL_APPROVAL_PENDING),
                                item.get("shift_approval_note", ""),
                                item.get("shift_aborted_reason", ""),
                                item.get("shift_decided_by"),
                                item.get("shift_decided_at"),
                                item.get("berth"),
                                item.get("last_port"),
                                item.get("next_port"),
                                item.get("created_by", "system"),
                                item.get("notes", ""),
                                item.get("created_at"),
                                item.get("updated_at"),
                            ),
                        )
                conn.commit()
            self.save_document(
                "Norma de Segurança",
                """Em caso de dúvida operacional, prevalece o princípio de segurança.
Qualquer falha de comunicação entre navio e autoridade portuária deve ser tratada como evento crítico.
O agente deve confirmar documentação obrigatória antes da janela de entrada.
""",
                created_by="system",
            )
            self.save_document(
                "Meteorologia e Marés",
                """Dados de marés e meteorologia devem ser revistos antes de cada manobra.
Se a intensidade do vento exceder o limite definido para o tipo de navio, a operação deve ser reavaliada.
O sistema futuro deverá integrar APIs externas para marés, vento e avisos costeiros.
""",
                created_by="system",
            )

    def _upsert_document_record(self, record: Dict, file_path: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents (
                        name, original_name, doc_type, size_bytes, updated_at, created_at,
                        uploaded_by, preview, file_path
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        original_name = EXCLUDED.original_name,
                        doc_type = EXCLUDED.doc_type,
                        size_bytes = EXCLUDED.size_bytes,
                        updated_at = EXCLUDED.updated_at,
                        uploaded_by = EXCLUDED.uploaded_by,
                        preview = EXCLUDED.preview,
                        file_path = EXCLUDED.file_path
                    """,
                    (
                        record["name"],
                        record["original_name"],
                        record["doc_type"],
                        record["size_bytes"],
                        record["updated_at"],
                        record["created_at"],
                        record["uploaded_by"],
                        record["preview"],
                        file_path,
                    ),
                )
            conn.commit()

    def _sync_document_records(self) -> None:
        existing_records = {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT name, original_name, doc_type, size_bytes, updated_at, created_at, uploaded_by, preview
                    FROM documents
                    """
                )
                for row in cur.fetchall():
                    existing_records[row["name"]] = row

        seen_names = set()
        for name in sorted(os.listdir(self.knowledge_dir)):
            path = os.path.join(self.knowledge_dir, name)
            if not os.path.isfile(path) or not is_allowed_document(name):
                continue
            seen_names.add(name)
            meta = file_metadata(path)
            previous = existing_records.get(name, {})
            preview = previous.get("preview", "")
            previous_updated_at = previous.get("updated_at")
            if hasattr(previous_updated_at, "isoformat"):
                previous_updated_at = previous_updated_at.isoformat()
            if (
                previous.get("size_bytes") != meta["size_bytes"]
                or previous_updated_at != meta["updated_at"]
                or not preview
            ):
                try:
                    text = extract_text_from_path(path)
                    preview = build_preview(text)
                except Exception as exc:
                    preview = f"Erro ao extrair conteúdo: {exc}"
            record = {
                "name": name,
                "original_name": previous.get("original_name", name),
                "doc_type": infer_document_type(name),
                "size_bytes": meta["size_bytes"],
                "updated_at": meta["updated_at"],
                "created_at": previous.get("created_at", meta["updated_at"]),
                "uploaded_by": previous.get("uploaded_by", "system"),
                "preview": preview,
            }
            self._upsert_document_record(record, path)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM documents")
                existing_names = {row["name"] for row in cur.fetchall()}
                stale_names = sorted(existing_names - seen_names)
                for name in stale_names:
                    cur.execute("DELETE FROM documents WHERE name = %s", (name,))
            conn.commit()

    def list_users(self) -> List[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, role, full_name, organization, email, phone, profile_completed_at
                    FROM app_users
                    ORDER BY username
                    """
                )
                rows = cur.fetchall()
        return [_normalize_user_profile_payload(row) for row in rows]

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        full_name: str = "",
        organization: str = "",
        email: str = "",
        phone: str = "",
    ) -> Dict:
        username = _normalize_username(username)
        if len(username) < 3:
            raise ValueError("O email deve ter pelo menos 3 caracteres.")
        if len(password) < 6:
            raise ValueError("A password deve ter pelo menos 6 caracteres.")
        if role not in {"admin", "agente", "piloto"}:
            raise ValueError("Role invalido.")
        profile = _normalize_user_profile_payload(
            {
                "username": username,
                "role": role,
                "full_name": full_name,
                "organization": organization,
                "email": email,
                "phone": phone,
            }
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM app_users WHERE username = %s", (username,))
                if cur.fetchone():
                    raise ValueError("Esse utilizador ja existe.")
                cur.execute(
                    """
                    INSERT INTO app_users (
                        username, password_hash, role, full_name, organization, email, phone, profile_completed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        username,
                        generate_password_hash(password, method=PASSWORD_HASH_METHOD),
                        role,
                        profile["full_name"],
                        profile["organization"],
                        profile["email"],
                        profile["phone"],
                        profile["profile_completed_at"],
                    ),
                )
            conn.commit()
        return profile

    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, password_hash, role, full_name, organization, email, phone, profile_completed_at
                    FROM app_users
                    WHERE username = %s
                    """,
                    (_normalize_username(username),),
                )
                user = cur.fetchone()
        if user and check_password_hash(user["password_hash"], password):
            return _normalize_user_profile_payload(user)
        return None

    def get_user_profile(self, username: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, role, full_name, organization, email, phone, profile_completed_at
                    FROM app_users
                    WHERE username = %s
                    """,
                    (_normalize_username(username),),
                )
                row = cur.fetchone()
        return _normalize_user_profile_payload(row) if row else None

    def set_user_role(self, username: str, role: str) -> Dict:
        username = _normalize_username(username)
        if role not in {"admin", "agente", "piloto"}:
            raise ValueError("Role invalido.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_users
                    SET role = %s,
                        profile_completed_at = CASE
                            WHEN COALESCE(full_name, '') <> ''
                             AND COALESCE(organization, '') <> ''
                             AND COALESCE(email, '') <> ''
                             AND COALESCE(phone, '') <> ''
                            THEN COALESCE(profile_completed_at, NOW())
                            ELSE NULL
                        END
                    WHERE username = %s
                    RETURNING username, role, full_name, organization, email, phone, profile_completed_at
                    """,
                    (role, username),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ValueError("Utilizador não encontrado.")
        return _normalize_user_profile_payload(row)

    def reset_user_password(self, username: str, new_password: str) -> bool:
        username = _normalize_username(username)
        if len(new_password) < 6:
            raise ValueError("A password deve ter pelo menos 6 caracteres.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE app_users SET password_hash = %s WHERE username = %s",
                    (generate_password_hash(new_password, method=PASSWORD_HASH_METHOD), username),
                )
                conn.commit()
                return cur.rowcount > 0

    def delete_user(self, username: str) -> None:
        normalized_username = _normalize_username(username)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, role
                    FROM app_users
                    WHERE username = %s
                    """,
                    (normalized_username,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Utilizador não encontrado.")
                if row["role"] == "admin":
                    cur.execute("SELECT COUNT(*) AS total FROM app_users WHERE role = 'admin'")
                    admin_total = cur.fetchone()["total"]
                    if admin_total <= 1:
                        raise ValueError("Não podes apagar o último admin.")
                cur.execute("DELETE FROM app_users WHERE username = %s", (normalized_username,))
            conn.commit()

    def update_user_profile(
        self,
        username: str,
        *,
        full_name: str,
        organization: str,
        email: str,
        phone: str,
    ) -> Dict:
        profile = _normalize_user_profile_payload(
            {
                "username": username,
                "full_name": full_name,
                "organization": organization,
                "email": email,
                "phone": phone,
                "role": (self.get_user_profile(username) or {}).get("role", "piloto"),
            }
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_users
                    SET
                        full_name = %s,
                        organization = %s,
                        email = %s,
                        phone = %s,
                        profile_completed_at = %s
                    WHERE username = %s
                    RETURNING username, role, full_name, organization, email, phone, profile_completed_at
                    """,
                    (
                        profile["full_name"],
                        profile["organization"],
                        profile["email"],
                        profile["phone"],
                        profile["profile_completed_at"],
                        _normalize_username(username),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ValueError("Utilizador não encontrado.")
        return _normalize_user_profile_payload(row)

    def save_document(self, title: str, content: str, created_by: str = "manual") -> str:
        filename = ensure_unique_filename(self.knowledge_dir, f"{slugify(title)}.md")
        path = os.path.join(self.knowledge_dir, filename)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content.strip() + "\n")
        meta = file_metadata(path)
        self._upsert_document_record(
            {
                "name": filename,
                "original_name": filename,
                "doc_type": infer_document_type(filename),
                "size_bytes": meta["size_bytes"],
                "updated_at": meta["updated_at"],
                "created_at": iso_now(),
                "uploaded_by": created_by,
                "preview": build_preview(content),
                "editable": is_text_editable(filename),
            },
            path,
        )
        return filename

    def save_uploaded_document(self, uploaded_file: FileStorage, created_by: str) -> str:
        filename = sanitize_upload_filename(uploaded_file.filename or "")
        if not is_allowed_document(filename):
            raise ValueError("Formato não suportado. Usa .pdf, .md, .txt, .docx ou .csv.")

        path = os.path.join(self.knowledge_dir, filename)
        stem, suffix = os.path.splitext(path)
        temp_path = f"{stem}.upload-{uuid.uuid4().hex}{suffix}"
        uploaded_file.save(temp_path)

        try:
            text = extract_text_from_path(temp_path)
        except Exception as exc:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise ValueError(f"Falha ao processar ficheiro: {exc}") from exc
        if not text.strip():
            os.remove(temp_path)
            raise ValueError("Não foi possível extrair texto útil do ficheiro.")

        os.replace(temp_path, path)

        meta = file_metadata(path)
        self._upsert_document_record(
            {
                "name": filename,
                "original_name": uploaded_file.filename or filename,
                "doc_type": infer_document_type(filename),
                "size_bytes": meta["size_bytes"],
                "updated_at": meta["updated_at"],
                "created_at": iso_now(),
                "uploaded_by": created_by,
                "preview": build_preview(text),
                "editable": is_text_editable(filename),
            },
            path,
        )
        return filename

    def list_documents(self) -> List[Dict]:
        self._sync_document_records()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        name, original_name, doc_type, size_bytes, updated_at, created_at,
                        uploaded_by, preview
                    FROM documents
                    ORDER BY updated_at DESC, name
                    """
                )
                rows = cur.fetchall()
        return [
            {
                **row,
                "size_label": format_bytes(row["size_bytes"]),
                "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
                "editable": is_text_editable(row["name"]),
            }
            for row in rows
        ]

    def get_document(self, name: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        name, original_name, doc_type, size_bytes, updated_at, created_at,
                        uploaded_by, preview, file_path
                    FROM documents
                    WHERE name = %s
                    """,
                    (name,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            **row,
            "size_label": format_bytes(row["size_bytes"]),
            "updated_at": row["updated_at"].isoformat(),
            "created_at": row["created_at"].isoformat(),
            "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
            "editable": is_text_editable(row["name"]),
        }

    def get_document_text(self, name: str) -> str:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        return extract_text_from_path(record["file_path"])

    def get_document_file_path(self, name: str) -> str:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        return record["file_path"]

    def update_document_text(self, name: str, content: str, updated_by: str) -> Dict:
        if not content.strip():
            raise ValueError("O conteúdo não pode estar vazio.")
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        if not is_text_editable(name):
            raise ValueError("Este tipo de ficheiro não pode ser editado no browser.")

        with open(record["file_path"], "w", encoding="utf-8") as handle:
            handle.write(content.strip() + "\n")
        meta = file_metadata(record["file_path"])

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE documents
                    SET
                        size_bytes = %s,
                        updated_at = %s,
                        uploaded_by = %s,
                        preview = %s
                    WHERE name = %s
                    """,
                    (
                        meta["size_bytes"],
                        meta["updated_at"],
                        updated_by,
                        build_preview(content),
                        name,
                    ),
                )
            conn.commit()
        return self.get_document(name)

    def delete_document(self, name: str) -> None:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        if os.path.exists(record["file_path"]):
            os.remove(record["file_path"])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM documents WHERE name = %s", (name,))
            conn.commit()

    def list_conversations(self, username: str) -> List[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text AS id, username, title, created_at, updated_at
                    FROM conversations
                    WHERE username = %s
                    ORDER BY updated_at DESC
                    """,
                    (username,),
                )
                rows = cur.fetchall()
        return [
            {
                **row,
                "updated_at": row["updated_at"].isoformat(),
                "created_at": row["created_at"].isoformat(),
                "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
            }
            for row in rows
        ]

    def create_conversation(self, username: str, title: str = DEFAULT_CONVERSATION_TITLE) -> Dict:
        conversation_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversations (id, username, title)
                    VALUES (%s, %s, %s)
                    RETURNING id::text AS id, username, title, created_at, updated_at
                    """,
                    (conversation_id, username, title),
                )
                row = cur.fetchone()
            conn.commit()
        return {
            **row,
            "updated_at": row["updated_at"].isoformat(),
            "created_at": row["created_at"].isoformat(),
            "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
        }

    def rename_conversation(self, username: str, conversation_id: str, title: str) -> Dict:
        clean_title = " ".join(title.strip().split())
        if not clean_title:
            raise ValueError("O título da conversa não pode ficar vazio.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE conversations
                    SET title = %s, updated_at = NOW()
                    WHERE id = %s AND username = %s
                    RETURNING id::text AS id, username, title, created_at, updated_at
                    """,
                    (clean_title, conversation_id, username),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ValueError("Conversa não encontrada.")
        return {
            **row,
            "updated_at": row["updated_at"].isoformat(),
            "created_at": row["created_at"].isoformat(),
            "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
        }

    def clear_conversation(self, username: str, conversation_id: str) -> None:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa não encontrada.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM messages WHERE conversation_id = %s",
                    (conversation_id,),
                )
                cur.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                    (conversation_id,),
                )
            conn.commit()

    def delete_conversation(self, username: str, conversation_id: str) -> Optional[str]:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa não encontrada.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM conversations WHERE id = %s AND username = %s",
                    (conversation_id, username),
                )
            conn.commit()
        remaining = self.list_conversations(username)
        return remaining[0]["id"] if remaining else None

    def ensure_conversation(self, username: str, conversation_id: Optional[str] = None) -> Dict:
        if conversation_id:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id::text AS id, username, title, created_at, updated_at
                        FROM conversations
                        WHERE id = %s AND username = %s
                        """,
                        (conversation_id, username),
                    )
                    row = cur.fetchone()
            if row:
                return {
                    **row,
                    "updated_at": row["updated_at"].isoformat(),
                    "created_at": row["created_at"].isoformat(),
                    "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
                }

        conversations = self.list_conversations(username)
        if conversations:
            return conversations[0]
        return self.create_conversation(username)

    def list_messages(self, username: str, conversation_id: str) -> List[Dict]:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id::text AS id,
                        conversation_id::text AS conversation_id,
                        role,
                        content,
                        citations,
                        created_at,
                        feedback_status,
                        feedback_note,
                        feedback_updated_at
                    FROM messages
                    WHERE conversation_id = %s
                    ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                )
                rows = cur.fetchall()
        return [
            {
                **row,
                "created_at": row["created_at"].isoformat(),
                "feedback_updated_at": (
                    row["feedback_updated_at"].isoformat() if row["feedback_updated_at"] else None
                ),
            }
            for row in rows
        ]

    def append_chat_message(
        self,
        username: str,
        conversation_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict]] = None,
    ) -> Dict:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa inválida para este utilizador.")
        message_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO messages (id, conversation_id, role, content, citations)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    RETURNING
                        id::text AS id,
                        conversation_id::text AS conversation_id,
                        role,
                        content,
                        citations,
                        created_at,
                        feedback_status,
                        feedback_note,
                        feedback_updated_at
                    """,
                    (message_id, conversation_id, role, content, json.dumps(citations or [])),
                )
                row = cur.fetchone()

                title_hint = None
                if role == "user":
                    cur.execute(
                        """
                        SELECT
                            title,
                            COUNT(*) FILTER (WHERE role = 'user') AS user_message_count
                        FROM conversations c
                        LEFT JOIN messages m ON m.conversation_id = c.id
                        WHERE c.id = %s
                        GROUP BY c.title
                        """,
                        (conversation_id,),
                    )
                    stats = cur.fetchone()
                    if stats and (
                        stats["title"] == DEFAULT_CONVERSATION_TITLE
                        or stats["user_message_count"] <= 1
                    ):
                        title_hint = _conversation_title_from_text(content)

                if title_hint:
                    cur.execute(
                        "UPDATE conversations SET title = %s, updated_at = NOW() WHERE id = %s",
                        (title_hint, conversation_id),
                    )
                else:
                    cur.execute(
                        "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                        (conversation_id,),
                    )
            conn.commit()
        return {
            **row,
            "created_at": row["created_at"].isoformat(),
            "feedback_updated_at": (
                row["feedback_updated_at"].isoformat() if row["feedback_updated_at"] else None
            ),
        }

    def get_runtime_state(self, key: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT value
                    FROM app_runtime_state
                    WHERE key = %s
                    """,
                    (key,),
                )
                row = cur.fetchone()
        value = row["value"] if row else None
        return value if isinstance(value, dict) else None

    def set_runtime_state(self, key: str, value: Dict) -> Dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_runtime_state (key, value, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    (key, json.dumps(value or {})),
                )
            conn.commit()
        return value

    def delete_runtime_state(self, key: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM app_runtime_state WHERE key = %s", (key,))
            conn.commit()

    def update_message_feedback(
        self,
        username: str,
        conversation_id: str,
        message_id: str,
        feedback_status: str,
        feedback_note: str = "",
    ) -> Dict:
        if feedback_status not in ALLOWED_FEEDBACK_STATUSES:
            raise ValueError("Estado de feedback inválido.")
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa inválida para este utilizador.")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE messages
                    SET
                        feedback_status = %s,
                        feedback_note = %s,
                        feedback_updated_at = NOW()
                    WHERE id = %s AND conversation_id = %s AND role = 'assistant'
                    RETURNING
                        id::text AS id,
                        conversation_id::text AS conversation_id,
                        role,
                        content,
                        citations,
                        created_at,
                        feedback_status,
                        feedback_note,
                        feedback_updated_at
                    """,
                    (feedback_status, feedback_note.strip(), message_id, conversation_id),
                )
                row = cur.fetchone()
                cur.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                    (conversation_id,),
                )
            conn.commit()
        if not row:
            raise ValueError("Mensagem não encontrada.")
        return {
            **row,
            "created_at": row["created_at"].isoformat(),
            "feedback_updated_at": (
                row["feedback_updated_at"].isoformat() if row["feedback_updated_at"] else None
            ),
        }

    def find_feedback_matches(self, username: str, question: str, limit: int = 3) -> List[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        assistant.id::text AS message_id,
                        assistant.conversation_id::text AS conversation_id,
                        assistant.content AS answer,
                        assistant.citations,
                        assistant.feedback_note,
                        assistant.feedback_updated_at,
                        user_msg.content AS question
                    FROM messages assistant
                    JOIN conversations c ON c.id = assistant.conversation_id
                    JOIN LATERAL (
                        SELECT content
                        FROM messages
                        WHERE conversation_id = assistant.conversation_id
                          AND role = 'user'
                          AND created_at <= assistant.created_at
                        ORDER BY created_at DESC
                        LIMIT 1
                    ) user_msg ON TRUE
                    WHERE c.username = %s
                      AND assistant.role = 'assistant'
                      AND assistant.feedback_status = %s
                    ORDER BY assistant.feedback_updated_at DESC NULLS LAST, assistant.created_at DESC
                    """,
                    (username, FEEDBACK_APPROVED),
                )
                rows = cur.fetchall()

        matches = []
        for row in rows:
            score = _text_similarity(question, row.get("question", ""))
            if score < 0.35:
                continue
            matches.append(
                {
                    **row,
                    "similarity": round(score, 3),
                    "feedback_updated_at": (
                        row["feedback_updated_at"].isoformat() if row["feedback_updated_at"] else None
                    ),
                }
            )
        matches.sort(
            key=lambda item: (
                item["similarity"],
                item.get("feedback_updated_at") or "",
            ),
            reverse=True,
        )
        return matches[:limit]

    def get_port_activity_snapshot(self, window_days: int = 5) -> Dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"{self._port_call_select_clause()} ORDER BY COALESCE(eta, ata, departure_at) ASC NULLS LAST, vessel_name")
                rows = cur.fetchall()
        records = [self._row_to_port_call_record(row) for row in rows]
        return _build_port_activity_snapshot(records, window_days=window_days)

    def get_port_call(self, port_call_id: str) -> Dict:
        with self._connect() as conn:
            payload = self._fetch_port_call_record(conn, port_call_id)
        if not payload:
            raise ValueError("Escala não encontrada.")
        return _decorate_port_call(payload)

    def create_port_call(
        self,
        vessel_name: str,
        eta: str,
        created_by: str,
        constraints: Optional[List[str]] = None,
        berth: str = "",
        last_port: str = "",
        next_port: str = "",
        notes: str = "",
        vessel_short_name: str = "",
        vessel_imo: str = "",
        vessel_call_sign: str = "",
        vessel_flag: str = "",
        vessel_type: str = "",
        vessel_loa_m: str = "",
        vessel_beam_m: str = "",
        vessel_gt_t: str = "",
        vessel_max_draft_m: str = "",
        vessel_dwt_t: str = "",
    ) -> Dict:
        clean_name = _clean_text(vessel_name)
        creator_username = _normalize_username(created_by) or "system"
        creator_profile = self.get_user_profile(creator_username)
        if len(clean_name) < 2:
            raise ValueError("Indica o nome do navio.")
        if not eta.strip():
            raise ValueError("O ETA é obrigatório.")
        vessel_profile = {
            "vessel_short_name": _clean_text(vessel_short_name),
            "vessel_imo": _clean_text(vessel_imo),
            "vessel_call_sign": _clean_text(vessel_call_sign),
            "vessel_flag": _clean_text(vessel_flag),
            "vessel_type": _clean_text(vessel_type),
            "vessel_loa_m": _clean_text(vessel_loa_m),
            "vessel_beam_m": _clean_text(vessel_beam_m),
            "vessel_gt_t": _clean_text(vessel_gt_t),
            "vessel_max_draft_m": _clean_text(vessel_max_draft_m),
            "vessel_dwt_t": _clean_text(vessel_dwt_t),
        }
        _validate_required_vessel_profile(vessel_profile)
        _validate_required_operational_profile(
            {
                "berth": berth,
                "last_port": last_port,
                "next_port": next_port,
            },
            (
                ("berth", "cais previsto"),
                ("last_port", "porto anterior"),
                ("next_port", "próximo destino"),
            ),
        )

        record = {
            "id": str(uuid.uuid4()),
            "vessel_name": clean_name,
            **vessel_profile,
            "status": PORT_CALL_STATUS_SCHEDULED,
            "approval_status": PORT_CALL_APPROVAL_PENDING,
            "approval_note": "",
            "aborted_reason": "",
            "decided_by": None,
            "decided_at": None,
            "eta": eta,
            "ata": None,
            "planned_departure_at": None,
            "departure_plan_note": "",
            "departure_at": None,
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": _clean_text(berth),
            "last_port": _clean_text(last_port),
            "next_port": _clean_text(next_port),
            "created_by": creator_username,
            "created_by_profile": _build_actor_snapshot(creator_profile, username=creator_username),
            "notes": notes.strip(),
            "created_at": iso_now(),
            "updated_at": iso_now(),
        }
        record["maneuver_history"] = [
            _normalize_maneuver_record(
                {
                    "type": "entry",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": eta,
                    "completed_at": None,
                    "origin": record["last_port"],
                    "destination": record["berth"],
                    "plan_note": notes.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": record["created_by"],
                    "created_by_profile": record["created_by_profile"],
                    "created_at": record["created_at"],
                    "updated_at": record["updated_at"],
                },
                fallback_created_by=record["created_by"],
            )
        ]
        record = _sync_port_call_from_history(record)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO port_calls (
                        id, vessel_name, vessel_short_name, vessel_imo, vessel_call_sign, vessel_flag, vessel_type,
                        vessel_loa_m, vessel_beam_m, vessel_gt_t, vessel_max_draft_m, vessel_dwt_t,
                        status, approval_status, approval_note, aborted_reason,
                        decided_by, decided_at, eta, ata, planned_departure_at, departure_plan_note, departure_at,
                        planned_shift_at, shift_plan_note, shift_at, shift_origin_berth, shift_destination_berth,
                        shift_approval_status, shift_approval_note, shift_aborted_reason, shift_decided_by, shift_decided_at,
                        maneuver_history, berth, last_port, next_port, created_by, notes, created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        record["id"],
                        record["vessel_name"],
                        record["vessel_short_name"],
                        record["vessel_imo"],
                        record["vessel_call_sign"],
                        record["vessel_flag"],
                        record["vessel_type"],
                        record["vessel_loa_m"],
                        record["vessel_beam_m"],
                        record["vessel_gt_t"],
                        record["vessel_max_draft_m"],
                        record["vessel_dwt_t"],
                        record["status"],
                        record["approval_status"],
                        record["approval_note"],
                        record["aborted_reason"],
                        record["decided_by"],
                        record["decided_at"],
                        record["eta"],
                        record["ata"],
                        record["planned_departure_at"],
                        record["departure_plan_note"],
                        record["departure_at"],
                        record["planned_shift_at"],
                        record["shift_plan_note"],
                        record["shift_at"],
                        record["shift_origin_berth"],
                        record["shift_destination_berth"],
                        record["shift_approval_status"],
                        record["shift_approval_note"],
                        record["shift_aborted_reason"],
                        record["shift_decided_by"],
                        record["shift_decided_at"],
                        json.dumps(record["maneuver_history"]),
                        record["berth"],
                        record["last_port"],
                        record["next_port"],
                        record["created_by"],
                        record["notes"],
                        record["created_at"],
                        record["updated_at"],
                    ),
                )
            conn.commit()
        return self.get_port_call(record["id"])

    def approve_port_call(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        actor_username = _normalize_username(decided_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            target = None
            if current["status"] == PORT_CALL_STATUS_SCHEDULED:
                target = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_PENDING})
            elif current["status"] == PORT_CALL_STATUS_IN_PORT:
                target = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING})
            else:
                raise ValueError("Só podes aprovar manobras ainda não executadas.")
            if not target:
                raise ValueError("Não existe manobra pendente para aprovar.")
            target["state"] = PORT_CALL_APPROVAL_APPROVED
            target["approval_note"] = approval_note.strip()
            target["aborted_reason"] = ""
            target["decided_by"] = actor_username
            target["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            target["decided_at"] = iso_now()
            target["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)


    def edit_maneuver_plan(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        actor_role: str,
        planned_at: str,
        origin: str,
        destination: str,
        draft_m: str,
        tug_count: str,
        constraints: Optional[List[str]] = None,
        plan_note: str = "",
        change_reason: str,
    ) -> Dict:
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)

        def mutator(current: Dict) -> Dict:
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada na escala.")
            if target.get("state") == "completed":
                raise ValueError("A manobra concluída já só pode ser ajustada no registo.")
            if not _can_edit_maneuver_plan(target, actor_role):
                raise ValueError("Depois de validada, esta manobra só pode ser editada por piloto.")
            target["planned_at"] = planned_at
            target["origin"] = _clean_text(origin)
            target["destination"] = _clean_text(destination)
            target["planned_draft_m"] = (draft_m or "").strip()
            target["tug_count"] = (tug_count or "").strip()
            target["plan_observations"] = (plan_note or "").strip()
            target["constraints"] = normalize_constraint_codes(constraints)
            target["plan_note"] = (plan_note or "").strip()
            target["updated_at"] = iso_now()
            _append_maneuver_change_log(
                target,
                actor_username=actor_username,
                actor_profile=actor_profile,
                reason=change_reason,
                summary="Planeamento atualizado.",
            )
            if target.get("type") == "entry":
                current["last_port"] = target["origin"]
                current["berth"] = target["destination"]
            elif target.get("type") == "departure":
                current["next_port"] = target["destination"]
            elif target.get("type") == "shift":
                current["shift_origin_berth"] = target["origin"]
                current["shift_destination_berth"] = target["destination"]
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def edit_maneuver_report(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        change_reason: str,
    ) -> Dict:
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)

        def mutator(current: Dict) -> Dict:
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada na escala.")
            if target.get("state") != "completed":
                raise ValueError("Só podes editar o registo de manobras já concluídas.")
            target["report_note"] = (notes or "").strip()
            target["execution_started_at"] = maneuver_started_at
            target["execution_finished_at"] = maneuver_finished_at
            target["reported_draft_m"] = draft_m.strip()
            target["reported_by"] = target.get("reported_by") or actor_username
            target["reported_by_profile"] = target.get("reported_by_profile") or _build_actor_snapshot(actor_profile, username=actor_username)
            target["reported_at"] = target.get("reported_at") or iso_now()
            target["updated_at"] = iso_now()
            _append_maneuver_change_log(
                target,
                actor_username=actor_username,
                actor_profile=actor_profile,
                reason=change_reason,
                summary=f"Registo revisto. Calado: {draft_m}.",
            )
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def schedule_departure_plan(
        self,
        port_call_id: str,
        planned_departure_at: str,
        updated_by: str,
        next_port: str = "",
        constraints: Optional[List[str]] = None,
        departure_plan_note: str = "",
    ) -> Dict:
        if not planned_departure_at.strip():
            raise ValueError("A hora prevista de saída é obrigatória.")
        destination = " ".join(next_port.strip().split())
        if not destination:
            raise ValueError("Indica o próximo destino da saída.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            if current["status"] != PORT_CALL_STATUS_IN_PORT:
                raise ValueError("Só podes planear saída para navios que estão em porto.")
            if _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED}):
                raise ValueError("Já existe uma saída ativa para esta escala.")
            departure = _normalize_maneuver_record(
                {
                    "type": "departure",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": planned_departure_at,
                    "completed_at": None,
                    "origin": current.get("berth", ""),
                    "destination": destination,
                    "plan_note": departure_plan_note.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": actor_username or current.get("created_by", "system"),
                    "created_by_profile": _build_actor_snapshot(actor_profile, username=actor_username),
                    "created_at": iso_now(),
                    "updated_at": iso_now(),
                },
                fallback_created_by=actor_username or current.get("created_by", "system"),
            )
            current["maneuver_history"].append(departure)
            current["next_port"] = destination
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def abort_departure_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de aborto da saída é obrigatório.")
        actor_username = _normalize_username(updated_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            departure = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not departure:
                raise ValueError("Não existe manobra de saída planeada para este navio.")
            if not _can_abort_departure_plan({"planned_departure_at": departure.get("planned_at")}):
                raise ValueError("A saída só pode ser abortada com pelo menos 1 hora de antecedência.")
            departure["state"] = PORT_CALL_APPROVAL_ABORTED
            departure["aborted_reason"] = reason
            departure["decided_by"] = actor_username
            departure["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            departure["decided_at"] = iso_now()
            departure["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def schedule_shift_plan(
        self,
        port_call_id: str,
        planned_shift_at: str,
        updated_by: str,
        destination_berth: str,
        constraints: Optional[List[str]] = None,
        shift_plan_note: str = "",
    ) -> Dict:
        if not planned_shift_at.strip():
            raise ValueError("A hora prevista da mudança é obrigatória.")
        destination = " ".join(destination_berth.strip().split())
        if not destination:
            raise ValueError("Indica o cais de destino da mudança.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            if current["status"] != PORT_CALL_STATUS_IN_PORT:
                raise ValueError("Só podes planear mudança para navios em porto.")
            if _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED}):
                raise ValueError("Já existe uma mudança ativa para esta escala.")
            shift = _normalize_maneuver_record(
                {
                    "type": "shift",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": planned_shift_at,
                    "completed_at": None,
                    "origin": current.get("berth", ""),
                    "destination": destination,
                    "plan_note": shift_plan_note.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": actor_username or current.get("created_by", "system"),
                    "created_by_profile": _build_actor_snapshot(actor_profile, username=actor_username),
                    "created_at": iso_now(),
                    "updated_at": iso_now(),
                },
                fallback_created_by=actor_username or current.get("created_by", "system"),
            )
            current["maneuver_history"].append(shift)
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def approve_shift_plan(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        actor_username = _normalize_username(decided_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("Só podes aprovar mudanças ainda não executadas.")
            shift["state"] = PORT_CALL_APPROVAL_APPROVED
            shift["approval_note"] = approval_note.strip()
            shift["aborted_reason"] = ""
            shift["decided_by"] = actor_username
            shift["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["decided_at"] = iso_now()
            shift["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)


    def abort_shift_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de aborto da mudança é obrigatório.")
        actor_username = _normalize_username(updated_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("Não existe manobra de mudança planeada para este navio.")
            if not _can_abort_shift_plan({"planned_shift_at": shift.get("planned_at")}):
                raise ValueError("A mudança só pode ser abortada com pelo menos 1 hora de antecedência.")
            shift["state"] = PORT_CALL_APPROVAL_ABORTED
            shift["aborted_reason"] = reason
            shift["decided_by"] = actor_username
            shift["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["decided_at"] = iso_now()
            shift["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def mark_shift_completed(
        self,
        port_call_id: str,
        shifted_at: str,
        updated_by: str,
    ) -> Dict:
        if not shifted_at.strip():
            raise ValueError("A hora real da mudança é obrigatória.")
        def mutator(current: Dict) -> Dict:
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("A mudança tem de estar aprovada antes de ser concluída.")
            shift["state"] = "completed"
            shift["completed_at"] = shifted_at
            shift["updated_at"] = iso_now()
            if shift.get("destination"):
                current["berth"] = shift["destination"]
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def attach_shift_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            shift = _latest_reportable_maneuver(current.get("maneuver_history", []), "shift")
            if not shift:
                raise ValueError("Só podes registar a mudança depois da manobra estar concluída.")
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            shift["report_note"] = note
            shift["execution_started_at"] = maneuver_started_at
            shift["execution_finished_at"] = maneuver_finished_at
            shift["reported_draft_m"] = draft_m.strip()
            shift["reported_by"] = actor_username
            shift["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["reported_at"] = iso_now()
            shift["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def abort_port_call(
        self,
        port_call_id: str,
        decided_by: str,
        aborted_reason: str,
        approval_note: str = "",
    ) -> Dict:
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de manobra abortada é obrigatório.")
        actor_username = _normalize_username(decided_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_SCHEDULED or not entry:
                raise ValueError("Só podes abortar manobras ainda não executadas.")
            if not _can_abort_port_call({"eta": entry.get("planned_at")}):
                raise ValueError("A manobra só pode ser abortada com pelo menos 2 horas de antecedência.")
            entry["state"] = PORT_CALL_APPROVAL_ABORTED
            entry["approval_note"] = approval_note.strip()
            entry["aborted_reason"] = reason
            entry["decided_by"] = actor_username
            entry["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            entry["decided_at"] = iso_now()
            entry["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def mark_port_call_arrived(
        self,
        port_call_id: str,
        arrived_at: str,
        updated_by: str,
        berth: str = "",
        notes: str = "",
    ) -> Dict:
        if not arrived_at.strip():
            raise ValueError("A hora real de chegada é obrigatória.")
        def mutator(current: Dict) -> Dict:
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_SCHEDULED or not entry:
                raise ValueError("Só podes confirmar entrada de manobras previstas.")
            entry["state"] = "completed"
            entry["completed_at"] = arrived_at
            if berth.strip():
                entry["destination"] = " ".join(berth.strip().split())
                current["berth"] = " ".join(berth.strip().split())
            if notes.strip():
                existing = current.get("notes", "").strip()
                current["notes"] = f"{existing} | {notes.strip()}".strip(" |")
            entry["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def mark_port_call_departed(
        self,
        port_call_id: str,
        departed_at: str,
        updated_by: str,
        next_port: str = "",
        notes: str = "",
    ) -> Dict:
        if not departed_at.strip():
            raise ValueError("A hora de saída é obrigatória.")
        def mutator(current: Dict) -> Dict:
            departure = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not departure:
                raise ValueError("Só podes registar saída de navios que estão em porto e com manobra aprovada.")
            departure["state"] = "completed"
            departure["completed_at"] = departed_at
            if next_port.strip():
                destination = " ".join(next_port.strip().split())
                departure["destination"] = destination
                current["next_port"] = destination
            if notes.strip():
                existing = current.get("notes", "").strip()
                current["notes"] = f"{existing} | {notes.strip()}".strip(" |")
            departure["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def attach_entry_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            entry = _latest_reportable_maneuver(current.get("maneuver_history", []), "entry")
            if not entry:
                raise ValueError("Só podes registar a entrada depois da manobra estar concluída.")
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            entry["report_note"] = note
            entry["execution_started_at"] = maneuver_started_at
            entry["execution_finished_at"] = maneuver_finished_at
            entry["reported_draft_m"] = draft_m.strip()
            entry["reported_by"] = actor_username
            entry["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            entry["reported_at"] = iso_now()
            entry["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def attach_departure_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            departure = _latest_reportable_maneuver(current.get("maneuver_history", []), "departure")
            if not departure:
                raise ValueError("Só podes registar a saída depois da manobra estar concluída.")
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            departure["report_note"] = note
            departure["execution_started_at"] = maneuver_started_at
            departure["execution_finished_at"] = maneuver_finished_at
            departure["reported_draft_m"] = draft_m.strip()
            departure["reported_by"] = actor_username
            departure["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            departure["reported_at"] = iso_now()
            departure["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)


def create_store(data_dir: str, knowledge_dir: str) -> BaseStore:
    backend = os.getenv("APP_STORAGE_BACKEND", "local").strip().lower()
    if backend == "postgres":
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("Define DATABASE_URL para usar APP_STORAGE_BACKEND=postgres.")
        return PostgresStore(database_url=database_url, knowledge_dir=knowledge_dir)
    return LocalStore(data_dir=data_dir, knowledge_dir=knowledge_dir)
