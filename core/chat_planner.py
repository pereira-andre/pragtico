"""Heuristic chat planner used to decide which sources the bot should consult."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

TIDE_QUERY_RE = re.compile(r"\b(mare|mares|preia mar|preia-mar|baixa mar|baixa-mar)\b")
WEATHER_QUERY_RE = re.compile(
    r"\b(meteorologia|meteorologic|condicoes meteorologicas|condicoes do tempo|tempo no porto|vento|visibilidade|humidade|temperatura|chuva)\b"
)
CURRENT_WEATHER_RE = re.compile(r"\b(atual|atuais|agora|neste momento|corrente|correntes|hoje)\b")
WEATHER_TIMELINE_RE = re.compile(
    r"\b(ao longo do dia|durante o dia|resto do dia|nas proximas horas|nas próximas horas|"
    r"ate|até|ate as|até as|ate ao|até ao)\b"
)
WAVE_QUERY_RE = re.compile(
    r"\b(ondulacao|ondulação|leitura costeira|altura significativa|periodo medio|período médio|estado do mar|"
    r"mar fora da barra|ondulacao na barra|ondulação na barra|condicoes na barra|condições na barra|"
    r"temperatura da agua|temperatura da água)\b"
)
WARNING_QUERY_RE = re.compile(
    r"\b(aviso local|avisos locais|avisos em vigor|avisos da capitania|anav|capitania|avisos?)\b"
)
DOCUMENT_QUERY_RE = re.compile(
    r"\b(regra|regras|documento|doc|instrucao|instrução|norma|normas|procedimento|procedimentos|"
    r"o que diz|segundo o|segundo a|it[\s\-_]?\d{1,3})\b"
)
RULE_CODE_RE = re.compile(r"\bit[\s\-_]?0*(\d{1,3})\b", flags=re.IGNORECASE)
LIVE_REASONING_RE = re.compile(
    r"\b(deve|devemos|pode|podemos|marcar|embarcar|trazer|autorizar|aprovar|viavel|viável)\b"
)
OPERATIONAL_DECISION_RE = re.compile(
    r"\b(piloto|manobra|navio|entrada|saida|saída|doca|lisnave|cais|porto)\b"
)


def normalize_planner_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


@dataclass(frozen=True)
class ChatExecutionPlan:
    question: str
    normalized_question: str
    primary_intent: str
    live_facets: tuple[str, ...] = ()
    weather_mode: str = "context"
    wants_documents: bool = False
    explicit_rule_codes: tuple[str, ...] = ()
    wants_operational_lookup: bool = False
    maneuver_lookup_type: str = ""
    requires_live_reasoning: bool = False
    requires_llm_synthesis: bool = False

    @property
    def has_live_facets(self) -> bool:
        return bool(self.live_facets)

    @property
    def should_answer_directly(self) -> bool:
        return self.primary_intent in {"live_environment", "operational_lookup"}

    def to_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "normalized_question": self.normalized_question,
            "primary_intent": self.primary_intent,
            "live_facets": list(self.live_facets),
            "weather_mode": self.weather_mode,
            "wants_documents": self.wants_documents,
            "explicit_rule_codes": list(self.explicit_rule_codes),
            "wants_operational_lookup": self.wants_operational_lookup,
            "maneuver_lookup_type": self.maneuver_lookup_type,
            "requires_live_reasoning": self.requires_live_reasoning,
            "requires_llm_synthesis": self.requires_llm_synthesis,
            "should_answer_directly": self.should_answer_directly,
        }


def build_chat_execution_plan(question: str) -> ChatExecutionPlan:
    raw_question = str(question or "").strip()
    clean_question = normalize_planner_text(raw_question)

    live_facets: list[str] = []
    if TIDE_QUERY_RE.search(clean_question):
        live_facets.append("tides")
    if WEATHER_QUERY_RE.search(clean_question):
        live_facets.append("weather")
    if WAVE_QUERY_RE.search(clean_question):
        live_facets.append("waves")
    if WARNING_QUERY_RE.search(clean_question):
        live_facets.append("warnings")

    weather_mode = "context"
    if "weather" in live_facets:
        has_timeline = bool(WEATHER_TIMELINE_RE.search(clean_question))
        has_current = bool(CURRENT_WEATHER_RE.search(clean_question))
        if has_timeline and has_current:
            weather_mode = "current_plus_timeline"
        elif has_timeline:
            weather_mode = "timeline"
        elif has_current:
            weather_mode = "current"

    maneuver_lookup_type = ""
    if re.search(r"\b(entrada|entry)\b", clean_question):
        maneuver_lookup_type = "entry"
    elif re.search(r"\b(saida|departure)\b", clean_question):
        maneuver_lookup_type = "departure"
    elif re.search(r"\b(mudanca|mudança|shift)\b", clean_question):
        maneuver_lookup_type = "shift"

    wants_operational_lookup = bool(
        re.search(r"\b(id|identificador)\b", clean_question) and "manobra" in clean_question
    )
    explicit_rule_codes = tuple(
        sorted({match.group(1).zfill(3) for match in RULE_CODE_RE.finditer(raw_question)})
    )
    wants_documents = bool(explicit_rule_codes) or bool(DOCUMENT_QUERY_RE.search(clean_question))
    requires_live_reasoning = bool(live_facets) and bool(
        LIVE_REASONING_RE.search(clean_question) and OPERATIONAL_DECISION_RE.search(clean_question)
    )
    requires_llm_synthesis = requires_live_reasoning or (bool(live_facets) and wants_documents)

    if wants_operational_lookup:
        primary_intent = "operational_lookup"
    elif requires_llm_synthesis:
        primary_intent = "mixed_live_and_documents" if wants_documents else "live_reasoning"
    elif live_facets:
        primary_intent = "live_environment"
    elif wants_documents:
        primary_intent = "documents"
    else:
        primary_intent = "general"

    return ChatExecutionPlan(
        question=raw_question,
        normalized_question=clean_question,
        primary_intent=primary_intent,
        live_facets=tuple(live_facets),
        weather_mode=weather_mode,
        wants_documents=wants_documents,
        explicit_rule_codes=explicit_rule_codes,
        wants_operational_lookup=wants_operational_lookup,
        maneuver_lookup_type=maneuver_lookup_type,
        requires_live_reasoning=requires_live_reasoning,
        requires_llm_synthesis=requires_llm_synthesis,
    )
