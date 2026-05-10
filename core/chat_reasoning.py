"""Conversation-state extraction for operational follow-up reasoning."""

from __future__ import annotations

from typing import Iterable
import re

from core.chat_context_scope import scoped_history_for_question
from core.chat_planner import ChatExecutionPlan, normalize_planner_text

VESSEL_TYPE_LABELS = {
    "roro": "Ro-Ro",
    "ro ro": "Ro-Ro",
    "ro/ro": "Ro-Ro",
    "contentores grande": "Contentores grande",
    "porta contentores grande": "Contentores grande",
    "container grande": "Contentores grande",
    "contentores": "Contentores",
    "container": "Contentores",
    "tanque": "Tanque",
    "graneis solidos": "Granéis sólidos",
    "graneis": "Granéis",
    "graneleiro": "Graneleiro",
    "bulk": "Graneleiro",
    "reefer": "Reefer",
    "reefers": "Reefer",
    "estilha": "Estilha",
    "passageiros": "Passageiros",
}

LOA_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?(?:comprimento|loa)\b",
    flags=re.IGNORECASE,
)
BARE_LOA_RE = re.compile(
    r"\b(?:navio|roro|ro\s*ro|ro-ro|ro/ro|loa|comprimento)\b[^\n.;,]{0,80}?\b(\d{2,3}(?:[.,]\d+)?)\s*m\b",
    flags=re.IGNORECASE,
)
DRAFT_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?calado\b"
    r"|\bcalado\s*(?:de|:)?\s*(\d+(?:[.,]\d+)?)\s*m(?:etros?)?\b",
    flags=re.IGNORECASE,
)
BEAM_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?(?:boca|beam)\b",
    flags=re.IGNORECASE,
)
TUG_RE = re.compile(
    r"\b(\d+)\s*(?:reboques|rebocadores|rebocador|reboque)\b",
    flags=re.IGNORECASE,
)
NO_BOW_RE = re.compile(
    r"\b(?:sem|s/?|nao tem|não tem|avariado|inoperacional)\s+"
    r"(?:bow\s*thruster|bowthruster|h[eé]lice de proa|hpr)\b",
    flags=re.IGNORECASE,
)
HAS_BOW_RE = re.compile(
    r"\b(?:com|tem)\s+(?:bow\s*thruster|bowthruster|h[eé]lice de proa|hpr)\b",
    flags=re.IGNORECASE,
)
THRUSTER_RE = re.compile(
    r"\b(bow thruster|stern thruster|thruster(?:s)?)\b",
    flags=re.IGNORECASE,
)
WIND_PATTERNS = (
    (re.compile(r"\b(?:vento\s*)?(?:sw|sudoeste)\b", flags=re.IGNORECASE), "vento SW / sudoeste"),
    (re.compile(r"\b(?:vento\s*s\b|sul)\b", flags=re.IGNORECASE), "vento Sul"),
    (re.compile(r"\b(?:vento\s*n\b|norte)\b", flags=re.IGNORECASE), "vento Norte"),
    (re.compile(r"\b(?:vento\s*w\b|oeste)\b", flags=re.IGNORECASE), "vento Oeste"),
    (re.compile(r"\bvento\s+E\b"), "vento Este"),
    (re.compile(r"\b(?:vento\s+este\b|vento\s+leste\b|leste|east)\b", flags=re.IGNORECASE), "vento Este"),
    (re.compile(r"\b(?:nevoeiro|nevoa|névoa)\b", flags=re.IGNORECASE), "nevoeiro"),
)
PROPELLER_RE = re.compile(r"\bpasso\s+(direito|esquerdo)\b", flags=re.IGNORECASE)
BERTHING_SIDE_RE = re.compile(r"\b(?:por|a|ao)\s+(estibordo|bombordo)\b", flags=re.IGNORECASE)
TIME_RE = re.compile(
    r"\b(?:as|às|para as|para às|para|pelas)\s*(\d{1,2}(?::\d{2}|h\d{0,2})|\d{3,4})\b"
    r"|\b(\d{1,2}(?::\d{2}|h\d{2}))\b",
    flags=re.IGNORECASE,
)
DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
RELATIVE_DATE_RE = re.compile(r"\b(hoje|amanh[ãa]|depois de amanh[ãa])\b", flags=re.IGNORECASE)
CARGO_NON_IMO_RE = re.compile(r"\bcarga\s+(?:nao|não)\s+imo\b|\b(?:nao|não)\s+imo\b", flags=re.IGNORECASE)
CARGO_IMO_RE = re.compile(r"\bcarga\s+imo\b|\bimo\b", flags=re.IGNORECASE)
CONTEXTUAL_FOLLOW_UP_RE = re.compile(
    r"^\s*(?:e|entao|então|mas|agora|nesse caso|neste caso|com isso|com base nisso)\b",
    flags=re.IGNORECASE,
)
FACILITY_PATTERNS = (
    (re.compile(r"\beco\s*-?\s*oil\b|\becooil\b|\becoil\b", flags=re.IGNORECASE), "Terminal ECO-OIL"),
    (re.compile(r"\btanquisado\b", flags=re.IGNORECASE), "Terminal da TANQUISADO"),
    (re.compile(r"\bpraias\s+do\s+sado\b|\bpirites\b", flags=re.IGNORECASE), "Terminal Praias do Sado"),
    (re.compile(r"\bsapec\b|\btps\b|\btgl\b", flags=re.IGNORECASE), "SAPEC / TPS-TGL"),
    (re.compile(r"\balstom\b|\babb\s*-?\s*alstom\b", flags=re.IGNORECASE), "Cais ALSTOM"),
    (re.compile(r"\bsecil\b", flags=re.IGNORECASE), "Terminal SECIL"),
    (re.compile(r"\btepor\s*set\b|\bteporset\b", flags=re.IGNORECASE), "TEPORSET"),
    (re.compile(r"\btms\s*1\b|\btms1\b|\bfontainhas\b", flags=re.IGNORECASE), "TMS1"),
    (re.compile(r"\btms\s*2\b|\btms2\b|\bterminal de contentores\b", flags=re.IGNORECASE), "TMS2"),
    (re.compile(r"\blisnave\b|\bmitrena\b", flags=re.IGNORECASE), "LISNAVE / Mitrena"),
)


def split_message_utterances(content: str) -> list[str]:
    """Split a compound user message without breaking decimal numbers such as 9.5m."""
    text = str(content or "").strip()
    if not text:
        return []

    parts: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char == ".":
            previous_char = text[index - 1] if index > 0 else ""
            next_char = text[index + 1] if index + 1 < len(text) else ""
            if previous_char.isdigit() and next_char.isdigit():
                continue
            if next_char and not next_char.isspace():
                continue
        elif char not in {"?", "!"}:
            continue

        segment = text[start:index + 1].strip()
        if segment:
            parts.append(segment)
        start = index + 1

    tail = text[start:].strip()
    if tail:
        parts.extend(part.strip() for part in re.split(r"\n+", tail) if part.strip())
    return parts


def build_compound_message_analysis_source(question: str) -> dict | None:
    segments = split_message_utterances(question)
    if len(segments) < 2:
        return None

    facts = _extract_message_facts(question)
    questions = [segment for segment in segments if "?" in segment]
    lines = ["Mensagem composta detetada. Processar os segmentos por ordem e acumular factos antes de responder."]
    lines.append("Segmentos:")
    for index, segment in enumerate(segments[:8], start=1):
        segment_type = "pergunta" if "?" in segment else "contexto"
        lines.append(f"{index}. ({segment_type}) {segment}")
    if facts:
        lines.append("Factos extraidos: " + " ".join(facts[:10]))
    if questions:
        lines.append("Perguntas explicitas a responder: " + " | ".join(questions[:5]))

    snippet = "\n".join(lines)
    return {
        "source_id": "MSG1",
        "document": "Analise estruturada da mensagem",
        "chunk_id": 0,
        "score": 1.0,
        "retrieval_mode": "message_analysis",
        "snippet": snippet,
        "text": snippet,
    }


def _clean_numeric(value: str) -> str:
    return str(value or "").strip().replace(".", ",")


def _clean_time(value: str) -> str:
    clean_value = str(value or "").strip().lower()
    if re.fullmatch(r"\d{3,4}", clean_value):
        digits = clean_value.zfill(4)
        return f"{digits[:2]}:{digits[2:]}"
    clean_value = clean_value.replace("h", ":")
    if clean_value.endswith(":"):
        clean_value += "00"
    return clean_value


def _extract_message_facts(content: str) -> list[str]:
    clean = normalize_planner_text(content)
    facts: list[str] = []

    for pattern, label in FACILITY_PATTERNS:
        if pattern.search(content or ""):
            facts.append(f"Cais/terminal referido: {label}.")
            break

    if re.search(r"\b(saida|sair|desatracar|desatracacao)\b", clean):
        facts.append("Operação pretendida: saída/desatracação.")
    elif re.search(r"\b(entrada|entrar)\b", clean):
        facts.append("Operação pretendida: entrada.")
    elif re.search(r"\b(atracar|atracacao)\b", clean):
        facts.append("Operação pretendida: atracação.")
    elif re.search(r"\b(mudanca|shift)\b", clean):
        facts.append("Operação pretendida: mudança.")
    if re.search(r"\b(cancelaram|cancelada|cancelado|cancelar|cancelou|abortada|abortado|abortar)\b", clean):
        facts.append("Contexto referido: manobra cancelada/abortada.")

    for token, label in VESSEL_TYPE_LABELS.items():
        if token in clean:
            facts.append(f"Tipo de navio: {label}.")
            break

    if CARGO_NON_IMO_RE.search(content or ""):
        facts.append("Carga: não IMO.")
    elif CARGO_IMO_RE.search(content or ""):
        facts.append("Carga: IMO/perigosa.")

    for pattern, label in WIND_PATTERNS:
        if pattern.search(content or ""):
            facts.append(f"Condição meteo referida: {label}.")
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
        facts.append(f"Calado: {_clean_numeric(draft_match.group(1) or draft_match.group(2))} m.")

    beam_match = BEAM_RE.search(content or "")
    if beam_match:
        facts.append(f"Boca: {_clean_numeric(beam_match.group(1))} m.")

    tug_match = TUG_RE.search(content or "")
    if tug_match:
        count = tug_match.group(1)
        facts.append(f"Referência a {count} rebocador(es)/reboque(s).")
    elif re.search(r"\b(reboque|reboques|rebocador|rebocadores)\b", content or "", flags=re.IGNORECASE):
        facts.append("Pedido de recomendação/necessidade de rebocadores.")

    if NO_BOW_RE.search(content or ""):
        facts.append("Bowthruster ausente/inoperacional.")
    elif HAS_BOW_RE.search(content or ""):
        facts.append("Bowthruster disponível.")
    elif THRUSTER_RE.search(content or ""):
        facts.append("Há referência explícita a thrusters do navio.")

    propeller_match = PROPELLER_RE.search(content or "")
    if propeller_match:
        facts.append(f"Passo do hélice: {propeller_match.group(1).lower()}.")

    berthing_side_match = BERTHING_SIDE_RE.search(content or "")
    if berthing_side_match:
        facts.append(f"Bordo de atracação: {berthing_side_match.group(1).lower()}.")

    time_match = TIME_RE.search(content or "")
    if time_match:
        raw_time = _clean_time(time_match.group(1) or time_match.group(2) or "")
        facts.append(f"Hora planeada/referida: {raw_time}.")

    date_match = DATE_RE.search(content or "")
    if date_match:
        facts.append(f"Data referida: {date_match.group(0)}.")
    else:
        for relative_date_match in RELATIVE_DATE_RE.finditer(content or ""):
            facts.append(f"Data relativa referida: {relative_date_match.group(1)}.")

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


def _looks_like_contextual_follow_up(question: str) -> bool:
    clean = normalize_planner_text(question)
    if not clean:
        return False
    if CONTEXTUAL_FOLLOW_UP_RE.search(question or ""):
        return True
    tokens = clean.split()
    return 1 <= len(tokens) <= 5 and any(
        token in clean
        for token in (
            "imo",
            "carga",
            "calado",
            "mare",
            "vento",
            "rebocador",
            "reboque",
            "entrada",
            "saida",
        )
    )


def _latest_user_case_facts(history: list[dict]) -> tuple[list[str], str]:
    for entry in reversed(history):
        if str(entry.get("role") or "").strip().lower() != "user":
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        facts = _extract_message_facts(content)
        if facts:
            return facts, content
    return [], ""


def _case_label_from_facts(facts: list[str]) -> str:
    priority_prefixes = (
        "Cais/terminal referido:",
        "Operação pretendida:",
        "Tipo de navio:",
        "LOA / comprimento:",
        "Calado:",
        "Boca:",
        "Carga:",
        "Hora planeada/referida:",
        "Data referida:",
        "Data relativa referida:",
    )
    selected: list[str] = []
    for prefix in priority_prefixes:
        selected.extend(fact for fact in facts if fact.startswith(prefix))
    selected = list(dict.fromkeys(selected))
    return " ".join(selected[:5]).strip()


def build_conversation_reasoning_state(
    question: str,
    history: list[dict],
    plan: ChatExecutionPlan,
) -> dict | None:
    contextual_follow_up = _looks_like_contextual_follow_up(question)
    if not (
        plan.needs_history_state
        or plan.requires_live_reasoning
        or plan.requires_llm_synthesis
        or contextual_follow_up
    ):
        return None

    fact_lines: list[str] = []
    prior_recommendation = ""
    scoped_history = scoped_history_for_question(question, history, max_messages=8)
    recent_messages = list(_iter_recent_messages(scoped_history))
    latest_case_facts, latest_case_message = _latest_user_case_facts(scoped_history)
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

    if not fact_lines and not prior_recommendation and not latest_case_facts:
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

    likely_case_label = _case_label_from_facts(latest_case_facts)
    current_case_label = _case_label_from_facts(current_facts)
    summary_parts = ["Ficha de contexto provável para resposta operacional."]
    if contextual_follow_up and likely_case_label:
        summary_parts.append(
            "Premissa de continuidade: a mensagem atual parece continuação curta; "
            f"assumir o último caso operacional se não houver conflito explícito. Caso provável: {likely_case_label}"
        )
        if latest_case_message:
            summary_parts.append(f"Última pergunta de caso: {latest_case_message[:240]}")
        summary_parts.append(
            "Aviso de resposta: se a conclusão depender desta premissa, mencionar em frase curta "
            "que se assume a continuação do mesmo caso; se houver dúvida, pedir confirmação do cais/caso."
        )
    elif current_case_label:
        summary_parts.append(f"Caso identificado na mensagem atual: {current_case_label}")
    elif likely_case_label:
        summary_parts.append(f"Contexto recente disponível, usar apenas se for relevante: {likely_case_label}")
    if fact_lines:
        summary_parts.append("Factos extraídos do histórico e da pergunta: " + " ".join(fact_lines[:8]))
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
        "likely_case": latest_case_facts,
        "contextual_follow_up": contextual_follow_up,
        "prior_recommendation": prior_recommendation,
        "focus": focus_parts,
        "source": source,
    }
