from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import heapq
import re
import unicodedata
from zoneinfo import ZoneInfo


def _normalize(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


TIME_QUERY_RE = re.compile(
    r"\b(quanto tempo|tempo|demora|demoram|leva|levo|levam|transito|viagem|percurso|eta|chegada)\b"
)
DISTANCE_QUERY_RE = re.compile(r"\b(distancia|distancias|milha|milhas|mn|nm|quanto falta|falta|faltam)\b")
ROUTE_DETAIL_QUERY_RE = re.compile(r"\b(rumo|rumos|proa|pernada|pernadas|wpt|waypoint|waypoints)\b")
ROUTE_LINK_RE = re.compile(r"\b(ate|a|ao|aos|para|desde|de|do|da)\b")
ROUTE_ORDER_QUERY_RE = re.compile(
    r"\b(ordem|sequencia|sequência)\b.*\b(cais|terminais|canal|canais)\b"
    r"|\b(cais|terminais|canal|canais)\b.*\b(ordem|sequencia|sequência)\b",
    flags=re.IGNORECASE,
)
SPEED_RE = re.compile(r"\b(\d{1,2}(?:[,.]\d+)?)\s*(?:kt|kts|nos|n[oó]s)\b")
START_TIME_RE = re.compile(
    r"\b(?:as|às|pelas|sair|saio|saida|saída|largada|partida)"
    r"(?:\s+(?:as|às|pelas))?\s+(\d{1,2})(?:\s*:?\s*(\d{2}))\b"
)

ORIGIN_BARRA = (
    r"\bentrada da barra\b",
    r"\bfora da barra\b",
    r"\bbarra\b",
    r"\bpilar\s*2\b",
    r"\bpilar\s*n\s*2\b",
    r"\bboia\s*2\b",
)
ORIGIN_FUNDEADOURO_NORTE = (
    r"\bfundeadouro\s+norte\b",
    r"\bfund\s+norte\b",
)
ORIGIN_FUNDEADOURO_SUL = (
    r"\bfundeadouro\s+sul\b",
    r"\bfundeadouro\s+de\s+troia\b",
    r"\bfundeadouro\s+troia\b",
    r"\bfund\s+troia\b",
    r"\bf\s*s\b",
    r"\btroia\b",
)
ORIGIN_CANAL_SUL = (r"\bcanal\s+sul\b",)
ORIGIN_TMS1 = (
    r"\btms\s*1\b",
    r"\btms1\b",
    r"\bterminal\s+multiusos\s+1\b",
    r"\bterminal\s+multiusos\s+zona\s+1\b",
    r"\bcais\s+das\s+fontainhas\b",
    r"\bfontainhas\b",
)

DEST_LISNAVE = (
    r"\blisnave\b",
    r"\bmitrena\b",
    r"\bestaleiro\b",
    r"\bestaleiros\b",
)
DEST_SOUTH_QUAYS = (
    r"\bcais\s+(?:a\s+)?sul\b",
    r"\bcais\s+do\s+sul\b",
    r"\btanquisado\b",
    r"\beco\s*oil\b",
    r"\becooil\b",
    r"\becoil\b",
    r"\blisnave\b",
    r"\bmitrena\b",
    r"\btermitrena\b",
    r"\bteporset\b",
    r"\btepor\s*set\b",
)
DEST_AUTOEUROPA = (
    r"\bauto\s*europa\b",
    r"\bautoeuropa\b",
    r"\bro\s*ro\b",
    r"\broro\b",
    r"\bcais\s*10\b",
    r"\bcais\s*11\b",
)
DEST_TMS2 = (
    r"\btms\s*2\b",
    r"\btms2\b",
    r"\bterminal\s+multiusos\s+2\b",
    r"\bterminal\s+multiusos\s+zona\s+2\b",
    r"\bterminal\s+de\s+contentores\b",
)
DEST_SAPEC = (
    r"\bsapec\b",
    r"\bsapec\s+solidos\b",
    r"\bsapec\s+liquidos\b",
)
DEST_PRAIAS = (
    r"\bpraias\s+do\s+sado\b",
    r"\bpraias\b",
    r"\bpirites\s+alentejanas\b",
)
DEST_JOAO_FARTO = (
    r"\bboia\s+joao\s+farto\b",
    r"\bjoao\s+farto\b",
)
DEST_OUTAO = (
    r"\boutao\b",
    r"\bsecil\s+outao\b",
)
DEST_FORA_BARRA = (
    r"\bfora\s+da\s+barra\b",
    r"\bentrada\s+da\s+barra\b",
    r"\bpilar\s*2\b",
    r"\bpilar\s*n\s*2\b",
    r"\bboia\s*2\b",
)
DEST_NORTH_QUAYS = (
    r"\bcais\s+(?:a\s+)?norte\b",
    r"\bcais\s+do\s+norte\b",
    r"\btms\s*1\b",
    r"\btms1\b",
    r"\btms\s*2\b",
    r"\btms2\b",
    r"\bauto\s*europa\b",
    r"\bautoeuropa\b",
    r"\bro\s*ro\b",
    r"\broro\b",
    r"\bcais\s*10\b",
    r"\bcais\s*11\b",
    r"\bpraias\s+do\s+sado\b",
    r"\bpraias\b",
    r"\bsapec\b",
)
DEST_ALSTOM = (
    r"\balstom\b",
    r"\babb\s*alstom\b",
    r"\babb\s*-?\s*alstom\b",
)
DEST_SECIL = (r"\bsecil\b",)
DEST_FUNDEADOURO_NORTE = ORIGIN_FUNDEADOURO_NORTE
DEST_FUNDEADOURO_SUL = ORIGIN_FUNDEADOURO_SUL


@dataclass(frozen=True)
class RouteTransitFact:
    metric: str
    origin_patterns: tuple[str, ...]
    destination_patterns: tuple[str, ...]
    answer: str
    source_document: str
    source_id: str
    specificity: int = 10
    reverse_answer: str = ""


@dataclass(frozen=True)
class RouteLeg:
    origin: str
    destination: str
    inbound_heading: int
    distance_nm: float

    @property
    def outbound_heading(self) -> int:
        return (self.inbound_heading + 180) % 360


@dataclass(frozen=True)
class RoutePlan:
    route_id: str
    label: str
    legs: tuple[RouteLeg, ...]


WAYPOINT_LABELS: dict[str, str] = {
    "pilot_station": "Pilot station / posição de embarque",
    "pilar_2": "Pilar 2 / entrada da Barra",
    "outao": "Outão",
    "joao_farto": "Bóia João Farto",
    "boia_1cc": "Bóia 1CC",
    "boia_3cc": "Bóia 3CC",
    "boia_5cc": "Bóia 5CC",
    "tms1": "TMS 1",
    "tms2": "TMS 2",
    "auto_europa": "Autoeuropa",
    "praias_sado": "Praias do Sado",
    "sapec": "SAPEC",
    "alstom": "Cais ALSTOM",
    "boia_4cs": "Bóia 4CS",
    "boia_6cs": "Bóia 6CS",
    "boia_12cs": "Bóia 12CS",
    "boia_14cs": "Bóia 14CS / fim do Canal Sul",
    "tanquisado_ecooil": "Tanquisado / Eco-Oil",
    "lisnave": "LISNAVE / docas / Hidrolift",
    "teporset": "Teporset",
}

WAYPOINT_PATTERNS: dict[str, tuple[str, ...]] = {
    "pilot_station": (
        r"\bpilot\s+station\b",
        r"\bposicao\s+de\s+embarque\b",
        r"\bposicao\s+embarque\b",
        r"\b1\s*nm\s+fora\s+da\s+barra\b",
        r"\buma\s+milha\s+fora\s+da\s+barra\b",
    ),
    "pilar_2": (
        r"\bpilar\s*2\b",
        r"\bpilar\s*n\s*2\b",
        r"\bentrada\s+da\s+barra\b",
        r"\bbarra\b",
        r"\bentrada\b",
    ),
    "outao": (r"\boutao\b",),
    "joao_farto": (
        r"\bboia\s+joao\s+farto\b",
        r"\bjoao\s+farto\b",
    ),
    "boia_1cc": (r"\bboia\s*1\s*cc\b", r"\b1\s*cc\b"),
    "boia_3cc": (r"\bboia\s*3\s*cc\b", r"\b3\s*cc\b"),
    "boia_5cc": (r"\bboia\s*5\s*cc\b", r"\b5\s*cc\b"),
    "tms1": (
        r"\btms\s*1\b",
        r"\btms1\b",
        r"\bterminal\s+multiusos\s+1\b",
        r"\bterminal\s+multiusos\s+zona\s+1\b",
    ),
    "tms2": (
        r"\btms\s*2\b",
        r"\btms2\b",
        r"\bterminal\s+multiusos\s+2\b",
        r"\bterminal\s+multiusos\s+zona\s+2\b",
        r"\bterminal\s+de\s+contentores\b",
    ),
    "auto_europa": (r"\bauto\s*europa\b", r"\bautoeuropa\b", r"\bcais\s*10\b", r"\bcais\s*11\b"),
    "praias_sado": (r"\bpraias\s+do\s+sado\b", r"\bpraias\b"),
    "sapec": (r"\bsapec\b",),
    "alstom": (r"\balstom\b", r"\babb\s*-?\s*alstom\b", r"\bfim\s+do\s+canal\s+norte\b"),
    "boia_4cs": (r"\bboia\s*4\s*cs\b", r"\b4\s*cs\b"),
    "boia_6cs": (r"\bboia\s*6\s*cs\b", r"\b6\s*cs\b"),
    "boia_12cs": (r"\bboia\s*12\s*cs\b", r"\b12\s*cs\b"),
    "boia_14cs": (
        r"\bboia\s*14\s*cs\b",
        r"\b14\s*cs\b",
        r"\bfim\s+do\s+canal\s+sul\b",
    ),
    "tanquisado_ecooil": (
        r"\btanquisado\b",
        r"\beco\s*oil\b",
        r"\becooil\b",
        r"\becoil\b",
    ),
    "lisnave": (
        r"\blisnave\b",
        r"\bmitrena\b",
        r"\bdocas\b",
        r"\bhidrolift\b",
    ),
    "teporset": (r"\bteporset\b", r"\btepor\s*set\b"),
}


NORTH_CHANNEL_LEGS: tuple[RouteLeg, ...] = (
    RouteLeg("pilot_station", "pilar_2", 40, 1.0),
    RouteLeg("pilar_2", "outao", 40, 2.8),
    RouteLeg("outao", "joao_farto", 40, 1.5),
    RouteLeg("joao_farto", "boia_1cc", 40, 0.6),
    RouteLeg("boia_1cc", "boia_3cc", 74, 0.7),
    RouteLeg("boia_3cc", "tms1", 105, 0.3),
    RouteLeg("tms1", "boia_5cc", 105, 0.2),
    RouteLeg("boia_5cc", "tms2", 120, 0.4),
    RouteLeg("tms2", "auto_europa", 120, 0.4),
    RouteLeg("auto_europa", "praias_sado", 115, 0.7),
    RouteLeg("praias_sado", "sapec", 130, 0.6),
    RouteLeg("sapec", "alstom", 120, 1.1),
)

SOUTH_CHANNEL_COMMON_LEGS: tuple[RouteLeg, ...] = (
    RouteLeg("pilot_station", "pilar_2", 40, 1.0),
    RouteLeg("pilar_2", "outao", 40, 2.8),
    RouteLeg("outao", "joao_farto", 55, 1.4),
    RouteLeg("joao_farto", "boia_4cs", 110, 1.0),
    RouteLeg("boia_4cs", "boia_6cs", 125, 2.3),
    RouteLeg("boia_6cs", "boia_12cs", 110, 2.0),
    RouteLeg("boia_12cs", "boia_14cs", 65, 0.5),
)

SETUBAL_ROUTE_PLANS: tuple[RoutePlan, ...] = (
    RoutePlan("canal_norte", "Canal Norte", NORTH_CHANNEL_LEGS),
    RoutePlan(
        "canal_sul_tanquisado_ecooil",
        "Canal Sul para Tanquisado / Eco-Oil",
        SOUTH_CHANNEL_COMMON_LEGS + (RouteLeg("boia_14cs", "tanquisado_ecooil", 311, 0.6),),
    ),
    RoutePlan(
        "canal_sul_lisnave",
        "Canal Sul para LISNAVE / docas / Hidrolift",
        SOUTH_CHANNEL_COMMON_LEGS + (RouteLeg("boia_14cs", "lisnave", 30, 0.5),),
    ),
    RoutePlan(
        "canal_sul_teporset",
        "Canal Sul para Teporset",
        SOUTH_CHANNEL_COMMON_LEGS + (RouteLeg("boia_14cs", "teporset", 60, 1.0),),
    ),
)


def _segment_distance_answer(origin: str, destination: str, distance: str) -> str:
    unit = "milha náutica" if distance == "1,0" else "milhas náuticas"
    return (
        f"Do {origin} até {destination} são {distance} {unit}. "
        "É uma distância de referência por segmento e pode ser somada a outros segmentos "
        "quando o percurso operacional fizer sentido."
    )


def _segment_distance_reverse(origin: str, reverse_origin_phrase: str, distance: str) -> str:
    unit = "milha náutica" if distance == "1,0" else "milhas náuticas"
    return (
        f"{reverse_origin_phrase} até ao {origin} são {distance} {unit}. "
        "É uma distância de referência por segmento e pode ser somada a outros segmentos "
        "quando o percurso operacional fizer sentido."
    )


def _decimal_comma(value: float, *, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _heading_label(value: int) -> str:
    return f"{value % 360:03d}°"


def _distance_unit(value: float) -> str:
    return "milha náutica" if abs(value - 1.0) < 0.05 else "milhas náuticas"


def _format_minutes(total_minutes: int) -> str:
    minutes = max(int(round(total_minutes)), 0)
    if minutes < 60:
        return f"{minutes} min"
    hours, remainder = divmod(minutes, 60)
    if remainder == 0:
        return f"{hours} h"
    return f"{hours} h {remainder:02d} min"


def _format_duration_words(total_minutes: int) -> str:
    minutes = max(int(round(total_minutes)), 0)
    if minutes < 60:
        return f"{minutes} minutos"
    hours, remainder = divmod(minutes, 60)
    hour_label = "1 hora" if hours == 1 else f"{hours} horas"
    if remainder == 0:
        return hour_label
    minute_label = "1 minuto" if remainder == 1 else f"{remainder} minutos"
    return f"{hour_label} e {minute_label}"


def _route_preference(clean_question: str) -> str:
    if re.search(r"\bcanal\s+norte\b", clean_question):
        return "canal_norte"
    if re.search(r"\bcanal\s+sul\b", clean_question):
        return "canal_sul"
    return ""


def _matched_waypoints(clean_question: str) -> list[tuple[str, int, int]]:
    matches: list[tuple[str, int, int]] = []
    for waypoint_id, patterns in WAYPOINT_PATTERNS.items():
        waypoint_matches = [
            match
            for pattern in patterns
            for match in [re.search(pattern, clean_question)]
            if match
        ]
        if not waypoint_matches:
            continue
        best = sorted(waypoint_matches, key=lambda item: (item.start(), -(item.end() - item.start())))[0]
        matches.append((waypoint_id, best.start(), best.end()))
    return sorted(matches, key=lambda item: (item[1], -(item[2] - item[1])))


def _route_points(route: RoutePlan) -> list[str]:
    if not route.legs:
        return []
    points = [route.legs[0].origin]
    points.extend(leg.destination for leg in route.legs)
    return points


def _reverse_leg(leg: RouteLeg) -> RouteLeg:
    return RouteLeg(
        origin=leg.destination,
        destination=leg.origin,
        inbound_heading=leg.outbound_heading,
        distance_nm=leg.distance_nm,
    )


def _route_candidate_legs(route: RoutePlan, origin: str, destination: str) -> tuple[RouteLeg, ...]:
    points = _route_points(route)
    if origin not in points or destination not in points or origin == destination:
        return ()
    origin_index = points.index(origin)
    destination_index = points.index(destination)
    if origin_index < destination_index:
        return route.legs[origin_index:destination_index]
    return tuple(_reverse_leg(leg) for leg in reversed(route.legs[destination_index:origin_index]))


def _best_route_plan(
    clean_question: str,
    origin: str,
    destination: str,
) -> tuple[RoutePlan, tuple[RouteLeg, ...]] | None:
    preference = _route_preference(clean_question)
    candidates: list[tuple[int, float, int, RoutePlan, tuple[RouteLeg, ...]]] = []
    for index, route in enumerate(SETUBAL_ROUTE_PLANS):
        legs = _route_candidate_legs(route, origin, destination)
        if not legs:
            continue
        distance = sum(leg.distance_nm for leg in legs)
        preference_score = 0
        if preference == route.route_id or (preference == "canal_sul" and route.route_id.startswith("canal_sul")):
            preference_score = 10
        candidates.append((preference_score, -distance, -index, route, legs))
    if not candidates:
        return _best_graph_route_plan(origin, destination)
    _, _, _, route, legs = sorted(candidates, key=lambda item: (item[0], item[1], item[2]), reverse=True)[0]
    return route, legs


def _best_graph_route_plan(origin: str, destination: str) -> tuple[RoutePlan, tuple[RouteLeg, ...]] | None:
    graph: dict[str, list[RouteLeg]] = {}
    for route in SETUBAL_ROUTE_PLANS:
        for leg in route.legs:
            graph.setdefault(leg.origin, []).append(leg)
            reverse = _reverse_leg(leg)
            graph.setdefault(reverse.origin, []).append(reverse)
    if origin not in graph or destination not in graph:
        return None

    queue: list[tuple[float, int, str, tuple[RouteLeg, ...]]] = [(0.0, 0, origin, ())]
    best_distance: dict[str, float] = {origin: 0.0}
    counter = 0
    while queue:
        distance, _order, node, path = heapq.heappop(queue)
        if node == destination:
            route = RoutePlan(
                "canal_sul_norte_via_joao_farto",
                "Canal Sul / Canal Norte via Bóia João Farto",
                path,
            )
            return route, path
        if distance > best_distance.get(node, float("inf")) + 1e-9:
            continue
        for leg in graph.get(node, []):
            new_distance = distance + leg.distance_nm
            if new_distance + 1e-9 >= best_distance.get(leg.destination, float("inf")):
                continue
            best_distance[leg.destination] = new_distance
            counter += 1
            heapq.heappush(queue, (new_distance, counter, leg.destination, path + (leg,)))
    return None


def _extract_route_points(clean_question: str) -> tuple[str, str] | None:
    matched = _matched_waypoints(clean_question)
    distinct: list[str] = []
    for waypoint_id, _, _ in matched:
        if waypoint_id not in distinct:
            distinct.append(waypoint_id)
    if len(distinct) < 2:
        return None
    return distinct[0], distinct[1]


def _extract_speed_knots(clean_question: str) -> float | None:
    speed_matches = list(SPEED_RE.finditer(clean_question))
    if not speed_matches:
        return None
    for match in speed_matches:
        before = clean_question[max(0, match.start() - 16):match.start()]
        if "vento" in before:
            continue
        value = float(match.group(1).replace(",", "."))
        if value > 0:
            return value
    return None


def _extract_start_time(clean_question: str) -> datetime | None:
    local_tz = ZoneInfo("Europe/Lisbon")
    now = datetime.now(local_tz)
    match = START_TIME_RE.search(clean_question)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if re.search(r"\bagora\b", clean_question):
        return now.replace(second=0, microsecond=0)
    return None


def _eta_line(clean_question: str, distance_nm: float) -> str:
    speed = _extract_speed_knots(clean_question)
    if not speed:
        return ""
    minutes = int(round((distance_nm / speed) * 60))
    text = f"A {_decimal_comma(speed)} kt, duração estimada: {_format_minutes(minutes)}."
    start_time = _extract_start_time(clean_question)
    if start_time:
        eta = start_time + timedelta(minutes=minutes)
        text += f" ETA ao destino: {eta.strftime('%H:%M')}."
    return text


def _format_route_plan_answer(
    question: str,
    clean_question: str,
    route: RoutePlan,
    legs: tuple[RouteLeg, ...],
) -> dict:
    origin_label = WAYPOINT_LABELS.get(legs[0].origin, legs[0].origin)
    destination_label = WAYPOINT_LABELS.get(legs[-1].destination, legs[-1].destination)
    total_distance = sum(leg.distance_nm for leg in legs)
    distance_label = _decimal_comma(total_distance)
    unit = _distance_unit(total_distance)
    leg_lines = [
        (
            f"- {WAYPOINT_LABELS.get(leg.origin, leg.origin)} -> "
            f"{WAYPOINT_LABELS.get(leg.destination, leg.destination)}: "
            f"rumo {_heading_label(leg.inbound_heading)}, "
            f"{_decimal_comma(leg.distance_nm)} NM"
        )
        for leg in legs
    ]
    eta_text = _eta_line(clean_question, total_distance)
    answer_parts = [
        (
            f"De {origin_label} até {destination_label}, pelo {route.label}, "
            f"faltam {distance_label} {unit}."
        ),
        "Pernadas:",
        *leg_lines,
    ]
    aliases = []
    if re.search(r"\btms\s*1\b|\btms1\b", clean_question):
        aliases.append("TMS1 / TMS 1")
    if re.search(r"\btms\s*2\b|\btms2\b", clean_question):
        aliases.append("TMS2 / TMS 2")
    if aliases:
        answer_parts.insert(1, "Alias operacional: " + "; ".join(aliases) + ".")
    if eta_text:
        answer_parts.append(eta_text)
    answer = "\n".join(answer_parts)
    return {
        "answer": answer,
        "sources": [
            {
                "document": "setubal_route_planning.json",
                "source_id": f"SETUBAL_ROUTE_PLAN_{route.route_id.upper()}",
                "retrieval_mode": "route_planning_graph",
                "snippet": answer,
                "question": question,
            }
        ],
        "answer_origin": "operational_route_transit",
    }


def _route_order_answer(question: str, clean_question: str) -> dict | None:
    if not ROUTE_ORDER_QUERY_RE.search(clean_question):
        return None
    if not re.search(r"\b(canal\s+norte|canal\s+sul|canais|cais)\b", clean_question):
        return None

    answer = (
        "Ordem prática dos cais/terminais por canal:\n"
        "- Entrada pelo Canal Norte: TMS 1 -> TMS 2 -> Autoeuropa/Ro-Ro (Cais 10/11) -> "
        "Praias do Sado -> SAPEC -> ALSTOM.\n"
        "- Saída pelo Canal Norte: inverter a sequência do terminal de origem até à Barra.\n"
        "- Entrada pelo Canal Sul: a partir da Bóia 14CS/fim do Canal Sul, confirmar o ramal final: "
        "Tanquisado/Eco-Oil, LISNAVE/Mitrena, Termitrena ou Teporset.\n"
        "- Saída pelo Canal Sul: inverter o ramal final até à Bóia 14CS e depois seguir para João Farto, Outão e Barra.\n"
        "A SECIL/Outão fica antes da divisão operacional dos canais principais; trata-a como destino próprio."
    )
    return {
        "answer": answer,
        "sources": [
            {
                "document": "Notas_Pilotagem.txt",
                "source_id": "ROUTE_ORDER_NORTH_SOUTH",
                "retrieval_mode": "route_order_fact",
                "snippet": answer,
                "question": question,
            }
        ],
        "answer_origin": "operational_route_transit",
    }


def _route_summary_answer(question: str, clean_question: str) -> dict | None:
    if _query_metric(clean_question) != "time":
        return None

    has_north_quays = bool(re.search(r"\bcais\s+(?:a\s+)?norte\b|\bcais\s+do\s+norte\b", clean_question))
    has_south_quays = bool(re.search(r"\bcais\s+(?:a\s+)?sul\b|\bcais\s+do\s+sul\b", clean_question))
    has_secil = "secil" in clean_question
    if _matches_any(clean_question, ORIGIN_FUNDEADOURO_NORTE) and has_north_quays and has_south_quays and has_secil:
        answer = (
            "Do Fundeadouro Norte para os cais a norte, isto é, de TMS 1 até SAPEC Líquidos, "
            "conta com 15 a 25 minutos a navegar, até SAPEC. Para a SECIL, cerca de 20 minutos. "
            "Para os cais a sul, isto é, Tanquisado, Eco-Oil, LISNAVE, Termitrena ou Teporset, "
            "conta com cerca de 1 hora e 30 minutos."
        )
        return _route_summary_payload(question, answer, "ROUTE_FUNDEADOURO_NORTE_MULTI_TIME")

    if _matches_any(clean_question, ORIGIN_CANAL_SUL) and has_south_quays and has_north_quays and has_secil:
        answer = (
            "Do Canal Sul para cais do sul, isto é, Tanquisado, Eco-Oil, LISNAVE, Termitrena ou Teporset, "
            "conta com cerca de 30 minutos a 1 hora. Do Canal Sul para cais a norte, isto é, de TMS 1 "
            "até SAPEC Líquidos, conta com cerca de 1 hora a 1 hora e 20 minutos. Do Canal Sul para a "
            "SECIL, cerca de 40 minutos."
        )
        return _route_summary_payload(question, answer, "ROUTE_CANAL_SUL_MULTI_TIME")

    mentions_barra = _matches_any(clean_question, ORIGIN_BARRA)
    mentions_sapec_or_praias = bool(re.search(r"\bsapec\b|\bpraias\s+do\s+sado\b|\bpraias\b", clean_question))
    mentions_fundeadouros = bool(re.search(r"\bfundeadouros?\b|\bfund\s+norte\b|\bfund\s+troia\b", clean_question))
    if mentions_barra and has_secil and mentions_sapec_or_praias and mentions_fundeadouros:
        answer = (
            "Da entrada da Barra, a SECIL demora cerca de 30 minutos; Praias do Sado e SAPEC demoram "
            "cerca de 1 hora e 20 minutos; o Fundeadouro Norte demora cerca de 45 minutos; e o "
            "Fundeadouro Sul demora cerca de 45 minutos a 1 hora, conforme a posição."
        )
        return _route_summary_payload(question, answer, "ROUTE_BARRA_SECIL_SAPEC_FUNDEADOUROS_TIME")

    return None


def _route_summary_payload(question: str, answer: str, source_id: str) -> dict:
    return {
        "answer": answer,
        "sources": [
            {
                "document": "Notas_Pilotagem.txt",
                "source_id": source_id,
                "retrieval_mode": "route_transit_summary",
                "snippet": answer,
                "question": question,
            }
        ],
        "answer_origin": "operational_route_transit",
    }


def _setubal_route_plan_answer(question: str, clean_question: str) -> dict | None:
    wants_route_plan = bool(
        DISTANCE_QUERY_RE.search(clean_question)
        or ROUTE_DETAIL_QUERY_RE.search(clean_question)
        or _extract_speed_knots(clean_question)
    )
    if not wants_route_plan or not ROUTE_LINK_RE.search(clean_question):
        return None
    route_points = _extract_route_points(clean_question)
    if not route_points:
        return None
    origin, destination = route_points
    selected = _best_route_plan(clean_question, origin, destination)
    if not selected:
        return None
    route, legs = selected
    return _format_route_plan_answer(question, clean_question, route, legs)


def _route_plan_should_precede_fact(clean_question: str) -> bool:
    return bool(
        ROUTE_DETAIL_QUERY_RE.search(clean_question)
        or _extract_speed_knots(clean_question)
        or re.search(r"\bquanto falta\b|\bfalta\b|\bfaltam\b|\bfim\s+do\s+canal\b", clean_question)
    )


ROUTE_TRANSIT_FACTS: tuple[RouteTransitFact, ...] = (
    RouteTransitFact(
        metric="distance",
        origin_patterns=ORIGIN_BARRA,
        destination_patterns=DEST_LISNAVE,
        answer=(
            "Do Pilar 2 / entrada da Barra até aos estaleiros da LISNAVE/Mitrena "
            "são cerca de 10,5 milhas náuticas pelo Canal Sul completo. Pelo atalho "
            "ou corta-mato, considera cerca de 10,0 milhas náuticas."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_BARRA_LISNAVE_DISTANCE",
        specificity=30,
        reverse_answer=(
            "Da LISNAVE/Mitrena até ao Pilar 2 / entrada da Barra são cerca de "
            "10,5 milhas náuticas pelo Canal Sul completo. Pelo atalho ou corta-mato, "
            "considera cerca de 10,0 milhas náuticas."
        ),
    ),
    RouteTransitFact(
        metric="distance",
        origin_patterns=ORIGIN_BARRA,
        destination_patterns=DEST_TMS2,
        answer=(
            "Do Pilar 2 / entrada da Barra até ao TMS2 / TMS 2 são cerca de 6,5 milhas "
            "náuticas pelo Canal Norte."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_BARRA_TMS2_DISTANCE",
        specificity=32,
        reverse_answer=(
            "Do TMS2 / TMS 2 até ao Pilar 2 / entrada da Barra são cerca de 6,5 milhas "
            "náuticas pelo Canal Norte."
        ),
    ),
    RouteTransitFact(
        metric="distance",
        origin_patterns=ORIGIN_TMS1,
        destination_patterns=DEST_ALSTOM,
        answer=_segment_distance_answer("TMS 1", "ao Cais ALSTOM", "3,5"),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_TMS1_ALSTOM_DISTANCE",
        specificity=42,
        reverse_answer=_segment_distance_reverse("TMS 1", "Do Cais ALSTOM", "3,5"),
    ),
    RouteTransitFact(
        metric="distance",
        origin_patterns=ORIGIN_TMS1,
        destination_patterns=DEST_SAPEC,
        answer=_segment_distance_answer("TMS 1", "ao SAPEC", "2,2"),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_TMS1_SAPEC_DISTANCE",
        specificity=42,
        reverse_answer=_segment_distance_reverse("TMS 1", "Do SAPEC", "2,2"),
    ),
    RouteTransitFact(
        metric="distance",
        origin_patterns=ORIGIN_TMS1,
        destination_patterns=DEST_PRAIAS,
        answer=_segment_distance_answer("TMS 1", "às Praias do Sado", "1,6"),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_TMS1_PRAIAS_DISTANCE",
        specificity=42,
        reverse_answer=_segment_distance_reverse("TMS 1", "Das Praias do Sado", "1,6"),
    ),
    RouteTransitFact(
        metric="distance",
        origin_patterns=ORIGIN_TMS1,
        destination_patterns=DEST_AUTOEUROPA,
        answer=_segment_distance_answer("TMS 1", "à Autoeuropa", "1,0"),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_TMS1_AUTOEUROPA_DISTANCE",
        specificity=42,
        reverse_answer=_segment_distance_reverse("TMS 1", "Da Autoeuropa", "1,0"),
    ),
    RouteTransitFact(
        metric="distance",
        origin_patterns=ORIGIN_TMS1,
        destination_patterns=DEST_JOAO_FARTO,
        answer=_segment_distance_answer("TMS 1", "à Bóia João Farto", "1,6"),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_TMS1_JOAO_FARTO_DISTANCE",
        specificity=42,
        reverse_answer=_segment_distance_reverse("TMS 1", "Da Bóia João Farto", "1,6"),
    ),
    RouteTransitFact(
        metric="distance",
        origin_patterns=ORIGIN_TMS1,
        destination_patterns=DEST_OUTAO,
        answer=_segment_distance_answer("TMS 1", "ao Outão", "3,0"),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_TMS1_OUTAO_DISTANCE",
        specificity=42,
        reverse_answer=_segment_distance_reverse("TMS 1", "Do Outão", "3,0"),
    ),
    RouteTransitFact(
        metric="distance",
        origin_patterns=ORIGIN_TMS1,
        destination_patterns=DEST_FORA_BARRA,
        answer=_segment_distance_answer("TMS 1", "fora da Barra / Pilar 2", "6,0"),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_TMS1_FORA_BARRA_DISTANCE",
        specificity=42,
        reverse_answer=_segment_distance_reverse("TMS 1", "De fora da Barra / Pilar 2", "6,0"),
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_BARRA,
        destination_patterns=DEST_LISNAVE,
        answer=(
            "Da entrada da Barra / Pilar 2 até aos estaleiros da LISNAVE/Mitrena, "
            "o tempo operacional prático é cerca de 1 hora e 30 minutos a 2 horas "
            "pelo Canal Sul. A distância de referência é 10,5 milhas náuticas pelo "
            "Canal Sul completo, ou cerca de 10,0 milhas pelo corta-mato."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_BARRA_LISNAVE_TIME",
        specificity=30,
        reverse_answer=(
            "Da LISNAVE/Mitrena até ao Pilar 2 / entrada da Barra, o tempo operacional "
            "prático é cerca de 1 hora e 30 minutos a 2 horas pelo Canal Sul. "
            "A distância de referência é 10,5 milhas náuticas pelo Canal Sul completo, "
            "ou cerca de 10,0 milhas pelo corta-mato."
        ),
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_BARRA,
        destination_patterns=DEST_SOUTH_QUAYS,
        answer=(
            "Da entrada da Barra para os destinos principais do Canal Sul "
            "(Tanquisado, Eco-Oil, LISNAVE, Termitrena ou Teporset), conta com "
            "cerca de 1 hora e 30 minutos a 2 horas."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_BARRA_CANAL_SUL_TIME",
        specificity=20,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_BARRA,
        destination_patterns=DEST_AUTOEUROPA,
        answer=(
            "Da entrada da Barra pelo Canal Norte até à Autoeuropa/Ro-Ro "
            "(Cais 10 e Cais 11), conta com cerca de 1 hora em condições normais."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_BARRA_AUTOEUROPA_TIME",
        specificity=28,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_BARRA,
        destination_patterns=DEST_NORTH_QUAYS,
        answer=(
            "Da entrada da Barra pelo Canal Norte até TMS 1, TMS 2 e "
            "Autoeuropa/Ro-Ro, conta com cerca de 1 hora. Para Praias do Sado "
            "e SAPEC, usa cerca de 1 hora e 20 minutos."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_BARRA_CANAL_NORTE_TIME",
        specificity=18,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_BARRA,
        destination_patterns=DEST_ALSTOM,
        answer=(
            "Da entrada da Barra para o Cais ALSTOM, marca a manobra 1 hora e "
            "30 minutos antes da preia-mar para chegar ao cais no reponto de preia-mar."
        ),
        source_document="IT-038_Alstom.txt",
        source_id="ROUTE_BARRA_ALSTOM_REPONTO_TIME",
        specificity=34,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_FUNDEADOURO_NORTE,
        destination_patterns=DEST_ALSTOM,
        answer=(
            "Do Fundeadouro Norte para o Cais ALSTOM, marca 45 minutos antes da "
            "preia-mar para chegar ao cais no reponto de preia-mar."
        ),
        source_document="IT-038_Alstom.txt",
        source_id="ROUTE_FUNDEADOURO_NORTE_ALSTOM_REPONTO_TIME",
        specificity=34,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_BARRA,
        destination_patterns=DEST_SECIL,
        answer="Da entrada da Barra até à SECIL, conta com cerca de 30 minutos.",
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_BARRA_SECIL_TIME",
        specificity=25,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_BARRA,
        destination_patterns=DEST_FUNDEADOURO_NORTE,
        answer="Da entrada da Barra até ao Fundeadouro Norte, conta com cerca de 45 minutos.",
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_BARRA_FUNDEADOURO_NORTE_TIME",
        specificity=24,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_BARRA,
        destination_patterns=DEST_FUNDEADOURO_SUL,
        answer=(
            "Da entrada da Barra até ao Fundeadouro Sul / Tróia, conta com cerca "
            "de 45 minutos a 1 hora, conforme a posição."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_BARRA_FUNDEADOURO_SUL_TIME",
        specificity=24,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_FUNDEADOURO_NORTE,
        destination_patterns=DEST_NORTH_QUAYS,
        answer=(
            "Do Fundeadouro Norte para os cais a norte, isto é, de TMS 1 até "
            "SAPEC Líquidos, conta com 15 a 25 minutos a navegar, até SAPEC."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_FUNDEADOURO_NORTE_CAIS_NORTE_TIME",
        specificity=26,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_FUNDEADOURO_NORTE,
        destination_patterns=DEST_SECIL,
        answer="Do Fundeadouro Norte para a SECIL, conta com cerca de 20 minutos.",
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_FUNDEADOURO_NORTE_SECIL_TIME",
        specificity=28,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_FUNDEADOURO_NORTE,
        destination_patterns=DEST_SOUTH_QUAYS,
        answer=(
            "Do Fundeadouro Norte para os cais a sul (Tanquisado, Eco-Oil, "
            "LISNAVE, Termitrena ou Teporset), conta com cerca de 1 hora e 30 minutos."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_FUNDEADOURO_NORTE_CAIS_SUL_TIME",
        specificity=24,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_FUNDEADOURO_SUL,
        destination_patterns=DEST_AUTOEUROPA,
        answer=(
            "Do Fundeadouro Sul / Tróia até à Autoeuropa/Ro-Ro (Cais 10/11), "
            "trata como uma mudança interna direta dentro do porto, não como "
            "Fundeadouro Sul -> Barra -> Canal Norte. A referência prática dos "
            "casebooks é cerca de 1 hora e 50 minutos a 2 horas, ajustando à "
            "posição concreta no fundeadouro, corrente e tráfego."
        ),
        source_document="practice_maneuver_experience.json",
        source_id="ROUTE_FUNDEADOURO_SUL_AUTOEUROPA_TIME",
        specificity=36,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_CANAL_SUL,
        destination_patterns=DEST_SOUTH_QUAYS,
        answer=(
            "Do Canal Sul para cais do sul (Tanquisado, Eco-Oil, LISNAVE, "
            "Termitrena ou Teporset), conta com cerca de 30 minutos a 1 hora."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_CANAL_SUL_CAIS_SUL_TIME",
        specificity=28,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_CANAL_SUL,
        destination_patterns=DEST_NORTH_QUAYS,
        answer=(
            "Do Canal Sul para cais a norte (TMS 1 até SAPEC Líquidos), conta "
            "com cerca de 1 hora a 1 hora e 20 minutos."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_CANAL_SUL_CAIS_NORTE_TIME",
        specificity=28,
    ),
    RouteTransitFact(
        metric="time",
        origin_patterns=ORIGIN_CANAL_SUL,
        destination_patterns=DEST_SECIL,
        answer="Do Canal Sul para a SECIL, conta com cerca de 40 minutos.",
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_CANAL_SUL_SECIL_TIME",
        specificity=30,
    ),
)


def _matches_any(clean_question: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, clean_question) for pattern in patterns)


def _first_match_index(clean_question: str, patterns: tuple[str, ...]) -> int | None:
    indexes = [
        match.start()
        for pattern in patterns
        for match in [re.search(pattern, clean_question)]
        if match
    ]
    return min(indexes) if indexes else None


def _query_metric(clean_question: str) -> str:
    wants_distance = bool(DISTANCE_QUERY_RE.search(clean_question))
    wants_time = bool(TIME_QUERY_RE.search(clean_question))
    if wants_distance and not wants_time:
        return "distance"
    if wants_time:
        return "time"
    return ""


def _looks_like_route_question(clean_question: str) -> bool:
    return bool(_query_metric(clean_question) and ROUTE_LINK_RE.search(clean_question))


def _pilot_station_distance_answer(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\b(posicao\s+de\s+embarque|posicao\s+embarque|pilotos?|pilot\s+station)\b", clean_question):
        return None
    if not re.search(r"\b(entrada\s+da\s+barra|barra|pilar\s*2)\b", clean_question):
        return None
    answer = (
        "A posição de embarque dos pilotos fica 1 milha náutica fora da entrada da Barra / Pilar 2. "
        "Na malha de percurso, é o segmento Pilot station / posição de embarque -> Pilar 2 / entrada da Barra, rumo 040°, distância 1,0 NM."
    )
    return {
        "answer": answer,
        "sources": [
            {
                "document": "setubal_route_planning.json",
                "source_id": "ROUTE_PILOT_STATION_BARRA_DISTANCE",
                "retrieval_mode": "route_transit_fact",
                "snippet": answer,
                "question": question,
            }
        ],
        "answer_origin": "operational_route_transit",
    }


def _reponto_time_from_question(question: str) -> tuple[int, int, str] | None:
    for match in re.finditer(r"\b(\d{1,2})(?::|h)(\d{2})\b", question or "", flags=re.IGNORECASE):
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute, f"{hour:02d}:{minute:02d}"
    return None


def _subtract_minutes_label(hour: int, minute: int, delta_minutes: int) -> str:
    total = (hour * 60 + minute - delta_minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _south_quay_reponto_lead_time_answer(question: str, clean_question: str) -> dict | None:
    if not _matches_any(clean_question, DEST_SOUTH_QUAYS):
        return None
    if not re.search(r"\b(reponto|preia|baixa|mare)\b", clean_question):
        return None

    origin_label = ""
    lead_minutes = 0
    source_id = ""
    if _matches_any(clean_question, ORIGIN_BARRA):
        origin_label = "da Barra/fora da Barra"
        lead_minutes = 120
        source_id = "ROUTE_BARRA_CAIS_SUL_REPONTO_LEAD_TIME"
    elif _matches_any(clean_question, ORIGIN_FUNDEADOURO_NORTE):
        origin_label = "do Fundeadouro Norte"
        lead_minutes = 90
        source_id = "ROUTE_FUNDEADOURO_NORTE_CAIS_SUL_REPONTO_LEAD_TIME"
    elif _matches_any(clean_question, ORIGIN_FUNDEADOURO_SUL):
        origin_label = "de Tróia/Fundeadouro Sul"
        lead_minutes = 60
        source_id = "ROUTE_TROIA_CAIS_SUL_REPONTO_LEAD_TIME"
    else:
        return None

    parsed = _reponto_time_from_question(question)
    reponto_label = parsed[2] if parsed else "do reponto pretendido"
    depart_label = (
        _subtract_minutes_label(parsed[0], parsed[1], lead_minutes)
        if parsed
        else f"cerca de {_format_duration_words(lead_minutes)} antes"
    )
    answer = (
        f"Percurso/duracao: {origin_label} para os cais a sul "
        f"(LISNAVE/Mitrena, Tanquisado, Eco-Oil, Termitrena ou Teporset) conta com cerca de {_format_duration_words(lead_minutes)}.\n"
        f"Para chegar ao reponto das {reponto_label}, a manobra/largada deve ser marcada por volta das {depart_label}.\n"
        "A fase critica no cais/doca é a referência: estes cais do Canal Sul devem ser trabalhados próximo do reponto de maré, porque ficam condicionados pela corrente.\n"
        "Confirmar ainda o cais/doca concreto, calado, vento, rebocadores e validação do Piloto Coordenador."
    )
    return {
        "answer": answer,
        "sources": [
            {
                "document": "Marcar_manobra_repontos_mare.txt",
                "source_id": source_id,
                "retrieval_mode": "route_transit_summary",
                "snippet": answer,
                "question": question,
            }
        ],
        "answer_origin": "operational_route_transit",
    }


def route_transit_answer(question: str, clean_question: str | None = None) -> dict | None:
    clean = clean_question or _normalize(question)
    pilot_distance = _pilot_station_distance_answer(question, clean)
    if pilot_distance:
        return pilot_distance
    lisnave_reponto = _south_quay_reponto_lead_time_answer(question, clean)
    if lisnave_reponto:
        return lisnave_reponto
    route_order = _route_order_answer(question, clean)
    if route_order:
        return route_order
    route_summary = _route_summary_answer(question, clean)
    if route_summary:
        return route_summary
    if _route_plan_should_precede_fact(clean):
        route_plan = _setubal_route_plan_answer(question, clean)
        if route_plan:
            return route_plan

    if not _looks_like_route_question(clean):
        return None

    metric = _query_metric(clean)
    matches = [
        fact
        for fact in ROUTE_TRANSIT_FACTS
        if fact.metric == metric
        and _matches_any(clean, fact.origin_patterns)
        and _matches_any(clean, fact.destination_patterns)
    ]
    if not matches:
        return _setubal_route_plan_answer(question, clean)

    fact = sorted(matches, key=lambda item: item.specificity, reverse=True)[0]
    origin_index = _first_match_index(clean, fact.origin_patterns)
    destination_index = _first_match_index(clean, fact.destination_patterns)
    is_reverse = (
        origin_index is not None
        and destination_index is not None
        and destination_index < origin_index
        and fact.reverse_answer
    )
    answer = fact.reverse_answer if is_reverse else fact.answer
    return {
        "answer": answer,
        "sources": [
            {
                "document": fact.source_document,
                "source_id": fact.source_id,
                "retrieval_mode": "route_transit_fact",
                "snippet": answer,
                "question": question,
            }
        ],
        "answer_origin": "operational_route_transit",
    }
