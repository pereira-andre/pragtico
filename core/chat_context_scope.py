"""Conversation context scoping helpers for long-running chat channels."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable


EXPLICIT_OPERATIONAL_ANCHOR_RE = re.compile(
    r"\b("
    r"lisnave|mitrena|hidrolift|eclusa|doca\s*(?:20|21|22|31|32|33)|d(?:20|21|22|31|32|33)|"
    r"secil|outao|outão|eco\s*-?\s*oil|ecooil|ecoil|tanquisado|"
    r"tms\s*1|tms1|tms\s*2|tms2|autoeuropa|auto\s*europa|"
    r"sapec|praias\s+do\s+sado|pirites|alstom|abb\s*-?\s*alstom|tepor\s*set|teporset|termitrena|"
    r"fundeadouro\s+(?:norte|sul|troia|tr[oó]ia)|barra|pilar\s*2|canal\s+(?:norte|sul)"
    r")\b",
    flags=re.IGNORECASE,
)


def normalize_context_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


def has_explicit_operational_anchor(question: str) -> bool:
    """Return True when the current question names a concrete new place/route."""
    return bool(EXPLICIT_OPERATIONAL_ANCHOR_RE.search(str(question or "")))


def scoped_history_for_question(
    question: str,
    history: Iterable[dict] | None,
    *,
    max_messages: int = 10,
) -> list[dict]:
    """Drop stale history when the current question explicitly names a new case.

    WhatsApp conversations are long-lived, so unrelated prior cases must not leak
    into a new operational question such as "entrada para a Secil E".
    """
    if not history:
        return []
    clean_question = normalize_context_text(question)
    scoped: list[dict] = []
    for item in history:
        content = str((item or {}).get("content") or "").strip()
        if clean_question and normalize_context_text(content) == clean_question:
            continue
        scoped.append(dict(item))
    if has_explicit_operational_anchor(question):
        return []
    return scoped[-max_messages:]
