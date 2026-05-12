from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


NAVIGATION_BASICS_FILENAME = "Nocoes_Basicas_Navegacao_Unidades.txt"


def _normalize(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents.lower()).strip()


def _format_number(value: float, decimals: int = 2, *, keep_trailing: bool = False) -> str:
    rounded = round(value, decimals)
    if abs(rounded - int(rounded)) < 10 ** -decimals:
        return str(int(rounded))
    if keep_trailing:
        return f"{rounded:.{decimals}f}".replace(".", ",")
    text = f"{rounded:.{decimals}f}".rstrip("0").rstrip(".")
    if "." not in text and decimals > 0 and abs(value - round(value)) > 0:
        text = f"{rounded:.{decimals}f}"
    return text.replace(".", ",")


@dataclass(frozen=True)
class UnitDef:
    key: str
    label_singular: str
    label_plural: str
    to_base: float
    kind: str
    aliases: tuple[str, ...]


DISTANCE_UNITS: tuple[UnitDef, ...] = (
    UnitDef("m", "metro", "metros", 1.0, "distance", ("m", "metro", "metros")),
    UnitDef("km", "km", "km", 1000.0, "distance", ("km", "quilometro", "quilometros")),
    UnitDef(
        "nm",
        "milha náutica",
        "milhas náuticas",
        1852.0,
        "distance",
        ("nm", "mn", "milha nautica", "milhas nauticas", "milha", "milhas"),
    ),
    UnitDef("yd", "jarda", "jardas", 0.9144, "distance", ("yd", "jarda", "jardas", "yard", "yards")),
    UnitDef("shackle", "manilha", "manilhas", 27.5, "distance", ("manilha", "manilhas")),
)

SPEED_UNITS: tuple[UnitDef, ...] = (
    UnitDef("kt", "nó", "nós", 1.0, "speed", ("kt", "kts", "no", "nos", "nó", "nós")),
    UnitDef("kmh", "km/h", "km/h", 1 / 1.852, "speed", ("km/h", "kmh", "quilometros por hora")),
)

UNIT_DEFS = DISTANCE_UNITS + SPEED_UNITS


@dataclass(frozen=True)
class BeaufortBand:
    force: int
    kt: str
    kmh: str
    description: str
    operational_note: str


BEAUFORT_SCALE: tuple[BeaufortBand, ...] = (
    BeaufortBand(0, "<1 kt", "<1 km/h", "calmaria", "Mar espelhado; sem efeito pratico de vento."),
    BeaufortBand(1, "1-3 kt", "1-5 km/h", "aragem", "Apenas indica direcao do vento."),
    BeaufortBand(2, "4-6 kt", "6-11 km/h", "brisa leve", "Vento fraco; normalmente pouco impacto na manobra."),
    BeaufortBand(3, "7-10 kt", "12-19 km/h", "brisa fraca", "Ja pode ser sentido em navios com grande area velica."),
    BeaufortBand(4, "11-16 kt", "20-28 km/h", "brisa moderada", "Relevante para aproximacao fina e cabos."),
    BeaufortBand(5, "17-21 kt", "29-38 km/h", "brisa fresca", "Exige atencao em navios leves ou com muita area velica."),
    BeaufortBand(6, "22-27 kt", "39-49 km/h", "vento fresco", "Avaliar viabilidade, meios e margem antes da manobra."),
    BeaufortBand(7, "28-33 kt", "50-61 km/h", "vento forte", "Zona critica; pode aproximar ou ultrapassar limites locais."),
    BeaufortBand(8, "34-40 kt", "62-74 km/h", "temporal", "Acima de 30 kt, aplicar regra local de suspensao de manobras."),
    BeaufortBand(9, "41-47 kt", "75-88 km/h", "temporal forte", "Condicao severa; manobra portuaria normalmente suspensa."),
    BeaufortBand(10, "48-55 kt", "89-102 km/h", "temporal muito forte", "Condicao severa e perigosa."),
    BeaufortBand(11, "56-63 kt", "103-117 km/h", "tempestade", "Condicao extrema."),
    BeaufortBand(12, ">=64 kt", ">=118 km/h", "furacao", "Condicao extrema."),
)

BEAUFORT_QUERY_RE = re.compile(r"\b(beaufort|bft|forca\s+\d{1,2}|força\s+\d{1,2})\b", flags=re.IGNORECASE)
UNIT_QUERY_RE = re.compile(
    r"\b(converte|converter|conversao|conversão|equivale|equivalem|quantos|quantas|quanto|"
    r"milha(?:s)?\s+nautic(?:a|as)|milha(?:s)?\s+náutic(?:a|as)|\bmn\b|\bnm\b|"
    r"manilha(?:s)?|jarda(?:s)?|yards?|km/h|kts?|n[oó]s)\b",
    flags=re.IGNORECASE,
)
SOURCE_COVERAGE_RE = re.compile(
    r"\b(fonte|fontes|documento|base|cobre|cobrem|inclui|incluem|conhecimento|indexavel|indexável|incorporad\w*)\b",
    flags=re.IGNORECASE,
)


def _unit_alias_pattern(alias: str) -> str:
    escaped = re.escape(alias)
    return escaped.replace(r"\ ", r"\s+")


def _unit_pattern(unit: UnitDef) -> str:
    return r"(?:%s)" % "|".join(_unit_alias_pattern(alias) for alias in sorted(unit.aliases, key=len, reverse=True))


UNIT_ALIASES: tuple[tuple[re.Pattern[str], UnitDef], ...] = tuple(
    (
        re.compile(rf"\b{_unit_pattern(unit)}\b", flags=re.IGNORECASE),
        unit,
    )
    for unit in sorted(UNIT_DEFS, key=lambda item: max(len(alias) for alias in item.aliases), reverse=True)
)

NUMBER_UNIT_RE = re.compile(
    r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>milhas?\s+nauticas?|milhas?\s+náuticas?|"
    r"quilometros?|quilómetros?|metros?|yards?|jardas?|manilhas?|km/h|kmh|km|kts?|n[oó]s|mn|nm|m|yd)\b",
    flags=re.IGNORECASE,
)


def _parse_number(value: str) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _find_unit(text: str) -> UnitDef | None:
    clean = _normalize(text)
    if clean in {"milha", "milhas"}:
        return next(unit for unit in DISTANCE_UNITS if unit.key == "nm")
    for pattern, unit in UNIT_ALIASES:
        if pattern.search(clean):
            return unit
    return None


def _target_unit(question: str, source_span: tuple[int, int]) -> UnitDef | None:
    candidates: list[tuple[int, UnitDef]] = []
    for pattern, unit in UNIT_ALIASES:
        for match in pattern.finditer(_normalize(question)):
            # The normalized string can have slightly different indexes, so use ordering only.
            candidates.append((match.start(), unit))
    if not candidates:
        return None

    source_text = question[source_span[0] : source_span[1]]
    source_unit = _find_unit(source_text)
    after_source = _normalize(question[source_span[1] :])
    for pattern, unit in UNIT_ALIASES:
        if unit == source_unit:
            continue
        if pattern.search(after_source):
            return unit
    return None


def _label(unit: UnitDef, value: float) -> str:
    if abs(value - 1) < 0.0000001:
        return unit.label_singular
    return unit.label_plural


def _convert(value: float, source_unit: UnitDef, target_unit: UnitDef) -> float:
    base = value * source_unit.to_base
    return base / target_unit.to_base


def _unit_reference_line(source_unit: UnitDef, target_unit: UnitDef) -> str:
    if {source_unit.key, target_unit.key} <= {"km", "nm"}:
        return "Referencia: 1 milha náutica = 1852 m = 1,852 km."
    if "shackle" in {source_unit.key, target_unit.key}:
        return "Referencia: 1 manilha = 27,5 m."
    if "yd" in {source_unit.key, target_unit.key}:
        return "Referencia: 1 jarda = 0,9144 m."
    if {source_unit.key, target_unit.key} <= {"kt", "kmh"}:
        return "Referencia: 1 nó = 1 milha náutica/h = 1,852 km/h."
    return "Referencia: usar milhas náuticas para distancias de navegação e nós para velocidade."


def _answer_unit_conversion(question: str) -> dict | None:
    match = NUMBER_UNIT_RE.search(question or "")
    if not match:
        return None
    value = _parse_number(match.group("number"))
    if value is None:
        return None
    source_unit = _find_unit(match.group("unit"))
    target_unit = _target_unit(question, match.span())
    if not source_unit or not target_unit or source_unit.kind != target_unit.kind:
        return None

    result = _convert(value, source_unit, target_unit)
    source_value = _format_number(value, 2)
    target_decimals = 2 if target_unit.key in {"nm", "m", "yd", "kmh"} else 1
    result_value = _format_number(result, target_decimals, keep_trailing=target_unit.key == "nm")
    answer = (
        f"{source_value} {_label(source_unit, value)} = {result_value} {_label(target_unit, result)}.\n"
        f"{_unit_reference_line(source_unit, target_unit)}"
    )
    return {
        "answer": answer,
        "sources": [build_navigation_basics_source(question) or _navigation_basics_fallback_source()],
        "answer_origin": "navigation_basics",
    }


def _beaufort_force(question: str) -> int | None:
    patterns = (
        r"\bbeaufort\s*(\d{1,2})\b",
        r"\bbft\s*(\d{1,2})\b",
        r"\bfor[cç]a\s*(\d{1,2})\b",
        r"\b(\d{1,2})\s*(?:beaufort|bft)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, question or "", flags=re.IGNORECASE)
        if not match:
            continue
        force = int(match.group(1))
        if 0 <= force <= 12:
            return force
    return None


def _answer_beaufort(question: str) -> dict | None:
    if not BEAUFORT_QUERY_RE.search(question or ""):
        return None
    force = _beaufort_force(question)
    if force is None:
        lines = ["Escala Beaufort resumida:"]
        for band in BEAUFORT_SCALE:
            lines.append(f"- Beaufort {band.force}: {band.kt} ({band.kmh}) - {band.description}.")
        return {
            "answer": "\n".join(lines),
            "sources": [build_navigation_basics_source(question) or _navigation_basics_fallback_source()],
            "answer_origin": "navigation_basics",
        }
    band = BEAUFORT_SCALE[force]
    answer = (
        f"Beaufort {band.force} = {band.kt} ({band.kmh}), descrição: {band.description}.\n"
        f"Nota operacional: {band.operational_note}"
    )
    return {
        "answer": answer,
        "sources": [build_navigation_basics_source(question) or _navigation_basics_fallback_source()],
        "answer_origin": "navigation_basics",
    }


def looks_like_navigation_basics_query(question: str) -> bool:
    text = str(question or "")
    if SOURCE_COVERAGE_RE.search(text) and re.search(
        r"\b(navegacao|navegação|unidades|milha|milhas|manilha|manilhas|jarda|jardas|beaufort|nos|n[oó]s)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    if BEAUFORT_QUERY_RE.search(text):
        return True
    if UNIT_QUERY_RE.search(text) and NUMBER_UNIT_RE.search(text):
        return True
    return bool(re.search(r"\b(tabela|escala)\b.*\bbeaufort\b", text, flags=re.IGNORECASE))


def answer_navigation_basics_direct(question: str) -> dict | None:
    if not looks_like_navigation_basics_query(question):
        return None
    if SOURCE_COVERAGE_RE.search(question or ""):
        answer = (
            "Sim. A fonte indexavel de noções básicas de navegação está carregada em Nocoes_Basicas_Navegacao_Unidades.txt.\n"
            "- Conversões: 1 milha náutica = 1852 m = 1,852 km; 1 nó = 1 milha náutica/h = 1,852 km/h.\n"
            "- Unidades práticas: 1 jarda = 0,9144 m; 1 manilha = 27,5 m.\n"
            "- Beaufort: a escala 0-12 está disponível; Beaufort 6 corresponde a 22-27 kt, vento fresco."
        )
        return {
            "answer": answer,
            "sources": [build_navigation_basics_source(question) or _navigation_basics_fallback_source()],
            "answer_origin": "navigation_basics",
        }
    return _answer_beaufort(question) or _answer_unit_conversion(question)


def _navigation_basics_fallback_source() -> dict:
    snippet = (
        "Nocoes basicas de navegacao: 1 milha nautica = 1852 m; "
        "1 no = 1,852 km/h; 1 jarda = 0,9144 m; 1 manilha = 27,5 m; "
        "escala Beaufort 0-12 em intervalos de vento."
    )
    return {
        "source_id": "NAVIGATION_BASICS",
        "document": NAVIGATION_BASICS_FILENAME,
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "navigation_basics",
        "snippet": snippet,
        "text": snippet,
    }


def build_navigation_basics_source(question: str, knowledge_dir: str | Path = "knowledge") -> dict | None:
    if not looks_like_navigation_basics_query(question):
        return None
    path = Path(knowledge_dir) / NAVIGATION_BASICS_FILENAME
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return _navigation_basics_fallback_source()
    return {
        "source_id": "NAVIGATION_BASICS",
        "document": NAVIGATION_BASICS_FILENAME,
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "navigation_basics",
        "snippet": text[:3500],
        "text": text,
    }
