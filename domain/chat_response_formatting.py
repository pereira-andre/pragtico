from __future__ import annotations

import re
from copy import copy
from typing import Any


LIVE_SLASH_ORIGIN_EMOJI = {
    "slash_weather": "🌦️",
    "slash_tides": "🌕",
    "slash_wave": "🌊",
    "slash_local_warnings": "⚠️",
}
OPERATIONAL_ORIGIN_EMOJI = {
    "slash_template": "📋",
    "slash_proposal": "📋",
    "slash_validation": "📋",
    "operational_lookup": "📋",
    "operational_update": "📋",
    "operational_replace": "📋",
    "pending_action_confirmed": "✅",
}
CONTEXT_LINE_EMOJIS = (
    ("Condições meteorológicas", "🌦️"),
    ("Meteorologia", "🌦️"),
    ("Evolução prevista", "🌦️"),
    ("Marés", "🌕"),
    ("Leitura costeira", "🌊"),
    ("Ondulação", "🌊"),
    ("Avisos locais", "⚠️"),
    ("Planeamento", "🗓️"),
    ("Chegadas previstas", "⏳"),
    ("Saídas recentes", "✅"),
    ("Arquivo", "📂"),
    ("Registo de escalas", "📋"),
)
KNOWN_PREFIXES = {
    "🌦️",
    "🌕",
    "🌊",
    "⚠️",
    "📋",
    "✅",
    "🗓️",
    "⏳",
    "📂",
    "🚢",
    "⚓",
}
LIVE_SOURCE_MODES = {"live_planner", "live_api", "structured"}
OPERATIONAL_SOURCE_MODES = {
    "operational_lookup",
    "operational_snapshot",
    "operational_scales",
    "operational_archive",
    "operational_action",
}
LIVE_FEED_QUESTION_RE = re.compile(
    r"\b("
    r"meteorologia|meteo|tempo|vento|mares|marés|preia|baixa|ondulacao|ondulação|"
    r"avisos?|anav|escala|escalas|manobra|manobras|chegadas?|sa[ií]das?|"
    r"planeamento|arquivo|navios?\s+em\s+porto|quadro"
    r")\b",
    flags=re.IGNORECASE,
)


def _answer_text(payload: dict[str, Any]) -> str:
    return str(payload.get("answer") or "").strip()


def _starts_with_known_emoji(line: str) -> bool:
    clean = line.lstrip()
    return any(clean.startswith(prefix) for prefix in KNOWN_PREFIXES)


def _prefix_first_content_line(text: str, emoji: str) -> str:
    if not text or not emoji:
        return text
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if _starts_with_known_emoji(line):
            return text
        leading = line[: len(line) - len(line.lstrip())]
        lines[index] = f"{leading}{emoji} {line.lstrip()}"
        return "\n".join(lines)
    return text


def _decorate_context_lines(text: str) -> str:
    lines = text.splitlines()
    changed = False
    for index, line in enumerate(lines):
        clean = line.lstrip()
        if not clean or _starts_with_known_emoji(clean):
            continue
        for prefix, emoji in CONTEXT_LINE_EMOJIS:
            if clean.startswith(prefix):
                leading = line[: len(line) - len(clean)]
                lines[index] = f"{leading}{emoji} {clean}"
                changed = True
                break
    return "\n".join(lines) if changed else text


def _source_modes(payload: dict[str, Any]) -> set[str]:
    return {
        str((source or {}).get("retrieval_mode") or "").strip()
        for source in (payload.get("sources") or [])
        if isinstance(source, dict)
    }


def _should_decorate_llm_response(payload: dict[str, Any], question: str) -> bool:
    if str(payload.get("answer_origin") or "") != "llm":
        return False
    modes = _source_modes(payload)
    if modes & LIVE_SOURCE_MODES:
        return True
    return bool(modes & OPERATIONAL_SOURCE_MODES) and bool(LIVE_FEED_QUESTION_RE.search(question or ""))


def _fallback_emoji_from_question(question: str) -> str:
    clean = str(question or "").lower()
    if re.search(r"\b(meteorologia|meteo|tempo|vento)\b", clean):
        return "🌦️"
    if re.search(r"\b(mar[eé]s|preia|baixa)\b", clean):
        return "🌕"
    if re.search(r"\b(ondulacao|ondulação|agita[cç][aã]o|leitura costeira)\b", clean):
        return "🌊"
    if re.search(r"\b(avisos?|anav|alerta)\b", clean):
        return "⚠️"
    if re.search(r"\b(chegadas?|eta|previstas?)\b", clean):
        return "⏳"
    if re.search(r"\b(sa[ií]das?|etd|recentes?)\b", clean):
        return "✅"
    if re.search(r"\b(arquivo|hist[oó]rico)\b", clean):
        return "📂"
    if re.search(r"\b(manobra|manobras|planeamento)\b", clean):
        return "🗓️"
    if re.search(r"\b(escala|escalas|navio|navios)\b", clean):
        return "📋"
    return "📋"


def add_contextual_response_emojis(payload: dict[str, Any], question: str = "") -> dict[str, Any]:
    """Add restrained UX emojis only to slash/live/operational feed responses."""
    text = _answer_text(payload)
    if not text:
        return payload

    origin = str(payload.get("answer_origin") or "")
    emoji = LIVE_SLASH_ORIGIN_EMOJI.get(origin) or OPERATIONAL_ORIGIN_EMOJI.get(origin)
    should_decorate_llm = _should_decorate_llm_response(payload, question)
    should_decorate_live = origin == "operational_live"
    if not emoji and should_decorate_llm:
        emoji = _fallback_emoji_from_question(question)
    if not emoji and should_decorate_live:
        emoji = _fallback_emoji_from_question(text)
    if not emoji:
        return payload

    decorated = _decorate_context_lines(text)
    if decorated == text:
        decorated = _prefix_first_content_line(text, emoji)
    if decorated == text:
        return payload

    updated = copy(payload)
    updated["answer"] = decorated
    return updated
