from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from typing import Any


TUG_GUIDANCE_FILENAME = "tug_operational_guidance.json"
TUG_QUERY_RE = re.compile(r"\b(reboque|reboques|rebocador|rebocadores)\b", flags=re.IGNORECASE)
TUG_DECISION_RE = re.compile(
    r"\b(quantos|quantas|numero|n[uú]mero|precis\w*|necess[aá]ri\w*|"
    r"recomend\w*|aconselh\w*|suger\w*|suficient\w*|bast\w*|cheg\w*)\b",
    flags=re.IGNORECASE,
)
TUG_CONTEXT_RE = re.compile(
    r"\b(entrada|entrar|atracar|atracacao|saida|sair|desatracar|desatracacao|"
    r"vento|corrente|mare|roro|ro\s*ro|graneleiro|reefer|estilha|contentores?|"
    r"lisnave|mitrena|bow\s*thruster|bowthruster|h[eé]lice de proa)\b",
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
NO_BOW_RE = re.compile(
    r"\b(?:sem|s/?|nao tem|não tem|avariado|inoperacional)\s+"
    r"(?:bow\s*thruster|bowthruster|h[eé]lice de proa|hpr)\b",
    flags=re.IGNORECASE,
)
HAS_BOW_RE = re.compile(
    r"\b(?:com|tem)\s+(?:bow\s*thruster|bowthruster|h[eé]lice de proa|hpr)\b",
    flags=re.IGNORECASE,
)
LISNAVE_RE = re.compile(r"\b(lisnave|mitrena|doca\s*\d{2}|d\d{2}|hidrolift|eclusa)\b", flags=re.IGNORECASE)


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


def _infer_wind_component(question: str) -> tuple[str, str]:
    clean = _normalize_text(question)
    if re.search(r"\b(sudoeste|sw)\b", clean):
        return "south", "SW forte / caso critico"
    if re.search(r"\b(sul|vento s)\b", clean):
        return "south", "S"
    if re.search(r"\b(oeste|west|vento w)\b", clean):
        return "south", "W tratado como S fraco"
    if re.search(r"\b(norte|vento n)\b", clean):
        return "north", "N"
    if re.search(r"\b(este|leste|east|vento e)\b", clean):
        return "north", "E tratado como N fraco"
    if re.search(r"\b(nevoeiro|nevoa|nevoa)\b", clean):
        return "south", "nevoeiro: risco de SW forte"
    return "", ""


def _minimum_no_bow_rule(question: str, loa: float | None, guidance: dict[str, Any]) -> str:
    if loa is None or not NO_BOW_RE.search(question or ""):
        return ""
    for item in guidance.get("no_bowthruster_minimums") or []:
        condition = str(item.get("condition") or "")
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
        if "150 m < LOA <= 199" in condition and 150 < loa <= 199:
            candidates.append(note)
        if "200 m <= LOA <= 250" in condition and 200 <= loa <= 250:
            candidates.append(note)
        if "LOA > 250" in condition and loa > 250:
            candidates.append(note)
    return " ".join(dict.fromkeys(item for item in candidates if item))


def _base_matrix_rule(
    guidance: dict[str, Any],
    vessel_group: str,
    wind_component: str,
    operation: str,
) -> str:
    if not vessel_group or not wind_component or not operation:
        return ""
    for item in guidance.get("base_matrix") or []:
        if (
            item.get("vessel_group") == vessel_group
            and item.get("wind_component") == wind_component
            and item.get("operation") == operation
        ):
            return str(item.get("note") or "")
    return ""


def _extract_context(question: str, guidance: dict[str, Any]) -> dict[str, Any]:
    loa = _extract_loa(question)
    wind_component, wind_label = _infer_wind_component(question)
    operation = _infer_operation(question, guidance.get("operation_aliases") or {})
    vessel_group = _infer_vessel_group(question, guidance)
    return {
        "loa": loa,
        "beam": _extract_beam(question),
        "operation": operation,
        "vessel_group": vessel_group,
        "wind_component": wind_component,
        "wind_label": wind_label,
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
    matrix_rule = _base_matrix_rule(
        guidance,
        context["vessel_group"],
        context["wind_component"],
        context["operation"],
    )
    if matrix_rule:
        applicable_rules.append(matrix_rule)
    no_bow_rule = _minimum_no_bow_rule(question, context["loa"], guidance)
    if no_bow_rule:
        applicable_rules.append(no_bow_rule)
    lisnave_rule = _lisnave_rule(question, context["loa"], guidance)
    if lisnave_rule:
        applicable_rules.append(lisnave_rule)

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
    if context["loa"] is not None:
        context_bits.append(f"LOA inferido: {context['loa']:g} m")
    if context["beam"] is not None:
        context_bits.append(f"boca inferida: {context['beam']:g} m")
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

    lines.append("Regras meteorologicas criticas:")
    for item in (guidance.get("weather_rules") or [])[:4]:
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
