"""Conversation-state extraction for operational follow-up reasoning."""

from __future__ import annotations

from typing import Iterable
import re

from core.chat_planner import ChatExecutionPlan, normalize_planner_text

VESSEL_TYPE_LABELS = {
    "roro": "Ro-Ro",
    "ro ro": "Ro-Ro",
    "contentores": "Contentores",
    "container": "Contentores",
    "tanque": "Tanque",
    "graneis": "Granéis",
    "graneis solidos": "Granéis sólidos",
    "passageiros": "Passageiros",
}

LOA_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?(?:comprimento|loa)\b",
    flags=re.IGNORECASE,
)
BARE_LOA_RE = re.compile(
    r"\b(?:navio|roro|ro\s*ro|ro-ro|loa|comprimento)\b[^\n.;,]{0,60}?\b(\d{2,3}(?:[.,]\d+)?)\s*m\b",
    flags=re.IGNORECASE,
)
DRAFT_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?calado\b",
    flags=re.IGNORECASE,
)
TUG_RE = re.compile(
    r"\b(\d+)\s*(?:reboques|rebocadores|rebocador|reboque)\b",
    flags=re.IGNORECASE,
)
THRUSTER_RE = re.compile(
    r"\b(bow thruster|stern thruster|thruster(?:s)?)\b",
    flags=re.IGNORECASE,
)


def _clean_numeric(value: str) -> str:
    return str(value or "").strip().replace(".", ",")


def _extract_message_facts(content: str) -> list[str]:
    clean = normalize_planner_text(content)
    facts: list[str] = []

    for token, label in VESSEL_TYPE_LABELS.items():
        if token in clean:
            facts.append(f"Tipo de navio: {label}.")
            break

    loa_match = LOA_RE.search(content or "")
    if loa_match:
        facts.append(f"LOA / comprimento: {_clean_numeric(loa_match.group(1))} m.")
    else:
        bare_loa_match = BARE_LOA_RE.search(content or "")
        if bare_loa_match:
            facts.append(f"LOA / comprimento: {_clean_numeric(bare_loa_match.group(1))} m.")

    draft_match = DRAFT_RE.search(content or "")
    if draft_match:
        facts.append(f"Calado: {_clean_numeric(draft_match.group(1))} m.")

    tug_match = TUG_RE.search(content or "")
    if tug_match:
        count = tug_match.group(1)
        facts.append(f"Referência a {count} rebocador(es)/reboque(s).")

    if THRUSTER_RE.search(content or ""):
        facts.append("Há referência explícita a thrusters do navio.")

    return list(dict.fromkeys(facts))


def _extract_assistant_recommendation(content: str) -> str:
    clean = normalize_planner_text(content)
    if not any(token in clean for token in ("recomendo", "recomendaria", "aconselho", "parecem", "suficient")):
        return ""
    condensed = re.sub(r"\s+", " ", str(content or "")).strip()
    return condensed[:280]


def _iter_recent_messages(history: list[dict], limit: int = 6) -> Iterable[dict]:
    meaningful = [item for item in history if str(item.get("content") or "").strip()]
    return meaningful[-limit:]


def build_conversation_reasoning_state(
    question: str,
    history: list[dict],
    plan: ChatExecutionPlan,
) -> dict | None:
    if not (plan.needs_history_state or plan.requires_live_reasoning or plan.requires_llm_synthesis):
        return None

    fact_lines: list[str] = []
    prior_recommendation = ""
    recent_messages = list(_iter_recent_messages(history))
    for entry in recent_messages:
        role = str(entry.get("role") or "").strip().lower()
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant" and not prior_recommendation:
            prior_recommendation = _extract_assistant_recommendation(content)
        fact_lines.extend(_extract_message_facts(content))

    current_facts = _extract_message_facts(question)
    if current_facts:
        fact_lines.extend(current_facts)
    fact_lines = list(dict.fromkeys(item for item in fact_lines if item))

    if not fact_lines and not prior_recommendation:
        return None

    focus_parts: list[str] = []
    if "weather" in plan.live_facets:
        focus_parts.append("usar o vento/meteorologia atual como evidência")
    if "tides" in plan.live_facets:
        focus_parts.append("usar o estado de maré atual como evidência")
    if any(token in plan.normalized_question for token in ("reboque", "rebocador")):
        focus_parts.append("concluir explicitamente se os rebocadores propostos são suficientes")
    if not focus_parts:
        focus_parts.append("responder à avaliação operacional pedida e não apenas descrever dados")

    summary_parts = []
    if fact_lines:
        summary_parts.append("Fatos extraídos do histórico e da pergunta: " + " ".join(fact_lines[:6]))
    if prior_recommendation:
        summary_parts.append(f"Recomendação anterior do assistente: {prior_recommendation}")
    summary_parts.append("Foco atual: " + "; ".join(focus_parts) + ".")
    summary = " ".join(summary_parts).strip()

    source = {
        "source_id": "CONV1",
        "document": "Estado conversacional",
        "chunk_id": 0,
        "score": 1.0,
        "retrieval_mode": "conversation_state",
        "snippet": summary,
        "text": summary,
    }
    return {
        "summary": summary,
        "facts": fact_lines,
        "prior_recommendation": prior_recommendation,
        "focus": focus_parts,
        "source": source,
    }
