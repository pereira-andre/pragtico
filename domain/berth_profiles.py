from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from typing import Any


PROFILE_FILENAME = "berth_profiles.json"

OVERVIEW_TERMS = {
    "conheces",
    "dizer",
    "fala",
    "falar",
    "gerais",
    "geral",
    "informacao",
    "informacoes",
    "resumo",
    "sabes",
    "sobre",
    "termos",
    "visao",
}
RULE_TERMS = {
    "condicao",
    "condicoes",
    "limitacao",
    "limitacoes",
    "limite",
    "limites",
    "regra",
    "regras",
    "restricao",
    "restricoes",
}
DIMENSION_TERMS = {
    "calado",
    "calados",
    "cabeços",
    "cabecos",
    "comprimento",
    "dimensao",
    "dimensoes",
    "duques",
    "loa",
    "metros",
    "profundidade",
    "sonda",
}
TUG_TERMS = {"reboque", "reboques", "rebocador", "rebocadores"}
SCALAR_QUESTION_TERMS = {
    "qual",
    "quanto",
    "quantos",
    "maximo",
    "maximos",
    "minimo",
    "minimos",
}
LOW_VALUE_ALIAS_TOKENS = {
    "cais",
    "terminal",
    "terminais",
    "setubal",
    "porto",
    "doca",
    "docas",
}


def _normalize_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", without_accents.lower())).strip()


def _compact_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_text(value))


def _tokenize(value: str | None) -> set[str]:
    return {token for token in _normalize_text(value).split() if len(token) > 2 and not token.isdigit()}


def _profile_path(knowledge_dir: str) -> str:
    return os.path.join(knowledge_dir, PROFILE_FILENAME)


def _file_signature(path: str) -> tuple[str, float]:
    try:
        return path, os.path.getmtime(path)
    except OSError:
        return path, 0.0


@lru_cache(maxsize=8)
def _load_profiles_cached(path: str, _mtime: float) -> tuple[dict[str, Any], ...]:
    if not path or not os.path.isfile(path):
        return ()
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    profiles = payload.get("profiles") if isinstance(payload, dict) else []
    if not isinstance(profiles, list):
        return ()
    normalized_profiles: list[dict[str, Any]] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        aliases = [str(alias or "").strip() for alias in profile.get("aliases", []) if str(alias or "").strip()]
        name = str(profile.get("name") or "").strip()
        document = str(profile.get("document") or "").strip()
        if name:
            aliases.append(name)
        if document:
            aliases.append(document)
        profile = dict(profile)
        profile["aliases"] = list(dict.fromkeys(aliases))
        normalized_profiles.append(profile)
    return tuple(normalized_profiles)


def load_berth_profiles(knowledge_dir: str) -> list[dict[str, Any]]:
    path, mtime = _file_signature(_profile_path(knowledge_dir))
    return [dict(profile) for profile in _load_profiles_cached(path, mtime)]


def _alias_score(question: str, profile: dict[str, Any]) -> float:
    normalized_question = _normalize_text(question)
    compact_question = _compact_text(question)
    best_score = 0.0
    question_tokens = _tokenize(question)
    for alias in profile.get("aliases", []) or []:
        alias_norm = _normalize_text(alias)
        alias_compact = _compact_text(alias)
        if not alias_norm or alias_norm in LOW_VALUE_ALIAS_TOKENS:
            continue
        alias_tokens = _tokenize(alias)
        signal_tokens = {token for token in alias_tokens if token not in LOW_VALUE_ALIAS_TOKENS}
        if not signal_tokens:
            continue
        score = 0.0
        if alias_norm in normalized_question:
            score = max(score, 2.0 + len(alias_norm) / 30)
        if alias_compact and len(alias_compact) >= 4 and alias_compact in compact_question:
            score = max(score, 2.0 + len(alias_compact) / 30)
        overlap = question_tokens & signal_tokens
        if overlap:
            score = max(score, len(overlap) / max(len(signal_tokens), 1))
        best_score = max(best_score, score)
    return best_score


def find_best_berth_profile(question: str, knowledge_dir: str) -> dict[str, Any] | None:
    profiles = load_berth_profiles(knowledge_dir)
    if not profiles:
        return None
    scored = []
    for profile in profiles:
        score = _alias_score(question, profile)
        if score <= 0:
            continue
        scored.append({"profile": profile, "score": round(score, 3)})
    if not scored:
        return None
    scored.sort(key=lambda item: item["score"], reverse=True)
    best = scored[0]
    second = scored[1] if len(scored) > 1 else None
    if best["score"] < 0.75:
        return None
    if second and best["score"] < 2.0 and (best["score"] - second["score"]) < 0.2:
        return None
    return best


def _profile_intent(question: str) -> str:
    tokens = _tokenize(question)
    if tokens & TUG_TERMS:
        return "tugs"
    if tokens & RULE_TERMS:
        return "restrictions"
    asks_scalar = bool(tokens & SCALAR_QUESTION_TERMS)
    if asks_scalar and tokens & DIMENSION_TERMS and not tokens & OVERVIEW_TERMS:
        return "scalar"
    if tokens & OVERVIEW_TERMS:
        return "overview"
    clean = _normalize_text(question)
    if clean.startswith(("o que sabes", "que sabes", "o que conheces")):
        return "overview"
    return "overview" if len(tokens) <= 6 else ""


def _first_item(profile: dict[str, Any], key: str) -> str:
    values = [str(item or "").strip() for item in profile.get(key, []) or [] if str(item or "").strip()]
    return values[0] if values else ""


def _join_items(values: list[str], *, limit: int = 3) -> str:
    clean_values = [str(item or "").strip().rstrip(".") for item in values if str(item or "").strip()]
    if not clean_values:
        return ""
    return "; ".join(clean_values[:limit]) + "."


def _document_code(document: str) -> str:
    match = re.match(r"([A-Z]+-\d{3})", str(document or ""), flags=re.IGNORECASE)
    return match.group(1).upper() if match else str(document or "").strip()


def build_berth_profile_answer(question: str, profile_match: dict[str, Any] | None) -> str:
    if not profile_match:
        return ""
    profile = profile_match.get("profile") or {}
    intent = _profile_intent(question)
    if not intent or intent == "scalar":
        return ""

    name = str(profile.get("name") or "").strip()
    document = _document_code(profile.get("document") or "")
    header = f"{name} ({document})" if document else name
    if not header:
        return ""

    if intent == "tugs":
        tug_guidance = _join_items(profile.get("tug_guidance", []) or [], limit=4)
        restrictions = _join_items(profile.get("restrictions", []) or [], limit=2)
        if not tug_guidance:
            return ""
        lines = [f"{header}: para rebocadores, a base é esta:"]
        lines.append(f"- Rebocadores: {tug_guidance}")
        if restrictions:
            lines.append(f"- Condicionantes do cais: {restrictions}")
        validation = str(profile.get("validation") or "").strip()
        if validation:
            lines.append(f"- Validação: {validation}.")
        return "\n".join(lines)

    if intent == "restrictions":
        lines = [f"{header}: restrições principais:"]
        for label, key, limit in (
            ("Limites", "vessel_limits", 3),
            ("Calado", "draft_rules", 3),
            ("Manobra", "maneuver_rules", 3),
            ("Noite", "night_rules", 2),
            ("Atenções", "restrictions", 4),
        ):
            text = _join_items(profile.get(key, []) or [], limit=limit)
            if text:
                lines.append(f"- {label}: {text}")
        validation = str(profile.get("validation") or "").strip()
        if validation:
            lines.append(f"- Validação: {validation}.")
        return "\n".join(lines)

    lines = [f"{header} em termos operacionais:"]
    overview = str(profile.get("overview") or "").strip()
    if overview:
        lines.append(f"- Função: {overview.rstrip('.')}.")
    dimension = _first_item(profile, "dimensions")
    if dimension:
        extra_dimension = _first_item({"dimensions": (profile.get("dimensions") or [])[1:]}, "dimensions")
        if extra_dimension:
            dimension = f"{dimension.rstrip('.')}; {extra_dimension.rstrip('.')}"
        lines.append(f"- Dimensões/limites: {dimension.rstrip('.')}.")
    draft = _join_items(profile.get("draft_rules", []) or [], limit=3)
    if draft:
        lines.append(f"- Calado: {draft}")
    maneuver = _join_items(profile.get("maneuver_rules", []) or [], limit=2)
    if maneuver:
        lines.append(f"- Manobra: {maneuver}")
    night = _join_items(profile.get("night_rules", []) or [], limit=2)
    if night:
        lines.append(f"- Noite: {night}")
    restrictions = _join_items(profile.get("restrictions", []) or [], limit=2)
    if restrictions:
        lines.append(f"- Restrições críticas: {restrictions}")
    validation = str(profile.get("validation") or "").strip()
    if validation:
        lines.append(f"- Validação: {validation}.")
    return "\n".join(lines)


def build_berth_profile_sources(profile_match: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not profile_match:
        return []
    profile = profile_match.get("profile") or {}
    name = str(profile.get("name") or "").strip()
    document = str(profile.get("document") or "").strip() or "Perfil estruturado de cais"
    snippets: list[str] = []
    for key in (
        "overview",
        "dimensions",
        "vessel_limits",
        "draft_rules",
        "maneuver_rules",
        "night_rules",
        "restrictions",
        "tug_guidance",
    ):
        value = profile.get(key)
        if isinstance(value, list):
            snippets.extend(str(item or "").strip() for item in value if str(item or "").strip())
        elif value:
            snippets.append(str(value).strip())
    snippet = f"Perfil estruturado de {name}: " + " ".join(dict.fromkeys(snippets))
    return [
        {
            "source_id": "BERTH1",
            "document": document,
            "chunk_id": 0,
            "score": profile_match.get("score", 0.95),
            "retrieval_mode": "berth_profile",
            "snippet": snippet,
            "text": snippet,
        }
    ]
