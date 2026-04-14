from __future__ import annotations

import re
import unicodedata
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


def _alias_canonical_berth(label: str, berth_options: Iterable[str] | None = None) -> str:
    clean = " ".join(str(label or "").strip().split())
    if not clean:
        return ""
    options = list(berth_options or BERTH_OPTIONS)
    compact = _berth_compact_key(clean)
    key = _berth_key(clean)

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
        "cais0a": "Lisnave - Cais 0 A",
        "cais0b": "Lisnave - Cais 0 B",
        "cais1a": "Lisnave - Cais 1 A",
        "cais1b": "Lisnave - Cais 1 B",
        "cais2a": "Lisnave - Cais 2 A",
        "cais2b": "Lisnave - Cais 2 B",
        "cais3a": "Lisnave - Cais 3 A",
        "cais3b": "Lisnave - Cais 3 B",
        "c0a": "Lisnave - Cais 0 A",
        "c0b": "Lisnave - Cais 0 B",
        "c1a": "Lisnave - Cais 1 A",
        "c1b": "Lisnave - Cais 1 B",
        "c2a": "Lisnave - Cais 2 A",
        "c2b": "Lisnave - Cais 2 B",
        "c3a": "Lisnave - Cais 3 A",
        "c3b": "Lisnave - Cais 3 B",
        "lisnavecais0a": "Lisnave - Cais 0 A",
        "lisnavecais0b": "Lisnave - Cais 0 B",
        "lisnavecais1a": "Lisnave - Cais 1 A",
        "lisnavecais1b": "Lisnave - Cais 1 B",
        "lisnavecais2a": "Lisnave - Cais 2 A",
        "lisnavecais2b": "Lisnave - Cais 2 B",
        "lisnavecais3a": "Lisnave - Cais 3 A",
        "lisnavecais3b": "Lisnave - Cais 3 B",
        "lisnavec0a": "Lisnave - Cais 0 A",
        "lisnavec0b": "Lisnave - Cais 0 B",
        "lisnavec1a": "Lisnave - Cais 1 A",
        "lisnavec1b": "Lisnave - Cais 1 B",
        "lisnavec2a": "Lisnave - Cais 2 A",
        "lisnavec2b": "Lisnave - Cais 2 B",
        "lisnavec3a": "Lisnave - Cais 3 A",
        "lisnavec3b": "Lisnave - Cais 3 B",
        "lisnave3a": "Lisnave - Cais 3 A",
        "lisnave3b": "Lisnave - Cais 3 B",
        "a3lisnave": "Lisnave - Cais 3 A",
        "lisnavea3": "Lisnave - Cais 3 A",
    }
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


def canonicalize_berth_label(label: str | None, berth_options: Iterable[str] | None = None) -> str:
    clean = " ".join(str(label or "").strip().split())
    if not clean:
        return ""
    options = list(berth_options or BERTH_OPTIONS)
    key = _berth_key(clean)
    compact = _berth_compact_key(clean)
    by_key = {_berth_key(item): item for item in options}
    by_compact = {_berth_compact_key(item): item for item in options}
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
    return canonicalize_berth_label(clean, options) in options


def is_anchorage_berth(label: str | None) -> bool:
    key = _berth_key(label)
    if not key:
        return False
    if "fundeadouro norte" in key:
        return True
    return "fundeadouro" in key and ("sul" in key or "troia" in key)


def slot_berth_options(berth_options: Iterable[str] | None = None) -> List[str]:
    options = list(berth_options or BERTH_OPTIONS)
    return [item for item in options if not is_anchorage_berth(item)]


def berth_sort_key(label: str | None, berth_options: Iterable[str] | None = None) -> tuple[int, str]:
    key = _berth_key(label)
    options = list(berth_options or BERTH_OPTIONS)
    indexed = {_berth_key(item): index for index, item in enumerate(options)}
    if key in indexed:
        return (indexed[key], key)
    for option_key, index in indexed.items():
        if option_key and (option_key in key or key in option_key):
            return (index, key)
    return (len(indexed) + 1, key)


def _group_vessels_by_berth(
    vessels: Iterable[Dict],
    berth_options: Iterable[str] | None = None,
) -> List[Dict]:
    groups: Dict[str, List[Dict]] = {}
    for item in vessels:
        label = item.get("berth_label") or "Sem cais atribuído"
        groups.setdefault(label, []).append(item)
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
    occupied_slot_count = len(berthed)
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
    berth_options: Iterable[str] | None = None,
) -> Optional[Dict]:
    options = list(berth_options or BERTH_OPTIONS)
    target_clean = " ".join(str(target_berth or "").strip().split())
    if not target_clean:
        return None
    target_canonical = canonicalize_berth_label(target_clean, options)
    if target_canonical not in options:
        return None
    if is_anchorage_berth(target_canonical):
        return None

    current_id = (current_port_call_id or "").strip()
    for item in in_port_items or []:
        item_id = (item.get("id") or item.get("port_call_id") or "").strip()
        if current_id and item_id == current_id:
            continue
        item_berth = item.get("berth_label") or item.get("berth") or ""
        item_canonical = canonicalize_berth_label(item_berth, options)
        if item_canonical == target_canonical:
            return item
    return None
