from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List

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
