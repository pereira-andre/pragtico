from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any


NAVIGATION_LIGHTS_FILENAME = "navigation_lights_setubal.json"

LIGHT_QUERY_RE = re.compile(
    r"\b(balizagem|balizas?|boias?|b[oó]ias?|luzes?|farol|far[oó]is|enfiamento|"
    r"ajudas?\s+a\s+navegac[aã]o|iala|caracter[ií]stica\s+(?:da|do|das|dos))\b",
    flags=re.IGNORECASE,
)
LIGHT_CODE_RE = re.compile(
    r"\b(?:\d{1,2}(?:CN|CS|CC)|D-\d{4}(?:\.\d+)?)\b",
    flags=re.IGNORECASE,
)
IALA_QUERY_RE = re.compile(r"\biala\b|\bsistema\s+de\s+balizagem\b", flags=re.IGNORECASE)
SOURCE_COVERAGE_RE = re.compile(
    r"\b(fonte|fontes|documento|base|cobre|cobrem|inclui|incluem|conhecimento|indexavel|indexável|incorporad\w*)\b",
    flags=re.IGNORECASE,
)
AREA_QUERY = {
    "SETÚBAL - CANAL NORTE": re.compile(r"\bcanal\s+norte\b|\bcn\b", flags=re.IGNORECASE),
    "SETÚBAL - CANAL SUL": re.compile(r"\bcanal\s+sul\b|\bcs\b", flags=re.IGNORECASE),
    "SETÚBAL": re.compile(r"\bbarra\b|\bset[uú]bal\b", flags=re.IGNORECASE),
}


def _normalize(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.replace("–", "-").replace("º", " ").replace("°", " ")
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _tokens(value: object) -> set[str]:
    return {token for token in _normalize(value).split() if token}


@lru_cache(maxsize=8)
def _load_navigation_lights_cached(path_str: str, mtime_ns: int) -> dict[str, Any]:
    del mtime_ns
    with open(path_str, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload.setdefault("entries", [])
    return payload


def load_navigation_lights(knowledge_dir: str | Path) -> dict[str, Any]:
    path = Path(knowledge_dir) / NAVIGATION_LIGHTS_FILENAME
    stat = path.stat()
    return _load_navigation_lights_cached(str(path), stat.st_mtime_ns)


def looks_like_navigation_lights_query(question: str) -> bool:
    clean = str(question or "")
    if SOURCE_COVERAGE_RE.search(clean) and LIGHT_QUERY_RE.search(clean):
        return True
    if IALA_QUERY_RE.search(clean):
        return True
    if LIGHT_CODE_RE.search(clean):
        return True
    return bool(LIGHT_QUERY_RE.search(clean))


def _entry_aliases(entry: dict[str, Any]) -> list[str]:
    aliases = list(entry.get("aliases") or [])
    aliases.extend(
        [
            entry.get("name", ""),
            entry.get("national_number", ""),
            entry.get("international_number", ""),
        ]
    )
    return [str(alias).strip() for alias in dict.fromkeys(aliases) if str(alias or "").strip()]


def _score_entry(question: str, entry: dict[str, Any]) -> int:
    query_norm = _normalize(question)
    query_tokens = _tokens(question)
    if not query_norm or not query_tokens:
        return 0

    score = 0
    name_norm = _normalize(entry.get("name", ""))
    if name_norm and name_norm not in {"norte", "sul"} and len(name_norm) >= 4 and name_norm in query_norm:
        score += 120

    for alias in _entry_aliases(entry):
        alias_norm = _normalize(alias)
        if not alias_norm:
            continue
        alias_tokens = alias_norm.split()
        if len(alias_tokens) == 1:
            token = alias_tokens[0]
            if token not in {"norte", "sul"} and len(token) >= 3 and token in query_tokens:
                score += 100
        elif alias_norm in query_norm:
            score += 90

    entry_tokens = _tokens(
        " ".join(
            str(value or "")
            for value in (
                entry.get("name"),
                entry.get("national_number"),
                entry.get("international_number"),
                entry.get("area"),
            )
        )
    )
    score += len(query_tokens & entry_tokens) * 6

    area = str(entry.get("area") or "")
    area_re = AREA_QUERY.get(area)
    if area_re and area_re.search(question):
        score += 10
    return score


def _matching_entries(question: str, payload: dict[str, Any], *, max_entries: int = 8) -> list[dict[str, Any]]:
    if IALA_QUERY_RE.search(question) and not LIGHT_CODE_RE.search(question):
        return []

    scored = []
    for index, entry in enumerate(payload.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        score = _score_entry(question, entry)
        if score:
            scored.append((score, -index, entry))
    scored.sort(reverse=True)
    if scored:
        best_score = scored[0][0]
        exactish = [entry for score, _index, entry in scored if score >= 90]
        if exactish:
            return exactish[:max_entries]
        return [entry for score, _index, entry in scored if score >= max(best_score - 12, 1)][:max_entries]

    for area, area_re in AREA_QUERY.items():
        if area_re.search(question):
            return [
                entry
                for entry in payload.get("entries") or []
                if isinstance(entry, dict) and entry.get("area") == area
            ][:max_entries]
    return []


def _entry_line(entry: dict[str, Any]) -> str:
    position = entry.get("position") or {}
    parts = [
        f"{entry.get('name') or '--'}",
        f"N.º nacional {entry.get('national_number') or '--'}",
    ]
    if entry.get("international_number"):
        parts.append(f"N.º internacional {entry.get('international_number')}")
    parts.extend(
        [
            f"zona {entry.get('area') or '--'}",
            f"posição {position.get('latitude') or '--'} {position.get('longitude') or '--'} ({position.get('datum') or 'WGS84'})",
            (
                f"característica {entry.get('characteristic')}"
                if entry.get("characteristic")
                else "sem característica luminosa indicada"
            ),
        ]
    )
    if entry.get("altitude_m"):
        parts.append(f"altitude {entry.get('altitude_m')} m")
    if entry.get("nominal_range_m"):
        parts.append(f"alcance {entry.get('nominal_range_m')} M")
    if entry.get("details"):
        parts.append(f"detalhes {entry.get('details')}")
    return " | ".join(parts)


def _summary_lines(payload: dict[str, Any]) -> list[str]:
    entries = [entry for entry in payload.get("entries") or [] if isinstance(entry, dict)]
    area_counts: dict[str, int] = {}
    for entry in entries:
        area = str(entry.get("area") or "SETÚBAL")
        area_counts[area] = area_counts.get(area, 0) + 1
    lines = [
        f"{payload.get('title') or 'Balizagem e lista de luzes de Setúbal'}.",
        payload.get("iala_note") or "Setúbal usa o sistema IALA A.",
        f"Fonte: {payload.get('source') or 'Lista de Luzes'}.",
    ]
    for area, count in area_counts.items():
        lines.append(f"- {area}: {count} ajudas à navegação.")
    return lines


def build_navigation_lights_source(question: str, knowledge_dir: str | Path) -> dict[str, Any] | None:
    if not looks_like_navigation_lights_query(question):
        return None
    try:
        payload = load_navigation_lights(knowledge_dir)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    entries = _matching_entries(question, payload)
    lines = [
        "Balizagem/luzes de Setúbal:",
        payload.get("iala_note") or "Setúbal usa o sistema IALA A.",
        f"Fonte: {payload.get('source') or 'Lista de Luzes'}.",
    ]
    if SOURCE_COVERAGE_RE.search(question or ""):
        selected = []
        for entry in payload.get("entries") or []:
            haystack = _normalize(" ".join(
                str(entry.get(key) or "")
                for key in ("name", "national_number", "international_number", "area")
            ))
            if (
                re.search(r"\b1cn\b", haystack)
                or re.search(r"\b2cs\b", haystack)
                or "doca pesca" in haystack
            ):
                selected.append(entry)
        if selected:
            entries = selected[:6]
    if entries:
        lines.append("Registos relevantes:")
        for entry in entries:
            lines.append(f"- {_entry_line(entry)}")
    else:
        lines.extend(_summary_lines(payload))

    warning = str(payload.get("warning") or "").strip()
    if warning:
        lines.append(warning)
    return {
        "source_id": "NAV_LIGHTS_SETUBAL",
        "document": "Lista_Luzes_Setubal.txt",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "navigation_lights",
        "snippet": "\n".join(lines),
        "text": "\n".join(lines),
        "entries": entries,
    }
