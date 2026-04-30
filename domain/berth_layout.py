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


def _latest_departure_maneuver(item: Dict, states: set[str] | None = None) -> Optional[Dict]:
    departures = [
        maneuver
        for maneuver in item.get("maneuver_history", []) or []
        if (maneuver.get("type") or "").strip().lower() == "departure"
    ]
    if states is not None:
        departures = [
            maneuver
            for maneuver in departures
            if (maneuver.get("state") or "").strip().lower() in states
        ]
    if not departures:
        return None
    departures.sort(
        key=lambda maneuver: (
            maneuver.get("planned_at")
            or maneuver.get("completed_at")
            or maneuver.get("decided_at")
            or maneuver.get("created_at")
            or ""
        )
    )
    return departures[-1]


def _berth_is_released_by_validated_departure(item: Dict, target_planned_at: str | None) -> bool:
    departure = _latest_departure_maneuver(item, {"approved"})
    if not departure:
        return False
    target_dt = _parse_iso_datetime(target_planned_at)
    departure_dt = (
        _parse_iso_datetime(departure.get("planned_at"))
        or _parse_iso_datetime(departure.get("completed_at"))
        or _parse_iso_datetime(departure.get("decided_at"))
    )
    if not target_dt or not departure_dt:
        return True
    return departure_dt <= target_dt


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
    occupancy_by_base: Dict[str, int] = {}
    for item in quay_vessels:
        base_label = _berth_base_label(item.get("berth_label"), options)
        if not base_label:
            continue
        occupancy_by_base[base_label] = occupancy_by_base.get(base_label, 0) + 1
    occupied_slot_count = sum(
        min(count, _berth_slot_capacity(base_label, options))
        for base_label, count in occupancy_by_base.items()
    )
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
    occupied_same_base: List[Dict] = []
    for item in in_port_items or []:
        item_id = (item.get("id") or item.get("port_call_id") or "").strip()
        if current_id and item_id == current_id:
            continue
        item_berth = item.get("berth_label") or item.get("berth") or ""
        item_canonical = canonicalize_berth_label(item_berth, options)
        if item_canonical == target_canonical:
            if _berth_is_released_by_validated_departure(item, target_planned_at):
                continue
            return item
        if _berth_base_label(item_canonical, options) == target_base:
            if _berth_is_released_by_validated_departure(item, target_planned_at):
                continue
            occupied_same_base.append(item)
    if target_capacity > 1 and len(occupied_same_base) >= target_capacity:
        return occupied_same_base[0]
    return None
