from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Dict, Iterable, List, Optional

BERTH_OPTIONS = [
    "Secil W",
    "Secil E",
    "Fundeadouro Norte",
    "Cais Palmeiras",
    "TMS 1 - Cais 3",
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
]
MULTI_SLOT_BERTHS = {
    TMS2_BASE_LABEL: TMS2_SLOT_LABELS,
}
TMS1_SLOT_LENGTHS_M = {
    "TMS 1 - Cais 3": 175.0,
    "TMS 1 - Cais 4": 175.0,
    "TMS 1 - Cais 5": 175.0,
    "TMS 1 - Cais 6": 175.0,
    "TMS 1 - Cais 7": 80.0,
    "TMS 1 - Cais 8": 230.0,
}
TMS1_SLOT_LABELS = list(TMS1_SLOT_LENGTHS_M)
TMS1_LARGE_VESSEL_LOA_M = 200.0
TMS1_MAX_LARGE_VESSELS = 2
TMS1_CAIS8_MAX_LOA_M = 230.0
TMS2_TOTAL_LENGTH_M = 723.0
TMS2_SLOT_LENGTHS_M = {label: TMS2_TOTAL_LENGTH_M / len(TMS2_SLOT_LABELS) for label in TMS2_SLOT_LABELS}
AUTOEUROPA_SLOT_LABELS = ["Cais 10 / Autoeuropa", "Cais 11 / Autoeuropa"]
AUTOEUROPA_EXCLUSIVE_LOA_M = 230.0

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
    slot_match = re.search(r"(?:posicao|pos|slot|lugar)?\s*([abc])(?:\s|$)", key)
    compact_slot_match = re.search(r"(?:tms2|terminalmultiusos2|terminalmultiusosdois)(?:posicao|pos|slot|lugar)?([abc])$", compact)
    slot = ""
    if compact_slot_match:
        slot = compact_slot_match.group(1)
    elif slot_match:
        slot = slot_match.group(1)
    if not slot:
        return TMS2_SLOT_LABELS[0]
    return TMS2_SLOT_LABELS[{"a": 0, "b": 1, "c": 2}[slot]]


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


def _contiguous_span_from_start(
    labels: list[str],
    lengths: dict[str, float],
    start_label: str,
    loa_m: float | None,
) -> list[str]:
    if start_label not in labels:
        return []
    if loa_m is None:
        return [start_label]
    start_index = labels.index(start_label)
    total = 0.0
    span: list[str] = []
    for label in labels[start_index:]:
        span.append(label)
        total += lengths.get(label, 0.0)
        if total >= loa_m:
            return span
    return span


def _shortest_contiguous_span_including(
    labels: list[str],
    lengths: dict[str, float],
    anchor_label: str,
    loa_m: float | None,
) -> list[str]:
    if anchor_label not in labels:
        return []
    if loa_m is None:
        return [anchor_label]
    anchor_index = labels.index(anchor_label)
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
    candidates.sort(key=lambda span: (len(span), labels.index(span[0])))
    return candidates[0]


def _berth_span_labels(canonical: str, loa_m: float | None) -> list[str]:
    if canonical in TMS1_SLOT_LABELS:
        if canonical == "TMS 1 - Cais 8":
            return [canonical]
        return _contiguous_span_from_start(TMS1_SLOT_LABELS, TMS1_SLOT_LENGTHS_M, canonical, loa_m)
    if canonical in TMS2_SLOT_LABELS:
        return _shortest_contiguous_span_including(TMS2_SLOT_LABELS, TMS2_SLOT_LENGTHS_M, canonical, loa_m)
    if canonical in AUTOEUROPA_SLOT_LABELS and loa_m is not None and loa_m >= AUTOEUROPA_EXCLUSIVE_LOA_M:
        return AUTOEUROPA_SLOT_LABELS[:]
    return [canonical] if canonical else []


def _tms2_required_length_m(lengths: list[float]) -> float:
    if not lengths:
        return 0.0
    ordered = sorted(lengths, reverse=True)
    total = ordered[0] * 1.1
    previous = ordered[0]
    for current in ordered[1:]:
        total += max(previous * 0.1, current * 0.1) + current
        previous = current
    total += ordered[-1] * 0.1
    return total


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
        capacity_loa = target_loa if target_loa is not None else AUTOEUROPA_EXCLUSIVE_LOA_M
        if capacity_loa >= AUTOEUROPA_EXCLUSIVE_LOA_M and autoeuropa_occupants:
            return autoeuropa_occupants[0]
        for item in autoeuropa_occupants:
            item_loa = _item_loa_m(item)
            if item_loa is None or item_loa >= AUTOEUROPA_EXCLUSIVE_LOA_M:
                return item
        if len(autoeuropa_occupants) >= len(AUTOEUROPA_SLOT_LABELS):
            return autoeuropa_occupants[0]
        return None

    if target_canonical in TMS1_SLOT_LABELS:
        if target_canonical == "TMS 1 - Cais 8" and target_loa is not None and target_loa > TMS1_CAIS8_MAX_LOA_M:
            return _capacity_conflict(target_canonical, "limite físico do TMS 1 - Cais 8")
        target_span = _berth_span_labels(target_canonical, target_loa)
        if target_loa is not None and sum(TMS1_SLOT_LENGTHS_M.get(label, 0.0) for label in target_span) < target_loa:
            return _capacity_conflict(target_canonical, "comprimento disponível no TMS 1")
        tms1_occupants = [
            (item, item_canonical)
            for item, item_canonical in active_items
            if item_canonical in TMS1_SLOT_LABELS
        ]
        target_span_set = set(target_span)
        for item, item_canonical in tms1_occupants:
            if target_span_set & set(_berth_span_labels(item_canonical, _item_loa_m(item))):
                return item
        large_occupants = [
            item
            for item, _item_canonical in tms1_occupants
            if (_item_loa_m(item) or 0.0) >= TMS1_LARGE_VESSEL_LOA_M
        ]
        if target_loa is not None and target_loa >= TMS1_LARGE_VESSEL_LOA_M and len(large_occupants) >= TMS1_MAX_LARGE_VESSELS:
            return large_occupants[0] if large_occupants else _capacity_conflict(target_canonical, "limite de navios grandes no TMS 1")
        return None

    if target_canonical in TMS2_SLOT_LABELS:
        target_span = _berth_span_labels(target_canonical, target_loa)
        if target_loa is not None and sum(TMS2_SLOT_LENGTHS_M.get(label, 0.0) for label in target_span) < target_loa:
            return _capacity_conflict(target_canonical, "comprimento disponível no TMS 2")
        tms2_occupants = [
            (item, item_canonical)
            for item, item_canonical in active_items
            if item_canonical in TMS2_SLOT_LABELS
        ]
        target_span_set = set(target_span)
        for item, item_canonical in tms2_occupants:
            if target_span_set & set(_berth_span_labels(item_canonical, _item_loa_m(item))):
                return item
        lengths = [target_loa if target_loa is not None else TMS2_SLOT_LENGTHS_M[target_canonical]]
        lengths.extend(_item_loa_m(item) or TMS2_SLOT_LENGTHS_M[item_canonical] for item, item_canonical in tms2_occupants)
        if _tms2_required_length_m(lengths) > TMS2_TOTAL_LENGTH_M:
            return tms2_occupants[0][0] if tms2_occupants else _capacity_conflict(target_canonical, "comprimento útil do TMS 2")
        return None

    if target_capacity > 1 and len(occupied_same_base) >= target_capacity:
        return occupied_same_base[0]
    return None
