from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from typing import Any


SAFETY_LIMITS_FILENAME = "operational_safety_limits.json"
SAFETY_QUERY_RE = re.compile(
    r"\b(nevoeiro|nevoa|névoa|neblina|fog|mist|visibilidade|vento|rajada|"
    r"meteorologia|meteo|condicoes|condições|suspens\w*|suspender|adiar|"
    r"cancel\w*|abort\w*|segur\w*|risco\w*)\b",
    flags=re.IGNORECASE,
)
MANEUVER_QUERY_RE = re.compile(
    r"\b(manobra|manobras|navio|entrada|saida|saída|atracar|desatracar|"
    r"pilotagem|piloto|cais|doca|barra|reboque|rebocador|cabos?|amarra[cç][aã]o|"
    r"bow\s*thruster|bowthruster|h[eé]lice\s+de\s+proa)\b",
    flags=re.IGNORECASE,
)
EMERGENCY_RESPONSE_RE = re.compile(
    r"\b(emerg[eê]ncia|urg[eê]ncia|socorro|perigo|avaria|problema|blackout|apag[aã]o|"
    r"sem\s+m[aá]quina|perda\s+de\s+m[aá]quina|m[aá]quina\s+avariad\w*|"
    r"sem\s+governo|perda\s+de\s+governo|leme\s+avariad\w*|desgovernad\w*|"
    r"perd\w*\s+bow|bow\s*thruster\s+avariad\w*|h[eé]lice\s+de\s+proa\s+avariad\w*|"
    r"part\w*\s+cabos?|cabos?\s+partid\w*|part\w*\s+o\s+cabo|cabo\s+do\s+reboque|"
    r"encalh\w*|colid\w*|colis[aã]o|abalro\w*|"
    r"deriva|a\s+derivar|largar\s+ferro|fundear\s+de\s+emerg[eê]ncia)\b",
    flags=re.IGNORECASE,
)
EMERGENCY_STANDALONE_RE = re.compile(
    r"\b(blackout|apag[aã]o|sem\s+m[aá]quina|perd\w*\s+bow|part\w*\s+cabos?|cabos?\s+partid\w*|"
    r"cabo\s+do\s+reboque|encalh\w*|colid\w*|colis[aã]o|abalro\w*)\b",
    flags=re.IGNORECASE,
)


def _normalize_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", without_accents.lower())).strip()


def _limits_path(knowledge_dir: str) -> str:
    return os.path.join(knowledge_dir, SAFETY_LIMITS_FILENAME)


def _file_signature(path: str) -> tuple[str, float]:
    try:
        return path, os.path.getmtime(path)
    except OSError:
        return path, 0.0


@lru_cache(maxsize=8)
def _load_limits_cached(path: str, _mtime: float) -> dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def load_operational_safety_limits(knowledge_dir: str) -> dict[str, Any]:
    path, mtime = _file_signature(_limits_path(knowledge_dir))
    return dict(_load_limits_cached(path, mtime))


def looks_like_operational_safety_question(question: str) -> bool:
    text = question or ""
    if looks_like_emergency_response_question(text):
        return True
    if not SAFETY_QUERY_RE.search(text):
        return False
    return bool(MANEUVER_QUERY_RE.search(text) or re.search(r"\b(porto|setubal|setúbal)\b", text, re.IGNORECASE))


def looks_like_emergency_response_question(question: str) -> bool:
    text = question or ""
    if not EMERGENCY_RESPONSE_RE.search(text):
        return False
    if EMERGENCY_STANDALONE_RE.search(text):
        return True
    return bool(
        MANEUVER_QUERY_RE.search(text)
        or re.search(r"\b(porto|setubal|setúbal|vts|vhf|canal|rebocador|rebocadores)\b", text, re.IGNORECASE)
    )


def _safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _condition_has_fog(condition: str, guidance: dict[str, Any]) -> bool:
    clean_condition = _normalize_text(condition)
    if not clean_condition:
        return False
    fog_terms = guidance.get("fog_terms") or ["nevoeiro", "nevoa", "neblina", "fog", "mist"]
    return any(re.search(rf"\b{re.escape(_normalize_text(term))}\b", clean_condition) for term in fog_terms)


def evaluate_weather_safety(forecast: dict | None, guidance: dict[str, Any]) -> dict[str, Any]:
    current = (forecast or {}).get("current") or {}
    thresholds = guidance.get("thresholds") or {}
    suspend_above = _safe_float(thresholds.get("wind_suspend_above_kts")) or 30.0
    resume_below = _safe_float(thresholds.get("wind_resume_below_kts")) or 25.0
    fog_visibility = _safe_float(thresholds.get("fog_visibility_km_reference")) or 1.0

    wind_kts = _safe_float(current.get("wind_kts"))
    gust_kts = _safe_float(current.get("gust_kts"))
    strongest_wind = max([value for value in (wind_kts, gust_kts) if value is not None], default=None)
    visibility_km = _safe_float(current.get("vis_km"))
    condition = str(current.get("condition") or "")
    fog_detected = _condition_has_fog(condition, guidance)
    if visibility_km is not None and visibility_km <= fog_visibility:
        fog_detected = True

    suspended = False
    reasons: list[str] = []
    if fog_detected:
        suspended = True
        reasons.append("nevoeiro/visibilidade reduzida em porto")
    if strongest_wind is not None and strongest_wind > suspend_above:
        suspended = True
        reasons.append(f"vento/rajada {strongest_wind:g} kt superior a {suspend_above:g} kt")

    hold_after_wind_suspension = (
        strongest_wind is not None and resume_below <= strongest_wind <= suspend_above
    )

    return {
        "suspended": suspended,
        "reasons": reasons,
        "hold_after_wind_suspension": hold_after_wind_suspension,
        "wind_kts": wind_kts,
        "gust_kts": gust_kts,
        "strongest_wind_kts": strongest_wind,
        "visibility_km": visibility_km,
        "condition": condition,
        "wind_suspend_above_kts": suspend_above,
        "wind_resume_below_kts": resume_below,
        "fog_detected": fog_detected,
    }


def build_weather_safety_status_lines(forecast: dict | None, knowledge_dir: str) -> list[str]:
    guidance = load_operational_safety_limits(knowledge_dir)
    if not guidance or not forecast:
        return []
    status = evaluate_weather_safety(forecast, guidance)
    resume_below = status["wind_resume_below_kts"]
    if status["suspended"]:
        return [
            "Regra operacional de segurança:",
            "- Manobras suspensas neste momento: " + "; ".join(status["reasons"]) + ".",
            f"- Retoma: visibilidade restaurada e, se a suspensão foi por vento, vento abaixo de {resume_below:g} kt.",
        ]
    if status["hold_after_wind_suspension"]:
        return [
            "Regra operacional de segurança:",
            f"- Vento/rajada entre {resume_below:g} e {status['wind_suspend_above_kts']:g} kt.",
            f"- Se a suspensão por vento já foi acionada, manter suspenso até baixar abaixo de {resume_below:g} kt.",
        ]
    return []


def build_operational_safety_source(
    question: str,
    knowledge_dir: str,
    *,
    forecast: dict | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    guidance = load_operational_safety_limits(knowledge_dir)
    if not guidance:
        return None

    status = evaluate_weather_safety(forecast, guidance) if forecast else {}
    if not force and not status.get("suspended") and not looks_like_operational_safety_question(question):
        return None

    lines = [
        f"{guidance.get('title') or 'Limites operacionais de seguranca'}:",
    ]
    for rule in guidance.get("rules") or []:
        note = str(rule.get("note") or "").strip()
        if note:
            lines.append(f"- {note}")

    if status:
        if status.get("suspended"):
            lines.append(
                "Estado atual inferido: SUSPENDER todas as manobras; motivo(s): "
                + "; ".join(status["reasons"])
                + "."
            )
        elif status.get("hold_after_wind_suspension"):
            lines.append(
                "Estado atual inferido: vento/rajada ainda na zona de retencao; se a suspensao por vento ja foi acionada, manter suspenso ate ficar abaixo de "
                f"{status['wind_resume_below_kts']:g} kt."
            )
        else:
            lines.append("Estado atual inferido: sem limiar automatico de suspensao por nevoeiro ou vento nos dados live.")

    for item in guidance.get("response_guidance") or []:
        lines.append(f"- {item}")

    snippet = "\n".join(lines)
    return {
        "source_id": "SAFE1",
        "document": SAFETY_LIMITS_FILENAME,
        "chunk_id": 0,
        "score": 1.0,
        "retrieval_mode": "operational_safety_limits",
        "snippet": snippet,
        "text": snippet,
    }


def build_emergency_response_source(question: str, knowledge_dir: str) -> dict[str, Any] | None:
    guidance = load_operational_safety_limits(knowledge_dir)
    emergency = guidance.get("emergency_response") or {}
    if not emergency or not looks_like_emergency_response_question(question):
        return None

    clean_question = _normalize_text(question)
    scenario_rules = []
    for item in emergency.get("scenarios") or []:
        terms = [_normalize_text(term) for term in item.get("trigger_terms") or [] if _normalize_text(term)]
        if terms and not any(term in clean_question for term in terms):
            continue
        scenario_rules.append(item)

    lines = [
        emergency.get("title") or "Resposta imediata a emergencia operacional",
        "Prioridade: emergencia tem precedencia sobre regras normais de rebocadores.",
    ]
    if scenario_rules:
        lines.append("Cenario identificado:")
        for item in sorted(scenario_rules, key=lambda value: value.get("priority") or 999):
            note = str(item.get("note") or "").strip()
            if note:
                lines.append(f"- {note}")

    rules = sorted(emergency.get("rules") or [], key=lambda item: item.get("priority") or 999)
    if rules:
        lines.append("Acoes comuns sempre:")
        for item in rules:
            note = str(item.get("note") or "").strip()
            if note:
                lines.append(f"- {note}")
    guidance_items = emergency.get("response_guidance") or []
    if guidance_items:
        lines.append("Orientacao de resposta:")
        for item in guidance_items:
            lines.append(f"- {item}")

    snippet = "\n".join(lines)
    return {
        "source_id": "SAFE_EMERGENCY",
        "document": SAFETY_LIMITS_FILENAME,
        "chunk_id": 2,
        "score": 1.0,
        "retrieval_mode": "operational_emergency_response",
        "snippet": snippet,
        "text": snippet,
    }
