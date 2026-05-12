from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from typing import Dict, List, Optional

from domain.berth_layout import BERTH_OPTIONS, canonicalize_berth_label, is_anchorage_berth, is_known_berth_label
from domain.chat_action_config import (
    ABORT_FIELD_HINTS,
    ACTION_SPECS,
    DISPLAY_FIELD_LABELS,
    FIELD_ALIASES,
    GENERIC_ACTION_FAMILIES,
    JSON_BLOCK_RE,
    MANEUVER_ID_SENSITIVE_ACTIONS,
    MANEUVER_REPORT_FIELD_HINTS,
    MANEUVER_TYPES,
    NESTED_FIELD_GROUPS,
    OPERATIONAL_OBJECT_HINTS,
    OPERATIONAL_QUERY_HINT_RE,
    OPERATIONAL_STRONG_ACTION_HINT_RE,
    OPERATIONAL_TIMING_QUERY_HINT_RE,
    OPERATIONAL_WEAK_ACTION_HINT_RE,
    PENDING_UPDATE_FIELD_ALIASES,
    PORT_CALL_FIELD_HINTS,
    QUERY_HINTS,
    REQUIRED_FIELDS_BY_ACTION,
    SLASH_COMMAND_ALIASES,
    SLASH_COMMAND_FIELD_ALIASES,
    TIME_ONLY_RE,
    TIME_OR_STATUS_HINT_RE,
)
from domain.chat_action_templates import (
    build_action_reply_template,
    build_approval_reply_template,
    build_command_abort_reply_template,
    build_command_report_reply_template,
    build_create_maneuver_reply_template,
    build_delete_maneuver_reply_template,
    build_delete_report_reply_template,
    build_delete_scale_reply_template,
    build_edit_maneuver_plan_reply_template,
    build_edit_report_reply_template,
    build_maneuver_report_reply_template,
    build_port_call_reply_template,
    build_scale_edit_reply_template,
    build_slash_help,
    build_validate_maneuver_reply_template,
    display_missing_field_labels,
    build_abort_reply_template,
)


def _lookup_key(value: Optional[str]) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


def _normalized_ascii_text(value: Optional[str]) -> str:
    normalized, _ = _normalized_ascii_text_with_index_map(value)
    return normalized


def _normalized_ascii_text_with_index_map(value: Optional[str]) -> tuple[str, List[int]]:
    source = (value or "").strip().lower()
    normalized_chars: List[str] = []
    index_map: List[int] = []
    for index, char in enumerate(source):
        normalized_piece = unicodedata.normalize("NFKD", char).encode("ascii", "ignore").decode("ascii")
        if not normalized_piece:
            continue
        normalized_chars.append(normalized_piece)
        index_map.extend([index] * len(normalized_piece))
    return "".join(normalized_chars), index_map


def _extract_constraint_flags(question: str) -> List[str]:
    text = " ".join((question or "").strip().split())
    if not text:
        return []
    lowered = text.lower()
    flags: List[str] = []
    specs = (
        ("daylight", ("day-light", "day light", "daylight")),
        ("gas", ("gás / carga perigosa", "gas / carga perigosa", "gás", "gas", "carga perigosa")),
        ("estrategico", ("estratégico", "estrategico")),
    )
    for code, aliases in specs:
        for alias in aliases:
            match = re.search(
                rf"{re.escape(alias)}\s*(?:=|:|\beh\b|\be\b)\s*(.{0,24})",
                lowered,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            segment = match.group(1)
            if any(marker in segment for marker in ("✅", "sim", "yes", "true")):
                if code not in flags:
                    flags.append(code)
                break
    return flags


def looks_like_operational_command(question: str) -> bool:
    clean = _lookup_key(question)
    if not clean:
        return False
    if "arquivo" in clean or "archived" in clean:
        return False
    has_query_hint = bool(OPERATIONAL_QUERY_HINT_RE.search(clean)) or "?" in question
    has_strong_action = bool(OPERATIONAL_STRONG_ACTION_HINT_RE.search(clean))
    has_weak_action = bool(OPERATIONAL_WEAK_ACTION_HINT_RE.search(clean))
    has_timing_query = bool(OPERATIONAL_TIMING_QUERY_HINT_RE.search(clean))
    if has_query_hint and has_timing_query and not has_strong_action:
        return False
    if has_query_hint and not has_strong_action and not has_weak_action:
        return False
    if has_strong_action:
        return True
    if has_query_hint and has_weak_action:
        return False
    if has_weak_action:
        return True
    if any(token in clean for token in OPERATIONAL_OBJECT_HINTS) and TIME_OR_STATUS_HINT_RE.search(clean):
        return not has_query_hint
    if any(clean.startswith(token) for token in QUERY_HINTS):
        return False
    if has_query_hint:
        return False
    return bool(re.search(r"\b(ata|atd|eta|etd)\b", clean))


def looks_like_operational_query(question: str) -> bool:
    clean = _lookup_key(question)
    if not clean:
        return False
    return bool(OPERATIONAL_QUERY_HINT_RE.search(clean) or "?" in question)


def looks_like_port_call_payload(question: str) -> bool:
    extracted = _extract_labelled_values(question)
    if not extracted:
        return False
    hits = PORT_CALL_FIELD_HINTS.intersection(extracted.keys())
    strong_hits = {
        "eta_local",
        "berth",
        "last_port",
        "next_port",
        "vessel_imo",
        "vessel_type",
        "vessel_loa_m",
        "vessel_beam_m",
    }.intersection(extracted.keys())
    vessel_identity = {
        "vessel_name",
        "vessel_imo",
        "vessel_call_sign",
    }.intersection(extracted.keys())
    return len(hits) >= 5 and len(strong_hits) >= 3 and bool(vessel_identity)


def looks_like_maneuver_report_payload(question: str) -> bool:
    extracted = _extract_labelled_values(question)
    if not extracted:
        return False
    report_hits = MANEUVER_REPORT_FIELD_HINTS.intersection(extracted.keys())
    has_window = {
        "maneuver_started_local",
        "maneuver_finished_local",
    }.issubset(extracted.keys())
    return bool(has_window and len(report_hits) >= 3)


def looks_like_abort_payload(question: str) -> bool:
    extracted = _extract_labelled_values(question)
    if not extracted:
        return False
    return bool(ABORT_FIELD_HINTS.intersection(extracted.keys()))


def extract_json_object(text: str) -> Optional[Dict]:
    if not text:
        return None
    clean = text.strip()
    try:
        payload = json.loads(clean)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass

    match = JSON_BLOCK_RE.search(clean)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def allowed_actions_for_role(role: str) -> List[str]:
    clean_role = (role or "").strip().lower()
    return [
        action
        for action, spec in ACTION_SPECS.items()
        if clean_role in spec["roles"]
    ]


def canonicalize_action_name(raw_action: str, maneuver_type: str = "") -> str:
    clean_action = _lookup_key(raw_action).replace(" ", "_")
    clean_type = (maneuver_type or "").strip().lower()
    if clean_action in ACTION_SPECS:
        return clean_action
    if clean_type not in MANEUVER_TYPES:
        clean_type = "entry"

    generic_aliases = {
        "approve_maneuver": f"approve_{clean_type}",
        "approve_manobra": f"approve_{clean_type}",
        "approve_port_call": f"approve_{clean_type}",
        "approve_scale": f"approve_{clean_type}",
        "approve": f"approve_{clean_type}",
        "abort_maneuver": f"abort_{clean_type}",
        "cancel_maneuver": "delete_maneuver",
        "abort_port_call": f"abort_{clean_type}",
        "cancel_port_call": "delete_port_call",
        "complete_maneuver": f"complete_{clean_type}",
        "confirm_maneuver": f"complete_{clean_type}",
        "complete_port_call": f"complete_{clean_type}",
        "report_maneuver": f"{clean_type}_report",
        "edit_plan": "edit_maneuver_plan",
        "update_plan": "edit_maneuver_plan",
        "edit_maneuver": "edit_maneuver_plan",
        "edit_report": "edit_maneuver_plan",
        "update_report": "edit_maneuver_plan",
    }
    if clean_action in generic_aliases:
        return generic_aliases[clean_action]

    for family_prefix, family_name in GENERIC_ACTION_FAMILIES:
        if clean_action.startswith(family_prefix):
            if family_name == "approve":
                return f"approve_{clean_type}"
            if family_name == "abort":
                return f"abort_{clean_type}"
            if family_name == "cancel":
                return "delete_maneuver"
            if family_name == "complete":
                return f"complete_{clean_type}"
            if family_name == "report":
                return f"{clean_type}_report"
            if family_name == "edit_plan":
                return "edit_maneuver_plan"
            if family_name == "edit_report":
                return "edit_maneuver_plan"
    return clean_action


def _canonical_field_name(name: str) -> str:
    clean = _lookup_key(name).replace(" ", "_")
    return FIELD_ALIASES.get(clean, name.strip())


def normalize_action_fields(action: str, fields: Dict) -> Dict:
    normalized: Dict = {}

    def _consume(payload: Dict) -> None:
        for key, value in (payload or {}).items():
            canonical = _canonical_field_name(str(key))
            if canonical in NESTED_FIELD_GROUPS and isinstance(value, dict):
                _consume(value)
                continue
            if isinstance(value, dict):
                normalized[canonical] = {
                    sub_key: sub_value
                    for sub_key, sub_value in value.items()
                }
                continue
            if isinstance(value, list):
                normalized[canonical] = value
                continue
            if value is None:
                normalized[canonical] = ""
            else:
                normalized[canonical] = str(value).strip()

    _consume(fields or {})

    if "constraints" in normalized and isinstance(normalized.get("constraints"), str):
        normalized["constraints"] = [
            item.strip()
            for item in re.split(r"[,\s]+", normalized["constraints"])
            if item.strip()
        ]
    if "reason" in normalized:
        if action in {"edit_maneuver_plan", "edit_maneuver_report"} and not normalized.get("change_reason"):
            normalized["change_reason"] = normalized["reason"]
        elif action in {"abort_entry", "abort_departure", "abort_shift"} and not normalized.get("aborted_reason"):
            normalized["aborted_reason"] = normalized["reason"]
        normalized.pop("reason", None)

    if action in {"abort_entry", "abort_departure", "abort_shift"}:
        if normalized.get("change_reason") and not normalized.get("aborted_reason"):
            normalized["aborted_reason"] = normalized["change_reason"]
        normalized.pop("change_reason", None)

    if action == "edit_maneuver_plan" and normalized.get("eta_local") and not normalized.get("planned_at_local"):
        normalized["planned_at_local"] = normalized["eta_local"]

    if action == "create_port_call":
        if normalized.get("draft_m") and not normalized.get("vessel_max_draft_m"):
            normalized["vessel_max_draft_m"] = normalized["draft_m"]
        if normalized.get("vessel_max_draft_m") and not normalized.get("draft_m"):
            normalized["draft_m"] = normalized["vessel_max_draft_m"]
        vessel_name = " ".join(str(normalized.get("vessel_name") or "").split())
        vessel_imo = " ".join(str(normalized.get("vessel_imo") or "").split())
        if re.fullmatch(r"\d{7}", vessel_name):
            if not vessel_imo:
                normalized["vessel_imo"] = vessel_name
            normalized["vessel_name"] = ""
    for berth_field in ("berth", "origin_berth", "destination_berth"):
        berth_value = " ".join(str(normalized.get(berth_field) or "").split())
        if not berth_value:
            continue
        canonical = canonicalize_berth_label(berth_value, berth_options=BERTH_OPTIONS)
        if is_known_berth_label(canonical, berth_options=BERTH_OPTIONS):
            normalized[berth_field] = canonical
    if normalized.get("notes") is None:
        normalized["notes"] = ""
    return normalized


def required_missing_fields(action: str, fields: Dict) -> List[str]:
    required = REQUIRED_FIELDS_BY_ACTION.get(action, set())
    missing = []
    for field in required:
        value = fields.get(field)
        if isinstance(value, list):
            if not value:
                missing.append(field)
            continue
        if not " ".join(str(value or "").split()):
            missing.append(field)
    return sorted(missing)


def required_target_missing_fields(action: str, target: Dict) -> List[str]:
    if action == "create_port_call":
        return []

    missing = []
    maneuver_id = _normalize_maneuver_id((target or {}).get("maneuver_id", ""))
    if not maneuver_id and not (
        " ".join(str((target or {}).get("reference_code") or "").split())
        or " ".join(str((target or {}).get("vessel_name") or "").split())
    ):
        missing.append("target_port_call")

    maneuver_actions = {
        "edit_maneuver_plan",
        "delete_maneuver",
        "approve_entry",
        "approve_departure",
        "approve_shift",
        "entry_report",
        "departure_report",
        "shift_report",
        "edit_maneuver_report",
        "delete_maneuver_report",
        "abort_entry",
        "abort_departure",
        "abort_shift",
    }
    if action in maneuver_actions and not maneuver_id and (target or {}).get("maneuver_type", "") not in MANEUVER_TYPES:
        missing.append("maneuver_type")
    return missing


def proposal_missing_field_labels(action: str, fields: Dict, target: Dict) -> List[str]:
    missing = required_missing_fields(action, fields) + required_target_missing_fields(action, target)
    if _edit_action_needs_update_field(action, fields):
        missing.append("update_field")
    deduped = []
    for item in missing:
        if item not in deduped:
            deduped.append(item)
    return display_missing_field_labels(deduped)


def _edit_action_needs_update_field(action: str, fields: Dict) -> bool:
    editable_fields_by_action = {
        "edit_port_call": {
            "vessel_name",
            "eta_local",
            "berth",
            "last_port",
            "next_port",
            "notes",
            "constraints",
            "vessel_short_name",
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
        },
        "edit_maneuver_plan": {
            "planned_at_local",
            "planned_departure_at_local",
            "planned_shift_at_local",
            "origin",
            "destination",
            "origin_berth",
            "destination_berth",
            "berth",
            "next_port",
            "draft_m",
            "tug_count",
            "constraints",
            "notes",
            "plan_observations",
        },
        "edit_maneuver_report": {
            "maneuver_started_local",
            "maneuver_finished_local",
            "draft_m",
            "notes",
        },
    }
    editable_fields = editable_fields_by_action.get(action)
    if not editable_fields:
        return False
    payload = fields or {}
    for key in editable_fields:
        value = payload.get(key)
        if isinstance(value, list):
            if value:
                return False
            continue
        if " ".join(str(value or "").split()):
            return False
    return True


def _invalid_berth_field_labels(action: str, fields: Dict, target: Dict) -> List[str]:
    maneuver_type = (target.get("maneuver_type") or "").strip().lower()
    invalid: List[str] = []

    def register(field_key: str, raw_value: object, custom_label: str) -> None:
        clean = " ".join(str(raw_value or "").split())
        if not clean:
            return
        canonical = canonicalize_berth_label(clean, berth_options=BERTH_OPTIONS)
        if is_known_berth_label(canonical, berth_options=BERTH_OPTIONS):
            return
        if custom_label not in invalid:
            invalid.append(custom_label)

    if action in {"create_port_call", "edit_port_call"}:
        register("berth", fields.get("berth"), "cais/fundeadouro válido")
    elif action == "schedule_shift":
        register("origin_berth", fields.get("origin_berth") or fields.get("origin"), "origem válida")
        register("destination_berth", fields.get("destination_berth") or fields.get("destination"), "destino válido")
    elif action == "edit_maneuver_plan":
        if maneuver_type == "entry":
            register("berth", fields.get("berth") or fields.get("destination"), "destino válido")
        elif maneuver_type == "shift":
            register("origin_berth", fields.get("origin_berth") or fields.get("origin"), "origem válida")
            register("destination_berth", fields.get("destination_berth") or fields.get("destination"), "destino válido")

    return invalid


def _clean_extracted_value(canonical: str, raw_value: str) -> str:
    clean = " ".join(str(raw_value or "").strip().split())
    if not clean:
        return ""
    clean_key = _lookup_key(clean)
    if _is_placeholder_field_value(canonical, clean, clean_key):
        return ""
    clean = re.split(
        r"\bFicha do Navio\b|\bDados Operacionais\b|\bRestri[cç][õo]es?\b",
        clean,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ,;.:-")
    if canonical == "tug_count":
        match = re.search(r"\d+", clean)
        return match.group(0) if match else clean
    if canonical == "maneuver_id":
        match = re.search(r"[A-Za-z0-9-]+", clean)
        return match.group(0) if match else clean
    if canonical in {"vessel_loa_m", "vessel_beam_m", "vessel_gt_t", "vessel_dwt_t", "vessel_max_draft_m", "draft_m"}:
        match = re.search(r"\d+(?:[\s.,]\d+)*", clean)
        if not match:
            return clean
        numeric_value = match.group(0)
        return re.sub(r"(?<=\d)\s+(?=\d)", "", numeric_value)
    return clean


def _is_placeholder_field_value(canonical: str, value: str, lookup_value: str | None = None) -> bool:
    clean_key = lookup_value if lookup_value is not None else _lookup_key(value)
    if not clean_key:
        return True
    placeholder_keys = {
        "dd mm aaaa hh mm",
        "aaaa mm dd hh mm",
        "sim nao desconhecido",
        "yes no unknown",
        "entrada saida mudanca",
        "entry departure shift",
        "saida mudanca",
        "departure shift",
    }
    if clean_key in placeholder_keys:
        return True
    if canonical in {
        "eta_local",
        "planned_at_local",
        "planned_departure_at_local",
        "planned_shift_at_local",
        "maneuver_started_local",
        "maneuver_finished_local",
    } and re.search(r"\b(dd|aaaa|yyyy|hh)\b", clean_key):
        return True
    if canonical == "maneuver_type":
        type_hits = sum(
            1
            for token in ("entrada", "entry", "saida", "departure", "mudanca", "shift")
            if re.search(rf"\b{token}\b", clean_key)
        )
        if type_hits > 1:
            return True
    if canonical == "constraints" and clean_key in {
        "daylight gas estrategico",
        "daylight gas estrategico opcoes",
    }:
        return True
    if canonical in {"vessel_bow_thruster", "vessel_stern_thruster"} and clean_key in {
        "sim nao",
        "sim nao desconhecido",
        "yes no",
        "yes no unknown",
    }:
        return True
    return False


def _line_label_lookup(alias_map: Dict[str, List[str]]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    alias_items: List[tuple[str, str]] = []
    for canonical, aliases in alias_map.items():
        for alias in aliases:
            clean_alias = _lookup_key(alias)
            if clean_alias:
                alias_items.append((clean_alias, canonical))
    for clean_alias, canonical in sorted(alias_items, key=lambda item: len(item[0]), reverse=True):
        lookup.setdefault(clean_alias, canonical)
    return lookup


def _extract_line_labelled_values(question: str, alias_map: Dict[str, List[str]]) -> Optional[Dict[str, object]]:
    if "\n" not in (question or ""):
        return None
    alias_lookup = _line_label_lookup(alias_map)
    extracted: Dict[str, object] = {}
    saw_label = False
    for raw_line in (question or "").splitlines():
        line = raw_line.strip()
        if not line or (":" not in line and "=" not in line):
            continue
        separator_indexes = [
            index
            for index in (line.find(":"), line.find("="))
            if index >= 0
        ]
        if not separator_indexes:
            continue
        separator_index = min(separator_indexes)
        raw_label = line[:separator_index].strip(" \t-*•")
        canonical = alias_lookup.get(_lookup_key(raw_label))
        if not canonical:
            continue
        saw_label = True
        raw_value = line[separator_index + 1 :]
        clean_value = _clean_extracted_value(canonical, raw_value)
        if clean_value:
            extracted[canonical] = clean_value
    return extracted if saw_label else None


def _extract_values_from_alias_map(question: str, alias_map: Dict[str, List[str]]) -> Dict[str, object]:
    line_values = _extract_line_labelled_values(question, alias_map)
    if line_values is not None:
        return line_values

    text = " ".join((question or "").strip().split())
    if not text:
        return {}
    search_text, index_map = _normalized_ascii_text_with_index_map(text)
    raw_hits = []
    for canonical, aliases in alias_map.items():
        sorted_aliases = sorted(
            aliases,
            key=lambda item: len(_normalized_ascii_text(item)),
            reverse=True,
        )
        for alias in sorted_aliases:
            needle = _normalized_ascii_text(alias).strip()
            pattern = re.compile(
                rf"(^|[\s,;]){re.escape(needle)}\s*(?:=|:|\beh\b|\be\b|\bé\b)\s*",
                flags=re.IGNORECASE,
            )
            match = pattern.search(search_text)
            if not match:
                continue
            raw_hits.append((match.start(), match.end(), canonical, needle))
            break
    if not raw_hits:
        return {}
    raw_hits.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    hits = []
    for hit in raw_hits:
        if hits and hit[0] < hits[-1][1]:
            continue
        hits.append(hit)
    extracted: Dict[str, object] = {}
    for index, (_start, value_start, canonical, needle) in enumerate(hits):
        next_start = hits[index + 1][0] if index + 1 < len(hits) else len(search_text)
        original_value_start = index_map[value_start] if value_start < len(index_map) else len(text)
        original_end = index_map[next_start] if next_start < len(index_map) else len(text)
        raw_value = text[original_value_start:original_end]
        clean_value = _clean_extracted_value(canonical, raw_value)
        if clean_value:
            extracted[canonical] = clean_value
    return extracted


def _extract_labelled_values(question: str) -> Dict[str, object]:
    extracted = _extract_values_from_alias_map(question, PENDING_UPDATE_FIELD_ALIASES)
    constraint_flags = _extract_constraint_flags(question)
    if constraint_flags:
        extracted["constraints"] = constraint_flags
    return extracted


def _normalize_command_name(value: str) -> str:
    return _lookup_key(value).replace(" ", "-")


def _normalize_maneuver_type_label(value: str) -> str:
    clean = _lookup_key(value)
    if _is_placeholder_field_value("maneuver_type", value, clean):
        return ""
    mapping = {
        "entrada": "entry",
        "entry": "entry",
        "saida": "departure",
        "saida do navio": "departure",
        "departure": "departure",
        "mudanca": "shift",
        "mudanca de cais": "shift",
        "shift": "shift",
    }
    return mapping.get(clean, "")


def _normalize_maneuver_id(value: str) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        return ""
    return re.sub(r"[^A-Za-z0-9-]", "", clean).lower()


def _looks_like_scale_reference(value: str) -> bool:
    clean = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    return clean.startswith("PTSET")


def _looks_like_maneuver_id_token(value: str) -> bool:
    clean = _normalize_maneuver_id(value)
    return bool(
        re.fullmatch(r"[a-f0-9]{8}", clean)
        or re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4,})+", clean)
    )


def _extract_positional_slash_target(body: str) -> Dict[str, str]:
    if ":" in (body or "") or "=" in (body or ""):
        return {}
    tokens = [token.strip() for token in re.split(r"[\s,;]+", body or "") if token.strip()]
    if not tokens:
        return {}

    identifier_tokens: List[str] = []
    maneuver_type = ""
    for token in tokens:
        normalized_type = _normalize_maneuver_type_label(token)
        if normalized_type and not maneuver_type:
            maneuver_type = normalized_type
            continue
        if re.fullmatch(r"[A-Za-z0-9-]{6,}", token):
            identifier_tokens.append(token)

    positional_target: Dict[str, str] = {}
    if maneuver_type:
        positional_target["maneuver_type"] = maneuver_type
    if len(identifier_tokens) == 1:
        identifier = identifier_tokens[0]
        if _looks_like_maneuver_id_token(identifier):
            positional_target["maneuver_id"] = _normalize_maneuver_id(identifier)
        else:
            positional_target["reference_code"] = identifier
    elif len(identifier_tokens) > 1:
        reference_token = next((token for token in identifier_tokens if _looks_like_scale_reference(token)), "")
        maneuver_token = next((token for token in identifier_tokens if _looks_like_maneuver_id_token(token)), "")
        if reference_token:
            positional_target["reference_code"] = reference_token
        elif maneuver_token and len(identifier_tokens) == 2:
            positional_target["reference_code"] = next(token for token in identifier_tokens if token != maneuver_token)
        if maneuver_token:
            positional_target["maneuver_id"] = _normalize_maneuver_id(maneuver_token)
    return positional_target


def _extract_slash_maneuver_type(value: str) -> str:
    if "\n" in (value or ""):
        alias_lookup = _line_label_lookup({"maneuver_type": SLASH_COMMAND_FIELD_ALIASES["maneuver_type"]})
        for raw_line in (value or "").splitlines():
            line = raw_line.strip()
            if not line or (":" not in line and "=" not in line):
                continue
            separator_indexes = [
                index
                for index in (line.find(":"), line.find("="))
                if index >= 0
            ]
            if not separator_indexes:
                continue
            separator_index = min(separator_indexes)
            raw_label = line[:separator_index].strip(" \t-*•")
            if alias_lookup.get(_lookup_key(raw_label)) == "maneuver_type":
                return _normalize_maneuver_type_label(line[separator_index + 1 :])
    labelled = _extract_values_from_alias_map(
        value or "",
        {"maneuver_type": SLASH_COMMAND_FIELD_ALIASES["maneuver_type"]},
    )
    if "maneuver_type" in labelled:
        return _normalize_maneuver_type_label(str(labelled.get("maneuver_type") or ""))
    match = re.search(
        r"\btipo\s+de\s+manobra\s*(?:=|:|\beh\b|\be\b)\s*(entrada|saida|saída|mudanca|mudança|entry|departure|shift)\b",
        value or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return _normalize_maneuver_type_label(match.group(1))


def looks_like_slash_command(question: str) -> bool:
    return bool((question or "").strip().startswith("/"))


def parse_slash_command(question: str, role: str) -> Optional[Dict]:
    raw = (question or "").strip()
    if not raw.startswith("/"):
        return None
    first_line, _, remainder = raw.partition("\n")
    first_line = first_line.strip()
    head, _, tail = first_line.partition(" ")
    command_name = _normalize_command_name(head.lstrip("/"))
    command = SLASH_COMMAND_ALIASES.get(command_name)
    body = "\n".join(part for part in [tail.strip(), remainder.strip()] if part).strip()
    if not command:
        return {
            "intent": "help",
            "answer": "Comando não reconhecido.\n\n" + build_slash_help(role),
        }
    if command == "help":
        return {"intent": "help", "answer": build_slash_help(role)}
    if command == "local_warnings":
        return {"intent": "query", "command": "local_warnings", "argument": body}
    if command == "wave":
        return {"intent": "query", "command": "wave", "argument": body}
    if command == "tides":
        return {"intent": "query", "command": "tides", "argument": body}
    if command == "weather":
        return {"intent": "query", "command": "weather", "argument": body}
    if command in {"planning", "planning_approved", "planning_pending"}:
        return {"intent": "query", "command": command, "argument": body}
    if command in {"colreg_list", "colreg_rule"}:
        return {"intent": "query", "command": command, "argument": body or tail.strip()}
    if command == "rule":
        return {"intent": "query", "command": "rule", "argument": body or tail.strip()}
    if command in {
        "consult_scale",
        "consult_maneuver",
        "consult_scale_cost",
        "consult_maneuver_cost",
        "consult_vessel",
    }:
        return {"intent": "query", "command": command, "argument": body or tail.strip()}
    if command == "event_report":
        return {"intent": "event_report", "argument": body or tail.strip()}

    command_aliases = _extract_values_from_alias_map(body, SLASH_COMMAND_FIELD_ALIASES) if body else {}
    extracted_fields = _extract_labelled_values(body)
    positional_target = _extract_positional_slash_target(tail.strip()) if tail.strip() else {}
    target = {
        "maneuver_id": _normalize_maneuver_id(
            extracted_fields.get("maneuver_id") or command_aliases.get("maneuver_id", "")
        ),
        "reference_code": " ".join(
            str(extracted_fields.get("reference_code") or command_aliases.get("reference_code") or "").split()
        ),
        "vessel_name": " ".join(str(extracted_fields.get("vessel_name") or "").split()),
        "maneuver_type": _extract_slash_maneuver_type(body)
        or _normalize_maneuver_type_label(extracted_fields.get("maneuver_type") or command_aliases.get("maneuver_type", "")),
        "raw_query": raw,
        "argument": body,
    }
    if not target["maneuver_id"] and positional_target.get("maneuver_id"):
        target["maneuver_id"] = positional_target["maneuver_id"]
    if not target["reference_code"] and positional_target.get("reference_code"):
        target["reference_code"] = positional_target["reference_code"]
    if not target["vessel_name"] and positional_target.get("vessel_name"):
        target["vessel_name"] = positional_target["vessel_name"]
    if not target["maneuver_type"] and positional_target.get("maneuver_type"):
        target["maneuver_type"] = positional_target["maneuver_type"]
    for target_only_field in ("reference_code", "maneuver_id", "maneuver_type"):
        extracted_fields.pop(target_only_field, None)

    if command == "validate_maneuver":
        template = build_validate_maneuver_reply_template()
        if target["reference_code"] and target["maneuver_type"]:
            target["maneuver_id"] = ""
        has_explicit_target = bool(target["maneuver_id"]) or bool(
            (target["reference_code"] or target["vessel_name"]) and target["maneuver_type"]
        )
        has_direct_validation_description = bool(
            target["maneuver_type"]
            and re.search(r"\b(tanquisado|eco[-\s]*oil|ecooil|ecoil|lisnave|mitrena|tms\s*1|tms1)\b", body, re.IGNORECASE)
        )
        if has_direct_validation_description:
            target["reference_code"] = ""
            target["vessel_name"] = ""
            return {"intent": "validate", "target": target}
        if not has_explicit_target:
            return {"intent": "template", "answer": template}
        return {"intent": "validate", "target": target}

    maneuver_payload_fields = {
        "planned_at_local",
        "planned_departure_at_local",
        "planned_shift_at_local",
        "origin",
        "destination",
        "origin_berth",
        "destination_berth",
        "draft_m",
        "tug_count",
        "maneuver_started_local",
        "maneuver_finished_local",
        "aborted_reason",
    }

    action = ""
    template = ""
    if command == "register_scale":
        action = "create_port_call"
        template = build_action_reply_template(action)
    elif command == "edit_scale":
        action = "edit_port_call"
        template = build_action_reply_template(action)
        if any(field in extracted_fields for field in maneuver_payload_fields):
            return {
                "intent": "unsupported",
                "answer": "Este comando edita a escala, não a manobra. Para alterar planeamento usa /editar-manobra.\n\n" + build_edit_maneuver_plan_reply_template(),
            }
        if not (target["reference_code"] or target["vessel_name"]):
            proposal = normalize_action_candidate(
                {
                    "intent": "action",
                    "action": action,
                    "confidence": 1.0,
                    "reason": f"Comando explícito /{command_name}.",
                    "target": target,
                    "fields": extracted_fields,
                    "missing_fields": [],
                },
                role,
            )
            if proposal and proposal.get("intent") == "action":
                proposal["missing_fields"] = proposal_missing_field_labels(action, proposal.get("fields") or {}, proposal.get("target") or {})
                return {"intent": "template", "answer": template, "proposal": proposal}
            if proposal and proposal.get("intent") == "unsupported":
                return {"intent": "unsupported", "answer": proposal.get("reason") or "A ação pedida não está autorizada para este perfil."}
            return {"intent": "template", "answer": template}
    elif command == "delete_scale":
        action = "delete_port_call"
        template = build_action_reply_template(action)
        if any(field in extracted_fields for field in maneuver_payload_fields):
            return {
                "intent": "unsupported",
                "answer": "Este comando apaga a escala, não a manobra. Para cancelar uma manobra pendente usa /cancelar-manobra.\n\n" + build_delete_maneuver_reply_template(),
            }
        if not (target["reference_code"] or target["vessel_name"]):
            proposal = normalize_action_candidate(
                {
                    "intent": "action",
                    "action": action,
                    "confidence": 1.0,
                    "reason": f"Comando explícito /{command_name}.",
                    "target": target,
                    "fields": extracted_fields,
                    "missing_fields": [],
                },
                role,
            )
            if proposal and proposal.get("intent") == "action":
                proposal["missing_fields"] = proposal_missing_field_labels(action, proposal.get("fields") or {}, proposal.get("target") or {})
                return {"intent": "template", "answer": template, "proposal": proposal}
            if proposal and proposal.get("intent") == "unsupported":
                return {"intent": "unsupported", "answer": proposal.get("reason") or "A ação pedida não está autorizada para este perfil."}
            return {"intent": "template", "answer": template}
    else:
        if command == "create_maneuver" and target["maneuver_type"] == "entry":
            return {
                "intent": "unsupported",
                "answer": "A entrada inicial já fica criada quando registas a escala. Para alterar essa entrada usa /editar-manobra.\n\n" + build_edit_maneuver_plan_reply_template(),
            }
        maneuver_action_map = {
            "create_maneuver": {
                "departure": "schedule_departure",
                "shift": "schedule_shift",
            },
            "edit_maneuver": {
                "entry": "edit_maneuver_plan",
                "departure": "edit_maneuver_plan",
                "shift": "edit_maneuver_plan",
            },
            "delete_maneuver": {
                "entry": "delete_maneuver",
                "departure": "delete_maneuver",
                "shift": "delete_maneuver",
            },
            "approve_maneuver": {
                "entry": "approve_entry",
                "departure": "approve_departure",
                "shift": "approve_shift",
            },
            "create_report": {
                "entry": "entry_report",
                "departure": "departure_report",
                "shift": "shift_report",
            },
            "edit_report": {
                "entry": "edit_maneuver_report",
                "departure": "edit_maneuver_report",
                "shift": "edit_maneuver_report",
            },
            "delete_report": {
                "entry": "delete_maneuver_report",
                "departure": "delete_maneuver_report",
                "shift": "delete_maneuver_report",
            },
            "abort_maneuver": {
                "entry": "abort_entry",
                "departure": "abort_departure",
                "shift": "abort_shift",
            },
        }
        template_map = {
            "create_maneuver": build_create_maneuver_reply_template(),
            "edit_maneuver": build_edit_maneuver_plan_reply_template(),
            "delete_maneuver": build_delete_maneuver_reply_template(),
            "approve_maneuver": build_approval_reply_template(),
            "create_report": build_command_report_reply_template(),
            "edit_report": build_edit_report_reply_template(),
            "delete_report": build_delete_report_reply_template(),
            "abort_maneuver": build_command_abort_reply_template(),
        }
        template = template_map.get(command, "")
        has_maneuver_target = bool(target["maneuver_id"])
        if (not has_maneuver_target and not (target["reference_code"] or target["vessel_name"])) or (not has_maneuver_target and not target["maneuver_type"]):
            preview_action = ""
            if command == "edit_maneuver":
                preview_action = "edit_maneuver_plan"
            elif command == "delete_maneuver":
                preview_action = "delete_maneuver"
            elif target["maneuver_type"]:
                preview_action = maneuver_action_map.get(command, {}).get(target["maneuver_type"], "")
            elif has_maneuver_target and command == "create_report":
                preview_action = "entry_report"
            elif has_maneuver_target and command == "approve_maneuver":
                preview_action = "approve_entry"
            elif has_maneuver_target and command == "edit_report":
                preview_action = "edit_maneuver_report"
            elif has_maneuver_target and command == "delete_report":
                preview_action = "delete_maneuver_report"
            elif has_maneuver_target and command == "abort_maneuver":
                preview_action = "abort_entry"
            if preview_action:
                proposal = normalize_action_candidate(
                    {
                        "intent": "action",
                        "action": preview_action,
                        "confidence": 1.0,
                        "reason": f"Comando explícito /{command_name}.",
                        "target": target,
                        "fields": extracted_fields,
                        "missing_fields": [],
                    },
                    role,
                )
                if proposal and proposal.get("intent") == "action":
                    proposal["missing_fields"] = proposal_missing_field_labels(preview_action, proposal.get("fields") or {}, proposal.get("target") or {})
                    return {"intent": "template", "answer": template, "proposal": proposal}
                if proposal and proposal.get("intent") == "unsupported":
                    return {"intent": "unsupported", "answer": proposal.get("reason") or "A ação pedida não está autorizada para este perfil."}
            return {"intent": "template", "answer": template}
        action = maneuver_action_map.get(command, {}).get(target["maneuver_type"], "")
        if not action and target["maneuver_id"]:
            fallback_by_command = {
                "create_report": "entry_report",
                "approve_maneuver": "approve_entry",
                "edit_report": "edit_maneuver_report",
                "delete_report": "delete_maneuver_report",
                "abort_maneuver": "abort_entry",
                "edit_maneuver": "edit_maneuver_plan",
                "delete_maneuver": "delete_maneuver",
            }
            action = fallback_by_command.get(command, "")
        if not action:
            return {"intent": "template", "answer": template}

    proposal = normalize_action_candidate(
        {
            "intent": "action",
            "action": action,
            "confidence": 1.0,
            "reason": f"Comando explícito /{command_name}.",
            "target": target,
            "fields": extracted_fields,
            "missing_fields": [],
        },
        role,
    )
    if proposal and proposal.get("intent") == "action":
        fields = proposal.setdefault("fields", {})
        if action == "schedule_departure":
            if fields.get("planned_at_local") and not fields.get("planned_departure_at_local"):
                fields["planned_departure_at_local"] = fields["planned_at_local"]
            if fields.get("destination") and not fields.get("next_port"):
                fields["next_port"] = fields["destination"]
        elif action == "schedule_shift":
            if fields.get("planned_at_local") and not fields.get("planned_shift_at_local"):
                fields["planned_shift_at_local"] = fields["planned_at_local"]
            if fields.get("destination") and not fields.get("destination_berth"):
                fields["destination_berth"] = fields["destination"]
            if fields.get("origin") and not fields.get("origin_berth"):
                fields["origin_berth"] = fields["origin"]
    if not proposal or proposal.get("intent") != "action":
        answer = proposal.get("reason") if proposal else "Comando inválido."
        if template and not (proposal and proposal.get("intent") == "unsupported"):
            answer = f"{answer}\n\n{template}"
        return {"intent": "unsupported", "answer": answer}
    return {"intent": "action", "proposal": proposal}


def _normalize_time_only(value: str, fallback: str) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not TIME_ONLY_RE.match(clean):
        return clean
    fallback_clean = " ".join(str(fallback or "").strip().split())
    if "T" not in fallback_clean:
        return clean
    date_part = fallback_clean.split("T", 1)[0]
    return f"{date_part}T{clean}"


def extract_pending_field_updates(question: str, proposal: Dict) -> Dict[str, str]:
    extracted = _extract_labelled_values(question)
    if not extracted:
        return {}
    fields = proposal.get("fields") or {}
    action = proposal.get("action") or ""

    if action == "edit_maneuver_plan" and extracted.get("planned_at_local"):
        fallback = fields.get("planned_at_local") or fields.get("eta_local") or ""
        extracted["planned_at_local"] = _normalize_time_only(extracted["planned_at_local"], fallback)
    if action == "schedule_departure" and extracted.get("planned_departure_at_local"):
        fallback = fields.get("planned_departure_at_local") or ""
        extracted["planned_departure_at_local"] = _normalize_time_only(extracted["planned_departure_at_local"], fallback)
    if action == "schedule_shift" and extracted.get("planned_shift_at_local"):
        fallback = fields.get("planned_shift_at_local") or ""
        extracted["planned_shift_at_local"] = _normalize_time_only(extracted["planned_shift_at_local"], fallback)
    return extracted


def extract_pending_target_updates(question: str) -> Dict[str, str]:
    extracted = _extract_values_from_alias_map(
        question,
        {
            "maneuver_id": SLASH_COMMAND_FIELD_ALIASES["maneuver_id"],
            "reference_code": SLASH_COMMAND_FIELD_ALIASES["reference_code"],
            "vessel_name": PENDING_UPDATE_FIELD_ALIASES["vessel_name"],
            "maneuver_type": SLASH_COMMAND_FIELD_ALIASES["maneuver_type"],
        },
    )
    target = {}
    if extracted.get("maneuver_id"):
        target["maneuver_id"] = _normalize_maneuver_id(str(extracted["maneuver_id"]))
    if extracted.get("reference_code"):
        target["reference_code"] = " ".join(str(extracted["reference_code"]).split())
    if extracted.get("vessel_name"):
        target["vessel_name"] = " ".join(str(extracted["vessel_name"]).split())
    if extracted.get("maneuver_type"):
        maneuver_type = _normalize_maneuver_type_label(str(extracted["maneuver_type"]))
        if maneuver_type:
            target["maneuver_type"] = maneuver_type
    return target


def merge_action_candidate(existing: Dict, updates: Dict, role: str) -> Optional[Dict]:
    if not isinstance(existing, dict):
        return None
    if not isinstance(updates, dict):
        return existing

    action = (updates.get("action") or existing.get("action") or "").strip()
    target = dict(existing.get("target") or {})
    for key, value in (updates.get("target") or {}).items():
        if isinstance(value, str):
            if " ".join(value.split()):
                target[key] = value
            continue
        if value not in (None, [], {}):
            target[key] = value
    existing_fields = existing.get("fields") or {}
    update_fields = dict(updates.get("fields") or {})
    update_reason = " ".join(str(updates.get("reason") or "").strip().split())
    if action == "edit_maneuver_plan" and update_reason:
        if not " ".join(str(update_fields.get("change_reason") or existing_fields.get("change_reason") or "").split()):
            update_fields["change_reason"] = update_reason
    elif action in {"abort_entry", "abort_departure", "abort_shift"} and update_reason:
        if not " ".join(str(update_fields.get("aborted_reason") or existing_fields.get("aborted_reason") or "").split()):
            update_fields["aborted_reason"] = update_reason
    merged_fields = dict(existing_fields)
    for key, value in update_fields.items():
        if isinstance(value, str):
            if " ".join(value.split()):
                merged_fields[key] = value
            continue
        if isinstance(value, list):
            if value:
                merged_fields[key] = value
            continue
        if isinstance(value, dict):
            if value:
                merged_fields[key] = value
            continue
        if value is not None:
            merged_fields[key] = value
    fields = normalize_action_fields(action, merged_fields)
    candidate = normalize_action_candidate(
        {
            "intent": "action",
            "action": action,
            "confidence": updates.get("confidence", existing.get("confidence", 0.0)),
            "reason": updates.get("reason") or existing.get("reason", ""),
            "target": target,
            "fields": fields,
            "missing_fields": [],
        },
        role,
    )
    if not candidate:
        return None
    for key in ("port_call_id", "maneuver_id"):
        if existing.get(key) and not candidate.get(key):
            candidate[key] = existing.get(key)
    return candidate


def visible_port_calls_from_activity(port_activity: Dict) -> List[Dict]:
    visible = {}
    for key in ("arrivals", "in_port", "departed", "aborted", "departure_candidates"):
        for item in port_activity.get(key, []) or []:
            item_id = item.get("id")
            if item_id:
                visible[item_id] = item
    for key in ("planned_maneuvers", "archived_maneuvers"):
        for item in port_activity.get(key, []) or []:
            item_id = item.get("port_call_id") or item.get("id")
            if not item_id:
                continue
            existing = visible.get(item_id, {})
            visible[item_id] = {
                **item,
                **existing,
                "id": item_id,
                "reference_code": existing.get("reference_code") or item.get("reference_code"),
                "vessel_name": existing.get("vessel_name") or item.get("vessel_name"),
            }
    return sorted(
        visible.values(),
        key=lambda item: (
            item.get("eta")
            or item.get("ata")
            or item.get("departure_at")
            or item.get("planned_value")
            or item.get("actual_value")
            or item.get("date_value")
            or "",
            item.get("vessel_name") or "",
        ),
    )


def summarize_port_calls(port_calls: List[Dict], limit: int = 18) -> str:
    if not port_calls:
        return "Sem escalas visíveis."
    rows = []
    for item in port_calls[:limit]:
        berth_label = item.get("berth_label") or item.get("berth") or "--"
        status_label = item.get("status_label") or item.get("status") or "--"
        if (item.get("status") or "").strip().lower() == "in_port" and is_anchorage_berth(berth_label):
            status_label = "em quadro"
        rows.append(
            " | ".join(
                [
                    item.get("reference_code") or "--",
                    item.get("vessel_name") or "--",
                    status_label,
                    berth_label,
                    f"ETA {item.get('eta_label') or '--'}",
                    f"ATA {item.get('ata_label') or '--'}",
                    f"ATD {item.get('departure_label') or '--'}",
                ]
            )
        )
    extra = len(port_calls) - limit
    if extra > 0:
        rows.append(f"+{extra} escala(s) adicionais")
    return "\n".join(f"- {row}" for row in rows)


def build_operational_action_prompt(
    *,
    question: str,
    role: str,
    now_local: datetime,
    port_calls: List[Dict],
    berth_options: List[str],
    constraint_options: List[Dict],
) -> str:
    allowed_actions = allowed_actions_for_role(role)
    action_lines = []
    for action in allowed_actions:
        spec = ACTION_SPECS[action]
        action_lines.append(f"- {action}: {spec['label']}")
    constraints = ", ".join(item.get("code", "") for item in constraint_options if item.get("code"))
    berths = "\n".join(f"- {item}" for item in berth_options)

    return f"""
És um classificador de ações operacionais para um portal marítimo.
Responde apenas com JSON puro, sem markdown.

Data/hora local atual: {now_local.strftime("%Y-%m-%d %H:%M")} Europe/Lisbon
Papel autenticado: {role}

Objetivo:
- Dizer se a mensagem é uma ação operacional executável no portal.
- Nunca propor edição do arquivo histórico. O arquivo é só consulta.
- Se faltar informação obrigatória, preencher `missing_fields`.
- Se a mensagem for só consulta, usa `intent: "question"`.
- Se a mensagem pedir algo proibido ou fora de papel/arquivo, usa `intent: "unsupported"`.
- Trata a ocupação portuária por slots de cais.
- Fundeadouro Norte e Fundeadouro Sul / Tróia são quadros/fundeadouros: podem ter vários navios e não contam como slots ocupados.
- Para efeitos desta demo, não rejeites uma atribuição de cais só porque o LOA do navio parece maior do que a extensão nominal do cais.
- Se o utilizador perguntar por dimensões, limita-te aos factos documentais; não concluas automaticamente que o navio "não cabe".
- Perguntas de consulta sobre janela/horário, por exemplo "a que horas", "quando", "podemos marcar piloto", "deve embarcar piloto" ou "pode atracar", são normalmente `intent: "question"` mesmo que mencionem entrada, saída ou marcação.

Ações permitidas para este papel:
{chr(10).join(action_lines) or "- nenhuma"}

Tipos de manobra possíveis:
- entry
- departure
- shift

Restrições válidas:
- {constraints or "nenhuma"}

Cais conhecidos:
{berths or "- nenhum"}

Escalas visíveis neste utilizador:
{summarize_port_calls(port_calls)}

Regras de saída:
- Campos do schema: intent, action, confidence, reason, target, fields, missing_fields.
- `intent` deve ser um de: action, question, unsupported.
- `action` deve ser uma ação permitida ou string vazia.
- `confidence` entre 0 e 1.
- `target.reference_code` e `target.vessel_name` devem vir limpos, sem inventar.
- `target.maneuver_type` deve ser entry, departure, shift ou vazio.
- Em datas/horas operacionais, usa formato `YYYY-MM-DDTHH:MM` no fuso local.
- Usa apenas a hora prevista como referência de planeamento: ETA nas entradas, ETD/hora prevista nas saídas e hora prevista nas mudanças. Não proponhas campos separados de marcação interna.
- Se a mensagem pedir alterar um planeamento existente, prefere `edit_maneuver_plan`.
- Se a mensagem pedir rever um registo já concluído, prefere `edit_maneuver_report`.
- Em `fields.constraints`, devolve códigos válidos.

Mensagem do utilizador:
{question}
""".strip()


def build_pending_action_update_prompt(
    *,
    question: str,
    role: str,
    proposal: Dict,
    berth_options: List[str],
    constraint_options: List[Dict],
) -> str:
    constraints = ", ".join(item.get("code", "") for item in constraint_options if item.get("code"))
    berths = "\n".join(f"- {item}" for item in berth_options)
    current_fields = json.dumps(proposal.get("fields") or {}, ensure_ascii=False, indent=2)
    current_target = json.dumps(proposal.get("target") or {}, ensure_ascii=False, indent=2)
    missing = ", ".join(proposal.get("missing_fields") or []) or "nenhum"

    return f"""
Estás a completar ou corrigir uma ação operacional já pendente num portal marítimo.
Responde apenas com JSON puro, sem markdown.

Papel autenticado: {role}
Ação pendente atual: {proposal.get("action", "")}
Target atual:
{current_target}

Campos atuais:
{current_fields}

Campos ainda em falta:
{missing}

Restrições válidas:
- {constraints or "nenhuma"}

Cais conhecidos:
{berths or "- nenhum"}

Objetivo:
- Se o utilizador estiver a responder ao que falta ou a corrigir dados, devolve `intent: "update"`.
- Se o utilizador quiser trocar para outra ação operacional, devolve `intent: "replace"` e inclui a nova ação completa.
- Se o utilizador quiser desistir, devolve `intent: "cancel"`.
- Se não houver alteração operacional concreta, devolve `intent: "question"`.

Schema:
- `intent`: update | replace | cancel | question | unsupported
- `action`: ação operacional ou string vazia
- `confidence`: 0..1
- `reason`: texto curto
- `target`: objeto com correções opcionais de target
- `fields`: apenas os campos novos/corrigidos

Regras:
- Em `update`, mantém a mesma ação salvo correção explícita do utilizador.
- Usa formato `YYYY-MM-DDTHH:MM` para datas/horas locais.
- Usa apenas a hora prevista como referência de planeamento; não devolvas campos separados de marcação interna.
- Não inventes valores em falta.
- Mantém a lógica operacional por slots de cais.
- Fundeadouros são quadros e não contam como slots ocupados.
- Não invalides uma atribuição de cais apenas por comparação direta entre LOA do navio e extensão nominal do cais.

Mensagem do utilizador:
{question}
""".strip()


def normalize_action_candidate(candidate: Dict, role: str) -> Optional[Dict]:
    if not isinstance(candidate, dict):
        return None
    intent = (candidate.get("intent") or "").strip().lower()
    raw_action = (candidate.get("action") or "").strip()
    if intent not in {"action", "question", "unsupported"}:
        return None

    if intent != "action":
        return {
            "intent": intent,
            "action": "",
            "confidence": float(candidate.get("confidence") or 0.0),
            "reason": " ".join((candidate.get("reason") or "").strip().split()),
            "target": {},
            "fields": {},
            "missing_fields": [],
        }

    target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
    fields = candidate.get("fields") if isinstance(candidate.get("fields"), dict) else {}
    maneuver_type = (target.get("maneuver_type") or fields.get("maneuver_type") or "").strip().lower()
    if maneuver_type not in MANEUVER_TYPES:
        maneuver_type = ""
    action = canonicalize_action_name(raw_action, maneuver_type)
    spec = ACTION_SPECS.get(action)
    if not spec or (role or "").strip().lower() not in spec["roles"]:
        return {
            "intent": "unsupported",
            "action": "",
            "confidence": float(candidate.get("confidence") or 0.0),
            "reason": "A ação pedida não está autorizada para este perfil.",
            "target": {},
            "fields": {},
            "missing_fields": [],
        }

    normalized_fields = normalize_action_fields(action, fields)
    if action == "schedule_departure":
        if normalized_fields.get("planned_at_local") and not normalized_fields.get("planned_departure_at_local"):
            normalized_fields["planned_departure_at_local"] = normalized_fields["planned_at_local"]
        if normalized_fields.get("destination") and not normalized_fields.get("next_port"):
            normalized_fields["next_port"] = normalized_fields["destination"]
    elif action == "schedule_shift":
        if normalized_fields.get("planned_at_local") and not normalized_fields.get("planned_shift_at_local"):
            normalized_fields["planned_shift_at_local"] = normalized_fields["planned_at_local"]
        if normalized_fields.get("destination") and not normalized_fields.get("destination_berth"):
            normalized_fields["destination_berth"] = normalized_fields["destination"]
        if normalized_fields.get("origin") and not normalized_fields.get("origin_berth"):
            normalized_fields["origin_berth"] = normalized_fields["origin"]
    elif action == "edit_maneuver_plan":
        if maneuver_type == "entry":
            if normalized_fields.get("destination") and not normalized_fields.get("berth"):
                normalized_fields["berth"] = normalized_fields["destination"]
        elif maneuver_type == "departure":
            if normalized_fields.get("destination") and not normalized_fields.get("next_port"):
                normalized_fields["next_port"] = normalized_fields["destination"]
        elif maneuver_type == "shift":
            if normalized_fields.get("destination") and not normalized_fields.get("destination_berth"):
                normalized_fields["destination_berth"] = normalized_fields["destination"]
            if normalized_fields.get("origin") and not normalized_fields.get("origin_berth"):
                normalized_fields["origin_berth"] = normalized_fields["origin"]
    if action == "create_port_call":
        if not normalized_fields.get("vessel_name"):
            normalized_fields["vessel_name"] = " ".join((target.get("vessel_name") or "").strip().split())
        target["maneuver_type"] = ""
    normalized_target = {
        "maneuver_id": _normalize_maneuver_id(target.get("maneuver_id", "")),
        "reference_code": " ".join((target.get("reference_code") or "").strip().split()),
        "vessel_name": " ".join((target.get("vessel_name") or "").strip().split()),
        "maneuver_type": maneuver_type,
    }
    invalid_berth_labels = _invalid_berth_field_labels(action, normalized_fields, normalized_target)
    if invalid_berth_labels:
        invalid_text = ", ".join(invalid_berth_labels)
        return {
            "intent": "unsupported",
            "action": "",
            "confidence": float(candidate.get("confidence") or 0.0),
            "reason": (
                f"Não reconheci {invalid_text}. "
                "Usa um cais/fundeadouro do catálogo do porto e repete ou reformula o pedido."
            ),
            "target": {},
            "fields": {},
            "missing_fields": [],
        }
    constraints = normalized_fields.get("constraints")
    if not isinstance(constraints, list):
        constraints = []
    missing_fields = proposal_missing_field_labels(action, normalized_fields, normalized_target)

    return {
        "intent": "action",
        "action": action,
        "confidence": max(0.0, min(float(candidate.get("confidence") or 0.0), 1.0)),
        "reason": " ".join((candidate.get("reason") or "").strip().split()),
        "target": normalized_target,
        "fields": {
            key: value
            for key, value in normalized_fields.items()
            if key != "constraints"
        }
        | {"constraints": [str(item).strip() for item in constraints if str(item).strip()]},
        "missing_fields": missing_fields,
    }


def resolve_port_call(port_calls: List[Dict], target: Dict) -> Optional[Dict]:
    if not port_calls:
        return None
    target_maneuver_id = _normalize_maneuver_id(target.get("maneuver_id"))
    if target_maneuver_id:
        matches = []
        for item in port_calls:
            row_maneuver_id = _normalize_maneuver_id(item.get("maneuver_id", ""))
            if row_maneuver_id and (
                row_maneuver_id == target_maneuver_id
                or row_maneuver_id.startswith(target_maneuver_id)
            ):
                matches.append(item)
                continue
            for maneuver in item.get("maneuver_history", []) or []:
                current_id = _normalize_maneuver_id(maneuver.get("id", ""))
                if current_id == target_maneuver_id or current_id.startswith(target_maneuver_id):
                    matches.append(item)
                    break
        if len(matches) == 1:
            return matches[0]
    target_reference = _lookup_key(target.get("reference_code"))
    target_vessel = _lookup_key(target.get("vessel_name"))

    if target_reference:
        for item in port_calls:
            if _lookup_key(item.get("reference_code")) == target_reference:
                return item
        id_exact = [
            item
            for item in port_calls
            if _lookup_key(item.get("id")) == target_reference
        ]
        if len(id_exact) == 1:
            return id_exact[0]
        id_prefix = [
            item
            for item in port_calls
            if _lookup_key(item.get("id")).startswith(target_reference)
        ]
        if len(id_prefix) == 1:
            return id_prefix[0]

    if target_vessel:
        exact = [
            item
            for item in port_calls
            if _lookup_key(item.get("vessel_name")) == target_vessel
        ]
        if len(exact) == 1:
            return exact[0]

        partial = [
            item
            for item in port_calls
            if target_vessel in _lookup_key(item.get("vessel_name"))
        ]
        if len(partial) == 1:
            return partial[0]
    return None


def _maneuver_sort_key(item: Dict) -> tuple:
    return (
        item.get("planned_at") or "",
        item.get("completed_at") or "",
        item.get("updated_at") or "",
        item.get("created_at") or "",
    )


def resolve_maneuver(port_call: Dict, action: str, maneuver_type: str, maneuver_id: str = "") -> Optional[Dict]:
    clean_maneuver_id = _normalize_maneuver_id(maneuver_id)
    history_all = list(port_call.get("maneuver_history", []) or [])
    if clean_maneuver_id:
        direct_matches = []
        for item in history_all:
            current_id = _normalize_maneuver_id(item.get("id", ""))
            if current_id == clean_maneuver_id or current_id.startswith(clean_maneuver_id):
                direct_matches.append(item)
        if len(direct_matches) == 1:
            return direct_matches[0]
        if len(direct_matches) > 1:
            direct_matches.sort(key=_maneuver_sort_key)
            return direct_matches[-1]
        return None
    clean_type = (maneuver_type or "").strip().lower()
    if clean_type not in MANEUVER_TYPES:
        return None
    history = [
        item
        for item in port_call.get("maneuver_history", []) or []
        if (item.get("type") or "").strip().lower() == clean_type
    ]
    if not history:
        return None

    if action in {"edit_maneuver_report", "delete_maneuver_report"}:
        valid_states = {"completed", "aborted"}
    elif action.endswith("_report"):
        valid_states = {"approved", "completed", "aborted"}
    elif action.startswith("approve_"):
        valid_states = {"pending"}
    elif action.startswith("abort_"):
        valid_states = {"approved"}
    elif action == "delete_maneuver":
        valid_states = {"pending"}
    elif action == "edit_maneuver_plan":
        valid_states = {"pending", "approved"}
    else:
        valid_states = {"pending", "approved", "completed"}

    candidates = [item for item in history if (item.get("state") or "").strip().lower() in valid_states]
    if action in {"edit_maneuver_report", "delete_maneuver_report"}:
        completed_candidates = [
            item
            for item in candidates
            if (item.get("state") or "").strip().lower() in {"completed", "aborted"}
        ]
        if completed_candidates:
            candidates = completed_candidates
    if not candidates:
        candidates = history
    candidates.sort(key=_maneuver_sort_key)
    return candidates[-1]


def candidate_maneuvers_for_action(port_call: Dict, action: str, maneuver_type: str) -> List[Dict]:
    clean_type = (maneuver_type or "").strip().lower()
    if clean_type not in MANEUVER_TYPES:
        return []
    history = [
        item
        for item in port_call.get("maneuver_history", []) or []
        if (item.get("type") or "").strip().lower() == clean_type
    ]
    if not history:
        return []
    if action in {"edit_maneuver_report", "delete_maneuver_report"}:
        valid_states = {"completed", "aborted"}
    elif action.endswith("_report"):
        valid_states = {"approved", "completed", "aborted"}
    elif action.startswith("approve_"):
        valid_states = {"pending"}
    elif action.startswith("abort_"):
        valid_states = {"approved"}
    elif action == "delete_maneuver":
        valid_states = {"pending"}
    elif action == "edit_maneuver_plan":
        valid_states = {"pending", "approved"}
    else:
        valid_states = {"pending", "approved", "completed"}
    candidates = [item for item in history if (item.get("state") or "").strip().lower() in valid_states]
    if action in {"edit_maneuver_report", "delete_maneuver_report"}:
        completed_candidates = [
            item
            for item in candidates
            if (item.get("state") or "").strip().lower() in {"completed", "aborted"}
        ]
        if completed_candidates:
            candidates = completed_candidates
    if not candidates:
        candidates = history
    return sorted(candidates, key=_maneuver_sort_key)


def action_prefers_explicit_maneuver_id(action: str) -> bool:
    return action in MANEUVER_ID_SENSITIVE_ACTIONS


def infer_maneuver_type(port_call: Dict, action: str) -> str:
    matching_types = [
        maneuver_type
        for maneuver_type in sorted(MANEUVER_TYPES)
        if resolve_maneuver(port_call, action, maneuver_type)
    ]
    if len(matching_types) == 1:
        return matching_types[0]
    return ""


def action_for_maneuver_type(action: str, maneuver_type: str) -> str:
    clean_type = (maneuver_type or "").strip().lower()
    if clean_type not in MANEUVER_TYPES:
        return action
    if action.startswith("approve_"):
        return f"approve_{clean_type}"
    if action.startswith("abort_"):
        return f"abort_{clean_type}"
    if action.startswith("complete_"):
        return f"complete_{clean_type}"
    if action.endswith("_report"):
        return f"{clean_type}_report"
    return action


def format_action_summary(proposal: Dict, port_call: Optional[Dict] = None) -> str:
    action = proposal.get("action") or ""
    spec = ACTION_SPECS.get(action, {})
    label = spec.get("label") or action or "Ação"
    target = port_call or {}
    proposal_target = proposal.get("target", {}) or {}
    fields = proposal.get("fields") or {}
    missing_fields = proposal.get("missing_fields") or []
    if missing_fields:
        lines = [f"Comando recebido: {label}. Ainda não está pronto para confirmar."]
    else:
        lines = [f"Proposta pronta para confirmar: {label}."]

    recognized: List[str] = []
    scale_reference = (target.get("reference_code") if target else proposal_target.get("reference_code")) or ""
    vessel_name = (target.get("vessel_name") if target else proposal_target.get("vessel_name")) or ""
    if scale_reference or vessel_name:
        recognized.append(f"Escala: {scale_reference or '--'} · {vessel_name or '--'}")
    maneuver_id = proposal_target.get("maneuver_id") or proposal.get("maneuver_id")
    if maneuver_id:
        recognized.append(f"ID da manobra: {maneuver_id}")
    maneuver_type = proposal.get("target", {}).get("maneuver_type")
    if maneuver_type:
        maneuver_label = {
            "entry": "entrada",
            "departure": "saída",
            "shift": "mudança",
        }.get(maneuver_type, maneuver_type)
        recognized.append(f"Tipo de manobra: {maneuver_label}")
    for key, value in fields.items():
        if value in ("", None, []):
            continue
        if key == "constraints" and isinstance(value, list):
            clean_value = ", ".join(value)
        else:
            clean_value = str(value)
        recognized.append(f"{DISPLAY_FIELD_LABELS.get(key, key)}: {clean_value}")
    if recognized:
        lines.append("")
        lines.append("Elementos reconhecidos:")
        lines.extend(f"- {item}." if not item.endswith(".") else f"- {item}" for item in recognized)
    else:
        lines.append("")
        lines.append("Elementos reconhecidos: nenhum dado operacional preenchido.")

    if missing_fields:
        lines.append("")
        lines.append("Em falta/corrigir:")
        lines.extend(f"- {item}." for item in missing_fields)
        template = build_action_reply_template(action, missing_fields)
        if template:
            lines.append("")
            lines.append(template)
        lines.append("")
        lines.append("Completa só os campos em falta ou corrige os campos indicados.")
    else:
        lines.append("")
        lines.append("Confirma para aplicar a alteração no portal.")
    return "\n".join(lines)


def looks_like_port_call_registration_request(question: str) -> bool:
    clean = _lookup_key(question)
    if not clean:
        return False
    if "manobra" in clean:
        return False
    if looks_like_maneuver_report_payload(question) or looks_like_abort_payload(question):
        return False
    if re.search(r"\b(regist\w*\s+(esta\s+)?escala|nova escala|cria\w*\s+escala|register scale)\b", clean):
        return True
    return looks_like_port_call_payload(question)
