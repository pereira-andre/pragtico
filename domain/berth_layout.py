"""Berth layout helpers — slot occupancy analysis and anchorage classification."""

from __future__ import annotations

from typing import Dict, List


# ---------------------------------------------------------------------------
# Anchorage detection
# ---------------------------------------------------------------------------

_ANCHORAGE_KEYWORDS = (
    "fundeadouro",
    "anchorage",
    "quadro",
)


def is_anchorage_berth(berth_code: str | None) -> bool:
    """Return True if *berth_code* represents an anchorage / waiting area.

    Anchorages are identified by well-known keywords in the berth name.
    They are distinct from quay berths: vessels at anchorage are counted
    separately and do not consume quay slot capacity.
    """
    if not berth_code:
        return False
    normalised = berth_code.strip().lower()
    return any(keyword in normalised for keyword in _ANCHORAGE_KEYWORDS)


# ---------------------------------------------------------------------------
# Slot occupancy
# ---------------------------------------------------------------------------

_BERTH_GEO_ORDER = [
    "Secil", "Fundeadouro Norte", "Cais Palmeiras",
    "TMS 1", "TMS 2", "Autoeuropa", "Cais 10", "Cais 11",
    "Praias do Sado", "Pirites", "SAPEC",
    "ALSTOM", "PAN", "Tróia", "Fundeadouro Sul",
    "Tanquisado", "Eco-Oil",
    "Lisnave", "Teporset",
]


def _berth_geo_sort_key(name: str) -> int:
    lower = name.lower()
    for idx, prefix in enumerate(_BERTH_GEO_ORDER):
        if prefix.lower() in lower:
            return idx
    return 9999


def build_slot_occupancy(
    in_port_list: List[Dict],
    berth_options: List[str],
) -> Dict:
    """Analyse vessels currently in port and return a slot-occupancy summary.

    Parameters
    ----------
    in_port_list:
        List of decorated port-call dicts for vessels currently in port.
        Each dict is expected to carry a ``berth_label`` key with the
        human-readable berth name.
    berth_options:
        Master list of all known berth names (used to compute total slot
        capacity).  Anchorage berths are excluded from the quay capacity
        count.

    Returns
    -------
    dict with the following keys:

    ``berthed``
        List of ``{"berth": str, "count": int, "vessels": list}`` dicts
        for vessels at quay berths, sorted in geographic order.
    ``anchorages``
        List of ``{"berth": str, "count": int, "vessels": list}`` dicts
        for vessels at anchorage / waiting areas, sorted in geographic
        order.
    ``quay_vessel_count``
        Number of vessels currently at a quay berth.
    ``anchorage_vessel_count``
        Number of vessels currently at an anchorage.
    ``occupied_slot_count``
        Number of distinct quay berth slots that are occupied.
    ``free_slot_count``
        Number of quay berth slots that are currently free.
    ``slot_capacity_count``
        Total number of quay berth slots (anchorages excluded).
    """
    # Separate quay berths from anchorages in the master berth list so we
    # can compute capacity correctly.
    quay_berth_options = [b for b in (berth_options or []) if not is_anchorage_berth(b)]
    slot_capacity_count = len(quay_berth_options)

    # Bucket each in-port vessel by berth label.
    quay_map: Dict[str, List[Dict]] = {}
    anchorage_map: Dict[str, List[Dict]] = {}

    for item in (in_port_list or []):
        berth_label = (item.get("berth_label") or item.get("berth") or "").strip()
        if is_anchorage_berth(berth_label):
            anchorage_map.setdefault(berth_label, []).append(item)
        else:
            quay_map.setdefault(berth_label, []).append(item)

    # Build sorted berth-group lists.
    berthed: List[Dict] = [
        {"berth": berth, "count": len(vessels), "vessels": vessels}
        for berth, vessels in sorted(
            quay_map.items(), key=lambda pair: _berth_geo_sort_key(pair[0])
        )
    ]
    anchorages: List[Dict] = [
        {"berth": berth, "count": len(vessels), "vessels": vessels}
        for berth, vessels in sorted(
            anchorage_map.items(), key=lambda pair: _berth_geo_sort_key(pair[0])
        )
    ]

    quay_vessel_count = sum(len(vessels) for vessels in quay_map.values())
    anchorage_vessel_count = sum(len(vessels) for vessels in anchorage_map.values())
    occupied_slot_count = len(quay_map)
    free_slot_count = max(slot_capacity_count - occupied_slot_count, 0)

    return {
        "berthed": berthed,
        "anchorages": anchorages,
        "quay_vessel_count": quay_vessel_count,
        "anchorage_vessel_count": anchorage_vessel_count,
        "occupied_slot_count": occupied_slot_count,
        "free_slot_count": free_slot_count,
        "slot_capacity_count": slot_capacity_count,
    }
