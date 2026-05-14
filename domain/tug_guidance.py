from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from typing import Any

from domain.operational_safety import looks_like_emergency_response_question
from domain.port_entities import detect_port_entities


TUG_GUIDANCE_FILENAME = "tug_operational_guidance.json"
TUG_QUERY_RE = re.compile(r"\b(reboque|reboques|rebocador|rebocadores)\b", flags=re.IGNORECASE)
TUG_DECISION_RE = re.compile(
    r"\b(quantos|quantas|numero|n[uú]mero|onde|posicion\w*|meter|colocar|"
    r"precis\w*|necess[aá]ri\w*|recomend\w*|aconselh\w*|suger\w*|"
    r"suficient\w*|bast\w*|cheg\w*)\b",
    flags=re.IGNORECASE,
)
TUG_CONTEXT_RE = re.compile(
    r"\b(entrada|entrar|atracar|atracacao|saida|sair|desatracar|desatracacao|"
    r"vento|corrente|mare|roro|ro\s*ro|graneleiro|reefer|estilha|contentores?|"
    r"lisnave|mitrena|bow\s*thruster|bowthruster|h[eé]lice de proa|"
    r"proa|popa|costado|convencionais?|azipodes?|push|pull)\b",
    flags=re.IGNORECASE,
)
LOA_RE = re.compile(
    r"\b(?:loa|comprimento|navio|roro|ro\s*ro|ro-ro|ro/ro|graneleiro|reefer|estilha|contentores?)\b[^\n.;,]{0,80}?\b(\d{2,3}(?:[.,]\d+)?)\s*m\b"
    r"|\b(\d{2,3}(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?(?:loa|comprimento)\b",
    flags=re.IGNORECASE,
)
BEAM_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?(?:boca|beam)\b",
    flags=re.IGNORECASE,
)
DRAFT_RE = re.compile(
    r"\b(?:calado|draft)\b[^\n.;,]{0,40}?\b(\d+(?:[.,]\d+)?)\s*m\b"
    r"|\b(\d+(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?(?:calado|draft)\b",
    flags=re.IGNORECASE,
)
REQUESTED_TUG_COUNT_RE = re.compile(
    r"\b(?:(?:com|tenho|temos|tem|pedid\w*|solicitad\w*|usar|usamos|leva|levam|levar|meter|meto|colocar)\s+)?"
    r"(\d{1,2})(?:\s*[ºoaª])?\s+(?:reboques?|rebocadores?)\b",
    flags=re.IGNORECASE,
)
REQUESTED_TUG_ORDINAL_RE = re.compile(
    r"\b(quarto|quarta|quinto|quinta|sexto|sexta)\s+(?:reboque|rebocador)\b",
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
LISNAVE_RE = re.compile(r"\b(lisnave|mitrena|estaleiro|estaleiros|doca\s*\d{2}|d\d{2}|hidrolift|eclusa)\b", flags=re.IGNORECASE)
WEST_EAST_EQUIVALENCE_SCOPE = {"TMS-2", "Autoeuropa"}
WEST_EAST_EQUIVALENCE_EXCLUSIONS = {"Lisnave", "Tanquisado", "Eco-Oil", "Termitrena", "Teporset"}


def _normalize_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", without_accents.lower())).strip()


def _guidance_path(knowledge_dir: str) -> str:
    return os.path.join(knowledge_dir, TUG_GUIDANCE_FILENAME)


def _file_signature(path: str) -> tuple[str, float]:
    try:
        return path, os.path.getmtime(path)
    except OSError:
        return path, 0.0


@lru_cache(maxsize=8)
def _load_guidance_cached(path: str, _mtime: float) -> dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def load_tug_guidance(knowledge_dir: str) -> dict[str, Any]:
    path, mtime = _file_signature(_guidance_path(knowledge_dir))
    return dict(_load_guidance_cached(path, mtime))


def looks_like_tug_decision_question(question: str) -> bool:
    if looks_like_emergency_response_question(question or ""):
        return False
    if not TUG_QUERY_RE.search(question or ""):
        return False
    return bool(TUG_DECISION_RE.search(question or "") or TUG_CONTEXT_RE.search(question or ""))


def _safe_float(raw_value: str | None) -> float | None:
    if raw_value is None:
        return None
    try:
        return float(str(raw_value).replace(",", "."))
    except ValueError:
        return None


def _extract_loa(question: str) -> float | None:
    match = LOA_RE.search(question or "")
    if not match:
        return None
    return _safe_float(match.group(1) or match.group(2))


def _extract_beam(question: str) -> float | None:
    match = BEAM_RE.search(question or "")
    if not match:
        return None
    return _safe_float(match.group(1))


def _extract_draft(question: str) -> float | None:
    match = DRAFT_RE.search(question or "")
    if not match:
        return None
    return _safe_float(match.group(1) or match.group(2))


def _extract_requested_tug_count(question: str) -> int | None:
    match = REQUESTED_TUG_COUNT_RE.search(question or "")
    if match:
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            pass
    ordinal = REQUESTED_TUG_ORDINAL_RE.search(question or "")
    if not ordinal:
        return None
    return {
        "quarto": 4,
        "quarta": 4,
        "quinto": 5,
        "quinta": 5,
        "sexto": 6,
        "sexta": 6,
    }.get(_normalize_text(ordinal.group(1)))


def _matched_entity_names(question: str) -> set[str]:
    names = {item.get("name", "") for item in detect_port_entities(question or "") if item.get("name")}
    if re.search(r"\b(estaleiro|estaleiros)\b", _normalize_text(question)):
        names.add("Lisnave")
    return names


def _west_east_equivalence_allowed(entity_names: set[str]) -> bool:
    if entity_names & WEST_EAST_EQUIVALENCE_EXCLUSIONS:
        return False
    return bool(entity_names & WEST_EAST_EQUIVALENCE_SCOPE)


def _is_strong_wind(question: str) -> bool:
    clean = _normalize_text(question)
    if re.search(r"\b(vento|rajada|rajadas)\b.{0,25}\b(forte|fortes|muito|rijo|rijos)\b", clean):
        return True
    if re.search(r"\b(forte|fortes|muito|rijo|rijos)\b.{0,25}\b(vento|rajada|rajadas)\b", clean):
        return True
    if re.search(r"\b(norte|sul|leste|este|oeste|sw|nw|ne|se|n|s|e|w)\b.{0,15}\b(forte|fortes|rijo|rijos)\b", clean):
        return True
    return bool(re.search(r"\b(sustentado|rajada|rajadas)\b", clean))


def _infer_wind_direction(question: str) -> str:
    clean = _normalize_text(question)
    raw_question = str(question or "")
    if re.search(r"\b(sudoeste|sw)\b", clean):
        return "SW"
    if re.search(r"\b(oeste|west|vento w)\b", clean):
        return "W"
    if re.search(r"\bvento\s+E\b", raw_question) or re.search(r"\b(leste|east|vento este|vento leste)\b", clean):
        return "E"
    if re.search(r"\b(norte|vento n)\b", clean):
        return "N"
    if re.search(r"\b(sul|vento s)\b", clean):
        return "S"
    return ""


def _infer_operation(question: str, aliases: dict[str, list[str]]) -> str:
    clean = _normalize_text(question)
    for operation, tokens in (aliases or {}).items():
        for token in tokens:
            if re.search(rf"\b{re.escape(_normalize_text(token))}\b", clean):
                return operation
    return ""


def _infer_vessel_group(question: str, guidance: dict[str, Any]) -> str:
    clean = _normalize_text(question)
    for group_key, group in (guidance.get("vessel_type_groups") or {}).items():
        aliases = group.get("aliases") or []
        for alias in aliases:
            if re.search(rf"\b{re.escape(_normalize_text(alias))}\b", clean):
                return str(group_key)
    return ""


def _infer_wind_component(question: str, entity_names: set[str]) -> tuple[str, str]:
    clean = _normalize_text(question)
    raw_question = str(question or "")
    if re.search(r"\b(sudoeste|sw)\b", clean):
        return "south", "SW forte / caso critico"
    if re.search(r"\b(sul|vento s)\b", clean):
        return "south", "S"
    if re.search(r"\b(oeste|west|vento w)\b", clean):
        if _west_east_equivalence_allowed(entity_names):
            return "south", "W tratado como S fraco nos terminais TMS2/Autoeuropa"
        return "", "W mencionado: equivalencia W=S fraco nao aplicada sem contexto TMS2/Autoeuropa"
    if re.search(r"\b(norte|vento n)\b", clean):
        return "north", "N"
    if re.search(r"\bvento\s+E\b", raw_question) or re.search(r"\b(leste|east|vento este|vento leste)\b", clean):
        if _west_east_equivalence_allowed(entity_names):
            return "north", "E tratado como N fraco nos terminais TMS2/Autoeuropa"
        return "", "E mencionado: equivalencia E=N fraco nao aplicada sem contexto TMS2/Autoeuropa"
    if re.search(r"\b(nevoeiro|nevoa|nevoa)\b", clean):
        return "", "nevoeiro: suspensao por visibilidade; SW posterior e conhecimento local, nao dimensiona rebocadores"
    return "", ""


def _infer_bow_orientation(question: str, entity_names: set[str]) -> str:
    clean = _normalize_text(question)
    if re.search(r"\bproa a sul\b|\bproa sul\b", clean):
        return "bow_south"
    if re.search(r"\bpopa a sul\b|\bpopa sul\b|\bproa a norte\b|\bproa norte\b", clean):
        return "stern_south"
    if {"Tanquisado", "Eco-Oil"} & entity_names:
        return "bow_south"
    if "Lisnave" in entity_names:
        if re.search(r"\b(?:d20|d21|d22|doca 20|doca 21|doca 22)\b", clean):
            return "stern_south"
        if re.search(
            r"\b(?:d31|d32|d33|doca 31|doca 32|doca 33|"
            r"c0a|c0b|c1a|c1b|c2a|c2b|c3a|c3b|cais 0|cais 1|cais 2|cais 3)\b",
            clean,
        ):
            return "bow_south"
    return ""


def _minimum_no_bow_rule(question: str, loa: float | None, draft: float | None, guidance: dict[str, Any]) -> str:
    if loa is None or not NO_BOW_RE.search(question or ""):
        return ""
    for item in guidance.get("no_bowthruster_minimums") or []:
        condition = str(item.get("condition") or "")
        if "LOA < 120" in condition and "calado >= 8" in condition and loa < 120 and draft is not None and draft >= 8:
            return str(item.get("note") or "")
        if "calado >= 8" in condition:
            continue
        if "LOA < 120" in condition and loa < 120:
            return str(item.get("note") or "")
        if "120 m <= LOA <= 150" in condition and 120 <= loa <= 150:
            return str(item.get("note") or "")
        if "LOA > 150" in condition and loa > 150:
            return str(item.get("note") or "")
    return ""


def _lisnave_rule(question: str, loa: float | None, guidance: dict[str, Any]) -> str:
    if loa is None or not LISNAVE_RE.search(question or ""):
        return ""
    clean = _normalize_text(question)
    to_hidrolift = bool(re.search(r"\b(hidrolift|eclusa|d31|d32|d33|doca 31|doca 32|doca 33)\b", clean))
    candidates = []
    for item in guidance.get("lisnave_rules") or []:
        condition = str(item.get("condition") or "")
        note = str(item.get("note") or "")
        if "LOA <= 100" in condition and loa <= 100 and "eclusa" not in condition.lower():
            candidates.append(note)
        if "LOA <= 100" in condition and loa <= 100 and to_hidrolift and "Hidrolift" in condition:
            candidates.append(note)
        if "100 m < LOA <= 150" in condition and 100 < loa <= 150:
            candidates.append(note)
        if "150 m < LOA <= 199" in condition and 150 < loa <= 199:
            candidates.append(note)
        if "200 m <= LOA <= 250" in condition and 200 <= loa <= 250:
            candidates.append(note)
        if "LOA > 250" in condition and loa > 250:
            candidates.append(note)
    return " ".join(dict.fromkeys(item for item in candidates if item))


def _rule_matches_context(item: dict[str, Any], context: dict[str, Any]) -> bool:
    vessel_group = str(item.get("vessel_group") or "")
    wind_component = str(item.get("wind_component") or "")
    operation = str(item.get("operation") or "")
    if vessel_group and vessel_group != context.get("vessel_group"):
        return False
    if wind_component and wind_component != context.get("wind_component"):
        return False
    if operation and operation != "any" and operation != context.get("operation"):
        return False
    wind_direction = str(item.get("wind_direction") or "")
    if wind_direction and wind_direction != context.get("wind_direction"):
        return False
    min_loa_exclusive = item.get("min_loa_exclusive")
    if min_loa_exclusive is not None:
        loa = context.get("loa")
        if loa is None or not loa > float(min_loa_exclusive):
            return False
    if item.get("wind_strength") == "strong" and not context.get("strong_wind"):
        return False
    excluded_entities = set(item.get("excluded_entities") or [])
    if excluded_entities and excluded_entities & set(context.get("entity_names") or []):
        return False
    required_entities = set(item.get("required_entities") or [])
    if required_entities and not (required_entities & set(context.get("entity_names") or [])):
        return False
    return True


def _matrix_rules(
    guidance: dict[str, Any],
    context: dict[str, Any],
) -> list[str]:
    if not context.get("vessel_group") or not context.get("wind_component") or not context.get("operation"):
        return []
    rules = []
    for item in guidance.get("conditional_overrides") or []:
        if _rule_matches_context(item, context):
            note = str(item.get("note") or "")
            if note:
                rules.append(note)
    for item in guidance.get("base_matrix") or []:
        if _rule_matches_context(item, context):
            note = str(item.get("note") or "")
            if note:
                rules.append(note)
            break
    return list(dict.fromkeys(rules))


def _berth_minimum_rules(guidance: dict[str, Any], context: dict[str, Any]) -> list[str]:
    rules = []
    entity_names = set(context.get("entity_names") or [])
    for item in guidance.get("berth_minimums") or []:
        required_entities = set(item.get("required_entities") or [])
        if required_entities and not (required_entities & entity_names):
            continue
        note = str(item.get("note") or "")
        if note:
            rules.append(note)
    return list(dict.fromkeys(rules))


def _specific_positioning_rules(guidance: dict[str, Any], context: dict[str, Any]) -> list[str]:
    rules = []
    for item in guidance.get("berth_lateral_wind_positioning_rules") or []:
        if _rule_matches_context(item, context):
            note = str(item.get("note") or "")
            if note:
                rules.append(note)
    return list(dict.fromkeys(rules))


def _multi_tug_positioning_rules(guidance: dict[str, Any], context: dict[str, Any]) -> list[str]:
    requested_count = context.get("requested_tug_count")
    if not requested_count:
        return []
    rules = []
    entity_names = set(context.get("entity_names") or [])
    bow_orientation = str(context.get("bow_orientation") or "")
    for item in guidance.get("multi_tug_positioning_rules") or []:
        item_count = item.get("tug_count")
        if item_count is not None and int(item_count) != int(requested_count):
            continue
        required_entities = set(item.get("required_entities") or [])
        if required_entities and not (required_entities & entity_names):
            continue
        orientation = str(item.get("orientation") or "")
        if orientation and (not bow_orientation or orientation != bow_orientation):
            continue
        note = str(item.get("note") or "")
        if note:
            rules.append(note)
    return list(dict.fromkeys(rules))


def _extract_context(question: str, guidance: dict[str, Any]) -> dict[str, Any]:
    loa = _extract_loa(question)
    entity_names = _matched_entity_names(question)
    wind_component, wind_label = _infer_wind_component(question, entity_names)
    wind_direction = _infer_wind_direction(question)
    operation = _infer_operation(question, guidance.get("operation_aliases") or {})
    vessel_group = _infer_vessel_group(question, guidance)
    draft = _extract_draft(question)
    return {
        "loa": loa,
        "beam": _extract_beam(question),
        "draft": draft,
        "operation": operation,
        "vessel_group": vessel_group,
        "wind_component": wind_component,
        "wind_direction": wind_direction,
        "wind_label": wind_label,
        "strong_wind": _is_strong_wind(question),
        "entity_names": sorted(entity_names),
        "requested_tug_count": _extract_requested_tug_count(question),
        "bow_orientation": _infer_bow_orientation(question, entity_names),
        "has_no_bowthruster": bool(NO_BOW_RE.search(question or "")),
        "has_bowthruster": bool(HAS_BOW_RE.search(question or "")),
        "mentions_lisnave": bool(LISNAVE_RE.search(question or "")),
    }


def build_tug_operational_guidance_source(question: str, knowledge_dir: str) -> dict[str, Any] | None:
    if not looks_like_tug_decision_question(question):
        return None
    guidance = load_tug_guidance(knowledge_dir)
    if not guidance:
        return None

    context = _extract_context(question, guidance)
    applicable_rules = []
    applicable_rules.extend(_matrix_rules(guidance, context))
    no_bow_rule = _minimum_no_bow_rule(question, context["loa"], context["draft"], guidance)
    if no_bow_rule:
        applicable_rules.append(no_bow_rule)
    lisnave_rule = _lisnave_rule(question, context["loa"], guidance)
    if lisnave_rule:
        applicable_rules.append(lisnave_rule)
    applicable_rules.extend(_berth_minimum_rules(guidance, context))

    lines = [
        f"{guidance.get('title') or 'Regras praticas de rebocadores'}:",
        "Prioridade: esta regra pratica e o baseline operacional; a IT-016 confirma/agrava minimos legais, sobretudo DWT e cargas perigosas, mas nao deve reduzir esta recomendacao.",
    ]
    context_bits = []
    if context["vessel_group"]:
        group = (guidance.get("vessel_type_groups") or {}).get(context["vessel_group"], {})
        context_bits.append(f"tipo/grupo inferido: {group.get('label') or context['vessel_group']}")
    if context["operation"]:
        context_bits.append(f"operacao inferida: {context['operation']}")
    if context["wind_label"]:
        context_bits.append(f"vento inferido: {context['wind_label']}")
    if context["wind_direction"]:
        context_bits.append(f"direcao de vento inferida: {context['wind_direction']}")
    if context["loa"] is not None:
        context_bits.append(f"LOA inferido: {context['loa']:g} m")
    if context["beam"] is not None:
        context_bits.append(f"boca inferida: {context['beam']:g} m")
    if context["draft"] is not None:
        context_bits.append(f"calado inferido: {context['draft']:g} m")
    if context["entity_names"]:
        context_bits.append("terminal/cais inferido: " + ", ".join(context["entity_names"]))
    if context["requested_tug_count"]:
        context_bits.append(f"rebocadores pedidos/informados: {context['requested_tug_count']}")
    if context["bow_orientation"]:
        orientation_label = {
            "bow_south": "proa a sul",
            "stern_south": "popa a sul / proa a norte",
        }.get(context["bow_orientation"], context["bow_orientation"])
        context_bits.append(f"orientacao inferida: {orientation_label}")
    if context["strong_wind"]:
        context_bits.append("vento forte inferido")
    if context["has_no_bowthruster"]:
        context_bits.append("sem bowthruster")
    elif context["has_bowthruster"]:
        context_bits.append("com bowthruster")
    if context_bits:
        lines.append("Contexto extraido: " + "; ".join(context_bits) + ".")

    if applicable_rules:
        lines.append("Regras diretamente aplicaveis:")
        for item in dict.fromkeys(applicable_rules):
            lines.append(f"- {item}")

    positioning_rules = (
        _specific_positioning_rules(guidance, context)
        + _multi_tug_positioning_rules(guidance, context)
        + (guidance.get("positioning_rules") or [])
    )
    if positioning_rules:
        lines.append("Posicionamento pratico dos rebocadores:")
        for item in positioning_rules:
            lines.append(f"- {item}")

    lines.append("Regras meteorologicas criticas:")
    for item in guidance.get("weather_rules") or []:
        lines.append(f"- {item}")

    lines.append("Regras gerais a manter:")
    for item in (guidance.get("principles") or [])[:4]:
        lines.append(f"- {item}")
    for item in guidance.get("current_and_berthing_rules") or []:
        lines.append(f"- {item}")

    return {
        "source_id": "TUG1",
        "document": TUG_GUIDANCE_FILENAME,
        "chunk_id": 0,
        "score": 1.0,
        "retrieval_mode": "operational_tug_guidance",
        "snippet": "\n".join(lines),
        "text": "\n".join(lines),
    }
