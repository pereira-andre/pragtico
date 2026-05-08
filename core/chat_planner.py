"""Heuristic chat planner used to decide which sources the bot should consult."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

TIDE_QUERY_RE = re.compile(r"\b(mare|mares|preia mar|preia-mar|baixa mar|baixa-mar)\b")
WEATHER_QUERY_RE = re.compile(
    r"\b(meteorologia|meteorologic|meteo|condicoes meteorologicas|condicoes do tempo|estado do tempo|"
    r"metrologia|metrologica|metrologicas|metrologico|metrologicos|"
    r"metereologia|metereologica|metereologicas|metereologico|metereologicos|"
    r"previsao meteorologica|previsoes meteorologicas|previsao do tempo|prognostico|"
    r"como esta o tempo|como vai estar o tempo|tempo no porto|tempo em setubal|tempo para setubal|"
    r"vento|visibilidade|"
    r"nevoeiro|nevoa|névoa|neblina|fog|mist|humidade|temperatura|chuva|"
    r"luz do dia|periodo luminoso|periodo de luz|periodos luminosos|nascer do sol|por do sol|"
    r"lua|fase da lua|fase lunar|moon|daylight)\b"
)
CURRENT_WEATHER_RE = re.compile(
    r"\b(atual|atuais|atualmente|actualmente|agora|neste momento|corrente|correntes|hoje|"
    r"como esta|como esta o tempo|estado do tempo)\b"
)
WEATHER_TIMELINE_RE = re.compile(
    r"\b(ao longo do dia|durante o dia|resto do dia|proximas horas|próximas horas|nas proximas horas|nas próximas horas|"
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
OPERATION_TIME_RE = re.compile(
    r"\b(?:as|às|para as|para às|para|pelas)\s*(?:\d{1,2}[:h]\d{2}|\d{3,4})\b"
    r"|\b\d{1,2}[:h]\d{2}\b"
    r"|\b(?:hoje|amanha|amanhã|depois de amanha|depois de amanhã)\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    flags=re.IGNORECASE,
)
OPERATION_SCHEDULING_RE = re.compile(
    r"\b(marc\w*|agend\w*|program\w*|plane\w*|manobra|entrada|saida|saída|sair|"
    r"atracar|desatracar|atracacao|atracação|desatracacao|desatracação)\b"
)
DOCUMENT_QUERY_RE = re.compile(
    r"\b(regra|regras|documento|doc|instrucao|instrução|norma|normas|procedimento|procedimentos|"
    r"o que diz|segundo o|segundo a|it[\s\-_]?\d{1,3})\b"
)
TUG_TERMS_RE = re.compile(r"\b(reboque|reboques|rebocador|rebocadores)\b")
TUG_RECOMMENDATION_RE = re.compile(
    r"\b(quantos|quantas|numero|número|qtd|necessari\w*|precis\w*|"
    r"recomend\w*|aconselh\w*|suger\w*|indic\w*)\b"
    r".{0,90}\b(reboque|reboques|rebocador|rebocadores)\b"
    r"|"
    r"\b(reboque|reboques|rebocador|rebocadores)\b"
    r".{0,90}\b(quantos|quantas|numero|número|qtd|necessari\w*|precis\w*|"
    r"recomend\w*|aconselh\w*|suger\w*|indic\w*)\b"
)
TUG_OPERATION_CONTEXT_RE = re.compile(
    r"\b(navio|navios|atracar|desatracar|entrada|saida|saída|manobra|roro|ro ro|ro-ro|"
    r"loa|comprimento|calado|thruster|bow|stern)\b"
)
FOLLOW_UP_REFERENCE_RE = re.compile(
    r"\b(os dois|ambos|essas|esses|isso|isto|nisso|nisto|com base nisso|com base nisto|"
    r"nesse caso|neste caso|a partir disso|a partir disto|o mesmo|a mesma|"
    r"como disseste|como referiste|com base no que disseste)\b"
)
PORT_FACILITY_INVENTORY_RE = re.compile(
    r"\b(quais|quantos|quantas|existem|lista|listar|enumera|inventario|inventário)\b"
    r".*\b(cais|berco|bercos|berço|berços|doca|docas|terminal|terminais|instalacao|instalação|instalacoes|instalações)\b"
    r"|"
    r"\b(cais|berco|bercos|berço|berços|doca|docas|terminal|terminais|instalacao|instalação|instalacoes|instalações)\b"
    r".*\b(quais|quantos|quantas|existem|lista|listar|enumera|inventario|inventário)\b"
)
PORT_SCOPE_RE = re.compile(r"\b(porto|setubal|setúbal)\b")
FACILITY_SCOPE_RE = re.compile(
    r"\b(lisnave|mitrena|secil|sapec|tanquisado|eco\s*oil|ecooil|ecoil|praias|"
    r"alstom|autoeuropa|auto\s*europa|tms\s*1|tms1|tms\s*2|tms2|teporset|"
    r"tepor\s*set|termitrena|terminal|terminais|cais|doca|docas|fundeadouro|fundeadouros)\b"
)
FACILITY_TECHNICAL_RE = re.compile(
    r"\b(comprimento|loa|calado|sonda|profundidade|altura|limite|limites|"
    r"maximo|maxima|maximos|maximas|minimo|minima|noite|noturn[oa]|manobr\w*|"
    r"atracar|desatracar|entrar|sair|reponto|reboque|reboques|rebocador|"
    r"rebocadores|restric\w*|regra|regras|permitid\w*|pode|posso)\b"
)
RULE_CODE_RE = re.compile(r"\bit[\s\-_]?0*(\d{1,3})\b", flags=re.IGNORECASE)
LIVE_REASONING_RE = re.compile(
    r"\b(avali\w*|consider\w*|recomend\w*|aconselh\w*|suger\w*|indic\w*|"
    r"devo|deve\w*|devia\w*|deveri\w*|pod\w*|posso|podes|podia\w*|poderi\w*|"
    r"precis\w*|necessari\w*|convem|marc\w*|embarc\w*|traz\w*|autor\w*|aprov\w*|"
    r"permit\w*|viavel|aceitavel|suficient\w*|bast\w*|cheg\w*|ach\w*|"
    r"corret\w*|correct\w*|cert\w*|segur\w*|risco\w*|arrisc\w*|condicion\w*|limit\w*|imped\w*|"
    r"suspens\w*|suspend\w*|cancel\w*|abort\w*|adi\w*)\b"
    r"|"
    r"\b(da para|faz sentido|vale a pena|e melhor|melhor opcao|melhor opção)\b"
)
OPERATIONAL_DECISION_RE = re.compile(
    r"\b(piloto|manobra|manobras|navio|navios|entrada|saida|saída|sair|atracar|desatracar|doca|lisnave|cais|"
    r"barra|fundeadouro|fundeadouros|reboque|reboques|rebocador|rebocadores|"
    r"thruster|calado|loa|roro|ro ro|ro-ro)\b"
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
    needs_history_state: bool = False
    needs_answer_critic: bool = False

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
            "needs_history_state": self.needs_history_state,
            "needs_answer_critic": self.needs_answer_critic,
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
    if (
        "tides" not in live_facets
        and OPERATION_TIME_RE.search(raw_question)
        and OPERATION_SCHEDULING_RE.search(clean_question)
    ):
        live_facets.append("tides")

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
    wants_port_facility_inventory = bool(PORT_SCOPE_RE.search(clean_question)) and bool(
        PORT_FACILITY_INVENTORY_RE.search(clean_question)
    )
    wants_tug_recommendation = bool(TUG_RECOMMENDATION_RE.search(clean_question)) and bool(
        TUG_OPERATION_CONTEXT_RE.search(clean_question) or live_facets
    )
    wants_tug_rules = wants_tug_recommendation or bool(
        TUG_TERMS_RE.search(clean_question) and DOCUMENT_QUERY_RE.search(clean_question)
    )
    wants_facility_technical_synthesis = bool(
        FACILITY_SCOPE_RE.search(clean_question) and FACILITY_TECHNICAL_RE.search(clean_question)
    )
    wants_documents = (
        bool(explicit_rule_codes)
        or bool(DOCUMENT_QUERY_RE.search(clean_question))
        or wants_port_facility_inventory
        or wants_tug_rules
        or wants_facility_technical_synthesis
    )
    asks_operational_decision = (
        bool(LIVE_REASONING_RE.search(clean_question) and OPERATIONAL_DECISION_RE.search(clean_question))
        or wants_tug_recommendation
    )
    requires_live_reasoning = bool(live_facets) and asks_operational_decision
    requires_llm_synthesis = (
        requires_live_reasoning
        or wants_tug_recommendation
        or wants_facility_technical_synthesis
        or (bool(live_facets) and wants_documents)
        or wants_port_facility_inventory
    )
    has_follow_up_reference = bool(FOLLOW_UP_REFERENCE_RE.search(clean_question))
    needs_history_state = requires_live_reasoning or has_follow_up_reference or wants_tug_recommendation
    needs_answer_critic = (
        requires_live_reasoning
        or wants_tug_recommendation
        or (has_follow_up_reference and asks_operational_decision)
    )

    if wants_operational_lookup:
        primary_intent = "operational_lookup"
    elif requires_llm_synthesis:
        if requires_live_reasoning:
            primary_intent = "live_reasoning"
        elif live_facets and wants_documents:
            primary_intent = "mixed_live_and_documents"
        else:
            primary_intent = "document_synthesis"
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
        needs_history_state=needs_history_state,
        needs_answer_critic=needs_answer_critic,
    )
