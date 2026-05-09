from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


def _normalize(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


TIME_QUERY_RE = re.compile(
    r"\b(quanto tempo|tempo|demora|demoram|leva|levo|levam|transito|viagem|percurso)\b"
)
DISTANCE_QUERY_RE = re.compile(r"\b(distancia|distancias|milha|milhas|mn)\b")
ROUTE_LINK_RE = re.compile(r"\b(ate|a|ao|aos|para)\b")

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
            "Do Pilar 2 / entrada da Barra até ao TMS 2 são cerca de 6,5 milhas "
            "náuticas pelo Canal Norte."
        ),
        source_document="Notas_Pilotagem.txt",
        source_id="ROUTE_BARRA_TMS2_DISTANCE",
        specificity=32,
        reverse_answer=(
            "Do TMS 2 até ao Pilar 2 / entrada da Barra são cerca de 6,5 milhas "
            "náuticas pelo Canal Norte."
        ),
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
            "LISNAVE, Termitrena ou Teporset), conta com cerca de 1 hora."
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


def route_transit_answer(question: str, clean_question: str | None = None) -> dict | None:
    clean = clean_question or _normalize(question)
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
        return None

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
