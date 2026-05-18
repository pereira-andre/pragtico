"""Compiled patterns used by deterministic operational chat sources."""

import re


PORTAL_ACTIVITY_CONTEXT_RE = re.compile(
    r"\b(navio|navios|escala|escalas|planead\w*|previst\w*|programad\w*|"
    r"marcad\w*|arquivo|historico|histórico|eta|etd|recent\w*|ultim\w*|"
    r"em porto|em cais|quadro|ocupad\w*|ocupac\w*|agent\w*|piloto|pilotos)\b"
)
PORTAL_MOVEMENT_CONTEXT_RE = re.compile(
    r"\b(chegada|chegadas|entrada|entradas|saida|saída|saidas|saídas|partida|partidas)\b"
    r".*\b(navio|navios|escala|escalas|previst\w*|planead\w*|recent\w*|ultim\w*|hoje|amanha|amanhã|eta|etd)\b"
    r"|"
    r"\b(navio|navios|escala|escalas|previst\w*|planead\w*|recent\w*|ultim\w*|hoje|amanha|amanhã|eta|etd)\b"
    r".*\b(chegada|chegadas|entrada|entradas|saida|saída|saidas|saídas|partida|partidas)\b"
)
PORTAL_MANEUVER_CONTEXT_RE = re.compile(
    r"\bmanobras?\b.*\b(planead\w*|previst\w*|programad\w*|marcad\w*|"
    r"arquivo|historico|histórico|hoje|amanha|amanhã|ontem)\b"
    r"|"
    r"\b(planead\w*|previst\w*|programad\w*|marcad\w*|arquivo|historico|histórico|"
    r"hoje|amanha|amanhã|ontem)\b.*\bmanobras?\b"
)
DAYLIGHT_QUERY_RE = re.compile(
    r"\b(luz do dia|periodo luminoso|periodos luminosos|periodo de luz|periodos de luz|"
    r"nascer do sol|por do sol|poe se o sol|pôr do sol|daylight)\b"
)
MOON_QUERY_RE = re.compile(r"\b(lua|fase da lua|fase lunar|moon)\b")
WEATHER_FORECAST_TODAY_RE = re.compile(
    r"\b(previsao|previsoes|previsao meteorologica|previsoes meteorologicas|metrologia|metrologica|metereologia|metereologica|prognostico|"
    r"como vai estar|vai estar|meteo)\b.*\b(hoje|resto do dia|proximas horas|próximas horas)\b"
    r"|"
    r"\b(hoje|resto do dia|proximas horas|próximas horas)\b.*\b(previsao|previsoes|prognostico|meteorologia|metrologia|metereologia|meteo|tempo)\b"
)
WEATHER_FORECAST_DAYS_RE = re.compile(
    r"\b(proximos dias|próximos dias|dias seguintes|amanha|amanhã|depois de amanha|depois de amanhã|"
    r"previsao geral|previsões gerais|previsoes gerais)\b"
)
TUG_LIVE_WEATHER_RE = re.compile(
    r"\b(meteorolog\w*|metereolog\w*|metrolog\w*|meteo|condicoes meteorologicas|"
    r"condicoes do tempo|estado do tempo|tempo|atual|atuais|actual|actuais|"
    r"agora|neste momento|previst\w*|previs\w*|proximas horas|próximas horas)\b"
)
LOCAL_WARNING_CODE_RE = re.compile(r"\b(?:anav\s*)?(?:n[.ºo]*\s*)?(\d{1,3}/\d{2,4})\b", re.IGNORECASE)
BERTHED_VESSELS_QUERY_RE = re.compile(
    r"\b(navios?|embarcacoes|embarcações)\b.*\b(em cais|atracad\w*|amarrad\w*)\b"
    r"|"
    r"\b(em cais|atracad\w*|amarrad\w*)\b.*\b(navios?|embarcacoes|embarcações)\b"
)
PLANNED_MANEUVER_SUBJECT_RE = re.compile(
    r"\b(navios?|manobras?|entradas?|saidas?|saídas|partidas?|mudancas?|mudanças)\b"
)
PLANNED_MANEUVER_MARKER_RE = re.compile(
    r"\b(planeamento|planead\w*|previst\w*|programad\w*|agendad\w*|marcad\w*|"
    r"agenda|futur\w*|proxim\w*)\b"
)
VESSEL_DETAIL_QUERY_RE = re.compile(
    r"\b(dados|detalhes|informacao|informação|caracteristicas|características|ficha|perfil)\b"
    r".*\b(navio|embarcacao|embarcação|imo|indicativo|call sign)\b"
    r"|"
    r"\b(navio|embarcacao|embarcação|imo|indicativo|call sign)\b"
    r".*\b(dados|detalhes|informacao|informação|caracteristicas|características|ficha|perfil)\b"
)
OPERATIONAL_FRAGMENT_TERMS_RE = re.compile(
    r"\b(navio|embarcacao|embarcação|reboques?|rebocadores?|fundear|fundeadouro|ferro|"
    r"entrada|saida|saída|atracar|desatracar|manobra)\b",
    re.IGNORECASE,
)
OPERATIONAL_DECISION_TERMS_RE = re.compile(
    r"\b(quantos|quantas|onde|como|quando|qual|quais|pode|posso|devo|deve|"
    r"aconselha|aconselhas|recomenda|recomendas|observa|observacao|observação|"
    r"precisa|necess[aá]rio|suficiente|meter|colocar|posicionar|o que)\b",
    re.IGNORECASE,
)
MANEUVER_APPROVER_QUERY_RE = re.compile(
    r"\b(quem|qual)\b.*\b(aprovou|aprovado|aprovada|validou|validado|validada|validador)\b.*\b(manobra|entrada|saida|saída|mudanca|mudança)\b"
    r"|"
    r"\b(aprovou|validou)\b.*\b(manobra|entrada|saida|saída|mudanca|mudança)\b"
)
AGENT_AGENCY_QUERY_RE = re.compile(
    r"\b(agencia|agência)\b.*\b(agent\w*|trabalha|pertence|qual|que)\b"
    r"|"
    r"\b(agent\w*|trabalha|pertence)\b.*\b(agencia|agência)\b"
)
AGENT_LOOKUP_QUERY_RE = re.compile(r"\b(qual|quem)\b.*\bagente\b|\bagente\b.*\b(navio|escala|manobra)\b")
MANEUVER_TIME_RE = re.compile(
    r"\b(?:as|às|para as|para às|para|pelas)\s*(\d{1,2}(?::\d{2}|h\d{0,2})|\d{3,4})\b"
    r"|\b(\d{1,2}(?::\d{2}|h\d{2}))\b",
    flags=re.IGNORECASE,
)
SOURCE_COVERAGE_QUERY_RE = re.compile(
    r"\b(fonte|fontes|documento|base|cobre|cobrem|inclui|incluem|conhecimento|indexavel|indexável|incorporad\w*)\b",
    re.IGNORECASE,
)

PT_MONTH_QUERY = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}
