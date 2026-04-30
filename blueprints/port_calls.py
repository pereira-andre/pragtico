"""Port calls blueprint — CRUD, approvals, maneuver plans, reports."""

import json
import logging
import re
from datetime import datetime, timedelta

from flask import Blueprint, Response, flash, redirect, render_template, request, session, url_for

from core import services
from domain.error_catalog import flash_error_message
from core.helpers import (
    build_departure_plan_note,
    build_entry_request_note,
    build_maneuver_context,
    build_pilot_report_note,
    build_scale_context,
    build_shift_plan_note,
    ensure_maneuver_hour_capacity_for_approval,
    ensure_portal_berth_is_available,
    ensure_portal_berth_is_physically_available,
    login_required,
    normalize_portal_berth,
    parse_local_datetime_input,
    port_call_scope_required,
    redirect_to_portal_target,
    require_form_text,
    role_required,
)
from core.portal_notifications import (
    latest_maneuver_by_type,
    maneuver_by_id,
    record_maneuver_notification,
)
from storage import VESSEL_TYPE_LOOKUP, _lookup_key, normalize_constraint_codes
from core.validators import (
    validate_datetime_range,
    validate_operational_feedback_status,
    validate_imo,
    validate_not_past_datetime,
    normalize_thruster_state,
    validate_optional_text,
    validate_positive_number,
    validate_required_text,
    validate_tug_count,
    validate_vessel_dimensions,
)

logger = logging.getLogger(__name__)

bp = Blueprint("port_calls", __name__)

PORT_CALL_JSON_TEMPLATE = {
    "vessel_name": "MSC Lyria",
    "eta": "2026-04-20T14:30:00+01:00",
    "berth": "Secil W",
    "last_port": "Sines",
    "next_port": "Vigo",
    "vessel_imo": "9723345",
    "vessel_call_sign": "CQAN7",
    "vessel_flag": "Madeira",
    "vessel_type": "Graneis sólidos",
    "vessel_loa_m": "189.9",
    "vessel_beam_m": "32.2",
    "vessel_gt_t": "32540",
    "vessel_dwt_t": "38600",
    "vessel_max_draft_m": "11.8",
    "vessel_bow_thruster": "yes",
    "vessel_stern_thruster": "unknown",
    "draft_m": "11.2",
    "tug_count": 2,
    "constraints": ["daylight"],
    "notes": "Janela de maré confirmada com agente.",
}

VESSEL_CATALOG_STATE_KEY = "port_call_vessel_catalog"
VESSEL_CATALOG_DELETED_KEYS_KEY = "deleted_keys"
VESSEL_CATALOG_JSON_TEMPLATE = {
    "vessels": [
        {
            "vessel_name": "MSC Lyria",
            "vessel_imo": "9723345",
            "vessel_call_sign": "CQAN7",
            "vessel_flag": "Madeira",
            "vessel_type": "Graneis sólidos",
            "vessel_loa_m": "189.9",
            "vessel_beam_m": "32.2",
            "vessel_gt_t": "32540",
            "vessel_dwt_t": "38600",
            "vessel_max_draft_m": "11.8",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "unknown",
            "service_rate_profile": "Linha regular",
            "regular_line_calls_365d": "0",
            "pilotage_up_rate": "",
            "tup_reduction_profile": "regular_line",
            "service_notes": "Base 0: o PRAGtico soma as escalas executadas a partir da data de registo durante 365 dias.",
        }
    ]
}
MANEUVER_JSON_TEMPLATE = {
    "maneuvers": [
        {
            "port_call_id": "PTSET26MSCL0001",
            "type": "departure",
            "planned_at": "2026-04-20T19:30:00+01:00",
            "next_port": "Vigo",
            "draft_m": "10.8",
            "tug_count": 2,
            "constraints": ["daylight"],
            "notes": "Saída planeada pelo agente.",
        },
        {
            "reference_code": "PTSET26MSCL0001",
            "type": "shift",
            "planned_at": "2026-04-20T16:00:00+01:00",
            "destination_berth": "TMS 2 - Posição A",
            "draft_m": "10.8",
            "tug_count": 1,
            "constraints": [],
            "notes": "Mudança interna.",
        },
    ]
}
VESSEL_CATALOG_FIELDS = (
    "vessel_name",
    "vessel_imo",
    "vessel_call_sign",
    "vessel_flag",
    "vessel_type",
    "vessel_loa_m",
    "vessel_beam_m",
    "vessel_gt_t",
    "vessel_dwt_t",
    "vessel_max_draft_m",
    "vessel_bow_thruster",
    "vessel_stern_thruster",
    "service_rate_profile",
    "regular_line_calls_365d",
    "pilotage_up_rate",
    "tup_reduction_profile",
    "service_notes",
)
VESSEL_CATALOG_IMPORT_ALIASES = {
    "vessel_name": ("vessel_name", "name", "ship_name"),
    "vessel_imo": ("vessel_imo", "imo"),
    "vessel_call_sign": ("vessel_call_sign", "call_sign", "callsign"),
    "vessel_flag": ("vessel_flag", "flag"),
    "vessel_type": ("vessel_type", "ship_type", "type"),
    "vessel_loa_m": ("vessel_loa_m", "loa", "loa_m"),
    "vessel_beam_m": ("vessel_beam_m", "beam", "beam_m"),
    "vessel_gt_t": ("vessel_gt_t", "gt", "gt_t"),
    "vessel_dwt_t": ("vessel_dwt_t", "dwt", "dwt_t"),
    "vessel_max_draft_m": ("vessel_max_draft_m", "max_draft", "max_draft_m"),
    "vessel_bow_thruster": ("vessel_bow_thruster", "bow_thruster"),
    "vessel_stern_thruster": ("vessel_stern_thruster", "stern_thruster"),
    "service_rate_profile": ("service_rate_profile", "tax_profile", "service_profile"),
    "regular_line_calls_365d": ("regular_line_calls_365d", "scale_count_365d", "calls_365d"),
    "pilotage_up_rate": ("pilotage_up_rate", "custom_up_rate"),
    "tup_reduction_profile": ("tup_reduction_profile", "tup_profile"),
    "service_notes": ("service_notes", "tax_notes"),
}


def _demo_future_iso(days: int, *, hour: int, minute: int = 0) -> str:
    now = datetime.now().astimezone()
    candidate = (now + timedelta(days=days)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def _build_port_call_json_template() -> dict:
    return {
        **PORT_CALL_JSON_TEMPLATE,
        "eta": _demo_future_iso(2, hour=14, minute=30),
    }


def _build_maneuver_json_template(port_call: dict | None = None) -> dict:
    reference_code = (port_call or {}).get("reference_code") or "PTSET26MSCL0001"
    return {
        "maneuvers": [
            {
                "reference_code": reference_code,
                "type": "departure",
                "planned_at": _demo_future_iso(2, hour=19, minute=30),
                "next_port": (port_call or {}).get("next_port") or "Vigo",
                "draft_m": "10.8",
                "tug_count": 2,
                "constraints": ["daylight"],
                "notes": "Saída planeada pelo agente.",
            },
            {
                "reference_code": reference_code,
                "type": "shift",
                "planned_at": _demo_future_iso(3, hour=10),
                "destination_berth": "TMS 2 - Posição A",
                "draft_m": "10.8",
                "tug_count": 1,
                "constraints": [],
                "notes": "Mudança interna.",
            },
        ]
    }


def _emit_maneuver_notification(
    *,
    port_call: dict,
    maneuver: dict | None,
    event_type: str,
    actor_username: str,
    previous_maneuver: dict | None = None,
) -> None:
    try:
        record_maneuver_notification(
            port_call=port_call,
            maneuver=maneuver,
            event_type=event_type,
            actor_username=actor_username,
            previous_maneuver=previous_maneuver,
        )
    except Exception:
        logger.exception(
            "Falha inesperada ao publicar notificacao live para %s/%s.",
            port_call.get("id"),
            event_type,
        )


def _ensure_maneuver_destination_can_be_approved(
    port_call: dict,
    maneuver_type: str,
    *,
    label: str = "Cais destino",
) -> None:
    """Validate berth occupation at pilot approval time for entry/shift maneuvers."""
    maneuver = latest_maneuver_by_type(port_call, maneuver_type)
    if not maneuver or maneuver.get("state") != "pending":
        return
    if maneuver_type not in {"entry", "shift"}:
        return
    ensure_portal_berth_is_available(
        maneuver.get("destination", ""),
        current_port_call_id=port_call.get("id", ""),
        label=label,
        target_planned_at=maneuver.get("planned_at"),
    )


def _iso_to_local_input_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


def _build_scale_edit_defaults(port_call: dict) -> dict:
    catalog_record = _catalog_record_for_vessel(port_call)
    return {
        "vessel_name": port_call.get("vessel_name", ""),
        "eta_local": _iso_to_local_input_value(port_call.get("eta")),
        "berth": port_call.get("berth", ""),
        "last_port": port_call.get("last_port", ""),
        "next_port": port_call.get("next_port", ""),
        "vessel_imo": port_call.get("vessel_imo", ""),
        "vessel_call_sign": port_call.get("vessel_call_sign", ""),
        "vessel_flag": port_call.get("vessel_flag", ""),
        "vessel_type": port_call.get("vessel_type", ""),
        "vessel_loa_m": port_call.get("vessel_loa_m", ""),
        "vessel_beam_m": port_call.get("vessel_beam_m", ""),
        "vessel_gt_t": port_call.get("vessel_gt_t", ""),
        "vessel_dwt_t": port_call.get("vessel_dwt_t", ""),
        "vessel_max_draft_m": port_call.get("vessel_max_draft_m", ""),
        "vessel_bow_thruster": port_call.get("vessel_bow_thruster", "unknown"),
        "vessel_stern_thruster": port_call.get("vessel_stern_thruster", "unknown"),
        "service_rate_profile": catalog_record.get("service_rate_profile", ""),
        "regular_line_calls_365d": catalog_record.get("regular_line_calls_365d", ""),
        "pilotage_up_rate": catalog_record.get("pilotage_up_rate", ""),
        "tup_reduction_profile": catalog_record.get("tup_reduction_profile", ""),
        "service_notes": catalog_record.get("service_notes", ""),
        "notes": port_call.get("notes", ""),
    }


def _first_payload_value(payload: dict, *keys: str, default=""):
    for key in keys:
        if key in payload and payload.get(key) is not None:
            value = payload.get(key)
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return default


def _string_payload_value(payload: dict, *keys: str, default: str = "") -> str:
    value = _first_payload_value(payload, *keys, default=default)
    if value is None:
        return default
    return str(value).strip()


def _integer_payload_value(payload: dict, *keys: str, default: str = "") -> str:
    value = _string_payload_value(payload, *keys, default=default)
    if not value:
        return ""
    try:
        numeric_value = float(value.replace(",", "."))
    except ValueError as exc:
        raise ValueError("Base linha regular deve ser um número inteiro.") from exc
    if not numeric_value.is_integer():
        raise ValueError("Base linha regular deve ser um número inteiro.")
    number = int(numeric_value)
    if number < 0 or number > 999:
        raise ValueError("Base linha regular deve estar entre 0 e 999.")
    return str(number)


def _canonical_vessel_type_label(value: str) -> str:
    clean = require_form_text(value, "Tipo de navio")
    meta = VESSEL_TYPE_LOOKUP.get(_lookup_key(clean))
    if not meta:
        raise ValueError("Tipo de navio inválido. Escolhe um dos tipos com ícone definidos no sistema.")
    return meta["label"]


def _vessel_type_meta(value: str | None) -> dict:
    return (
        VESSEL_TYPE_LOOKUP.get(_lookup_key(value))
        or VESSEL_TYPE_LOOKUP.get(_lookup_key("Restantes"))
        or {"label": "Restantes", "icon": ""}
    )


def _load_json_upload_payload(label: str) -> object:
    uploaded_file = request.files.get("payload_file")
    raw_payload = ""
    if uploaded_file and uploaded_file.filename:
        raw_payload = uploaded_file.read().decode("utf-8-sig")
    if not raw_payload.strip():
        raw_payload = request.form.get("payload_json", "")
    if not raw_payload.strip():
        raise ValueError(f"Indica o JSON de {label} ou carrega um ficheiro .json.")
    relaxed_payload = raw_payload
    relaxed_payload = re.sub(r'("constraints"\s*:\s*)(?=(,|\}))', r"\1[]", relaxed_payload)
    relaxed_payload = re.sub(r",(\s*[}\]])", r"\1", relaxed_payload)
    try:
        return json.loads(relaxed_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido na linha {exc.lineno}, coluna {exc.colno}.") from exc


def _payload_list_from_json(
    payload: object,
    *,
    plural_keys: tuple[str, ...],
    singular_keys: tuple[str, ...],
    label: str,
) -> list[dict]:
    if isinstance(payload, dict):
        for key in plural_keys:
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            for key in singular_keys:
                if isinstance(payload.get(key), dict):
                    payload = [payload[key]]
                    break
            else:
                payload = [payload]
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"O JSON de {label} tem de conter um objeto ou uma lista de objetos.")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"Cada item no JSON de {label} tem de ser um objeto.")
    return payload


def _vessel_catalog_key(record: dict) -> str:
    imo = re.sub(r"\D", "", _string_payload_value(record, "vessel_imo"))
    if imo:
        return f"imo:{imo}"
    vessel_name = re.sub(r"\s+", " ", _string_payload_value(record, "vessel_name")).casefold()
    return f"name:{vessel_name}" if vessel_name else ""


def _coerce_vessel_catalog_payload(payload: dict) -> dict:
    return {
        "vessel_name": _string_payload_value(payload, "vessel_name", "name", "ship_name"),
        "vessel_imo": _string_payload_value(payload, "vessel_imo", "imo"),
        "vessel_call_sign": _string_payload_value(payload, "vessel_call_sign", "call_sign", "callsign"),
        "vessel_flag": _string_payload_value(payload, "vessel_flag", "flag"),
        "vessel_type": _string_payload_value(payload, "vessel_type", "ship_type", "type"),
        "vessel_loa_m": _string_payload_value(payload, "vessel_loa_m", "loa", "loa_m"),
        "vessel_beam_m": _string_payload_value(payload, "vessel_beam_m", "beam", "beam_m"),
        "vessel_gt_t": _string_payload_value(payload, "vessel_gt_t", "gt", "gt_t"),
        "vessel_dwt_t": _string_payload_value(payload, "vessel_dwt_t", "dwt", "dwt_t"),
        "vessel_max_draft_m": _string_payload_value(payload, "vessel_max_draft_m", "max_draft", "draft_m"),
        "vessel_bow_thruster": _first_payload_value(
            payload, "vessel_bow_thruster", "bow_thruster", default="unknown"
        ),
        "vessel_stern_thruster": _first_payload_value(
            payload, "vessel_stern_thruster", "stern_thruster", default="unknown"
        ),
        "service_rate_profile": _string_payload_value(payload, "service_rate_profile", "tax_profile", "service_profile"),
        "regular_line_calls_365d": _integer_payload_value(
            payload, "regular_line_calls_365d", "scale_count_365d", "calls_365d"
        ),
        "regular_line_calls_anchor_at": _string_payload_value(
            payload, "regular_line_calls_anchor_at", "regular_line_anchor_at", "calls_anchor_at"
        ),
        "pilotage_up_rate": validate_positive_number(
            _string_payload_value(payload, "pilotage_up_rate", "custom_up_rate"),
            "UP pilotagem",
            required=False,
            max_value=9999.0,
        ),
        "tup_reduction_profile": _string_payload_value(payload, "tup_reduction_profile", "tup_profile"),
        "service_notes": validate_optional_text(
            _string_payload_value(payload, "service_notes", "tax_notes", "notes")
        ),
    }


def _validate_vessel_catalog_record(payload: dict) -> dict:
    record = _coerce_vessel_catalog_payload(payload)
    record["vessel_name"] = require_form_text(record["vessel_name"], "Nome do navio")
    record["vessel_imo"] = validate_imo(record["vessel_imo"])
    record["vessel_call_sign"] = require_form_text(record["vessel_call_sign"], "Indicativo")
    record["vessel_flag"] = require_form_text(record["vessel_flag"], "Bandeira")
    record["vessel_type"] = _canonical_vessel_type_label(record["vessel_type"])
    record.update(validate_vessel_dimensions(record))
    record["vessel_bow_thruster"] = normalize_thruster_state(record.get("vessel_bow_thruster"), "Bow thruster")
    record["vessel_stern_thruster"] = normalize_thruster_state(record.get("vessel_stern_thruster"), "Stern thruster")
    return record


def _read_vessel_catalog_records() -> list[dict]:
    state = _read_vessel_catalog_state()
    records = state.get("items") or []
    return [item for item in records if isinstance(item, dict)]


def _read_vessel_catalog_state() -> dict:
    state = services.store.get_runtime_state(VESSEL_CATALOG_STATE_KEY) or {}
    return state if isinstance(state, dict) else {}


def _read_deleted_vessel_catalog_keys() -> set[str]:
    state = _read_vessel_catalog_state()
    return {
        str(key)
        for key in (state.get(VESSEL_CATALOG_DELETED_KEYS_KEY) or [])
        if str(key).strip()
    }


def _write_vessel_catalog_records(records: list[dict], *, deleted_keys: set[str] | None = None) -> None:
    if deleted_keys is None:
        deleted_keys = _read_deleted_vessel_catalog_keys()
    services.store.set_runtime_state(
        VESSEL_CATALOG_STATE_KEY,
        {
            "version": 2,
            "items": records,
            VESSEL_CATALOG_DELETED_KEYS_KEY: sorted(deleted_keys),
        },
    )


def _catalog_record_for_vessel(record: dict) -> dict:
    key = _vessel_catalog_key(record)
    if not key or key in _read_deleted_vessel_catalog_keys():
        return {}
    for current in _read_vessel_catalog_records():
        current_key = current.get("key") or _vessel_catalog_key(current)
        if current_key == key:
            return current
    return {}


def _catalog_record_for_import_payload(payload: dict, coerced: dict | None = None) -> dict:
    deleted_keys = _read_deleted_vessel_catalog_keys()
    payload_imo = re.sub(r"\D", "", _string_payload_value(payload, "vessel_imo", "imo"))
    if coerced and not payload_imo:
        payload_imo = re.sub(r"\D", "", _string_payload_value(coerced, "vessel_imo"))
    payload_call_sign = _string_payload_value(payload, "vessel_call_sign", "call_sign", "callsign").casefold()
    if coerced and not payload_call_sign:
        payload_call_sign = _string_payload_value(coerced, "vessel_call_sign").casefold()
    payload_name = _string_payload_value(payload, "vessel_name", "name", "ship_name").casefold()
    if coerced and not payload_name:
        payload_name = _string_payload_value(coerced, "vessel_name").casefold()

    for current in _read_vessel_catalog_records():
        current_key = current.get("key") or _vessel_catalog_key(current)
        if current_key in deleted_keys:
            continue
        current_imo = re.sub(r"\D", "", _string_payload_value(current, "vessel_imo"))
        if payload_imo and current_imo and payload_imo == current_imo:
            return current
        current_call_sign = _string_payload_value(current, "vessel_call_sign", "call_sign").casefold()
        if payload_call_sign and current_call_sign and payload_call_sign == current_call_sign:
            return current
        current_name = _string_payload_value(current, "vessel_name", "name").casefold()
        if payload_name and current_name and payload_name == current_name:
            return current
    return {}


def _payload_has_catalog_field(payload: dict, field: str) -> bool:
    for key in VESSEL_CATALOG_IMPORT_ALIASES.get(field, (field,)):
        if key not in payload:
            continue
        value = payload.get(key)
        if value is not None and str(value).strip():
            return True
    return False


def _coerce_port_call_payload_with_catalog(payload: dict) -> dict:
    form_data = _coerce_port_call_payload(payload)
    catalog_record = _catalog_record_for_import_payload(payload, form_data)
    if not catalog_record:
        return form_data

    for field in VESSEL_CATALOG_FIELDS:
        catalog_value = catalog_record.get(field)
        if catalog_value in {"", None}:
            continue
        if field in {"vessel_bow_thruster", "vessel_stern_thruster"}:
            if not _payload_has_catalog_field(payload, field) and form_data.get(field) in {"", None, "unknown"}:
                form_data[field] = catalog_value
            continue
        if not str(form_data.get(field) or "").strip():
            form_data[field] = catalog_value
    return form_data


def _remove_vessel_catalog_record(key: str, *, hide: bool) -> dict:
    clean_key = str(key or "").strip()
    if not clean_key:
        raise ValueError("Navio não identificado.")
    records = _read_vessel_catalog_records()
    deleted_keys = _read_deleted_vessel_catalog_keys()
    kept: list[dict] = []
    removed: dict = {}
    for current in records:
        current_key = current.get("key") or _vessel_catalog_key(current)
        if current_key == clean_key:
            removed = current
            continue
        kept.append(current)
    if hide:
        deleted_keys.add(clean_key)
    else:
        deleted_keys.discard(clean_key)
    _write_vessel_catalog_records(kept, deleted_keys=deleted_keys)
    return removed or {"key": clean_key}


def _parse_catalog_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _catalog_date_label(value: object) -> str:
    parsed = _parse_catalog_datetime(value)
    return parsed.strftime("%d/%m/%Y") if parsed else "--"


def _catalog_port_call_count_datetime(port_call: dict) -> datetime | None:
    status = (port_call.get("status") or "").strip().lower()
    if status == "departed":
        keys = ("departure_at", "ata", "eta")
    elif status == "in_port":
        keys = ("ata", "eta")
    else:
        return None
    for key in keys:
        parsed = _parse_catalog_datetime(port_call.get(key))
        if parsed:
            return parsed
    return None


def _regular_line_counter_meta(record: dict, port_calls: list[dict], *, now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    try:
        base_calls = int(float(str(record.get("regular_line_calls_365d") or "0").replace(",", ".")))
    except ValueError:
        base_calls = 0
    anchor_at = (
        _parse_catalog_datetime(record.get("regular_line_calls_anchor_at"))
        or _parse_catalog_datetime(record.get("updated_at"))
        or _parse_catalog_datetime(record.get("created_at"))
    )
    base_expires_at = anchor_at + timedelta(days=365) if anchor_at else None
    base_is_active = bool(anchor_at and base_expires_at and now < base_expires_at)
    effective_base = base_calls if base_is_active else 0
    count_start = anchor_at if base_is_active and anchor_at else now - timedelta(days=365)
    counted_scales = 0
    for port_call in port_calls:
        counted_at = _catalog_port_call_count_datetime(port_call)
        if counted_at and count_start <= counted_at <= now:
            counted_scales += 1
    effective_calls = effective_base + counted_scales
    return {
        "regular_line_calls_365d": str(effective_base) if base_calls and not base_is_active else record.get("regular_line_calls_365d", ""),
        "regular_line_base_calls_365d": effective_base,
        "regular_line_counted_scales_365d": counted_scales,
        "regular_line_effective_calls_365d": effective_calls,
        "regular_line_calls_anchor_at": anchor_at.isoformat() if anchor_at else record.get("regular_line_calls_anchor_at", ""),
        "regular_line_calls_anchor_label": _catalog_date_label(anchor_at),
        "regular_line_calls_expires_at": base_expires_at.isoformat() if base_expires_at else "",
        "regular_line_calls_expires_label": _catalog_date_label(base_expires_at),
        "regular_line_base_expired": bool(base_calls and not base_is_active),
    }


def _delete_all_vessel_catalog_records() -> int:
    vessels = _build_vessel_catalog_options(services.store.get_port_activity_snapshot(window_days=3650))
    deleted_keys = _read_deleted_vessel_catalog_keys()
    removed_count = 0
    for vessel in vessels:
        key = vessel.get("key") or _vessel_catalog_key(vessel)
        if not key:
            continue
        deleted_keys.add(key)
        removed_count += 1
    _write_vessel_catalog_records([], deleted_keys=deleted_keys)
    return removed_count


def _upsert_vessel_catalog_record(payload: dict, *, updated_by: str, validate: bool = True) -> dict:
    record = _validate_vessel_catalog_record(payload) if validate else _coerce_vessel_catalog_payload(payload)
    if not record.get("vessel_name") or not record.get("vessel_imo"):
        return record
    key = _vessel_catalog_key(record)
    records = _read_vessel_catalog_records()
    deleted_keys = _read_deleted_vessel_catalog_keys()
    deleted_keys.discard(key)
    now_dt = datetime.now().astimezone()
    now = now_dt.isoformat()
    saved = {
        **record,
        "key": key,
        "updated_by": updated_by,
        "updated_at": now,
    }
    if not saved.get("regular_line_calls_anchor_at"):
        saved["regular_line_calls_anchor_at"] = now
    replaced = False
    for index, current in enumerate(records):
        if (current.get("key") or _vessel_catalog_key(current)) != key:
            continue
        created_at = current.get("created_at") or now
        current_anchor = _parse_catalog_datetime(current.get("regular_line_calls_anchor_at"))
        current_anchor_active = bool(current_anchor and now_dt < current_anchor + timedelta(days=365))
        if (
            str(current.get("regular_line_calls_365d") or "")
            == str(saved.get("regular_line_calls_365d") or "")
            and current.get("regular_line_calls_anchor_at")
            and current_anchor_active
        ):
            saved["regular_line_calls_anchor_at"] = current["regular_line_calls_anchor_at"]
        else:
            saved["regular_line_calls_anchor_at"] = now
        saved_values = {
            field: value
            for field, value in saved.items()
            if value not in {"", None}
        }
        merged = {**current, **saved_values}
        merged["created_at"] = created_at
        merged["updated_at"] = now
        merged["updated_by"] = updated_by
        records[index] = merged
        saved = merged
        replaced = True
        break
    if not replaced:
        saved["created_at"] = now
        records.append(saved)
    records.sort(
        key=lambda item: (item.get("vessel_name", "").casefold(), item.get("vessel_imo", ""))
    )
    _write_vessel_catalog_records(records, deleted_keys=deleted_keys)
    return saved


def _port_call_matches_vessel_catalog_record(port_call: dict, record: dict, *, keys: set[str] | None = None) -> bool:
    port_call_key = _vessel_catalog_key(_vessel_catalog_record_from_port_call(port_call))
    if keys and port_call_key in keys:
        return True
    record_imo = re.sub(r"\D", "", _string_payload_value(record, "vessel_imo"))
    port_call_imo = re.sub(r"\D", "", _string_payload_value(port_call, "vessel_imo"))
    if record_imo and port_call_imo and record_imo == port_call_imo:
        return True
    record_call_sign = _string_payload_value(record, "vessel_call_sign", "call_sign").casefold()
    port_call_call_sign = _string_payload_value(port_call, "vessel_call_sign", "call_sign").casefold()
    if record_call_sign and port_call_call_sign and record_call_sign == port_call_call_sign:
        return True
    record_name = _string_payload_value(record, "vessel_name", "name").casefold()
    port_call_name = _string_payload_value(port_call, "vessel_name", "name").casefold()
    return bool(record_name and port_call_name and record_name == port_call_name)


def _sync_vessel_catalog_record_to_active_port_calls(
    record: dict,
    *,
    updated_by: str,
    previous_key: str = "",
) -> int:
    keys = {_vessel_catalog_key(record)}
    if previous_key:
        keys.add(previous_key)
    keys = {key for key in keys if key}
    synced = 0
    try:
        port_calls = services.store.list_port_calls()
    except Exception:
        logger.exception("Falha ao listar escalas para sincronizar ficha de navio.")
        return synced

    for port_call in port_calls:
        if port_call.get("status") not in {"scheduled", "in_port"}:
            continue
        if not _port_call_matches_vessel_catalog_record(port_call, record, keys=keys):
            continue
        try:
            services.store.edit_port_call(
                port_call_id=port_call["id"],
                updated_by=updated_by,
                vessel_name=record.get("vessel_name") or port_call.get("vessel_name", ""),
                eta=port_call.get("eta") or "",
                berth=port_call.get("berth") or port_call.get("berth_label", ""),
                last_port=port_call.get("last_port", ""),
                next_port=port_call.get("next_port", ""),
                notes=port_call.get("notes", ""),
                vessel_imo=record.get("vessel_imo") or port_call.get("vessel_imo", ""),
                vessel_call_sign=record.get("vessel_call_sign") or port_call.get("vessel_call_sign", ""),
                vessel_flag=record.get("vessel_flag") or port_call.get("vessel_flag", ""),
                vessel_type=record.get("vessel_type") or port_call.get("vessel_type", ""),
                vessel_loa_m=record.get("vessel_loa_m") or port_call.get("vessel_loa_m", ""),
                vessel_beam_m=record.get("vessel_beam_m") or port_call.get("vessel_beam_m", ""),
                vessel_gt_t=record.get("vessel_gt_t") or port_call.get("vessel_gt_t", ""),
                vessel_max_draft_m=record.get("vessel_max_draft_m") or port_call.get("vessel_max_draft_m", ""),
                vessel_dwt_t=record.get("vessel_dwt_t") or port_call.get("vessel_dwt_t", ""),
                vessel_bow_thruster=record.get("vessel_bow_thruster") or port_call.get("vessel_bow_thruster", "unknown"),
                vessel_stern_thruster=record.get("vessel_stern_thruster") or port_call.get("vessel_stern_thruster", "unknown"),
                change_reason="Atualização da ficha do navio no catálogo.",
            )
            synced += 1
        except Exception:
            logger.exception("Falha ao sincronizar ficha de navio com escala %s.", port_call.get("id"))
    return synced


def _load_vessel_catalog_json_payload() -> list[dict]:
    return _payload_list_from_json(
        _load_json_upload_payload("navios"),
        plural_keys=("vessels", "ships", "items"),
        singular_keys=("vessel", "ship"),
        label="navios",
    )


def _vessel_catalog_record_from_port_call(port_call: dict) -> dict:
    return {
        "vessel_name": port_call.get("vessel_name", ""),
        "vessel_imo": port_call.get("vessel_imo", ""),
        "vessel_call_sign": port_call.get("vessel_call_sign", ""),
        "vessel_flag": port_call.get("vessel_flag", ""),
        "vessel_type": port_call.get("vessel_type", ""),
        "vessel_loa_m": port_call.get("vessel_loa_m", ""),
        "vessel_beam_m": port_call.get("vessel_beam_m", ""),
        "vessel_gt_t": port_call.get("vessel_gt_t", ""),
        "vessel_dwt_t": port_call.get("vessel_dwt_t", ""),
        "vessel_max_draft_m": port_call.get("vessel_max_draft_m", ""),
        "vessel_bow_thruster": port_call.get("vessel_bow_thruster", "unknown"),
        "vessel_stern_thruster": port_call.get("vessel_stern_thruster", "unknown"),
    }


def _build_vessel_catalog_options(activity: dict | None = None) -> list[dict]:
    records_by_key = {}
    deleted_keys = _read_deleted_vessel_catalog_keys()
    for record in _read_vessel_catalog_records():
        key = record.get("key") or _vessel_catalog_key(record)
        if key in deleted_keys:
            continue
        if key:
            records_by_key[key] = {**record, "key": key, "scale_count": 0, "_catalog_port_calls": []}

    if activity is None:
        try:
            activity = services.store.get_port_activity_snapshot(window_days=3650)
        except Exception:
            logger.exception("Falha ao construir catálogo de navios a partir das escalas.")
            activity = {}
    seen_scale_ids = set()
    for group_name in ("arrivals", "in_port", "departed", "aborted"):
        for port_call in activity.get(group_name, []) or []:
            port_call_id = port_call.get("id")
            if not port_call_id or port_call_id in seen_scale_ids:
                continue
            seen_scale_ids.add(port_call_id)
            record = _vessel_catalog_record_from_port_call(port_call)
            key = _vessel_catalog_key(record)
            if not key or key in deleted_keys:
                continue
            current = records_by_key.setdefault(key, {**record, "key": key, "scale_count": 0, "_catalog_port_calls": []})
            current["scale_count"] = int(current.get("scale_count") or 0) + 1
            current.setdefault("_catalog_port_calls", []).append(port_call)
            for field in VESSEL_CATALOG_FIELDS:
                if not current.get(field) and record.get(field):
                    current[field] = record[field]
            current["latest_scale_reference"] = port_call.get(
                "reference_code", current.get("latest_scale_reference", "")
            )
            current["latest_eta_label"] = port_call.get("eta_label", current.get("latest_eta_label", ""))

    options = list(records_by_key.values())
    now = datetime.now().astimezone()
    for item in options:
        port_calls = item.pop("_catalog_port_calls", [])
        item.update(_regular_line_counter_meta(item, port_calls, now=now))
        vessel_type_meta = _vessel_type_meta(item.get("vessel_type"))
        item["vessel_type_label"] = vessel_type_meta.get("label", item.get("vessel_type") or "Restantes")
        item["vessel_type_icon"] = vessel_type_meta.get("icon", "")
    options.sort(
        key=lambda item: (item.get("vessel_name", "").casefold(), item.get("vessel_imo", ""))
    )
    return options


def _filter_vessel_catalog_options(vessels: list[dict], *, q: str = "", vessel_type: str = "") -> list[dict]:
    query = _lookup_key(q)
    type_key = _lookup_key(vessel_type)
    filtered: list[dict] = []
    for vessel in vessels:
        current_type_key = _lookup_key(vessel.get("vessel_type_label") or vessel.get("vessel_type"))
        if type_key and current_type_key != type_key:
            continue
        if query:
            haystack = _lookup_key(
                " ".join(
                    str(vessel.get(field) or "")
                    for field in (
                        "vessel_name",
                        "vessel_imo",
                        "vessel_call_sign",
                        "vessel_flag",
                        "vessel_type",
                        "vessel_type_label",
                        "latest_scale_reference",
                    )
                )
            )
            if query not in haystack:
                continue
        filtered.append(vessel)
    return filtered


def _vessel_catalog_summary(vessels: list[dict], filtered_vessels: list[dict]) -> dict:
    type_count = len(
        {
            vessel.get("vessel_type_label") or vessel.get("vessel_type") or "Restantes"
            for vessel in vessels
        }
    )
    return {
        "total_count": len(vessels),
        "filtered_count": len(filtered_vessels),
        "type_count": type_count,
        "scale_count": sum(int(vessel.get("scale_count") or 0) for vessel in vessels),
    }


def _coerce_port_call_payload(payload: dict) -> dict:
    return {
        "vessel_name": _string_payload_value(payload, "vessel_name"),
        "vessel_short_name": _string_payload_value(payload, "vessel_short_name"),
        "vessel_imo": _string_payload_value(payload, "vessel_imo"),
        "vessel_call_sign": _string_payload_value(payload, "vessel_call_sign"),
        "vessel_flag": _string_payload_value(payload, "vessel_flag"),
        "vessel_type": _string_payload_value(payload, "vessel_type"),
        "vessel_loa_m": _string_payload_value(payload, "vessel_loa_m"),
        "vessel_beam_m": _string_payload_value(payload, "vessel_beam_m"),
        "vessel_gt_t": _string_payload_value(payload, "vessel_gt_t"),
        "vessel_max_draft_m": _string_payload_value(payload, "vessel_max_draft_m"),
        "vessel_dwt_t": _string_payload_value(payload, "vessel_dwt_t"),
        "vessel_bow_thruster": _first_payload_value(payload, "vessel_bow_thruster", default="unknown"),
        "vessel_stern_thruster": _first_payload_value(payload, "vessel_stern_thruster", default="unknown"),
        "service_rate_profile": _string_payload_value(payload, "service_rate_profile"),
        "regular_line_calls_365d": _string_payload_value(payload, "regular_line_calls_365d"),
        "pilotage_up_rate": _string_payload_value(payload, "pilotage_up_rate"),
        "tup_reduction_profile": _string_payload_value(payload, "tup_reduction_profile"),
        "service_notes": _string_payload_value(payload, "service_notes"),
        "eta_local": _string_payload_value(payload, "eta_local", "eta"),
        "berth": _string_payload_value(payload, "berth"),
        "last_port": _string_payload_value(payload, "last_port"),
        "next_port": _string_payload_value(payload, "next_port"),
        "draft_m": _string_payload_value(payload, "draft_m"),
        "constraints": _first_payload_value(payload, "constraints", default=[]),
        "tug_count": _string_payload_value(payload, "tug_count"),
        "notes": _string_payload_value(payload, "notes"),
    }


def _create_port_call_from_payload(form_data: dict, *, created_by: str) -> dict:
    eta = parse_local_datetime_input(form_data["eta_local"], "ETA")
    validate_not_past_datetime(eta, "ETA")
    berth = normalize_portal_berth(form_data["berth"], "Cais previsto")
    last_port = require_form_text(form_data["last_port"], "Porto anterior")
    next_port = require_form_text(form_data["next_port"], "Próximo destino")
    draft_m = validate_positive_number(form_data["draft_m"], "Calado (m)", max_value=30.0)
    tug_count = validate_tug_count(form_data["tug_count"])
    validate_imo(form_data["vessel_imo"])
    form_data["vessel_type"] = _canonical_vessel_type_label(form_data["vessel_type"])
    form_data["constraints"] = normalize_constraint_codes(form_data.get("constraints"))
    validated_dims = validate_vessel_dimensions(form_data)
    form_data.update(validated_dims)
    form_data["vessel_bow_thruster"] = normalize_thruster_state(form_data.get("vessel_bow_thruster"), "Bow thruster")
    form_data["vessel_stern_thruster"] = normalize_thruster_state(form_data.get("vessel_stern_thruster"), "Stern thruster")
    return services.store.create_port_call(
        vessel_name=form_data["vessel_name"],
        eta=eta,
        created_by=created_by,
        constraints=form_data["constraints"],
        berth=berth,
        last_port=last_port,
        next_port=next_port,
        vessel_short_name=form_data["vessel_short_name"],
        vessel_imo=form_data["vessel_imo"],
        vessel_call_sign=form_data["vessel_call_sign"],
        vessel_flag=form_data["vessel_flag"],
        vessel_type=form_data["vessel_type"],
        vessel_loa_m=form_data["vessel_loa_m"],
        vessel_beam_m=form_data["vessel_beam_m"],
        vessel_gt_t=form_data["vessel_gt_t"],
        vessel_max_draft_m=form_data["vessel_max_draft_m"],
        vessel_dwt_t=form_data["vessel_dwt_t"],
        vessel_bow_thruster=form_data["vessel_bow_thruster"],
        vessel_stern_thruster=form_data["vessel_stern_thruster"],
        notes=build_entry_request_note({**form_data, "draft_m": draft_m, "tug_count": tug_count}),
    )


def _load_port_call_json_payloads() -> list[dict]:
    return _payload_list_from_json(
        _load_json_upload_payload("escalas"),
        plural_keys=("port_calls", "scales", "items"),
        singular_keys=("port_call", "scale"),
        label="escalas",
    )


def _load_maneuver_json_payloads() -> list[dict]:
    return _payload_list_from_json(
        _load_json_upload_payload("manobras"),
        plural_keys=("maneuvers", "manoeuvres", "items"),
        singular_keys=("maneuver", "manoeuvre"),
        label="manobras",
    )


def _json_download_response(payload: dict, filename: str) -> Response:
    body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return Response(
        body,
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _export_filename(stem: str) -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M")
    return f"pragtico-{stem}-{stamp}.json"


def _port_call_lookup(records: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for port_call in records:
        for value in (
            port_call.get("id"),
            port_call.get("reference_code"),
            port_call.get("scale_reference"),
        ):
            key = str(value or "").strip()
            if key:
                lookup[key] = port_call
    return lookup


def _resolve_port_call_for_maneuver(payload: dict, lookup: dict[str, dict]) -> dict:
    for key_name in ("port_call_id", "scale_id", "reference_code", "scale_reference"):
        key = _string_payload_value(payload, key_name)
        if key and key in lookup:
            return lookup[key]
    raise ValueError("Cada manobra tem de indicar port_call_id ou reference_code de uma escala existente.")


def _maneuver_payload_type(payload: dict) -> str:
    maneuver_type = _string_payload_value(payload, "type", "maneuver_type", "kind").lower()
    aliases = {
        "entrada": "entry",
        "entry": "entry",
        "chegada": "entry",
        "saida": "departure",
        "saída": "departure",
        "departure": "departure",
        "shift": "shift",
        "mudanca": "shift",
        "mudança": "shift",
    }
    resolved = aliases.get(maneuver_type)
    if not resolved:
        raise ValueError("Tipo de manobra inválido. Usa entry, departure ou shift.")
    return resolved


def _maneuver_payload_planned_at(payload: dict, maneuver_type: str) -> str:
    value = _string_payload_value(
        payload,
        "planned_at",
        "planned_at_local",
        "eta",
        "eta_local",
        "planned_departure_at_local",
        "planned_shift_at_local",
    )
    return parse_local_datetime_input(value, "Hora prevista da manobra")


def _schedule_maneuver_from_payload(payload: dict, *, lookup: dict[str, dict], updated_by: str) -> dict:
    port_call = _resolve_port_call_for_maneuver(payload, lookup)
    maneuver_type = _maneuver_payload_type(payload)
    planned_at = _maneuver_payload_planned_at(payload, maneuver_type)
    validate_not_past_datetime(planned_at, "Hora prevista da manobra")
    constraints = normalize_constraint_codes(_first_payload_value(payload, "constraints", default=[]))
    draft_m = validate_positive_number(_string_payload_value(payload, "draft_m", "draft"), "Calado (m)", max_value=30.0)
    tug_count = validate_tug_count(_string_payload_value(payload, "tug_count", "tugs"))
    notes = _string_payload_value(payload, "notes", "plan_note", "observations")

    if maneuver_type == "entry":
        origin_port = require_form_text(
            _string_payload_value(payload, "origin_port", "origin", "last_port"),
            "Porto anterior",
        )
        destination_berth = normalize_portal_berth(
            _string_payload_value(payload, "destination_berth", "destination", "berth"),
            "Cais previsto",
        )
        return services.store.schedule_entry_plan(
            port_call_id=port_call["id"],
            planned_entry_at=planned_at,
            updated_by=updated_by,
            origin_port=origin_port,
            destination_berth=destination_berth,
            constraints=constraints,
            entry_plan_note=build_entry_request_note(
                {
                    "last_port": origin_port,
                    "berth": destination_berth,
                    "draft_m": draft_m,
                    "tug_count": tug_count,
                    "constraints": constraints,
                    "notes": notes,
                }
            ),
            draft_m=draft_m,
            tug_count=tug_count,
        )

    if maneuver_type == "departure":
        next_port = require_form_text(
            _string_payload_value(payload, "next_port", "destination", "destination_port"),
            "Próximo destino",
        )
        return services.store.schedule_departure_plan(
            port_call_id=port_call["id"],
            planned_departure_at=planned_at,
            updated_by=updated_by,
            next_port=next_port,
            constraints=constraints,
            departure_plan_note=build_departure_plan_note(
                {
                    "origin_berth": port_call.get("berth", ""),
                    "draft_m": draft_m,
                    "tug_count": tug_count,
                    "constraints": constraints,
                    "notes": notes,
                }
            ),
            draft_m=draft_m,
            tug_count=tug_count,
        )

    destination_berth = normalize_portal_berth(
        _string_payload_value(payload, "destination_berth", "destination", "berth"),
        "Cais destino",
    )
    return services.store.schedule_shift_plan(
        port_call_id=port_call["id"],
        planned_shift_at=planned_at,
        updated_by=updated_by,
        destination_berth=destination_berth,
        constraints=constraints,
        shift_plan_note=build_shift_plan_note(
            {
                "origin_berth": port_call.get("berth", ""),
                "destination_berth": destination_berth,
                "draft_m": draft_m,
                "tug_count": tug_count,
                "constraints": constraints,
                "notes": notes,
            }
        ),
        draft_m=draft_m,
        tug_count=tug_count,
    )


def _maneuver_export_items(port_calls: list[dict]) -> list[dict]:
    items: list[dict] = []
    for port_call in port_calls:
        for maneuver in port_call.get("maneuver_history", []) or []:
            if not isinstance(maneuver, dict):
                continue
            items.append(
                {
                    "port_call_id": port_call.get("id", ""),
                    "reference_code": port_call.get("reference_code", ""),
                    "vessel_name": port_call.get("vessel_name", ""),
                    "maneuver_id": maneuver.get("id", ""),
                    "type": maneuver.get("type", ""),
                    "state": maneuver.get("state", ""),
                    "planned_at": maneuver.get("planned_at", ""),
                    "origin": maneuver.get("origin", ""),
                    "destination": maneuver.get("destination", ""),
                    "planned_draft_m": maneuver.get("planned_draft_m", ""),
                    "tug_count": maneuver.get("tug_count", ""),
                    "constraints": maneuver.get("constraints", []),
                    "plan_note": maneuver.get("plan_note", ""),
                    "approved_by": maneuver.get("decided_by", ""),
                    "approved_at": maneuver.get("decided_at", ""),
                    "completed_at": maneuver.get("completed_at", ""),
                    "reported_by": maneuver.get("reported_by", ""),
                    "report_note": maneuver.get("report_note", ""),
                }
            )
    return items


def _build_port_call_register_context(register_form: dict | None = None) -> dict:
    from core.helpers import build_tracked_scales, filter_port_activity_for_session
    port_activity = services.store.get_port_activity_snapshot(window_days=5)
    port_activity = filter_port_activity_for_session(port_activity)
    historical_activity = services.store.get_port_activity_snapshot(window_days=3650)
    historical_activity = filter_port_activity_for_session(historical_activity)
    vessel_catalog = _build_vessel_catalog_options(historical_activity)
    return {
        "port_activity": port_activity,
        "tracked_scales": build_tracked_scales(historical_activity),
        "vessel_catalog": vessel_catalog,
        "vessel_catalog_json": json.dumps(vessel_catalog, ensure_ascii=False),
        "port_call_json_template": json.dumps(_build_port_call_json_template(), ensure_ascii=False, indent=2),
        "register_form": register_form or {},
        "title": "Escalas",
    }


def _build_vessel_catalog_context() -> dict:
    activity = services.store.get_port_activity_snapshot(window_days=3650)
    vessels = _build_vessel_catalog_options(activity)
    q = request.args.get("q", "").strip()
    selected_type = request.args.get("vessel_type", "").strip()
    filtered_vessels = _filter_vessel_catalog_options(
        vessels,
        q=q,
        vessel_type=selected_type,
    )
    return {
        "vessels": filtered_vessels,
        "vessel_catalog": vessels,
        "vessel_summary": _vessel_catalog_summary(vessels, filtered_vessels),
        "vessel_filters": {
            "q": q,
            "vessel_type": selected_type,
        },
        "vessel_catalog_json_template": json.dumps(VESSEL_CATALOG_JSON_TEMPLATE, ensure_ascii=False, indent=2),
        "title": "Navios",
    }


def _get_vessel_catalog_record_or_404(vessel_key: str) -> dict:
    clean_key = str(vessel_key or "").strip()
    deleted_keys = _read_deleted_vessel_catalog_keys()
    for vessel in _build_vessel_catalog_options(services.store.get_port_activity_snapshot(window_days=3650)):
        key = vessel.get("key") or _vessel_catalog_key(vessel)
        if key == clean_key and key not in deleted_keys:
            return {**vessel, "key": key}
    raise ValueError("Navio não encontrado no catálogo.")


def _vessel_catalog_txt(record: dict) -> str:
    lines = [
        "PRAGtico - Ficha do Navio",
        "",
        f"Navio: {record.get('vessel_name') or '--'}",
        f"IMO: {record.get('vessel_imo') or '--'}",
        f"Indicativo: {record.get('vessel_call_sign') or '--'}",
        f"Bandeira: {record.get('vessel_flag') or '--'}",
        f"Tipo: {record.get('vessel_type_label') or record.get('vessel_type') or '--'}",
        f"LOA: {record.get('vessel_loa_m') or '--'} m",
        f"Boca: {record.get('vessel_beam_m') or '--'} m",
        f"GT: {record.get('vessel_gt_t') or '--'}",
        f"DWT: {record.get('vessel_dwt_t') or '--'}",
        f"Calado máximo: {record.get('vessel_max_draft_m') or '--'} m",
        f"Bow thruster: {record.get('vessel_bow_thruster') or '--'}",
        f"Stern thruster: {record.get('vessel_stern_thruster') or '--'}",
        "",
        "Serviços e taxas",
        f"Perfil de serviços: {record.get('service_rate_profile') or '--'}",
        f"Base linha regular 365 dias: {record.get('regular_line_base_calls_365d', record.get('regular_line_calls_365d') or 0)}",
        f"Escalas PRAGtico após base: {record.get('regular_line_counted_scales_365d', 0)}",
        f"Total linha regular 365 dias: {record.get('regular_line_effective_calls_365d', record.get('regular_line_calls_365d') or 0)}",
        f"Base válida desde: {record.get('regular_line_calls_anchor_label') or '--'}",
        f"Base expira em: {record.get('regular_line_calls_expires_label') or '--'}",
        f"UP pilotagem: {record.get('pilotage_up_rate') or '--'}",
        f"Perfil redução TUP: {record.get('tup_reduction_profile') or '--'}",
        f"Notas: {record.get('service_notes') or '--'}",
        "",
        "Histórico",
        f"Escalas registadas: {record.get('scale_count') or 0}",
        f"Última escala: {record.get('latest_scale_reference') or '--'}",
        f"Última ETA: {record.get('latest_eta_label') or '--'}",
    ]
    return "\n".join(lines) + "\n"


@bp.route("/port-calls/register")
@login_required
@role_required("admin", "agente")
def port_call_register():
    """Página de registo de nova escala portuária."""
    return render_template(
        "port_call_register.html",
        **_build_port_call_register_context(),
    )


@bp.route("/vessels")
@login_required
@role_required("admin")
def vessel_catalog():
    """Página dedicada à consulta e gestão de navios frequentes."""
    return render_template(
        "vessel_catalog.html",
        **_build_vessel_catalog_context(),
    )


@bp.route("/vessels", methods=["POST"])
@login_required
@role_required("admin")
def create_vessel_catalog_record():
    """Criar uma ficha de navio reutilizável. Apenas admin."""
    try:
        payload = {
            field: request.form.get(field, "").strip()
            for field in VESSEL_CATALOG_FIELDS
        }
        record = _validate_vessel_catalog_record(payload)
        saved = _upsert_vessel_catalog_record(record, updated_by=session["username"], validate=False)
        synced_count = _sync_vessel_catalog_record_to_active_port_calls(
            saved,
            updated_by=session["username"],
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.vessel_catalog"))
    except Exception:
        logger.exception("Falha inesperada ao criar navio frequente.")
        flash("Falha inesperada ao criar o navio.", "error")
        return redirect(url_for("port_calls.vessel_catalog"))

    sync_note = f" {synced_count} escala(s) ativa(s) sincronizada(s)." if synced_count else ""
    flash(f"Navio {saved.get('vessel_name', 'sem nome')} guardado no catálogo.{sync_note}", "success")
    return redirect(url_for("port_calls.vessel_catalog"))


@bp.route("/vessels/<path:vessel_key>/export.json")
@login_required
@role_required("admin")
def export_vessel_catalog_record_json(vessel_key: str):
    """Exportar a ficha de um navio em JSON."""
    try:
        vessel = _get_vessel_catalog_record_or_404(vessel_key)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.vessel_catalog"))
    filename_key = re.sub(r"[^a-z0-9]+", "-", (vessel.get("vessel_name") or "navio").lower()).strip("-")
    return _json_download_response(
        {
            "kind": "pragtico.vessel",
            "version": 1,
            "exported_at": datetime.now().astimezone().isoformat(),
            "vessel": vessel,
        },
        f"pragtico-vessel-{filename_key or 'navio'}.json",
    )


@bp.route("/vessels/<path:vessel_key>/export.txt")
@login_required
@role_required("admin")
def export_vessel_catalog_record_txt(vessel_key: str):
    """Exportar a ficha de um navio em TXT."""
    try:
        vessel = _get_vessel_catalog_record_or_404(vessel_key)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.vessel_catalog"))
    filename_key = re.sub(r"[^a-z0-9]+", "-", (vessel.get("vessel_name") or "navio").lower()).strip("-")
    return Response(
        _vessel_catalog_txt(vessel),
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pragtico-vessel-{filename_key or "navio"}.txt"'},
    )


@bp.route("/vessels/<path:vessel_key>/print")
@login_required
@role_required("admin")
def print_vessel_catalog_record(vessel_key: str):
    """Abrir uma ficha de navio pronta para impressão/PDF."""
    try:
        vessel = _get_vessel_catalog_record_or_404(vessel_key)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.vessel_catalog"))
    return render_template("vessel_print.html", vessel=vessel, title=f"Ficha {vessel.get('vessel_name', '')}")


@bp.route("/port-calls/<port_call_id>")
@login_required
@port_call_scope_required
def port_call_detail(port_call_id: str):
    """Página de detalhe de uma escala portuária."""
    try:
        port_call = services.store.get_port_call(port_call_id)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("dashboard_bp.dashboard"))
    except Exception:
        logger.exception("Falha inesperada ao abrir a escala %s.", port_call_id)
        flash("Falha inesperada ao abrir a escala.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))
    return render_template(
        "port_call_detail.html",
        port_call=port_call,
        scale=build_scale_context(port_call),
        scale_edit_defaults=_build_scale_edit_defaults(port_call),
        maneuver_json_template=json.dumps(_build_maneuver_json_template(port_call), ensure_ascii=False, indent=2),
        title=f"Escala {port_call['vessel_name']}",
    )


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>")
@login_required
@port_call_scope_required
def maneuver_detail(port_call_id: str, maneuver_id: str):
    """Página dedicada ao detalhe operacional de uma manobra."""
    try:
        port_call = services.store.get_port_call(port_call_id)
        maneuver_context = build_maneuver_context(port_call, maneuver_id)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))
    except Exception:
        logger.exception("Falha inesperada ao abrir a manobra %s/%s.", port_call_id, maneuver_id)
        flash("Falha inesperada ao abrir a manobra.", "error")
        return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))
    return render_template(
        "maneuver_detail.html",
        port_call=port_call,
        scale=maneuver_context["scale"],
        maneuver_view=maneuver_context,
        title=f"Manobra {maneuver_context['maneuver']['title']} · {port_call['vessel_name']}",
    )


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/feedback", methods=["POST"])
@login_required
@role_required("admin", "piloto")
@port_call_scope_required
def update_maneuver_case_feedback(port_call_id: str, maneuver_id: str):
    """Guardar feedback operacional validado sobre um caso histórico de manobra."""
    try:
        feedback_status = validate_operational_feedback_status(request.form.get("feedback_status"))
        feedback_note = validate_optional_text(request.form.get("feedback_note", ""))
        if feedback_status in {"avoid", "review"} and not feedback_note:
            raise ValueError("Indica uma nota para justificar este feedback operacional.")
        updated = services.store.update_maneuver_case_feedback(
            maneuver_id=maneuver_id,
            feedback_status=feedback_status,
            feedback_note=feedback_note,
            feedback_by=session["username"],
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.maneuver_detail", port_call_id=port_call_id, maneuver_id=maneuver_id))
    except Exception:
        logger.exception("Falha inesperada ao atualizar feedback do caso %s.", maneuver_id)
        flash("Falha inesperada ao guardar o feedback operacional.", "error")
        return redirect(url_for("port_calls.maneuver_detail", port_call_id=port_call_id, maneuver_id=maneuver_id))

    flash(
        f"Feedback operacional atualizado: {updated.get('feedback_status_label') or updated.get('feedback_status', '')}.",
        "success",
    )
    return redirect(url_for("port_calls.maneuver_detail", port_call_id=port_call_id, maneuver_id=maneuver_id))


@bp.route("/port-calls", methods=["POST"])
@login_required
@role_required("admin", "agente")
def create_port_call():
    """Criar uma nova escala portuária a partir do formulário de registo."""
    form_data = _coerce_port_call_payload(
        {
            "vessel_name": request.form.get("vessel_name", ""),
            "vessel_short_name": "",
            "vessel_imo": request.form.get("vessel_imo", ""),
            "vessel_call_sign": request.form.get("vessel_call_sign", ""),
            "vessel_flag": request.form.get("vessel_flag", ""),
            "vessel_type": request.form.get("vessel_type", ""),
            "vessel_loa_m": request.form.get("vessel_loa_m", ""),
            "vessel_beam_m": request.form.get("vessel_beam_m", ""),
            "vessel_gt_t": request.form.get("vessel_gt_t", ""),
            "vessel_max_draft_m": request.form.get("vessel_max_draft_m", ""),
            "vessel_dwt_t": request.form.get("vessel_dwt_t", ""),
            "vessel_bow_thruster": request.form.get("vessel_bow_thruster", "unknown"),
            "vessel_stern_thruster": request.form.get("vessel_stern_thruster", "unknown"),
            "service_rate_profile": request.form.get("service_rate_profile", ""),
            "regular_line_calls_365d": request.form.get("regular_line_calls_365d", ""),
            "pilotage_up_rate": request.form.get("pilotage_up_rate", ""),
            "tup_reduction_profile": request.form.get("tup_reduction_profile", ""),
            "service_notes": request.form.get("service_notes", ""),
            "eta_local": request.form.get("eta_local", ""),
            "berth": request.form.get("berth", ""),
            "last_port": request.form.get("last_port", ""),
            "next_port": request.form.get("next_port", ""),
            "draft_m": request.form.get("draft_m", ""),
            "constraints": request.form.getlist("constraints"),
            "tug_count": request.form.get("tug_count", ""),
            "notes": request.form.get("notes", ""),
        }
    )

    try:
        catalog_record = _validate_vessel_catalog_record(form_data)
        port_call = _create_port_call_from_payload(form_data, created_by=session["username"])
        _upsert_vessel_catalog_record(catalog_record, updated_by=session["username"], validate=False)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return render_template(
            "port_call_register.html",
            **_build_port_call_register_context(register_form=form_data),
        ), 400
    except Exception:
        logger.exception("Falha inesperada ao criar escala para %s.", session.get("username"))
        flash("Falha inesperada ao guardar a escala.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))

    flash(f"Escala registada para {port_call['vessel_name']} com ETA {port_call['eta_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "entry"),
        event_type="created",
        actor_username=session["username"],
    )
    if request.form.get("redirect_to") == "register":
        return redirect(url_for("port_calls.port_call_register"))
    return redirect(url_for("dashboard_bp.dashboard"))


@bp.route("/port-calls/import-json", methods=["POST"])
@login_required
@role_required("admin")
def import_port_call_json():
    """Criar uma ou mais escalas a partir de JSON colado ou carregado no browser."""
    try:
        payloads = _load_port_call_json_payloads()
        imported = []
        for payload in payloads:
            form_data = _coerce_port_call_payload_with_catalog(payload)
            catalog_record = _validate_vessel_catalog_record({**payload, **form_data})
            port_call = _create_port_call_from_payload(
                form_data,
                created_by=session["username"],
            )
            _upsert_vessel_catalog_record(catalog_record, updated_by=session["username"], validate=False)
            imported.append(port_call)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.port_call_register"))
    except Exception as exc:
        logger.exception("Falha inesperada ao importar escala por JSON para %s.", session.get("username"))
        detail = " ".join(str(exc).strip().split())
        if detail:
            flash(f"Falha inesperada ao importar a escala por JSON: {detail}", "error")
        else:
            flash("Falha inesperada ao importar a escala por JSON.", "error")
        return redirect(url_for("port_calls.port_call_register"))

    for port_call in imported:
        _emit_maneuver_notification(
            port_call=port_call,
            maneuver=latest_maneuver_by_type(port_call, "entry"),
            event_type="created",
            actor_username=session["username"],
        )
    if len(imported) == 1:
        port_call = imported[0]
        flash(f"Escala importada para {port_call['vessel_name']} com ETA {port_call['eta_label']}.", "success")
    else:
        flash(f"{len(imported)} escala(s) importada(s) por JSON.", "success")
    return redirect(url_for("port_calls.port_call_register"))


@bp.route("/port-calls/export-json")
@login_required
@role_required("admin")
def export_port_calls_json():
    """Exportar todas as escalas em JSON. Apenas admin."""
    records = services.store.list_port_calls()
    return _json_download_response(
        {
            "kind": "pragtico.port_calls",
            "version": 1,
            "exported_at": datetime.now().astimezone().isoformat(),
            "items": records,
        },
        _export_filename("port-calls"),
    )


@bp.route("/port-calls/vessels/import-json", methods=["POST"])
@login_required
@role_required("admin")
def import_vessel_catalog_json():
    """Importar ou atualizar fichas de navios reutilizáveis no registo de escalas."""
    try:
        payloads = _load_vessel_catalog_json_payload()
        imported = []
        synced_count = 0
        for payload in payloads:
            saved = _upsert_vessel_catalog_record(payload, updated_by=session["username"])
            imported.append(saved)
            synced_count += _sync_vessel_catalog_record_to_active_port_calls(
                saved,
                updated_by=session["username"],
            )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.vessel_catalog"))
    except Exception as exc:
        logger.exception("Falha inesperada ao importar navios por JSON para %s.", session.get("username"))
        detail = " ".join(str(exc).strip().split())
        flash(
            f"Falha inesperada ao importar navios por JSON: {detail}"
            if detail
            else "Falha inesperada ao importar navios por JSON.",
            "error",
        )
        return redirect(url_for("port_calls.vessel_catalog"))

    sync_note = f" {synced_count} escala(s) ativa(s) sincronizada(s)." if synced_count else ""
    flash(f"{len(imported)} navio(s) importado(s) ou atualizado(s) no catálogo.{sync_note}", "success")
    return redirect(url_for("port_calls.vessel_catalog"))


@bp.route("/port-calls/vessels/export-json")
@login_required
@role_required("admin")
def export_vessel_catalog_json():
    """Exportar o catálogo de navios visível ao admin."""
    activity = services.store.get_port_activity_snapshot(window_days=3650)
    vessels = _build_vessel_catalog_options(activity)
    return _json_download_response(
        {
            "kind": "pragtico.vessels",
            "version": 1,
            "exported_at": datetime.now().astimezone().isoformat(),
            "items": vessels,
        },
        _export_filename("vessels"),
    )


@bp.route("/port-calls/vessels/<path:vessel_key>/edit", methods=["POST"])
@login_required
@role_required("admin")
def edit_vessel_catalog_record(vessel_key: str):
    """Editar uma ficha de navio reutilizável. Apenas admin."""
    try:
        payload = {
            field: request.form.get(field, "").strip()
            for field in VESSEL_CATALOG_FIELDS
        }
        record = _validate_vessel_catalog_record(payload)
        previous = _remove_vessel_catalog_record(vessel_key, hide=True)
        saved = _upsert_vessel_catalog_record(record, updated_by=session["username"], validate=False)
        synced_count = _sync_vessel_catalog_record_to_active_port_calls(
            saved,
            updated_by=session["username"],
            previous_key=previous.get("key") or vessel_key,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.vessel_catalog"))
    except Exception:
        logger.exception("Falha inesperada ao editar navio %s.", vessel_key)
        flash("Falha inesperada ao editar o navio.", "error")
        return redirect(url_for("port_calls.vessel_catalog"))

    sync_note = f" {synced_count} escala(s) ativa(s) sincronizada(s)." if synced_count else ""
    flash(f"Navio {saved.get('vessel_name', 'sem nome')} atualizado no catálogo.{sync_note}", "success")
    return redirect(url_for("port_calls.vessel_catalog"))


@bp.route("/port-calls/vessels/<path:vessel_key>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_vessel_catalog_record(vessel_key: str):
    """Ocultar/remover um navio do catálogo reutilizável. Apenas admin."""
    try:
        removed = _remove_vessel_catalog_record(vessel_key, hide=True)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.vessel_catalog"))
    except Exception:
        logger.exception("Falha inesperada ao apagar navio %s.", vessel_key)
        flash("Falha inesperada ao apagar o navio.", "error")
        return redirect(url_for("port_calls.vessel_catalog"))

    flash(f"Navio {removed.get('vessel_name') or vessel_key} removido do catálogo.", "success")
    return redirect(url_for("port_calls.vessel_catalog"))


@bp.route("/port-calls/vessels/delete-all", methods=["POST"])
@login_required
@role_required("admin")
def delete_all_vessel_catalog_records():
    """Remover todos os navios do catálogo reutilizável visível ao admin."""
    try:
        confirm_text = request.form.get("confirm_text", "").strip().upper()
        if confirm_text != "APAGAR NAVIOS":
            raise ValueError("Para remover todos os navios escreve APAGAR NAVIOS.")
        removed_count = _delete_all_vessel_catalog_records()
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.vessel_catalog"))
    except Exception:
        logger.exception("Falha inesperada ao apagar todos os navios do catálogo.")
        flash("Falha inesperada ao apagar o catálogo de navios.", "error")
        return redirect(url_for("port_calls.vessel_catalog"))

    flash(f"{removed_count} navio(s) removido(s) do catálogo.", "success")
    return redirect(url_for("port_calls.vessel_catalog"))


@bp.route("/port-calls/maneuvers/import-json", methods=["POST"])
@login_required
@role_required("admin")
def import_maneuvers_json():
    """Importar planeamentos de manobras para escalas existentes. Apenas admin."""
    try:
        payloads = _load_maneuver_json_payloads()
        port_calls = services.store.list_port_calls()
        lookup = _port_call_lookup(port_calls)
        imported = []
        for payload in payloads:
            maneuver_type = _maneuver_payload_type(payload)
            port_call = _schedule_maneuver_from_payload(
                payload,
                lookup=lookup,
                updated_by=session["username"],
            )
            imported.append((port_call, maneuver_type))
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.port_call_register"))
    except Exception as exc:
        logger.exception("Falha inesperada ao importar manobras por JSON para %s.", session.get("username"))
        detail = " ".join(str(exc).strip().split())
        flash(
            f"Falha inesperada ao importar manobras por JSON: {detail}"
            if detail
            else "Falha inesperada ao importar manobras por JSON.",
            "error",
        )
        return redirect(url_for("port_calls.port_call_register"))

    for port_call, maneuver_type in imported:
        _emit_maneuver_notification(
            port_call=port_call,
            maneuver=latest_maneuver_by_type(port_call, maneuver_type),
            event_type="created",
            actor_username=session["username"],
        )
    flash(f"{len(imported)} manobra(s) importada(s) por JSON.", "success")
    return redirect(url_for("port_calls.port_call_register"))


@bp.route("/port-calls/<port_call_id>/maneuvers/import-json", methods=["POST"])
@login_required
@role_required("admin")
@port_call_scope_required
def import_port_call_maneuvers_json(port_call_id: str):
    """Importar planeamentos de manobras para a escala aberta. Apenas admin."""
    try:
        current = services.store.get_port_call(port_call_id)
        payloads = _load_maneuver_json_payloads()
        lookup = _port_call_lookup([current])
        imported = []
        for payload in payloads:
            scoped_payload = dict(payload)
            if not any(_string_payload_value(scoped_payload, key) for key in ("port_call_id", "scale_id", "reference_code", "scale_reference")):
                scoped_payload["port_call_id"] = current["id"]
            maneuver_type = _maneuver_payload_type(scoped_payload)
            port_call = _schedule_maneuver_from_payload(
                scoped_payload,
                lookup=lookup,
                updated_by=session["username"],
            )
            imported.append((port_call, maneuver_type))
            current = port_call
            lookup = _port_call_lookup([current])
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))
    except Exception as exc:
        logger.exception("Falha inesperada ao importar manobras por JSON para escala %s.", port_call_id)
        detail = " ".join(str(exc).strip().split())
        flash(
            f"Falha inesperada ao importar manobras por JSON: {detail}"
            if detail
            else "Falha inesperada ao importar manobras por JSON.",
            "error",
        )
        return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))

    for port_call, maneuver_type in imported:
        _emit_maneuver_notification(
            port_call=port_call,
            maneuver=latest_maneuver_by_type(port_call, maneuver_type),
            event_type="created",
            actor_username=session["username"],
        )
    flash(f"{len(imported)} manobra(s) importada(s) por JSON para esta escala.", "success")
    return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))


@bp.route("/port-calls/maneuvers/export-json")
@login_required
@role_required("admin")
def export_maneuvers_json():
    """Exportar todas as manobras conhecidas em JSON. Apenas admin."""
    records = services.store.list_port_calls()
    return _json_download_response(
        {
            "kind": "pragtico.maneuvers",
            "version": 1,
            "exported_at": datetime.now().astimezone().isoformat(),
            "items": _maneuver_export_items(records),
        },
        _export_filename("maneuvers"),
    )


@bp.route("/port-calls/<port_call_id>/edit", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def edit_port_call(port_call_id: str):
    """Editar os dados da escala/navio a partir da página de detalhe."""
    try:
        current = services.store.get_port_call(port_call_id)
        form_data = {
            "vessel_name": request.form.get("vessel_name", "").strip(),
            "vessel_imo": request.form.get("vessel_imo", "").strip(),
            "vessel_call_sign": request.form.get("vessel_call_sign", "").strip(),
            "vessel_flag": request.form.get("vessel_flag", "").strip(),
            "vessel_type": request.form.get("vessel_type", "").strip(),
            "vessel_loa_m": request.form.get("vessel_loa_m", "").strip(),
            "vessel_beam_m": request.form.get("vessel_beam_m", "").strip(),
            "vessel_gt_t": request.form.get("vessel_gt_t", "").strip(),
            "vessel_max_draft_m": request.form.get("vessel_max_draft_m", "").strip(),
            "vessel_dwt_t": request.form.get("vessel_dwt_t", "").strip(),
            "vessel_bow_thruster": request.form.get("vessel_bow_thruster", "unknown").strip(),
            "vessel_stern_thruster": request.form.get("vessel_stern_thruster", "unknown").strip(),
            "service_rate_profile": request.form.get("service_rate_profile", "").strip(),
            "regular_line_calls_365d": request.form.get("regular_line_calls_365d", "").strip(),
            "pilotage_up_rate": request.form.get("pilotage_up_rate", "").strip(),
            "tup_reduction_profile": request.form.get("tup_reduction_profile", "").strip(),
            "service_notes": request.form.get("service_notes", "").strip(),
            "eta_local": request.form.get("eta_local", "").strip(),
            "berth": request.form.get("berth", "").strip(),
            "last_port": request.form.get("last_port", "").strip(),
            "next_port": request.form.get("next_port", "").strip(),
            "notes": request.form.get("notes", "").strip(),
            "change_reason": request.form.get("change_reason", "").strip(),
        }
        change_reason = require_form_text(form_data["change_reason"], "Motivo da alteração")
        eta = parse_local_datetime_input(form_data["eta_local"], "ETA")
        if current.get("status") == "scheduled":
            validate_not_past_datetime(eta, "ETA")
        berth = (
            ensure_portal_berth_is_available(form_data["berth"], current_port_call_id=port_call_id, label="Cais")
            if current.get("status") == "in_port"
            else normalize_portal_berth(form_data["berth"], "Cais")
        )
        last_port = require_form_text(form_data["last_port"], "Porto anterior")
        next_port = require_form_text(form_data["next_port"], "Próximo destino")
        validate_imo(form_data["vessel_imo"])
        validated_dims = validate_vessel_dimensions(form_data)
        form_data.update(validated_dims)
        form_data["vessel_bow_thruster"] = normalize_thruster_state(form_data.get("vessel_bow_thruster"), "Bow thruster")
        form_data["vessel_stern_thruster"] = normalize_thruster_state(form_data.get("vessel_stern_thruster"), "Stern thruster")
        catalog_record = _validate_vessel_catalog_record(form_data)
        updated = services.store.edit_port_call(
            port_call_id=port_call_id,
            updated_by=session["username"],
            vessel_name=require_form_text(form_data["vessel_name"], "Nome do navio"),
            eta=eta,
            berth=berth,
            last_port=last_port,
            next_port=next_port,
            notes=form_data["notes"],
            vessel_imo=form_data["vessel_imo"],
            vessel_call_sign=require_form_text(form_data["vessel_call_sign"], "Indicativo"),
            vessel_flag=require_form_text(form_data["vessel_flag"], "Bandeira"),
            vessel_type=_canonical_vessel_type_label(form_data["vessel_type"]),
            vessel_loa_m=form_data["vessel_loa_m"],
            vessel_beam_m=form_data["vessel_beam_m"],
            vessel_gt_t=form_data["vessel_gt_t"],
            vessel_max_draft_m=form_data["vessel_max_draft_m"],
            vessel_dwt_t=form_data["vessel_dwt_t"],
            vessel_bow_thruster=form_data["vessel_bow_thruster"],
            vessel_stern_thruster=form_data["vessel_stern_thruster"],
            change_reason=change_reason,
        )
        _upsert_vessel_catalog_record(catalog_record, updated_by=session["username"], validate=False)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    except Exception:
        logger.exception("Falha inesperada ao editar a escala %s.", port_call_id)
        flash("Falha inesperada ao atualizar a escala.", "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Escala atualizada para {updated['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/approve", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def approve_port_call(port_call_id: str):
    """Aprovar a manobra de entrada ou saída pendente de uma escala."""
    try:
        current = services.store.get_port_call(port_call_id)
        target_type = "entry" if current.get("status") == "scheduled" else "departure"
        ensure_maneuver_hour_capacity_for_approval(current, target_type)
        if target_type == "entry":
            _ensure_maneuver_destination_can_be_approved(current, "entry", label="Cais")
        port_call = services.store.approve_port_call(
            port_call_id=port_call_id, decided_by=session["username"],
            approval_note=request.form.get("approval_note", "").strip(),
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Manobra aprovada para {port_call['vessel_name']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, target_type),
        event_type="approved",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/abort", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def abort_port_call(port_call_id: str):
    """Abortar a escala portuária e registar o motivo."""
    try:
        aborted_reason = validate_required_text(request.form.get("aborted_reason", ""), "Motivo de aborto")
        port_call = services.store.abort_port_call(
            port_call_id=port_call_id, decided_by=session["username"],
            aborted_reason=aborted_reason,
            approval_note=validate_optional_text(request.form.get("approval_note", "")),
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Manobra abortada para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/schedule-entry", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def schedule_entry_plan(port_call_id: str):
    """Planear uma nova entrada para uma escala que continuou prevista após aborto."""
    try:
        planned_entry_at = parse_local_datetime_input(request.form.get("planned_entry_at_local", "").strip(), "Hora prevista de entrada")
        validate_not_past_datetime(planned_entry_at, "Hora prevista de entrada")
        origin_port = require_form_text(request.form.get("origin_port", "").strip(), "Porto anterior")
        destination_berth = normalize_portal_berth(request.form.get("destination_berth", "").strip(), "Cais previsto")
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        tug_count = validate_tug_count(request.form.get("tug_count", "").strip())
        port_call = services.store.schedule_entry_plan(
            port_call_id=port_call_id,
            planned_entry_at=planned_entry_at,
            updated_by=session["username"],
            origin_port=origin_port,
            destination_berth=destination_berth,
            constraints=request.form.getlist("constraints"),
            entry_plan_note=build_entry_request_note({
                "last_port": origin_port,
                "berth": destination_berth,
                "draft_m": draft_m,
                "constraints": request.form.getlist("constraints"),
                "tug_count": tug_count,
                "notes": request.form.get("entry_plan_note", "").strip(),
            }),
            draft_m=draft_m,
            tug_count=tug_count,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Entrada planeada para {port_call['vessel_name']} às {port_call['eta_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "entry"),
        event_type="created",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/schedule-departure", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def schedule_departure_plan(port_call_id: str):
    """Planear a manobra de saída de uma escala prevista ou navio em porto."""
    try:
        current = services.store.get_port_call(port_call_id)
        planned_departure_at = parse_local_datetime_input(request.form.get("planned_departure_at_local", "").strip(), "Hora prevista de saída")
        validate_not_past_datetime(planned_departure_at, "Hora prevista de saída")
        next_port = require_form_text(request.form.get("next_port", "").strip(), "Próximo destino")
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        tug_count = validate_tug_count(request.form.get("tug_count", "").strip())
        port_call = services.store.schedule_departure_plan(
            port_call_id=port_call_id, planned_departure_at=planned_departure_at,
            updated_by=session["username"], next_port=next_port,
            constraints=request.form.getlist("constraints"),
            departure_plan_note=build_departure_plan_note({
                "origin_berth": current.get("berth", ""),
                "draft_m": draft_m, "constraints": request.form.getlist("constraints"),
                "tug_count": tug_count, "notes": request.form.get("departure_plan_note", "").strip(),
            }),
            draft_m=draft_m,
            tug_count=tug_count,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Saída planeada para {port_call['vessel_name']} às {port_call['planned_departure_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "departure"),
        event_type="created",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/abort-departure", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def abort_departure_plan(port_call_id: str):
    """Abortar a saída aprovada de um navio em porto."""
    try:
        aborted_reason = validate_required_text(request.form.get("aborted_reason", ""), "Motivo de aborto")
        port_call = services.store.abort_departure_plan(
            port_call_id=port_call_id, updated_by=session["username"],
            aborted_reason=aborted_reason,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Saída abortada para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/schedule-shift", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def schedule_shift_plan(port_call_id: str):
    """Planear uma mudança de cais para uma escala prevista ou navio em porto."""
    try:
        current = services.store.get_port_call(port_call_id)
        planned_shift_at = parse_local_datetime_input(request.form.get("planned_shift_at_local", "").strip(), "Hora prevista da mudança")
        validate_not_past_datetime(planned_shift_at, "Hora prevista da mudança")
        origin_berth = normalize_portal_berth(current.get("berth", ""), "Cais origem")
        destination_berth = normalize_portal_berth(request.form.get("destination_berth", "").strip(), "Cais destino")
        if destination_berth == origin_berth:
            raise ValueError("O cais destino tem de ser diferente do local atual do navio.")
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        tug_count = validate_tug_count(request.form.get("tug_count", "").strip())
        port_call = services.store.schedule_shift_plan(
            port_call_id=port_call_id, planned_shift_at=planned_shift_at,
            updated_by=session["username"], destination_berth=destination_berth,
            constraints=request.form.getlist("constraints"),
            shift_plan_note=build_shift_plan_note({
                "origin_berth": origin_berth, "destination_berth": destination_berth,
                "draft_m": draft_m, "constraints": request.form.getlist("constraints"),
                "tug_count": tug_count, "notes": request.form.get("shift_plan_note", "").strip(),
            }),
            draft_m=draft_m,
            tug_count=tug_count,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança planeada para {port_call['vessel_name']} às {port_call['planned_shift_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "shift"),
        event_type="created",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/approve-shift", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def approve_shift_plan(port_call_id: str):
    """Aprovar o planeamento de mudança de cais pendente."""
    try:
        current = services.store.get_port_call(port_call_id)
        ensure_maneuver_hour_capacity_for_approval(current, "shift")
        _ensure_maneuver_destination_can_be_approved(current, "shift", label="Cais destino")
        port_call = services.store.approve_shift_plan(
            port_call_id=port_call_id, decided_by=session["username"],
            approval_note=request.form.get("approval_note", "").strip(),
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança aprovada para {port_call['vessel_name']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "shift"),
        event_type="approved",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/abort-shift", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def abort_shift_plan(port_call_id: str):
    """Abortar a mudança aprovada de um navio em porto."""
    try:
        aborted_reason = validate_required_text(request.form.get("aborted_reason", ""), "Motivo de aborto")
        port_call = services.store.abort_shift_plan(
            port_call_id=port_call_id, updated_by=session["username"],
            aborted_reason=aborted_reason,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança abortada para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/cancel", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def cancel_maneuver(port_call_id: str, maneuver_id: str):
    """Cancelar uma manobra ainda pendente antes da aprovação do piloto."""
    try:
        current = services.store.get_port_call(port_call_id)
        target = maneuver_by_id(current, maneuver_id)
        if not target:
            raise ValueError("Manobra não encontrada.")
        if target.get("state") != "pending":
            raise ValueError("Só podes cancelar manobras pendentes. Depois da aprovação usa abortar.")
        removed_or_updated = services.store.delete_maneuver(
            port_call_id=port_call_id,
            maneuver_id=maneuver_id,
            updated_by=session["username"],
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Manobra cancelada para {removed_or_updated['vessel_name']}.", "success")
    if target.get("type") == "entry":
        return redirect(url_for("dashboard_bp.dashboard"))
    return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_maneuver(port_call_id: str, maneuver_id: str):
    """Apagar definitivamente uma manobra (incluindo arquivo). Apenas admin."""
    try:
        current = services.store.get_port_call(port_call_id)
        target = maneuver_by_id(current, maneuver_id)
        if not target:
            raise ValueError("Manobra não encontrada.")
        removed_or_updated = services.store.delete_maneuver(
            port_call_id=port_call_id,
            maneuver_id=maneuver_id,
            updated_by=session["username"],
            force=True,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Manobra apagada definitivamente para {removed_or_updated['vessel_name']}.", "success")
    if target.get("type") == "entry":
        return redirect(url_for("dashboard_bp.dashboard"))
    return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))


@bp.route("/port-calls/<port_call_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_port_call(port_call_id: str):
    """Apagar definitivamente uma escala e o respetivo arquivo de manobras. Apenas admin."""
    try:
        removed = services.store.delete_port_call(port_call_id)
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Escala apagada definitivamente para {removed['vessel_name']}.", "success")
    return redirect(url_for("dashboard_bp.dashboard"))


@bp.route("/port-calls/<port_call_id>/complete-shift", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def mark_shift_completed(port_call_id: str):
    """Confirmar a conclusão da mudança de cais e atualizar a localização do navio."""
    try:
        current = services.store.get_port_call(port_call_id)
        ensure_portal_berth_is_physically_available(
            current.get("shift_destination_berth") or current.get("berth", ""),
            current_port_call_id=port_call_id,
            label="Cais destino",
        )
        port_call = services.store.mark_shift_completed(
            port_call_id=port_call_id,
            shifted_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança concluída para {port_call['vessel_name']} às {port_call['shift_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "shift"),
        event_type="completed",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/arrive", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def mark_port_call_arrived(port_call_id: str):
    """Registar a chegada do navio ao porto e confirmar a manobra de entrada."""
    try:
        current = services.store.get_port_call(port_call_id)
        berth = ensure_portal_berth_is_physically_available(
            request.form.get("berth", "").strip() or current.get("berth", ""),
            current_port_call_id=port_call_id,
            label="Cais",
        )
        port_call = services.store.mark_port_call_arrived(
            port_call_id=port_call_id,
            arrived_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
            berth=berth,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Entrada confirmada para {port_call['vessel_name']} às {port_call['ata_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "entry"),
        event_type="completed",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/depart", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def mark_port_call_departed(port_call_id: str):
    """Registar a saída do navio do porto e encerrar a manobra de saída."""
    try:
        port_call = services.store.mark_port_call_departed(
            port_call_id=port_call_id,
            departed_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
            next_port=request.form.get("next_port", "").strip(),
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Saída registada para {port_call['vessel_name']} às {port_call['departure_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "departure"),
        event_type="completed",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/entry-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def attach_entry_report(port_call_id: str):
    """Guardar o registo de pilotagem da manobra de entrada."""
    try:
        current = services.store.get_port_call(port_call_id)
        target = (
            maneuver_by_id(current, request.form.get("maneuver_id", "").strip())
            or latest_maneuver_by_type(current, "entry")
        )
        if (target or {}).get("state") == "approved":
            ensure_portal_berth_is_physically_available(
                target.get("destination") or current.get("berth", ""),
                current_port_call_id=port_call_id,
                label="Cais",
            )
        maneuver_started_at = parse_local_datetime_input(request.form.get("maneuver_started_local", "").strip(), "Início da manobra")
        maneuver_finished_at = parse_local_datetime_input(request.form.get("maneuver_finished_local", "").strip(), "Fim da manobra")
        validate_datetime_range(maneuver_started_at, maneuver_finished_at)
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        note = build_pilot_report_note({"maneuver_started_at": maneuver_started_at, "maneuver_finished_at": maneuver_finished_at, "draft_m": draft_m, "notes": request.form.get("notes", "").strip()}, "Entrada")
        port_call = services.store.attach_entry_report(
            port_call_id=port_call_id,
            updated_by=session["username"],
            maneuver_started_at=maneuver_started_at,
            maneuver_finished_at=maneuver_finished_at,
            draft_m=draft_m,
            notes=note,
            maneuver_id=request.form.get("maneuver_id", "").strip() or None,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Registo da entrada guardado para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/departure-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def attach_departure_report(port_call_id: str):
    """Guardar o registo de pilotagem da manobra de saída."""
    try:
        maneuver_started_at = parse_local_datetime_input(request.form.get("maneuver_started_local", "").strip(), "Início da manobra")
        maneuver_finished_at = parse_local_datetime_input(request.form.get("maneuver_finished_local", "").strip(), "Fim da manobra")
        validate_datetime_range(maneuver_started_at, maneuver_finished_at)
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        note = build_pilot_report_note({"maneuver_started_at": maneuver_started_at, "maneuver_finished_at": maneuver_finished_at, "draft_m": draft_m, "notes": request.form.get("notes", "").strip()}, "Saída")
        port_call = services.store.attach_departure_report(
            port_call_id=port_call_id,
            updated_by=session["username"],
            maneuver_started_at=maneuver_started_at,
            maneuver_finished_at=maneuver_finished_at,
            draft_m=draft_m,
            notes=note,
            maneuver_id=request.form.get("maneuver_id", "").strip() or None,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Registo da saída guardado para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/shift-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def attach_shift_report(port_call_id: str):
    """Guardar o registo de pilotagem da manobra de mudança de cais."""
    try:
        current = services.store.get_port_call(port_call_id)
        target = (
            maneuver_by_id(current, request.form.get("maneuver_id", "").strip())
            or latest_maneuver_by_type(current, "shift")
        )
        if (target or {}).get("state") == "approved":
            ensure_portal_berth_is_physically_available(
                target.get("destination") or current.get("shift_destination_berth") or current.get("berth", ""),
                current_port_call_id=port_call_id,
                label="Cais destino",
            )
        maneuver_started_at = parse_local_datetime_input(request.form.get("maneuver_started_local", "").strip(), "Início da manobra")
        maneuver_finished_at = parse_local_datetime_input(request.form.get("maneuver_finished_local", "").strip(), "Fim da manobra")
        validate_datetime_range(maneuver_started_at, maneuver_finished_at)
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        note = build_pilot_report_note({"maneuver_started_at": maneuver_started_at, "maneuver_finished_at": maneuver_finished_at, "draft_m": draft_m, "notes": request.form.get("notes", "").strip()}, "Mudança")
        port_call = services.store.attach_shift_report(
            port_call_id=port_call_id,
            updated_by=session["username"],
            maneuver_started_at=maneuver_started_at,
            maneuver_finished_at=maneuver_finished_at,
            draft_m=draft_m,
            notes=note,
            maneuver_id=request.form.get("maneuver_id", "").strip() or None,
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Registo da mudança guardado para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/edit-plan", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def edit_maneuver_plan(port_call_id: str, maneuver_id: str):
    """Editar o planeamento de uma manobra existente e registar o motivo da alteração."""
    try:
        port_call = services.store.get_port_call(port_call_id)
        previous_maneuver = dict(maneuver_by_id(port_call, maneuver_id) or {})
        maneuver_context = build_maneuver_context(port_call, maneuver_id)
        maneuver_type = maneuver_context["maneuver"]["type"]
        origin = require_form_text(request.form.get("origin", "").strip(), "Origem")
        destination = require_form_text(request.form.get("destination", "").strip(), "Destino")
        if maneuver_type == "entry":
            destination = normalize_portal_berth(destination, "Destino")
        elif maneuver_type in {"departure", "shift"}:
            origin = normalize_portal_berth(origin, "Origem")
            if maneuver_type == "shift":
                destination = normalize_portal_berth(destination, "Destino")
                if destination == origin:
                    raise ValueError("O cais destino tem de ser diferente do local atual do navio.")
        port_call = services.store.edit_maneuver_plan(
            port_call_id=port_call_id, maneuver_id=maneuver_id,
            updated_by=session["username"], actor_role=session.get("role", ""),
            planned_at=parse_local_datetime_input(request.form.get("planned_at_local", "").strip(), "Hora prevista"),
            origin=origin,
            destination=destination,
            draft_m=validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0),
            tug_count=validate_tug_count(request.form.get("tug_count", "").strip()),
            constraints=request.form.getlist("constraints"),
            plan_note=request.form.get("plan_observations", "").strip(),
            change_reason=require_form_text(request.form.get("change_reason", "").strip(), "Motivo da alteração"),
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    except Exception:
        logger.exception("Falha inesperada ao editar planeamento %s/%s.", port_call_id, maneuver_id)
        flash("Falha inesperada ao editar a manobra.", "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Planeamento atualizado para {port_call['vessel_name']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=maneuver_by_id(port_call, maneuver_id),
        event_type="updated",
        actor_username=session["username"],
        previous_maneuver=previous_maneuver,
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/edit-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def edit_maneuver_report(port_call_id: str, maneuver_id: str):
    """Rever o registo operacional de uma manobra concluída e registar o motivo da alteração."""
    try:
        current = services.store.get_port_call(port_call_id)
        previous_maneuver = dict(maneuver_by_id(current, maneuver_id) or {})
        maneuver_started_at = parse_local_datetime_input(request.form.get("maneuver_started_local", "").strip(), "Início da manobra")
        maneuver_finished_at = parse_local_datetime_input(request.form.get("maneuver_finished_local", "").strip(), "Fim da manobra")
        validate_datetime_range(maneuver_started_at, maneuver_finished_at)
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        port_call = services.store.edit_maneuver_report(
            port_call_id=port_call_id, maneuver_id=maneuver_id,
            updated_by=session["username"],
            maneuver_started_at=maneuver_started_at, maneuver_finished_at=maneuver_finished_at,
            draft_m=draft_m,
            notes=build_pilot_report_note({"maneuver_started_at": maneuver_started_at, "maneuver_finished_at": maneuver_finished_at, "draft_m": draft_m, "notes": request.form.get("notes", "").strip()}, require_form_text(request.form.get("maneuver_label", "").strip(), "Manobra")),
            change_reason=require_form_text(request.form.get("change_reason", "").strip(), "Motivo da alteração"),
        )
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Registo revisto para {port_call['vessel_name']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=maneuver_by_id(port_call, maneuver_id),
        event_type="report_updated",
        actor_username=session["username"],
        previous_maneuver=previous_maneuver,
    )
    return redirect_to_portal_target(port_call_id)
