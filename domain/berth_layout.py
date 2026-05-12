from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BERTH_OPTIONS = [
    "Secil W",
    "Secil E",
    "Fundeadouro Norte",
    "Cais Palmeiras",
    "TMS 1 - Cais 4",
    "TMS 1 - Cais 5",
    "TMS 1 - Cais 6",
    "TMS 1 - Cais 7",
    "TMS 1 - Cais 8",
    "TMS 2",
    "Cais 10 / Autoeuropa",
    "Cais 11 / Autoeuropa",
    "Praias do Sado / Pirites Alentejanas",
    "SAPEC Sólidos",
    "SAPEC Líquidos",
    "ALSTOM",
    "PAN Tróia",
    "Fundeadouro Sul / Tróia",
    "Tanquisado (lado jusante)",
    "Eco-Oil (lado montante)",
    "Lisnave - Cais 0 B",
    "Lisnave - Cais 0 A",
    "Lisnave - Doca 20",
    "Lisnave - Doca 21",
    "Lisnave - Doca 22",
    "Lisnave - Cais 1 B",
    "Lisnave - Cais 1 A",
    "Lisnave - Cais 2 B",
    "Lisnave - Cais 2 A",
    "Lisnave - Cais 3 B",
    "Lisnave - Cais 3 A",
    "Lisnave - Doca 31",
    "Lisnave - Doca 32",
    "Lisnave - Doca 33",
    "Teporset",
]

TMS2_BASE_LABEL = "TMS 2"
TMS2_SLOT_LABELS = [
    "TMS 2 - Posição A",
    "TMS 2 - Posição B",
    "TMS 2 - Posição C",
    "TMS 2 - Posição D",
]
MULTI_SLOT_BERTHS = {
    TMS2_BASE_LABEL: TMS2_SLOT_LABELS,
}
TMS1_SLOT_LENGTHS_M = {
    "TMS 1 - Cais 4": 175.0,
    "TMS 1 - Cais 5": 175.0,
    "TMS 1 - Cais 6": 175.0,
    "TMS 1 - Cais 7": 80.0,
    "TMS 1 - Cais 8": 215.0,
}
TMS1_SLOT_LABELS = list(TMS1_SLOT_LENGTHS_M)
TMS1_LARGE_VESSEL_LOA_M = 200.0
TMS1_MAX_LARGE_VESSELS = 3
TMS1_CAIS8_MAX_LOA_M = 215.0
TMS2_TOTAL_LENGTH_M = 723.0
TMS2_SLOT_LENGTHS_M = {label: TMS2_TOTAL_LENGTH_M / len(TMS2_SLOT_LABELS) for label in TMS2_SLOT_LABELS}
AUTOEUROPA_SLOT_LABELS = ["Cais 10 / Autoeuropa", "Cais 11 / Autoeuropa"]
AUTOEUROPA_EXCLUSIVE_LOA_M = 230.0
SHARED_BERTH_CLEARANCE_M = 30.0
BERTH_CAPACITY_PROFILE_FILENAME = "berth_profiles.json"

TERMINAL_OPTIONS = [
    "Secil",
    "Fundeadouro Norte",
    "Cais Palmeiras",
    "TMS 1",
    "TMS 2",
    "Autoeuropa",
    "Praias do Sado / Pirites Alentejanas",
    "SAPEC Sólidos",
    "SAPEC Líquidos",
    "ALSTOM",
    "PAN Tróia",
    "Fundeadouro Sul / Tróia",
    "Tanquisado",
    "Eco-Oil",
    "Lisnave",
    "Teporset",
]


def _berth_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


def _berth_compact_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_value)


def _lisnave_quay_aliases() -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    side_terms = {
        "a": ("a", "w", "west", "oeste", "setubal", "ladosetubal", "ladow", "ladooeste"),
        "b": ("b", "e", "east", "este", "leste", "alcacer", "alcacerdosal", "ladoalcacer", "ladoalcacerdosal", "ladoe", "ladoeste", "ladoleste"),
    }
    for number in range(0, 4):
        for side, terms in side_terms.items():
            label = f"Lisnave - Cais {number} {side.upper()}"
            values = {
                f"cais{number}{side}",
                f"c{number}{side}",
                f"pontecais{number}{side}",
                f"lisnavecais{number}{side}",
                f"lisnavec{number}{side}",
                f"lisnavepontecais{number}{side}",
                f"lisnave{number}{side}",
                f"{number}{side}lisnave",
                f"{side}{number}lisnave",
                f"lisnave{side}{number}",
            }
            for term in terms:
                values.update(
                    {
                        f"cais{number}{term}",
                        f"c{number}{term}",
                        f"pontecais{number}{term}",
                        f"lisnavecais{number}{term}",
                        f"lisnavec{number}{term}",
                        f"lisnavepontecais{number}{term}",
                        f"lisnave{number}{term}",
                    }
                )
            aliases.update({value: label for value in values})
    return aliases


LISNAVE_QUAY_ALIASES = _lisnave_quay_aliases()


def _alias_canonical_berth(label: str, berth_options: Iterable[str] | None = None) -> str:
    clean = " ".join(str(label or "").strip().split())
    if not clean:
        return ""
    options = list(berth_options or BERTH_OPTIONS)
    compact = _berth_compact_key(clean)
    key = _berth_key(clean)
    tms2_slot = _tms2_slot_label(clean, options)
    if tms2_slot:
        return tms2_slot

    alias_map = {
        "tanquisado": "Tanquisado (lado jusante)",
        "ecooil": "Eco-Oil (lado montante)",
        "fundeadourosul": "Fundeadouro Sul / Tróia",
        "fundeadourosultroia": "Fundeadouro Sul / Tróia",
        "fundeadourotroia": "Fundeadouro Sul / Tróia",
        "fundeadouronorte": "Fundeadouro Norte",
        "doca20": "Lisnave - Doca 20",
        "doca21": "Lisnave - Doca 21",
        "doca22": "Lisnave - Doca 22",
        "doca31": "Lisnave - Doca 31",
        "doca32": "Lisnave - Doca 32",
        "doca33": "Lisnave - Doca 33",
        "d20": "Lisnave - Doca 20",
        "d21": "Lisnave - Doca 21",
        "d22": "Lisnave - Doca 22",
        "d31": "Lisnave - Doca 31",
        "d32": "Lisnave - Doca 32",
        "d33": "Lisnave - Doca 33",
        "docaseca20": "Lisnave - Doca 20",
        "docaseca21": "Lisnave - Doca 21",
        "docaseca22": "Lisnave - Doca 22",
        "docaseca31": "Lisnave - Doca 31",
        "docaseca32": "Lisnave - Doca 32",
        "docaseca33": "Lisnave - Doca 33",
        "cais31": "Lisnave - Doca 31",
        "cais32": "Lisnave - Doca 32",
        "cais33": "Lisnave - Doca 33",
        "lisnavedoca20": "Lisnave - Doca 20",
        "lisnavedoca21": "Lisnave - Doca 21",
        "lisnavedoca22": "Lisnave - Doca 22",
        "lisnavedoca31": "Lisnave - Doca 31",
        "lisnavedoca32": "Lisnave - Doca 32",
        "lisnavedoca33": "Lisnave - Doca 33",
        "lisnaved20": "Lisnave - Doca 20",
        "lisnaved21": "Lisnave - Doca 21",
        "lisnaved22": "Lisnave - Doca 22",
        "lisnaved31": "Lisnave - Doca 31",
        "lisnaved32": "Lisnave - Doca 32",
        "lisnaved33": "Lisnave - Doca 33",
        "tms2": "TMS 2",
        "terminalmultiusos2": "TMS 2",
        "terminalmultiusosdois": "TMS 2",
    }
    alias_map.update(LISNAVE_QUAY_ALIASES)
    if compact in alias_map and alias_map[compact] in options:
        return alias_map[compact]

    berth_number_match = re.search(r"(?:^| )cais ?([0-9]{1,2})(?: |$)", key) or re.search(r"cais([0-9]{1,2})", compact)
    berth_number = berth_number_match.group(1) if berth_number_match else ""
    if berth_number and ("autoeuropa" in compact or "autoeuropa" in key):
        candidate = f"Cais {berth_number} / Autoeuropa"
        if candidate in options:
            return candidate
    if berth_number and any(marker in compact for marker in {"multipurpose", "multiusos", "multiproposito", "multiplosusos"}):
        candidate = f"TMS 1 - Cais {berth_number}"
        if candidate in options:
            return candidate

    return ""


def _tms2_slot_label(label: str | None, berth_options: Iterable[str] | None = None) -> str:
    options = list(berth_options or BERTH_OPTIONS)
    if TMS2_BASE_LABEL not in options:
        return ""
    key = _berth_key(label)
    compact = _berth_compact_key(label)
    if not key and not compact:
        return ""
    is_tms2 = (
        "tms 2" in key
        or "tms2" in compact
        or "terminal multiusos 2" in key
        or "terminal multiusos dois" in key
    )
    if not is_tms2:
        return ""
    slot_match = re.search(r"(?:posicao|pos|slot|lugar)?\s*([abcd])(?:\s|$)", key)
    compact_slot_match = re.search(r"(?:tms2|terminalmultiusos2|terminalmultiusosdois)(?:posicao|pos|slot|lugar)?([abcd])$", compact)
    slot = ""
    if compact_slot_match:
        slot = compact_slot_match.group(1)
    elif slot_match:
        slot = slot_match.group(1)
    if not slot:
        return TMS2_SLOT_LABELS[0]
    return TMS2_SLOT_LABELS[{"a": 0, "b": 1, "c": 2, "d": 3}[slot]]


def _expanded_berth_options(berth_options: Iterable[str] | None = None) -> List[str]:
    options = list(berth_options or BERTH_OPTIONS)
    expanded: List[str] = []
    for item in options:
        expanded.append(item)
        expanded.extend(MULTI_SLOT_BERTHS.get(item, []))
    return list(dict.fromkeys(expanded))


def _berth_base_label(label: str | None, berth_options: Iterable[str] | None = None) -> str:
    canonical = canonicalize_berth_label(label, berth_options=berth_options)
    if canonical in TMS2_SLOT_LABELS:
        return TMS2_BASE_LABEL
    return canonical


def _berth_slot_capacity(label: str | None, berth_options: Iterable[str] | None = None) -> int:
    base_label = _berth_base_label(label, berth_options=berth_options)
    return len(MULTI_SLOT_BERTHS.get(base_label, [])) or 1


def _safe_length_m(*values: object) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        clean = str(value).strip().replace(",", ".")
        match = re.search(r"\d+(?:\.\d+)?", clean)
        if not match:
            continue
        try:
            parsed = float(match.group(0))
        except ValueError:
            continue
        if parsed > 0:
            return parsed
    return None


def _item_loa_m(item: Dict) -> float | None:
    return _safe_length_m(
        item.get("vessel_loa_m"),
        item.get("ship_loa_label"),
        item.get("loa"),
        item.get("loa_m"),
        item.get("vessel_length_m"),
    )


def _capacity_conflict(target_berth: str, reason: str) -> Dict:
    return {
        "id": "__berth_capacity__",
        "reference_code": "capacidade",
        "vessel_name": reason,
        "berth_label": target_berth,
        "capacity_reason": reason,
    }


def _capacity_profile_path() -> str:
    candidates: list[Path] = []
    configured = os.getenv("KNOWLEDGE_DIR", "").strip()
    if configured:
        candidates.append(Path(configured) / BERTH_CAPACITY_PROFILE_FILENAME)
    candidates.append(Path(__file__).resolve().parents[1] / "knowledge" / BERTH_CAPACITY_PROFILE_FILENAME)
    for path in candidates:
        if path.is_file():
            return str(path)
    return str(candidates[-1])


def _capacity_profile_signature(path: str) -> tuple[str, float]:
    try:
        return path, os.path.getmtime(path)
    except OSError:
        return path, 0.0


@lru_cache(maxsize=4)
def _load_capacity_profile_rules(path: str, _mtime: float) -> dict[str, dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    profiles = payload.get("profiles") if isinstance(payload, dict) else []
    rules: dict[str, dict[str, Any]] = {}
    for profile in profiles if isinstance(profiles, list) else []:
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get("id") or "").strip().lower()
        capacity_rules = profile.get("berth_capacity_rules")
        if profile_id and isinstance(capacity_rules, dict):
            rules[profile_id] = capacity_rules
    return rules


def _capacity_rules(profile_id: str) -> dict[str, Any]:
    path, mtime = _capacity_profile_signature(_capacity_profile_path())
    return dict(_load_capacity_profile_rules(path, mtime).get(profile_id, {}))


def _float_capacity_rule(profile_id: str, key: str, default: float) -> float:
    value = _capacity_rules(profile_id).get(key)
    parsed = _safe_length_m(value)
    return parsed if parsed is not None else default


def _int_capacity_rule(profile_id: str, key: str, default: int) -> int:
    value = _capacity_rules(profile_id).get(key)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _list_capacity_rule(profile_id: str, key: str, default: list[str]) -> list[str]:
    value = _capacity_rules(profile_id).get(key)
    if not isinstance(value, list):
        return default[:]
    clean = [str(item or "").strip() for item in value if str(item or "").strip()]
    return clean or default[:]


def _dict_float_capacity_rule(profile_id: str, key: str, default: dict[str, float]) -> dict[str, float]:
    value = _capacity_rules(profile_id).get(key)
    if not isinstance(value, dict):
        return default.copy()
    parsed: dict[str, float] = {}
    for raw_label, raw_length in value.items():
        label = str(raw_label or "").strip()
        length = _safe_length_m(raw_length)
        if label and length is not None:
            parsed[label] = length
    return parsed or default.copy()


def _tms1_slot_lengths_m() -> dict[str, float]:
    return _dict_float_capacity_rule("tms1", "slot_lengths_m", TMS1_SLOT_LENGTHS_M)


def _tms1_slot_labels() -> list[str]:
    return list(_tms1_slot_lengths_m())


def _tms1_large_vessel_loa_m() -> float:
    return _float_capacity_rule("tms1", "large_vessel_loa_m", TMS1_LARGE_VESSEL_LOA_M)


def _tms1_max_large_vessels() -> int:
    return _int_capacity_rule("tms1", "max_large_vessels_alongside", TMS1_MAX_LARGE_VESSELS)


def _tms1_cais8_max_loa_m() -> float:
    return _float_capacity_rule("tms1", "cais8_max_loa_m", TMS1_CAIS8_MAX_LOA_M)


def _tms1_isolated_slots() -> set[str]:
    return set(_list_capacity_rule("tms1", "isolated_slots", ["TMS 1 - Cais 8"]))


def _tms1_contiguous_slot_labels() -> list[str]:
    isolated = _tms1_isolated_slots()
    return [label for label in _tms1_slot_labels() if label not in isolated]


def _tms2_total_length_m() -> float:
    return _float_capacity_rule("tms2", "total_length_m", TMS2_TOTAL_LENGTH_M)


def _tms2_slot_labels() -> list[str]:
    return _list_capacity_rule("tms2", "slot_labels", TMS2_SLOT_LABELS)


def _tms2_slot_lengths_m() -> dict[str, float]:
    labels = _tms2_slot_labels()
    total_length = _tms2_total_length_m()
    if not labels:
        return TMS2_SLOT_LENGTHS_M.copy()
    return {label: total_length / len(labels) for label in labels}


def _autoeuropa_exclusive_loa_m() -> float:
    return _float_capacity_rule("auto_europa", "exclusive_loa_m", AUTOEUROPA_EXCLUSIVE_LOA_M)


def _shared_clearance_m(profile_id: str) -> float:
    return _float_capacity_rule(profile_id, "shared_clearance_m", SHARED_BERTH_CLEARANCE_M)


def _preferred_contiguous_span_including(
    labels: list[str],
    lengths: dict[str, float],
    anchor_label: str,
    loa_m: float | None,
    blocked_labels: set[str] | None = None,
) -> list[str]:
    if anchor_label not in labels:
        return []
    if loa_m is None:
        return [anchor_label]
    anchor_index = labels.index(anchor_label)
    blocked = set(blocked_labels or set())
    candidates: list[list[str]] = []
    for start in range(anchor_index + 1):
        total = 0.0
        span: list[str] = []
        for end in range(start, len(labels)):
            label = labels[end]
            span.append(label)
            total += lengths.get(label, 0.0)
            if end < anchor_index:
                continue
            if total >= loa_m:
                candidates.append(span[:])
                break
    if not candidates:
        return labels[:]

    def preference(span: list[str]) -> tuple[int, int, int]:
        if set(span) & blocked:
            blocked_rank = 1
        else:
            blocked_rank = 0
        if span[0] == anchor_label:
            direction_rank = 0
        elif span[-1] == anchor_label:
            direction_rank = 1
        else:
            direction_rank = 2
        return (blocked_rank, direction_rank, len(span))

    candidates.sort(key=preference)
    return candidates[0]


def _berth_span_labels(
    canonical: str,
    loa_m: float | None,
    blocked_labels: set[str] | None = None,
) -> list[str]:
    tms1_lengths = _tms1_slot_lengths_m()
    if canonical in tms1_lengths:
        if canonical in _tms1_isolated_slots():
            return [canonical]
        return _preferred_contiguous_span_including(
            _tms1_contiguous_slot_labels(),
            tms1_lengths,
            canonical,
            loa_m,
            blocked_labels,
        )
    tms2_lengths = _tms2_slot_lengths_m()
    if canonical in tms2_lengths:
        return _preferred_contiguous_span_including(
            _tms2_slot_labels(),
            tms2_lengths,
            canonical,
            loa_m,
            blocked_labels,
        )
    if canonical in AUTOEUROPA_SLOT_LABELS and loa_m is not None and loa_m >= _autoeuropa_exclusive_loa_m():
        return AUTOEUROPA_SLOT_LABELS[:]
    return [canonical] if canonical else []


def _required_length_with_clearances(lengths: list[float], clearance_m: float = SHARED_BERTH_CLEARANCE_M) -> float:
    valid_lengths = [length for length in lengths if length > 0]
    if not valid_lengths:
        return 0.0
    return sum(valid_lengths) + clearance_m * max(len(valid_lengths) - 1, 0)


def _tms1_ranges_touch(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    if left_start > right_start:
        left_start, left_end, right_start, right_end = right_start, right_end, left_start, left_end
    if left_end >= right_start:
        return True
    if left_end + 1 != right_start:
        return False
    labels = _tms1_slot_labels()
    left_label = labels[left_end]
    right_label = labels[right_start]
    return not (left_label in _tms1_isolated_slots() or right_label in _tms1_isolated_slots())


def _tms1_shared_clearance_conflict(
    target_canonical: str,
    target_span: list[str],
    target_loa: float | None,
    tms1_occupants: list[tuple[Dict, str]],
) -> Dict | None:
    if not target_span:
        return None
    tms1_labels = _tms1_slot_labels()
    tms1_lengths = _tms1_slot_lengths_m()
    placements: list[dict] = [
        {
            "item": None,
            "start": min(tms1_labels.index(label) for label in target_span),
            "end": max(tms1_labels.index(label) for label in target_span),
            "length": target_loa if target_loa is not None else tms1_lengths[target_canonical],
            "target": True,
        }
    ]
    for item, item_canonical in tms1_occupants:
        item_span = _berth_span_labels(item_canonical, _item_loa_m(item))
        if not item_span:
            continue
        placements.append(
            {
                "item": item,
                "start": min(tms1_labels.index(label) for label in item_span),
                "end": max(tms1_labels.index(label) for label in item_span),
                "length": _item_loa_m(item) or tms1_lengths[item_canonical],
                "target": False,
            }
        )

    group = [placements[0]]
    group_start = placements[0]["start"]
    group_end = placements[0]["end"]
    changed = True
    while changed:
        changed = False
        for placement in placements[1:]:
            if placement in group:
                continue
            if _tms1_ranges_touch(group_start, group_end, placement["start"], placement["end"]):
                group.append(placement)
                group_start = min(group_start, placement["start"])
                group_end = max(group_end, placement["end"])
                changed = True
    if len(group) <= 1:
        return None

    available = sum(tms1_lengths[tms1_labels[index]] for index in range(group_start, group_end + 1))
    required = _required_length_with_clearances([placement["length"] for placement in group], _shared_clearance_m("tms1"))
    if required <= available:
        return None
    for placement in group:
        if placement["item"]:
            return placement["item"]
    return _capacity_conflict(target_canonical, "folga mínima de 30 m entre navios no TMS 1")


def _occupied_slot_labels_for_item(item: Dict, berth_options: Iterable[str] | None = None) -> list[str]:
    canonical = canonicalize_berth_label(item.get("berth_label") or item.get("berth"), berth_options=berth_options)
    if not canonical or is_anchorage_berth(canonical):
        return []
    return _berth_span_labels(canonical, _item_loa_m(item))


def canonicalize_berth_label(label: str | None, berth_options: Iterable[str] | None = None) -> str:
    clean = " ".join(str(label or "").strip().split())
    if not clean:
        return ""
    options = list(berth_options or BERTH_OPTIONS)
    tms2_slot = _tms2_slot_label(clean, options)
    if tms2_slot:
        return tms2_slot
    key = _berth_key(clean)
    compact = _berth_compact_key(clean)
    expanded_options = _expanded_berth_options(options)
    by_key = {_berth_key(item): item for item in expanded_options}
    by_compact = {_berth_compact_key(item): item for item in expanded_options}
    if key in by_key:
        return by_key[key]
    if compact in by_compact:
        return by_compact[compact]

    aliased = _alias_canonical_berth(clean, options)
    if aliased:
        return aliased

    token_matches = [
        item
        for item in options
        if key and (_berth_key(item) in key or key in _berth_key(item))
    ]
    compact_matches = [
        item
        for item in options
        if compact and (_berth_compact_key(item) in compact or compact in _berth_compact_key(item))
    ]
    matches: List[str] = []
    for item in token_matches + compact_matches:
        if item not in matches:
            matches.append(item)
    return matches[0] if len(matches) == 1 else clean


def is_known_berth_label(label: str | None, berth_options: Iterable[str] | None = None) -> bool:
    clean = " ".join(str(label or "").strip().split())
    if not clean:
        return False
    options = list(berth_options or BERTH_OPTIONS)
    return canonicalize_berth_label(clean, options) in _expanded_berth_options(options)


def is_anchorage_berth(label: str | None) -> bool:
    key = _berth_key(label)
    if not key:
        return False
    if "fundeadouro norte" in key:
        return True
    return "fundeadouro" in key and ("sul" in key or "troia" in key)


def slot_berth_options(berth_options: Iterable[str] | None = None) -> List[str]:
    options = list(berth_options or BERTH_OPTIONS)
    slots: List[str] = []
    for item in options:
        if is_anchorage_berth(item):
            continue
        expanded = MULTI_SLOT_BERTHS.get(item)
        if expanded:
            slots.extend(expanded)
        else:
            slots.append(item)
    return list(dict.fromkeys(slots))


def dropdown_berth_options(berth_options: Iterable[str] | None = None) -> List[str]:
    options = list(berth_options or BERTH_OPTIONS)
    result: List[str] = []
    for item in options:
        expanded = MULTI_SLOT_BERTHS.get(item)
        if expanded:
            result.extend(expanded)
        else:
            result.append(item)
    return list(dict.fromkeys(result))


def berth_sort_key(label: str | None, berth_options: Iterable[str] | None = None) -> tuple[int, str]:
    canonical = canonicalize_berth_label(label, berth_options=berth_options)
    key = _berth_key(canonical or label)
    options = list(berth_options or BERTH_OPTIONS)
    indexed = {_berth_key(item): index for index, item in enumerate(options)}
    if key in indexed:
        return (indexed[key], key)
    for option_key, index in indexed.items():
        if option_key and (option_key in key or key in option_key):
            return (index, key)
    return (len(indexed) + 1, key)


def _parse_iso_datetime(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _latest_berth_release_maneuver(
    item: Dict,
    item_canonical: str,
    *,
    release_base_capacity: bool,
    release_states: Iterable[str] | None = None,
    berth_options: Iterable[str] | None = None,
) -> Optional[Dict]:
    options = list(berth_options or BERTH_OPTIONS)
    allowed_states = {
        str(state or "").strip().lower()
        for state in (release_states if release_states is not None else ("approved",))
        if str(state or "").strip()
    }
    if not allowed_states:
        return None
    item_base = _berth_base_label(item_canonical, options)
    candidates: List[Dict] = []
    for maneuver in item.get("maneuver_history", []) or []:
        maneuver_type = (maneuver.get("type") or "").strip().lower()
        state = (maneuver.get("state") or "").strip().lower()
        if state not in allowed_states:
            continue
        if maneuver_type == "departure":
            candidates.append(maneuver)
            continue
        if maneuver_type != "shift":
            continue
        origin = canonicalize_berth_label(maneuver.get("origin"), options)
        destination = canonicalize_berth_label(maneuver.get("destination"), options)
        if origin != item_canonical or destination == item_canonical:
            continue
        if release_base_capacity and _berth_base_label(destination, options) == item_base:
            continue
        candidates.append(maneuver)
    if not candidates:
        return None
    candidates.sort(
        key=lambda maneuver: (
            maneuver.get("planned_at")
            or maneuver.get("completed_at")
            or maneuver.get("decided_at")
            or maneuver.get("created_at")
            or ""
        )
    )
    return candidates[-1]


def _berth_is_released_by_validated_maneuver(
    item: Dict,
    item_canonical: str,
    target_planned_at: str | None,
    *,
    release_base_capacity: bool,
    release_states: Iterable[str] | None = None,
    berth_options: Iterable[str] | None = None,
) -> bool:
    release_maneuver = _latest_berth_release_maneuver(
        item,
        item_canonical,
        release_base_capacity=release_base_capacity,
        release_states=release_states,
        berth_options=berth_options,
    )
    if not release_maneuver:
        return False
    target_dt = _parse_iso_datetime(target_planned_at)
    release_dt = (
        _parse_iso_datetime(release_maneuver.get("planned_at"))
        or _parse_iso_datetime(release_maneuver.get("completed_at"))
        or _parse_iso_datetime(release_maneuver.get("decided_at"))
    )
    if not target_dt or not release_dt:
        return True
    return release_dt <= target_dt


def _group_vessels_by_berth(
    vessels: Iterable[Dict],
    berth_options: Iterable[str] | None = None,
) -> List[Dict]:
    groups: Dict[str, List[Dict]] = {}
    for item in vessels:
        label = item.get("berth_label") or "Sem cais atribuído"
        canonical = canonicalize_berth_label(label, berth_options=berth_options) or label
        groups.setdefault(canonical, []).append(item)
    return [
        {
            "berth": berth,
            "count": len(grouped_vessels),
            "vessels": grouped_vessels,
        }
        for berth, grouped_vessels in sorted(
            groups.items(),
            key=lambda pair: berth_sort_key(pair[0], berth_options),
        )
    ]


def build_slot_occupancy(
    in_port_items: Iterable[Dict],
    berth_options: Iterable[str] | None = None,
) -> Dict:
    vessels = list(in_port_items or [])
    options = list(berth_options or BERTH_OPTIONS)
    quay_vessels = [item for item in vessels if not is_anchorage_berth(item.get("berth_label"))]
    anchorage_vessels = [item for item in vessels if is_anchorage_berth(item.get("berth_label"))]
    berthed = _group_vessels_by_berth(quay_vessels, options)
    anchorages = _group_vessels_by_berth(anchorage_vessels, options)
    slot_capacity_count = len(slot_berth_options(options))
    occupied_slots: set[str] = set()
    for item in quay_vessels:
        occupied_slots.update(_occupied_slot_labels_for_item(item, options))
    occupied_slot_count = len(occupied_slots)
    return {
        "berthed": berthed,
        "anchorages": anchorages,
        "quay_vessel_count": len(quay_vessels),
        "anchorage_vessel_count": len(anchorage_vessels),
        "slot_capacity_count": slot_capacity_count,
        "occupied_slot_count": occupied_slot_count,
        "free_slot_count": max(slot_capacity_count - occupied_slot_count, 0),
    }


def find_occupied_berth_conflict(
    target_berth: str | None,
    in_port_items: Iterable[Dict],
    *,
    current_port_call_id: str = "",
    target_planned_at: str | None = None,
    target_vessel_loa_m: object = None,
    release_states: Iterable[str] | None = None,
    berth_options: Iterable[str] | None = None,
) -> Optional[Dict]:
    options = list(berth_options or BERTH_OPTIONS)
    target_clean = " ".join(str(target_berth or "").strip().split())
    if not target_clean:
        return None
    target_canonical = canonicalize_berth_label(target_clean, options)
    if target_canonical not in _expanded_berth_options(options):
        return None
    if is_anchorage_berth(target_canonical):
        return None

    current_id = (current_port_call_id or "").strip()
    target_base = _berth_base_label(target_canonical, options)
    target_capacity = _berth_slot_capacity(target_canonical, options)
    target_loa = _safe_length_m(target_vessel_loa_m)
    occupied_same_base: List[Dict] = []
    active_items: List[tuple[Dict, str]] = []
    for item in in_port_items or []:
        item_id = (item.get("id") or item.get("port_call_id") or "").strip()
        if current_id and item_id == current_id:
            continue
        item_berth = item.get("berth_label") or item.get("berth") or ""
        item_canonical = canonicalize_berth_label(item_berth, options)
        if not item_canonical or is_anchorage_berth(item_canonical):
            continue
        if _berth_is_released_by_validated_maneuver(
            item,
            item_canonical,
            target_planned_at,
            release_base_capacity=False,
            release_states=release_states,
            berth_options=options,
        ):
            continue
        active_items.append((item, item_canonical))
        if item_canonical == target_canonical:
            return item
        if _berth_base_label(item_canonical, options) == target_base:
            occupied_same_base.append(item)

    if target_canonical in AUTOEUROPA_SLOT_LABELS:
        autoeuropa_occupants = [
            item
            for item, item_canonical in active_items
            if item_canonical in AUTOEUROPA_SLOT_LABELS
        ]
        autoeuropa_exclusive_loa = _autoeuropa_exclusive_loa_m()
        capacity_loa = target_loa if target_loa is not None else autoeuropa_exclusive_loa
        if capacity_loa >= autoeuropa_exclusive_loa and autoeuropa_occupants:
            return autoeuropa_occupants[0]
        for item in autoeuropa_occupants:
            item_loa = _item_loa_m(item)
            if item_loa is None or item_loa >= autoeuropa_exclusive_loa:
                return item
        if len(autoeuropa_occupants) >= len(AUTOEUROPA_SLOT_LABELS):
            return autoeuropa_occupants[0]
        return None

    tms1_labels = _tms1_slot_labels()
    tms1_lengths = _tms1_slot_lengths_m()
    if target_canonical in tms1_labels:
        if target_canonical in _tms1_isolated_slots() and target_loa is not None and target_loa > _tms1_cais8_max_loa_m():
            return _capacity_conflict(target_canonical, "limite físico do TMS 1 - Cais 8")
        tms1_occupants = [
            (item, item_canonical)
            for item, item_canonical in active_items
            if item_canonical in tms1_labels
        ]
        occupied_tms1_labels = set()
        for item, item_canonical in tms1_occupants:
            occupied_tms1_labels.update(_berth_span_labels(item_canonical, _item_loa_m(item)))
        target_span = _berth_span_labels(target_canonical, target_loa, occupied_tms1_labels)
        if target_loa is not None and sum(tms1_lengths.get(label, 0.0) for label in target_span) < target_loa:
            return _capacity_conflict(target_canonical, "comprimento disponível no TMS 1")
        target_span_set = set(target_span)
        for item, item_canonical in tms1_occupants:
            if target_span_set & set(_berth_span_labels(item_canonical, _item_loa_m(item))):
                return item
        clearance_conflict = _tms1_shared_clearance_conflict(target_canonical, target_span, target_loa, tms1_occupants)
        if clearance_conflict:
            return clearance_conflict
        large_occupants = [
            item
            for item, _item_canonical in tms1_occupants
            if (_item_loa_m(item) or 0.0) >= _tms1_large_vessel_loa_m()
        ]
        if target_loa is not None and target_loa >= _tms1_large_vessel_loa_m() and len(large_occupants) >= _tms1_max_large_vessels():
            return large_occupants[0] if large_occupants else _capacity_conflict(target_canonical, "limite de navios grandes no TMS 1")
        return None

    tms2_labels = _tms2_slot_labels()
    tms2_lengths = _tms2_slot_lengths_m()
    if target_canonical in tms2_labels:
        tms2_occupants = [
            (item, item_canonical)
            for item, item_canonical in active_items
            if item_canonical in tms2_labels
        ]
        occupied_tms2_labels = set()
        for item, item_canonical in tms2_occupants:
            occupied_tms2_labels.update(_berth_span_labels(item_canonical, _item_loa_m(item)))
        target_span = _berth_span_labels(target_canonical, target_loa, occupied_tms2_labels)
        if target_loa is not None and sum(tms2_lengths.get(label, 0.0) for label in target_span) < target_loa:
            return _capacity_conflict(target_canonical, "comprimento disponível no TMS 2")
        target_span_set = set(target_span)
        for item, item_canonical in tms2_occupants:
            if target_span_set & set(_berth_span_labels(item_canonical, _item_loa_m(item))):
                return item
        lengths = [target_loa if target_loa is not None else tms2_lengths[target_canonical]]
        lengths.extend(_item_loa_m(item) or tms2_lengths[item_canonical] for item, item_canonical in tms2_occupants)
        if _required_length_with_clearances(lengths, _shared_clearance_m("tms2")) > _tms2_total_length_m():
            return tms2_occupants[0][0] if tms2_occupants else _capacity_conflict(target_canonical, "comprimento útil do TMS 2")
        return None

    if target_capacity > 1 and len(occupied_same_base) >= target_capacity:
        return occupied_same_base[0]
    return None
