"""Operational rules for Lisnave manoeuvre assistance."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

from domain.berth_layout import BERTH_OPTIONS, canonicalize_berth_label


LISNAVE_DOCK_BERTHS = {
    "Lisnave - Doca 20",
    "Lisnave - Doca 21",
    "Lisnave - Doca 22",
    "Lisnave - Doca 31",
    "Lisnave - Doca 32",
    "Lisnave - Doca 33",
}
LISNAVE_QUAY_BERTHS = {
    "Lisnave - Cais 0 A",
    "Lisnave - Cais 0 B",
    "Lisnave - Cais 1 A",
    "Lisnave - Cais 1 B",
    "Lisnave - Cais 2 A",
    "Lisnave - Cais 2 B",
    "Lisnave - Cais 3 A",
    "Lisnave - Cais 3 B",
}
LISNAVE_DOCK_MIN_TUGS = 4


def _text_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


def _compact_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text_key(value))


def _safe_int(value: Any) -> int | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        return int(float(clean.replace(",", ".")))
    except (TypeError, ValueError):
        return None


def canonical_lisnave_label(label: str | None, berth_options: Iterable[str] | None = None) -> str:
    """Return a canonical Lisnave berth for compact labels such as C3A or D33."""
    clean = " ".join(str(label or "").strip().split())
    if not clean:
        return ""
    canonical = canonicalize_berth_label(clean, berth_options=berth_options or BERTH_OPTIONS)
    if canonical in LISNAVE_DOCK_BERTHS or canonical in LISNAVE_QUAY_BERTHS:
        return canonical
    return ""


def lisnave_berth_kind(label: str | None, berth_options: Iterable[str] | None = None) -> str:
    """Classify a label as Lisnave dry dock, Lisnave quay, or neither."""
    canonical = canonical_lisnave_label(label, berth_options=berth_options)
    if canonical in LISNAVE_DOCK_BERTHS:
        return "dock"
    if canonical in LISNAVE_QUAY_BERTHS:
        return "quay"

    compact = _compact_key(label)
    if not compact:
        return ""
    if any(token in compact for token in ("d20", "d21", "d22", "d31", "d32", "d33")):
        return "dock"
    if any(token in compact for token in ("c0a", "c0b", "c1a", "c1b", "c2a", "c2b", "c3a", "c3b")):
        return "quay"
    return ""


def route_lisnave_profile(origin: str | None, destination: str | None, berth_options: Iterable[str] | None = None) -> dict:
    origin_kind = lisnave_berth_kind(origin, berth_options=berth_options)
    destination_kind = lisnave_berth_kind(destination, berth_options=berth_options)
    canonical_origin = canonical_lisnave_label(origin, berth_options=berth_options)
    canonical_destination = canonical_lisnave_label(destination, berth_options=berth_options)
    return {
        "origin_kind": origin_kind,
        "destination_kind": destination_kind,
        "origin_label": canonical_origin or str(origin or "").strip(),
        "destination_label": canonical_destination or str(destination or "").strip(),
        "involves_dock": origin_kind == "dock" or destination_kind == "dock",
        "involves_quay": origin_kind == "quay" or destination_kind == "quay",
        "target_kind": destination_kind or origin_kind,
        "target_label": canonical_destination or canonical_origin or str(destination or origin or "").strip(),
    }


def build_lisnave_rule_items(
    *,
    maneuver_type: str,
    origin: str | None,
    destination: str | None,
    tug_count: Any,
    berth_options: Iterable[str] | None = None,
) -> list[dict]:
    """Return deterministic checklist items for known Lisnave manoeuvre rules."""
    if (maneuver_type or "").strip().lower() not in {"entry", "departure", "shift"}:
        return []

    profile = route_lisnave_profile(origin, destination, berth_options=berth_options)
    count = _safe_int(tug_count)
    items: list[dict] = []

    if profile["involves_dock"]:
        if count is None or count < LISNAVE_DOCK_MIN_TUGS:
            detail_count = f"{count} previsto(s)" if count is not None else "sem número de rebocadores definido"
            items.append(
                {
                    "status": "caution",
                    "title": "Lisnave - doca",
                    "detail": (
                        "Manobras para docas Lisnave exigem pelo menos 4 rebocadores. "
                        "A entrada é atravessada à corrente: dois rebocadores empurram, um de cada lado, "
                        "e os restantes controlam popa/proa. Registo atual: "
                        f"{detail_count}."
                    ),
                }
            )
        else:
            items.append(
                {
                    "status": "ok",
                    "title": "Lisnave - doca",
                    "detail": (
                        f"Mínimo operacional de {LISNAVE_DOCK_MIN_TUGS} rebocadores cumprido para doca Lisnave "
                        f"({count} previsto(s))."
                    ),
                }
            )
        items.append(
            {
                "status": "info",
                "title": "Orientação Lisnave",
                "detail": "Navios que entram nas docas Lisnave ficam com proa a norte.",
            }
        )
    elif profile["involves_quay"]:
        items.append(
            {
                "status": "info",
                "title": "Orientação Lisnave",
                "detail": (
                    "Navios que ficam nos cais Lisnave ficam com proa a sul. "
                    "Navio pequeno para cais pode manobrar com 3 rebocadores; essa exceção não se aplica às docas."
                ),
            }
        )

    return items


def should_include_lisnave_rule_source(question: str) -> bool:
    key = _text_key(question)
    compact = _compact_key(question)
    if not key:
        return False
    if "lisnave" in key:
        return True
    if "doca" in key and any(token in key for token in ("rebocador", "rebocadores", "proa")):
        return True
    return bool(re.search(r"\b[cd](?:0a|0b|1a|1b|2a|2b|3a|3b|20|21|22|31|32|33)\b", key)) or any(
        token in compact
        for token in ("c3a", "c3b", "c2a", "c2b", "c1a", "c1b", "d31", "d32", "d33", "d21", "d22")
    )


def lisnave_rule_snippet() -> str:
    return "\n".join(
        [
            "Regra operacional estruturada Lisnave:",
            "- Docas Lisnave (D20, D21, D22, D31, D32, D33): mínimo 4 rebocadores.",
            "- Entrada em doca é atravessada à corrente: dois rebocadores empurram, um de cada lado, e os restantes controlam popa/proa.",
            "- Navios que entram nas docas ficam com proa a norte.",
            "- Cais Lisnave (C0A/C0B/C1A/C1B/C2A/C2B/C3A/C3B): navios ficam com proa a sul.",
            "- Navio pequeno para cais pode manobrar com 3 rebocadores; esta exceção não se aplica às docas.",
        ]
    )
