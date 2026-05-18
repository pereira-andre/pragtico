"""Aggregators that power the admin bot dashboard: health, signals, exceptions, sources."""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from core import services
from core.bot_settings import load_bot_settings
from core.feedback_governance import feedback_governance_state
from domain.berth_profiles import PROFILE_FILENAME, load_berth_profiles
from domain.knowledge_companions import companion_directory, load_document_companion
from domain.knowledge_evals import (
    default_eval_cases_path,
    evaluate_companion_cases,
    load_eval_cases_from_dir,
    load_eval_cases_from_store,
)
from domain.operational_safety import SAFETY_LIMITS_FILENAME, load_operational_safety_limits
from domain.practice_experience import (
    PRACTICE_EXPERIENCE_KNOWLEDGE_FILENAME,
    PRACTICE_EXPERIENCE_ACTIVE_STATUSES,
    list_practice_experience_records,
)
from domain.tug_guidance import TUG_GUIDANCE_FILENAME, load_tug_guidance

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Small utilities


def _iso_to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _window_bounds(hours: int) -> tuple[datetime, datetime]:
    now = _now_utc()
    return now - timedelta(hours=hours), now


def _pct_change(current: int, previous: int) -> int:
    if previous <= 0:
        return 100 if current > 0 else 0
    return round(((current - previous) / previous) * 100)


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"\s+", " ", text.encode("ascii", "ignore").decode("ascii").strip().lower())


def _knowledge_dir() -> str:
    store = getattr(services, "store", None)
    return getattr(store, "knowledge_dir", "") or getattr(services, "KNOWLEDGE_DIR", "") or ""


def _safe_list_documents() -> list[dict]:
    store = getattr(services, "store", None)
    if not store:
        return []
    try:
        return store.list_documents()
    except Exception:
        logger.exception("Falha a listar documentos.")
        return []


def _safe_reviewable_messages(limit: int = 400) -> list[dict]:
    store = getattr(services, "store", None)
    if not store:
        return []
    try:
        return store.list_reviewable_chat_messages(limit=limit)
    except Exception:
        logger.exception("Falha a listar mensagens reviewable.")
        return []


def _safe_maneuver_cases(limit: int = 400) -> list[dict]:
    store = getattr(services, "store", None)
    if not store:
        return []
    try:
        return store.list_maneuver_cases(limit=limit)
    except Exception:
        logger.exception("Falha a listar maneuver cases.")
        return []


def _safe_feedback_eval_cases() -> list[dict]:
    store = getattr(services, "store", None)
    if not store:
        return []
    try:
        return load_eval_cases_from_store(store)
    except Exception:
        logger.exception("Falha a listar feedback eval cases.")
        return []


def _safe_practice_records() -> list[dict]:
    store = getattr(services, "store", None)
    if not store:
        return []
    try:
        return list_practice_experience_records(store)
    except Exception:
        logger.exception("Falha a listar experiência prática.")
        return []


def _component_state(active: bool, *, partial: bool = False) -> str:
    if active:
        return "online"
    return "degraded" if partial else "offline"


def _component_state_label(state: str) -> str:
    if state == "online":
        return "Ativo"
    if state == "degraded":
        return "Parcial"
    return "Sem dados"


def _enabled_state(enabled: bool, *, degraded: bool = False) -> str:
    if not enabled:
        return "offline"
    return "degraded" if degraded else "online"


def _service_enabled(service: Any) -> bool:
    if service is None:
        return False
    enabled = getattr(service, "enabled", True)
    return bool(enabled() if callable(enabled) else enabled)


def _tide_service_enabled() -> bool:
    tide_service = getattr(services, "tide_service", None)
    return bool(tide_service and getattr(tide_service, "csv_path", ""))


def _live_operational_service_items() -> list[tuple[str, bool]]:
    return [
        ("marés", _tide_service_enabled()),
        ("meteo", _service_enabled(getattr(services, "weather_service", None))),
        ("ondulação", _service_enabled(getattr(services, "wave_service", None))),
        ("avisos", _service_enabled(getattr(services, "local_warning_service", None))),
    ]


def _count_live_operational_services() -> tuple[int, int]:
    items = _live_operational_service_items()
    return sum(1 for _, enabled in items if enabled), len(items)


def _feedback_source_label(source: str) -> str:
    clean_source = str(source or "").strip().lower()
    if clean_source == "whatsapp":
        return "WhatsApp"
    if clean_source == "web":
        return "Site"
    if clean_source == "api":
        return "API"
    return "Operador"


def _component_coverage_pct(components: Iterable[dict[str, Any]]) -> int:
    items = list(components)
    if not items:
        return 0
    score = 0.0
    for item in items:
        state = str(item.get("state") or "")
        if state == "online":
            score += 1.0
        elif state == "degraded":
            score += 0.55
    return round((score / len(items)) * 100)


def _build_tuning_component(
    *,
    label: str,
    state: str,
    source_name: str,
    runtime_hook: str,
    detail: str,
    facts: Iterable[str],
    action_url: str = "",
    action_label: str = "Abrir",
) -> dict[str, Any]:
    return {
        "label": label,
        "state": state,
        "state_label": _component_state_label(state),
        "source_name": source_name,
        "runtime_hook": runtime_hook,
        "detail": detail,
        "facts": [str(item).strip() for item in facts if str(item or "").strip()],
        "action_url": action_url,
        "action_label": action_label,
    }


def _build_tuning_group(
    *,
    group_id: str,
    title: str,
    description: str,
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    online_total = sum(1 for item in components if item.get("state") == "online")
    degraded_total = sum(1 for item in components if item.get("state") == "degraded")
    offline_total = sum(1 for item in components if item.get("state") == "offline")
    return {
        "id": group_id,
        "title": title,
        "description": description,
        "components": components,
        "coverage_pct": _component_coverage_pct(components),
        "online_total": online_total,
        "degraded_total": degraded_total,
        "offline_total": offline_total,
    }


# ----------------------------------------------------------------------------
# Learning signals


_STOPWORDS_TOPICS = {
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
    "e", "em", "na", "nas", "no", "nos", "o", "os", "ou", "para", "por",
    "que", "se", "um", "uma", "uns", "umas", "qual", "quais", "quando",
    "onde", "quem", "sao", "sim", "nao", "tem", "ter", "foi", "ser",
    "podes", "pode", "dizer", "saber",
}


def _topics_from_question(question: str) -> list[str]:
    tokens = re.findall(r"[a-zà-ÿ0-9]{3,}", _normalize_text(question))
    topics: list[str] = []
    for token in tokens:
        if token in _STOPWORDS_TOPICS:
            continue
        if len(token) < 3:
            continue
        topics.append(token)
    return topics


def _normalized_chat_feedback_status(message: dict) -> str:
    status = (message.get("feedback_status") or "").strip().lower()
    if status == "review" and (message.get("feedback_correction") or "").strip():
        return "corrected"
    return status


def build_learning_signals(*, window_hours: int = 168) -> dict:
    store = getattr(services, "store", None)
    if not store:
        return _empty_signals(window_hours)

    window_start, window_end = _window_bounds(window_hours)
    previous_start = window_start - (window_end - window_start)

    try:
        messages = store.list_reviewable_chat_messages(limit=400)
    except Exception:
        logger.exception("Falha a listar mensagens para sinais de aprendizagem.")
        messages = []
    try:
        cases = store.list_maneuver_cases(limit=400)
    except Exception:
        logger.exception("Falha a listar casos para sinais de aprendizagem.")
        cases = []

    positives_current = 0
    positives_previous = 0
    negatives_current = 0
    negatives_previous = 0
    recent_events: list[dict] = []
    topic_counter: Counter[str] = Counter()

    for msg in messages:
        feedback_status = _normalized_chat_feedback_status(msg)
        if feedback_status == "ignored":
            continue
        feedback_updated_at = _iso_to_datetime(msg.get("feedback_updated_at"))
        created_at = _iso_to_datetime(msg.get("created_at"))
        question = msg.get("question") or ""
        if question:
            for topic in _topics_from_question(question)[:4]:
                topic_counter[topic] += 1

        if feedback_status in {"approved", "corrected"}:
            if feedback_updated_at and window_start <= feedback_updated_at <= window_end:
                positives_current += 1
                recent_events.append(
                    {
                        "type": "positive",
                        "label": "Correção guardada" if feedback_status == "corrected" else "Resposta aprovada",
                        "detail": question or (msg.get("content") or "")[:120],
                        "timestamp": feedback_updated_at.isoformat(),
                    }
                )
            elif feedback_updated_at and previous_start <= feedback_updated_at < window_start:
                positives_previous += 1
        elif feedback_status == "review":
            if feedback_updated_at and window_start <= feedback_updated_at <= window_end:
                negatives_current += 1
                recent_events.append(
                    {
                        "type": "negative",
                        "label": "Bloqueio / correção pedida",
                        "detail": question or (msg.get("content") or "")[:120],
                        "timestamp": feedback_updated_at.isoformat(),
                    }
                )
            elif feedback_updated_at and previous_start <= feedback_updated_at < window_start:
                negatives_previous += 1

    approved_maneuvers = 0
    avoided_maneuvers = 0
    for case in cases:
        feedback_status = (case.get("feedback_status") or "").strip().lower()
        feedback_updated_at = _iso_to_datetime(case.get("feedback_updated_at"))
        latest_event = _iso_to_datetime(case.get("latest_event_at"))
        reference_code = case.get("reference_code") or case.get("vessel_name") or "Manobra"
        if feedback_status == "approved":
            if feedback_updated_at and window_start <= feedback_updated_at <= window_end:
                positives_current += 1
                approved_maneuvers += 1
                recent_events.append(
                    {
                        "type": "positive",
                        "label": "Manobra validada",
                        "detail": f"{reference_code} · {case.get('maneuver_type_label', 'Manobra')}",
                        "timestamp": feedback_updated_at.isoformat(),
                    }
                )
            elif feedback_updated_at and previous_start <= feedback_updated_at < window_start:
                positives_previous += 1
        elif feedback_status == "avoid":
            avoided_maneuvers += 1
            if feedback_updated_at and window_start <= feedback_updated_at <= window_end:
                negatives_current += 1
                recent_events.append(
                    {
                        "type": "negative",
                        "label": "Padrão a evitar",
                        "detail": f"{reference_code} · {case.get('maneuver_type_label', 'Manobra')}",
                        "timestamp": feedback_updated_at.isoformat(),
                    }
                )
            elif feedback_updated_at and previous_start <= feedback_updated_at < window_start:
                negatives_previous += 1
        elif not feedback_status and latest_event and window_start <= latest_event <= window_end:
            # Manobras concluídas recentemente sem alerta contam como sinal positivo implícito
            if (case.get("current_state") or "").strip().lower() == "completed":
                positives_current += 1

    recent_events.sort(key=lambda item: item.get("timestamp") or "", reverse=True)

    trending_topics = [
        {"topic": topic, "count": count}
        for topic, count in topic_counter.most_common(8)
    ]

    return {
        "window_hours": window_hours,
        "positives": {
            "total": positives_current,
            "previous_total": positives_previous,
            "trend_pct": _pct_change(positives_current, positives_previous),
            "approved_maneuvers": approved_maneuvers,
        },
        "negatives": {
            "total": negatives_current,
            "previous_total": negatives_previous,
            "trend_pct": _pct_change(negatives_current, negatives_previous),
            "avoided_patterns": avoided_maneuvers,
        },
        "recent_events": recent_events[:8],
        "trending_topics": trending_topics,
    }


def _empty_signals(window_hours: int) -> dict:
    return {
        "window_hours": window_hours,
        "positives": {"total": 0, "previous_total": 0, "trend_pct": 0, "approved_maneuvers": 0},
        "negatives": {"total": 0, "previous_total": 0, "trend_pct": 0, "avoided_patterns": 0},
        "recent_events": [],
        "trending_topics": [],
    }


# ----------------------------------------------------------------------------
# Exceptions — the admin queue, limited to items that actually need attention


def build_exceptions(*, limit: int = 10) -> dict:
    store = getattr(services, "store", None)
    items: list[dict] = []
    if not store:
        return {"items": items, "total": 0}

    try:
        messages = store.list_reviewable_chat_messages(limit=200)
    except Exception:
        messages = []
    try:
        cases = store.list_maneuver_cases(limit=200)
    except Exception:
        cases = []

    for msg in messages:
        if _normalized_chat_feedback_status(msg) == "review":
            message_id = str(msg.get("id") or "").strip()
            items.append(
                {
                    "type": "correction_review",
                    "severity": "high",
                    "label": "Correção bloqueada",
                    "detail": msg.get("question") or (msg.get("content") or "")[:120],
                    "url": (
                        "/admin/casebooks?chat_feedback=review&case_feedback=all"
                        f"#chat-{quote(message_id, safe='')}"
                    ),
                }
            )

    outlier_threshold = load_bot_settings().get("outlier_review_threshold", 0.85)
    # Outliers simples: manobras com report_note muito curta ou com flags de decisão
    for case in cases:
        decision_flags = (case.get("outcome_snapshot") or {}).get("decision_flags") or []
        if decision_flags and (case.get("feedback_status") or "").strip().lower() not in {"approved", "avoid", "review"}:
            items.append(
                {
                    "type": "case_outlier",
                    "severity": "medium",
                    "label": "Caso com alertas operacionais",
                    "detail": f"{case.get('reference_code') or 'Manobra'} · {', '.join(decision_flags[:2])}",
                    "url": f"/port-calls/{case.get('port_call_id', '')}/maneuvers/{case.get('maneuver_id', '')}",
                }
            )

    # Documentos sem companion são uma exceção (o bot não tem resposta estruturada)
    try:
        documents = store.list_documents()
    except Exception:
        documents = []
    knowledge_dir = getattr(store, "knowledge_dir", "") or getattr(services, "KNOWLEDGE_DIR", "")
    if knowledge_dir:
        missing_companion = 0
        missing_names: list[str] = []
        for doc in documents:
            name = doc.get("name", "")
            if not name:
                continue
            try:
                companion = load_document_companion(name, knowledge_dir)
            except Exception:
                companion = None
            if not companion:
                missing_companion += 1
                if len(missing_names) < 3:
                    missing_names.append(name)
        if missing_companion:
            items.append(
                {
                    "type": "companion_missing",
                    "severity": "low",
                    "label": f"{missing_companion} documento(s) sem Q&A estruturado",
                    "detail": ", ".join(missing_names) + (" …" if missing_companion > 3 else ""),
                    "url": "/admin/documents",
                }
            )

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda item: severity_rank.get(item.get("severity"), 99))
    return {
        "items": items[:limit],
        "total": len(items),
        "severity_counts": {
            "high": sum(1 for item in items if item.get("severity") == "high"),
            "medium": sum(1 for item in items if item.get("severity") == "medium"),
            "low": sum(1 for item in items if item.get("severity") == "low"),
        },
    }


# ----------------------------------------------------------------------------
# Sources — knowledge fountain the bot drinks from


_SOURCE_DEFS = (
    {
        "id": "documents",
        "label": "Documentos (IT/RG/P)",
        "description": "Regulamentos e instruções internas do Porto de Setúbal.",
    },
    {
        "id": "companions",
        "label": "Q&A estruturado",
        "description": "Companions por documento com perguntas/respostas curadas.",
    },
    {
        "id": "berth_profiles",
        "label": "Perfis de cais",
        "description": "Regras por cais/terminal, limites de calado e janelas de manobra.",
    },
    {
        "id": "operational_data",
        "label": "Escalas e atividade do porto",
        "description": (
            "Escalas e manobras registadas no portal; marés, meteorologia, ondulação e avisos "
            "aparecem no monitor live."
        ),
    },
    {
        "id": "practice",
        "label": "Experiência prática",
        "description": "Padrões derivados de manobras reais importadas de Excel.",
    },
    {
        "id": "evals",
        "label": "Evals de qualidade",
        "description": "Casos fixos + correções promovidas que testam a cobertura do bot.",
    },
)


def build_sources_snapshot() -> list[dict]:
    store = getattr(services, "store", None)
    knowledge_dir = getattr(store, "knowledge_dir", "") or getattr(services, "KNOWLEDGE_DIR", "") or ""

    documents: list[dict] = []
    try:
        documents = store.list_documents() if store else []
    except Exception:
        logger.exception("Falha a listar documentos para snapshot de fontes.")

    companions_dir = companion_directory(knowledge_dir) if knowledge_dir else ""
    companions_count = 0
    resolved_companions = 0
    if companions_dir and os.path.isdir(companions_dir):
        companions_count = sum(1 for name in os.listdir(companions_dir) if name.lower().endswith(".json"))
    for doc in documents:
        try:
            if knowledge_dir and load_document_companion(doc.get("name", ""), knowledge_dir):
                resolved_companions += 1
        except Exception:
            pass

    berth_profiles_total = 0
    berth_path = Path(knowledge_dir) / "berth_profiles.json" if knowledge_dir else None
    if berth_path and berth_path.exists():
        try:
            import json as _json
            payload = _json.loads(berth_path.read_text(encoding="utf-8"))
            berth_profiles_total = len(payload.get("profiles") or [])
        except Exception:
            berth_profiles_total = 0

    evals_static = 0
    evals_feedback = 0
    if knowledge_dir:
        evals_static = len(load_eval_cases_from_dir(os.path.join(knowledge_dir, "evals")))
    if store:
        try:
            evals_feedback = len(load_eval_cases_from_store(store))
        except Exception:
            evals_feedback = 0

    practice_total = 0
    practice_path = Path(knowledge_dir) / "practice_maneuver_experience.json" if knowledge_dir else None
    if practice_path and practice_path.exists():
        try:
            import json as _json
            payload = _json.loads(practice_path.read_text(encoding="utf-8"))
            practice_total = len(payload) if isinstance(payload, list) else len(payload.get("records") or [])
        except Exception:
            practice_total = 0

    port_calls_total = 0
    port_activity_available = False
    try:
        if store:
            port_activity = store.get_port_activity_snapshot(window_days=3650)
            port_activity_available = True
            port_calls_total = len(port_activity.get("arrivals", []) or [])
    except Exception:
        port_calls_total = 0
        port_activity_available = False

    snapshot: dict[str, dict] = {}
    for source in _SOURCE_DEFS:
        snapshot[source["id"]] = {**source, "count": 0, "coverage_pct": 0, "state": "offline", "action_url": ""}

    if documents:
        snapshot["documents"].update(
            count=len(documents),
            state="online",
            action_url="/admin/documents",
            meta=f"{len(documents)} ficheiros indexados",
        )
    if companions_count:
        coverage = (
            round((resolved_companions / len(documents)) * 100)
            if documents else 0
        )
        snapshot["companions"].update(
            count=companions_count,
            coverage_pct=coverage,
            state="online" if coverage >= 70 else "degraded" if coverage else "offline",
            action_url="/admin/documents",
            meta=f"{resolved_companions}/{len(documents)} documentos cobertos",
        )
    if berth_profiles_total:
        snapshot["berth_profiles"].update(
            count=berth_profiles_total,
            state="online",
            action_url="/admin/documents",
            meta=f"{berth_profiles_total} perfis",
        )
    if practice_total:
        snapshot["practice"].update(
            count=practice_total,
            state="online",
            action_url="/admin/bot#sources",
            meta=f"{practice_total} padrões importados",
        )
    snapshot["operational_data"].update(
        count=port_calls_total,
        state="online" if port_activity_available else "offline",
        action_url="/port-calls/register",
        meta=(
            f"{port_calls_total} escala(s) resolvidas"
            if port_calls_total
            else "Portal acessível; sem escalas resolvidas."
        ),
    )
    snapshot["evals"].update(
        count=evals_static + evals_feedback,
        state="online" if (evals_static + evals_feedback) else "offline",
        action_url="/admin/bot#quality",
        meta=f"{evals_static} fixos + {evals_feedback} promovidos",
    )

    return [snapshot[src["id"]] for src in _SOURCE_DEFS]


# ----------------------------------------------------------------------------
# Quality — evaluation run + health score


def build_quality_snapshot() -> dict:
    store = getattr(services, "store", None)
    knowledge_dir = getattr(store, "knowledge_dir", "") or getattr(services, "KNOWLEDGE_DIR", "") or ""

    static_cases = load_eval_cases_from_dir(os.path.join(knowledge_dir, "evals")) if knowledge_dir else []
    feedback_cases = load_eval_cases_from_store(store) if store else []
    static_cases = [
        {
            **case,
            "origin": "static_eval",
            "origin_label": "Eval fixo",
        }
        for case in static_cases
    ]
    feedback_cases = [
        {
            **case,
            "origin": "feedback_promoted",
            "origin_label": "Feedback promovido",
        }
        for case in feedback_cases
    ]

    seen: set[tuple[str, str]] = set()
    by_key: dict[tuple[str, str], dict] = {}
    deduped: list[dict] = []
    for case in static_cases + feedback_cases:
        key = (
            (case.get("document") or "").strip().lower(),
            (case.get("question") or "").strip().lower(),
        )
        if key in seen:
            existing = by_key.get(key)
            if existing and existing.get("origin") != case.get("origin"):
                existing["origin"] = "mixed"
                existing["origin_label"] = "Eval fixo + feedback"
            continue
        seen.add(key)
        by_key[key] = case
        deduped.append(case)

    results = evaluate_companion_cases(deduped, knowledge_dir) if (deduped and knowledge_dir) else []
    passed = sum(1 for item in results if item.get("passed"))
    failed = [item for item in results if not item.get("passed")]
    pass_rate = round((passed / len(results)) * 100) if results else 0
    eval_type_labels = {
        "companion": "Companions/RAG curado",
        "direct_operational": "Decisão operacional direta",
        "full_pipeline": "Pipeline completo",
    }
    type_summary: dict[str, dict] = {}
    for item in results:
        eval_type = str(item.get("eval_type") or "companion").strip() or "companion"
        bucket = type_summary.setdefault(
            eval_type,
            {
                "type": eval_type,
                "label": eval_type_labels.get(eval_type, eval_type.replace("_", " ")),
                "total": 0,
                "passed": 0,
                "failed": 0,
            },
        )
        bucket["total"] += 1
        if item.get("passed"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
    type_rows = []
    for data in type_summary.values():
        total = data["total"] or 1
        state = "online" if data["failed"] == 0 and data["total"] else "degraded" if data["total"] else "offline"
        type_rows.append({**data, "coverage_pct": round((data["passed"] / total) * 100), "state": state})
    type_rows.sort(key=lambda item: (-item["failed"], item["label"]))

    def _scenario_pack_label(item: dict) -> str:
        text = _normalize_text(f"{item.get('document', '')} {item.get('question', '')}")
        eval_type = str(item.get("eval_type") or "companion")
        if eval_type == "direct_operational":
            if any(token in text for token in ("rebocador", "rebocadores", "bowthruster", "roro")):
                return "Rebocadores e vento"
            if any(token in text for token in ("visibilidade", "nevoeiro", "vento", "mare", "maré")):
                return "Limites ambientais"
            return "Decisões operacionais diretas"
        if any(token in text for token in ("rebocador", "rebocadores", "it-016")):
            return "Regras de rebocadores"
        if any(token in text for token in ("calado", "barra", "fundeadouro")):
            return "Calados, barra e fundeadouros"
        if any(token in text for token in ("lisnave", "agulhas", "noite")):
            return "Lisnave e restrições especiais"
        if any(token in text for token in ("eco", "tanquisado", "secil", "sapec", "teporset", "termitrena")):
            return "Cais e terminais sensíveis"
        return "RAG documental geral"

    scenario_summary: dict[str, dict] = {}
    for item in results:
        label = _scenario_pack_label(item)
        bucket = scenario_summary.setdefault(
            label,
            {
                "label": label,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "samples": [],
            },
        )
        bucket["total"] += 1
        if item.get("passed"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
        if len(bucket["samples"]) < 3:
            bucket["samples"].append(str(item.get("question") or ""))
    scenario_pack_rows = []
    for data in scenario_summary.values():
        total = data["total"] or 1
        scenario_pack_rows.append(
            {
                **data,
                "coverage_pct": round((data["passed"] / total) * 100),
                "state": "online" if data["failed"] == 0 and data["total"] else "degraded",
            }
        )
    scenario_pack_rows.sort(key=lambda item: (-item["failed"], item["label"]))

    protected_candidates = [
        item
        for item in results
        if str(item.get("eval_type") or "companion") != "companion"
        or str(item.get("expected_answer_origin") or "").strip()
    ]
    protected_rows = []
    for item in protected_candidates[:12]:
        protected_rows.append(
            {
                "document": item.get("document", ""),
                "question": item.get("question", ""),
                "eval_type": item.get("eval_type") or "companion",
                "answer_origin": item.get("answer_origin") or "",
                "expected_answer_origin": item.get("expected_answer_origin") or "",
                "passed": bool(item.get("passed")),
                "state": "online" if item.get("passed") else "degraded",
            }
        )
    protected_total = len(protected_candidates)
    protected_passed = sum(1 for item in protected_candidates if item.get("passed"))

    document_summary: dict[str, dict] = {}
    for case in deduped:
        name = (case.get("document") or "").strip()
        if not name:
            continue
        bucket = document_summary.setdefault(
            name,
            {
                "document": name,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "feedback": 0,
                "static": 0,
                "action_url": "/admin/documents",
            },
        )
        bucket["total"] += 1
        origin = case.get("origin")
        if origin == "feedback_promoted":
            bucket["feedback"] += 1
        elif origin == "mixed":
            bucket["feedback"] += 1
            bucket["static"] += 1
        else:
            bucket["static"] += 1
    for case in feedback_cases:
        name = (case.get("document") or "").strip()
        if not name:
            continue
        document_summary.setdefault(
            name,
            {
                "document": name,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "feedback": 0,
                "static": 0,
                "action_url": "/admin/documents",
            },
        )
    for result in results:
        name = (result.get("document") or "").strip()
        if not name:
            continue
        if name not in document_summary:
            document_summary[name] = {
                "document": name,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "feedback": 0,
                "static": 0,
                "action_url": "/admin/documents",
            }
        if result.get("passed"):
            document_summary[name]["passed"] += 1
        else:
            document_summary[name]["failed"] += 1

    document_rows = []
    for data in document_summary.values():
        total = data["total"] or 1
        coverage = round((data["passed"] / total) * 100)
        state = "online" if data["failed"] == 0 and data["total"] else "degraded" if data["total"] else "offline"
        document_rows.append({**data, "coverage_pct": coverage, "state": state})
    document_rows.sort(key=lambda item: (-item["failed"], -item["feedback"], item["document"]))

    failure_rows = []
    for item in failed[:10]:
        missing = list(item.get("missing_substrings") or []) or list(item.get("missing_terms") or [])[:4]
        expected_answer = str(item.get("expected_answer") or "").strip()
        current_answer = str(item.get("answer") or "").strip()
        failure_rows.append(
            {
                "document": item.get("document", ""),
                "question": item.get("question", ""),
                "missing_summary": ", ".join(missing) or "Resposta vazia ou fora do esperado.",
                "eval_type": item.get("eval_type") or "companion",
                "answer_origin": item.get("answer_origin") or "",
                "expected_answer_origin": item.get("expected_answer_origin") or "",
                "expected_summary": expected_answer[:420],
                "current_answer": current_answer[:420],
                "origin": item.get("origin") or "static_eval",
                "origin_label": item.get("origin_label") or "Eval fixo",
                "term_coverage_pct": round(float(item.get("term_coverage") or 0) * 100),
                "source_message_id": item.get("source_message_id", ""),
                "updated_by": item.get("updated_by", ""),
                "action_url": "/admin/documents",
            }
        )

    feedback_source_counter: Counter[str] = Counter()
    feedback_recent_rows = []
    for item in feedback_cases:
        source_label = _feedback_source_label(item.get("source", ""))
        feedback_source_counter[source_label] += 1
        expected_parts = [
            str(value).strip()
            for value in (item.get("expected_substrings") or [])
            if str(value or "").strip()
        ]
        expected_hint = ", ".join(expected_parts[:3]).strip()
        if not expected_hint:
            expected_hint = re.sub(r"\s+", " ", str(item.get("expected_answer") or "").strip())
        if len(expected_hint) > 180:
            expected_hint = expected_hint[:179].rstrip() + "…"
        feedback_recent_rows.append(
            {
                "source_label": source_label,
                "document": str(item.get("document") or "").strip(),
                "question": str(item.get("question") or "").strip(),
                "expected_hint": expected_hint,
            }
        )

    return {
        "pass_rate_pct": pass_rate,
        "passed_total": passed,
        "active_cases_total": len(results),
        "failed_total": len(failed),
        "static_cases_total": len(static_cases),
        "feedback_cases_total": len(feedback_cases),
        "type_rows": type_rows,
        "scenario_pack_rows": scenario_pack_rows,
        "protected_total": protected_total,
        "protected_passed": protected_passed,
        "protected_failed": protected_total - protected_passed,
        "protected_rows": protected_rows,
        "document_rows": document_rows[:12],
        "failure_rows": failure_rows,
        "feedback_source_rows": [
            {"label": label, "count": count}
            for label, count in feedback_source_counter.most_common()
        ],
        "feedback_recent_rows": feedback_recent_rows[:8],
    }


def build_feedback_governance_snapshot(*, limit: int = 8) -> dict:
    """Return calibration queues for chat feedback governance."""
    messages = _safe_reviewable_messages(limit=500)
    state_labels = {
        "pending": "Sem feedback",
        "needs_triage": "Precisa classificar",
        "source_action": "Atualizar fonte/regra",
        "blocked": "Bloqueado",
        "ready_eval": "Pronto para eval",
        "trusted_memory": "Memoria reutilizavel",
        "ignored": "Ignorado",
        "triage": "Triagem",
    }
    state_counts: Counter[str] = Counter()
    destination_counts: Counter[str] = Counter()
    critical_open = 0
    rows = []

    for item in messages:
        status = str(item.get("feedback_status") or "").strip().lower()
        state = feedback_governance_state(item)
        state_name = str(state.get("state") or "pending")
        state_counts[state_name] += 1
        destination_label = str(state.get("destination_label") or "Sem destino")
        destination_counts[destination_label] += 1
        if state.get("is_critical") and state_name not in {"trusted_memory", "ignored"}:
            critical_open += 1
        if state_name in {"needs_triage", "source_action", "blocked", "ready_eval"} and len(rows) < limit:
            rows.append(
                {
                    "id": item.get("id") or item.get("message_id") or "",
                    "conversation_id": item.get("conversation_id", ""),
                    "owner_username": item.get("username", ""),
                    "status": status,
                    "state": state_name,
                    "state_label": state_labels.get(state_name, state_name),
                    "question": str(item.get("question") or "")[:220],
                    "answer": str(item.get("content") or item.get("answer") or "")[:260],
                    "missing": ", ".join(state.get("missing") or []),
                    "error_type_label": state.get("error_type_label", ""),
                    "scope_label": state.get("scope_label", ""),
                    "destination_label": destination_label,
                    "criticality_label": state.get("criticality_label", ""),
                    "updated_at_label": item.get("feedback_updated_at_label") or item.get("created_at_label") or "",
                    "action_url": (
                        "/admin/casebooks?source_type=chat&chat_feedback=all"
                        f"#chat-{item.get('id') or item.get('message_id') or ''}"
                    ),
                }
            )

    actionable_total = sum(state_counts[name] for name in ("needs_triage", "source_action", "blocked", "ready_eval"))
    return {
        "total": len(messages),
        "actionable_total": actionable_total,
        "critical_open": critical_open,
        "state_rows": [
            {"state": key, "label": state_labels.get(key, key), "count": count}
            for key, count in state_counts.most_common()
        ],
        "destination_rows": [
            {"label": label, "count": count}
            for label, count in destination_counts.most_common()
        ],
        "rows": rows,
    }


def build_tuning_map_snapshot(
    *,
    settings: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or load_bot_settings()
    quality = quality or build_quality_snapshot()

    store = getattr(services, "store", None)
    knowledge_dir = _knowledge_dir()
    documents = _safe_list_documents()
    reviewable_messages = _safe_reviewable_messages()
    maneuver_cases = _safe_maneuver_cases()
    feedback_eval_cases = _safe_feedback_eval_cases()
    practice_records = _safe_practice_records()

    companions_total = 0
    resolved_companions = 0
    companions_dir = companion_directory(knowledge_dir) if knowledge_dir else ""
    if companions_dir and os.path.isdir(companions_dir):
        companions_total = sum(1 for name in os.listdir(companions_dir) if name.lower().endswith(".json"))
    for document in documents:
        try:
            if knowledge_dir and load_document_companion(document.get("name", ""), knowledge_dir):
                resolved_companions += 1
        except Exception:
            continue
    companions_coverage = round((resolved_companions / len(documents)) * 100) if documents else 0
    missing_companions = max(len(documents) - resolved_companions, 0)

    berth_profiles = load_berth_profiles(knowledge_dir) if knowledge_dir else []
    berth_documents = sum(1 for item in berth_profiles if str(item.get("document") or "").strip())
    berth_aliases = sum(len(item.get("aliases") or []) for item in berth_profiles)

    tug_guidance = load_tug_guidance(knowledge_dir) if knowledge_dir else {}
    tug_groups_total = len(tug_guidance.get("vessel_type_groups") or {})
    tug_rules_total = (
        len(tug_guidance.get("base_matrix") or [])
        + len(tug_guidance.get("no_bowthruster_minimums") or [])
        + len(tug_guidance.get("lisnave_rules") or [])
    )

    safety_limits = load_operational_safety_limits(knowledge_dir) if knowledge_dir else {}
    safety_rules_total = len(safety_limits.get("rules") or [])
    safety_thresholds_total = len(safety_limits.get("thresholds") or {})

    approved_messages_total = 0
    correction_memory_total = 0
    review_pending_total = 0
    for item in reviewable_messages:
        status = str(item.get("feedback_status") or "").strip().lower()
        if status == "approved":
            approved_messages_total += 1
            if str(item.get("feedback_correction") or "").strip():
                correction_memory_total += 1
        elif status == "review":
            review_pending_total += 1

    feedback_source_counter: Counter[str] = Counter()
    for item in feedback_eval_cases:
        feedback_source_counter[_feedback_source_label(item.get("source", ""))] += 1
    feedback_sources_label = ", ".join(
        f"{label} × {count}" for label, count in feedback_source_counter.most_common()
    ) or "Sem correções promovidas."

    maneuver_case_total = len(maneuver_cases)
    maneuver_case_approved_total = sum(
        1 for item in maneuver_cases if str(item.get("feedback_status") or "").strip().lower() == "approved"
    )
    maneuver_case_alert_total = sum(
        1
        for item in maneuver_cases
        if str(item.get("feedback_status") or "").strip().lower() in {"avoid", "review"}
    )
    maneuver_case_flagged_total = sum(
        1 for item in maneuver_cases if (item.get("outcome_snapshot") or {}).get("decision_flags")
    )

    practice_total = len(practice_records)
    practice_active_total = sum(
        1
        for item in practice_records
        if str(item.get("feedback_status") or "").strip().lower() in PRACTICE_EXPERIENCE_ACTIVE_STATUSES
    )
    practice_review_total = sum(
        1 for item in practice_records if str(item.get("feedback_status") or "").strip().lower() == "review"
    )
    practice_source_exists = bool(
        knowledge_dir and (Path(knowledge_dir) / PRACTICE_EXPERIENCE_KNOWLEDGE_FILENAME).exists()
    )

    port_calls_total = 0
    port_activity_available = False
    try:
        if store:
            port_activity = store.get_port_activity_snapshot(window_days=3650)
            port_activity_available = True
            port_calls_total = len(port_activity.get("arrivals", []) or [])
    except Exception:
        logger.exception("Falha a listar atividade portuária para mapa de afinação.")
        port_activity_available = False

    tide_service = getattr(services, "tide_service", None)
    tide_csv_path = str(getattr(tide_service, "csv_path", "") or "")
    tide_csv_label = Path(tide_csv_path).name if tide_csv_path else "Sem CSV carregado"
    weather_service = getattr(services, "weather_service", None)
    wave_service = getattr(services, "wave_service", None)
    warning_service = getattr(services, "local_warning_service", None)
    live_services_active, live_services_total = _count_live_operational_services()

    groups = [
        _build_tuning_group(
            group_id="documental_base",
            title="Base documental",
            description="Texto, FAQ e evals que cobrem perguntas previsíveis com resposta controlada.",
            components=[
                _build_tuning_component(
                    label="Documentos base",
                    state=_component_state(bool(documents)),
                    source_name="store.documents",
                    runtime_hook="rag/search + citações",
                    detail="Corpus principal do porto: instruções, regulamentos e notas operacionais.",
                    facts=[
                        f"{len(documents)} documento(s) indexado(s)",
                        f"{resolved_companions}/{len(documents)} com companion" if documents else "",
                        "A base usada para snippets e respostas documentais.",
                    ],
                    action_url="/admin/documents",
                    action_label="Gerir documentos",
                ),
                _build_tuning_component(
                    label="Companions por documento",
                    state="online" if companions_total and companions_coverage >= 70 else "degraded" if companions_total else "offline",
                    source_name="knowledge/companions/*.json",
                    runtime_hook="load_document_companion -> build_companion_answer",
                    detail="FAQ estruturada que acelera respostas curtas e consistentes por documento.",
                    facts=[
                        f"{companions_total} companion(s) carregado(s)",
                        f"Cobertura {companions_coverage}%",
                        f"{missing_companions} documento(s) ainda sem FAQ" if documents else "",
                    ],
                    action_url="/admin/documents",
                    action_label="Completar companions",
                ),
                _build_tuning_component(
                    label="Evals fixos",
                    state=_component_state(bool(quality.get("static_cases_total"))),
                    source_name="knowledge/evals/*.json",
                    runtime_hook="evaluate_companion_cases",
                    detail="Casos estáveis que verificam se o bot continua a responder o que já foi afinado.",
                    facts=[
                        f"{quality.get('static_cases_total', 0)} caso(s) fixo(s)",
                        f"{quality.get('pass_rate_pct', 0)}% a passar no conjunto ativo",
                        f"{quality.get('failed_total', 0)} falha(s) aberta(s)",
                    ],
                    action_url="/admin/bot#quality",
                    action_label="Ver qualidade",
                ),
            ],
        ),
        _build_tuning_group(
            group_id="structured_knowledge",
            title="Conhecimento estruturado",
            description="JSONs operacionais que o runtime consulta diretamente sem depender só de texto corrido.",
            components=[
                _build_tuning_component(
                    label="Berth profiles",
                    state=_component_state(bool(berth_profiles)),
                    source_name=PROFILE_FILENAME,
                    runtime_hook="find_best_berth_profile",
                    detail="Perfis por instalação com LOA, calados, regras de noite, restrições e contexto operacional.",
                    facts=[
                        f"{len(berth_profiles)} perfil(is) disponível(is)",
                        f"{berth_documents} ligado(s) a documento(s)",
                        f"{berth_aliases} alias(es) no total" if berth_profiles else "",
                    ],
                    action_url="/admin/documents",
                    action_label="Ver perfis",
                ),
                _build_tuning_component(
                    label="Regras práticas de rebocadores",
                    state=_component_state(bool(tug_guidance)),
                    source_name=TUG_GUIDANCE_FILENAME,
                    runtime_hook="build_tug_operational_guidance_source",
                    detail="Matriz prática para quantos rebocadores pedir, com casos específicos como LISNAVE.",
                    facts=[
                        f"{tug_groups_total} grupo(s) de navio" if tug_guidance else "",
                        f"{tug_rules_total} regra(s) práticas",
                        f"{len(tug_guidance.get('lisnave_rules') or [])} regra(s) específicas LISNAVE" if tug_guidance else "",
                    ],
                    action_url="/admin/documents",
                    action_label="Ver regras",
                ),
                _build_tuning_component(
                    label="Limites de segurança",
                    state=_component_state(bool(safety_limits)),
                    source_name=SAFETY_LIMITS_FILENAME,
                    runtime_hook="build_operational_safety_source",
                    detail="Regras de suspensão/retoma por vento, rajada, visibilidade e segurança operacional.",
                    facts=[
                        f"{safety_rules_total} regra(s) declarada(s)",
                        f"{safety_thresholds_total} limiar(es) live",
                        str(safety_limits.get("title") or "").strip(),
                    ],
                    action_url="/admin/documents",
                    action_label="Ver limites",
                ),
            ],
        ),
        _build_tuning_group(
            group_id="supervised_memory",
            title="Memória supervisionada",
            description="Feedback humano que o bot reaproveita para não repetir erros nem voltar a inventar.",
            components=[
                _build_tuning_component(
                    label="Correções aprovadas",
                    state=_component_state(bool(correction_memory_total or approved_messages_total), partial=bool(review_pending_total)),
                    source_name="review_correction_memory",
                    runtime_hook="_build_review_correction_answer",
                    detail="Memória reutilizável de respostas corrigidas, incluindo reformulação para evitar copy-paste mecânico.",
                    facts=[
                        f"{correction_memory_total} correção(ões) aprovada(s)",
                        f"{approved_messages_total} resposta(s) aprovada(s) no total",
                        f"{review_pending_total} item(ns) ainda por rever",
                    ],
                    action_url="/admin/bot#exceptions",
                    action_label="Ver exceções",
                ),
                _build_tuning_component(
                    label="Feedback promovido para eval",
                    state=_component_state(bool(feedback_eval_cases), partial=bool(settings.get("auto_promote_corrections", True))),
                    source_name="feedback_eval_cases",
                    runtime_hook="load_eval_cases_from_store",
                    detail="Correções aprovadas que passam a testar automaticamente o bot nas próximas execuções.",
                    facts=[
                        f"{len(feedback_eval_cases)} caso(s) promovido(s)",
                        feedback_sources_label,
                        quality.get("feedback_recent_rows", [{}])[0].get("expected_hint", "") if quality.get("feedback_recent_rows") else "",
                    ],
                    action_url="/admin/bot#quality",
                    action_label="Ver promos",
                ),
                _build_tuning_component(
                    label="Confiança em feedback positivo",
                    state=_component_state(bool(settings.get("auto_trust_positive_feedback", True)), partial=True),
                    source_name="bot_settings.json",
                    runtime_hook="auto_trust_positive_feedback",
                    detail="Quando ligado, thumbs-up e aprovações reforçam respostas futuras sem exigir correção textual.",
                    facts=[
                        f"Modo {'ligado' if settings.get('auto_trust_positive_feedback', True) else 'desligado'}",
                        f"{approved_messages_total} resposta(s) elegível(is) para reforço",
                        f"Hint documental confiável: {float(settings.get('trusted_document_hint_similarity', 0.82)):.2f}",
                    ],
                    action_url="/admin/bot#toolbox",
                    action_label="Ajustar",
                ),
            ],
        ),
        _build_tuning_group(
            group_id="operational_history",
            title="Histórico operacional",
            description="Casos reais que afinam comparação, contexto e padrões práticos do assistente.",
            components=[
                _build_tuning_component(
                    label="Contexto provável",
                    state="online",
                    source_name="core/chat_context_scope.py + core/chat_reasoning.py",
                    runtime_hook="scoped_history_for_question -> build_conversation_reasoning_state",
                    detail="Camada que filtra histórico longo do WhatsApp e prepara uma ficha de caso para follow-ups curtos.",
                    facts=[
                        "Não envia a conversa inteira ao motor de resposta.",
                        "Extrai local, operação, dimensões, carga, hora, meteo e percurso.",
                        "Em caso de dúvida, obriga a assumir a premissa ou pedir confirmação.",
                    ],
                    action_url="/admin/tests",
                    action_label="Ver testes",
                ),
                _build_tuning_component(
                    label="Casebooks de manobra",
                    state=_component_state(bool(maneuver_case_total), partial=True),
                    source_name="maneuver_cases",
                    runtime_hook="rank_similar_maneuver_cases",
                    detail="Casos governados de manobra usados para comparar contexto, detectar outliers e justificar decisões.",
                    facts=[
                        f"{maneuver_case_total} caso(s) registado(s)",
                        f"{maneuver_case_approved_total} aprovado(s)",
                        f"{maneuver_case_alert_total} com alertas ou review",
                        f"{maneuver_case_flagged_total} com decision flags" if maneuver_case_total else "",
                    ],
                    action_url="/admin/casebooks",
                    action_label="Abrir casebooks",
                ),
                _build_tuning_component(
                    label="Experiência prática importada",
                    state=_component_state(bool(practice_active_total), partial=bool(practice_total or practice_source_exists)),
                    source_name=PRACTICE_EXPERIENCE_KNOWLEDGE_FILENAME,
                    runtime_hook="list_practice_experience_records",
                    detail="Padrões consolidados importados para reforçar prática local além dos casos governados um a um.",
                    facts=[
                        f"{practice_active_total}/{practice_total} padrão(ões) ativo(s)" if practice_total else "Sem padrões ativos carregados",
                        f"{practice_review_total} para rever" if practice_total else "",
                        "JSON fonte presente no knowledge dir" if practice_source_exists else "Sem JSON de prática no knowledge dir",
                    ],
                    action_url="/admin/casebooks",
                    action_label="Rever prática",
                ),
                _build_tuning_component(
                    label="Escalas e atividade do porto",
                    state=_component_state(port_activity_available),
                    source_name="port_calls",
                    runtime_hook="store.get_port_activity_snapshot",
                    detail="Contexto operativo vivo e histórico de escalas que o bot usa para responder sobre atividade recente.",
                    facts=[
                        (
                            f"{port_calls_total} escala(s) resolvida(s)"
                            if port_calls_total
                            else "Portal acessível; sem escalas resolvidas."
                        ),
                        "Serve de contexto à operação, não substitui as regras.",
                    ],
                    action_url="/port-calls/register",
                    action_label="Ver escalas",
                ),
            ],
        ),
        _build_tuning_group(
            group_id="runtime_live",
            title="Runtime e dados live",
            description="Serviços que entram na resposta quando a pergunta depende do dia, do tempo ou do estado operacional atual.",
            components=[
                _build_tuning_component(
                    label="Marés",
                    state=_component_state(bool(tide_csv_path)),
                    source_name="tide_service",
                    runtime_hook="/mares + linguagem natural",
                    detail="Aceita datas naturais como hoje, amanhã, ontem e datas explícitas do ano.",
                    facts=[
                        tide_csv_label,
                        "Comando slash e linguagem natural suportados.",
                    ],
                    action_url="/admin/bot#playground",
                    action_label="Testar no playground",
                ),
                _build_tuning_component(
                    label="Meteorologia",
                    state=_component_state(bool(weather_service and getattr(weather_service, "enabled", False))),
                    source_name="weather_service",
                    runtime_hook="forecast live",
                    detail="Fonte live para vento, rajadas, visibilidade e suporte a regras de segurança.",
                    facts=[
                        f"Estado {'ativo' if weather_service and getattr(weather_service, 'enabled', False) else 'inativo'}",
                    ],
                    action_url="/admin/bot#playground",
                    action_label="Testar",
                ),
                _build_tuning_component(
                    label="Ondulação",
                    state=_component_state(bool(wave_service and getattr(wave_service, "enabled", False))),
                    source_name="wave_service",
                    runtime_hook="wave live",
                    detail="Contexto marítimo live quando a resposta depende de estado de mar.",
                    facts=[
                        f"Estado {'ativo' if wave_service and getattr(wave_service, 'enabled', False) else 'inativo'}",
                    ],
                    action_url="/admin/bot#playground",
                    action_label="Testar",
                ),
                _build_tuning_component(
                    label="Avisos locais",
                    state=_component_state(bool(warning_service and getattr(warning_service, "enabled", False))),
                    source_name="local_warning_service",
                    runtime_hook="local warnings",
                    detail="Avisos e restrições locais que podem sobrepor o contexto base do documento.",
                    facts=[
                        f"Estado {'ativo' if warning_service and getattr(warning_service, 'enabled', False) else 'inativo'}",
                        f"{live_services_active}/{live_services_total} serviço(s) live ativo(s)",
                    ],
                    action_url="/admin/bot#playground",
                    action_label="Testar",
                ),
            ],
        ),
        _build_tuning_group(
            group_id="governance_controls",
            title="Governança e thresholds",
            description="Toggles e limiares que decidem quando confiar, bloquear, promover ou pedir revisão.",
            components=[
                _build_tuning_component(
                    label="Thresholds do bot",
                    state="online",
                    source_name="bot_settings.json",
                    runtime_hook="load_bot_settings",
                    detail="Limiariza similaridade, promoção automática e deteção de outliers.",
                    facts=[
                        f"review_guard_similarity = {float(settings.get('review_guard_similarity', 0.90)):.2f}",
                        f"review_correction_similarity = {float(settings.get('review_correction_similarity', 0.94)):.2f}",
                        f"outlier_review_threshold = {float(settings.get('outlier_review_threshold', 0.85)):.2f}",
                    ],
                    action_url="/admin/bot#toolbox",
                    action_label="Editar settings",
                ),
                _build_tuning_component(
                    label="Modo de governação",
                    state=_component_state(not bool(settings.get("require_admin_validation", False)), partial=True),
                    source_name="bot_settings.json",
                    runtime_hook="auto_promote_corrections + require_admin_validation",
                    detail="Define se o ciclo é mais autónomo ou se cada decisão precisa de validação humana explícita.",
                    facts=[
                        f"auto_promote_corrections = {'on' if settings.get('auto_promote_corrections', True) else 'off'}",
                        f"require_admin_validation = {'on' if settings.get('require_admin_validation', False) else 'off'}",
                        f"signals_window_hours = {int(settings.get('signals_window_hours', 168))}",
                    ],
                    action_url="/admin/bot#toolbox",
                    action_label="Ajustar governação",
                ),
            ],
        ),
    ]

    all_components = [component for group in groups for component in group.get("components", [])]
    online_total = sum(1 for item in all_components if item.get("state") == "online")
    degraded_total = sum(1 for item in all_components if item.get("state") == "degraded")
    offline_total = sum(1 for item in all_components if item.get("state") == "offline")

    return {
        "summary": {
            "implemented_total": len(all_components),
            "verified_total": online_total + degraded_total,
            "online_total": online_total,
            "degraded_total": degraded_total,
            "offline_total": offline_total,
            "live_services_active": live_services_active,
            "live_services_total": live_services_total,
        },
        "metrics": {
            "documents_total": len(documents),
            "companions_total": companions_total,
            "companions_coverage_pct": companions_coverage,
            "berth_profiles_total": len(berth_profiles),
            "tug_rules_total": tug_rules_total,
            "safety_rules_total": safety_rules_total,
            "correction_memory_total": correction_memory_total,
            "approved_messages_total": approved_messages_total,
            "feedback_eval_total": len(feedback_eval_cases),
            "practice_total": practice_total,
            "practice_active_total": practice_active_total,
            "maneuver_case_total": maneuver_case_total,
            "port_calls_total": port_calls_total,
            "live_services_active": live_services_active,
            "live_services_total": live_services_total,
        },
        "groups": groups,
    }


def build_pipeline_snapshot(
    *,
    tuning: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
    exceptions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quality = quality or build_quality_snapshot()
    sources = sources or build_sources_snapshot()
    exceptions = exceptions or build_exceptions()
    tuning = tuning or build_tuning_map_snapshot(quality=quality)

    metrics = tuning.get("metrics") or {}
    summary = tuning.get("summary") or {}
    online_sources_total = sum(1 for item in sources if item.get("state") == "online")

    trusted_memory_total = int(metrics.get("correction_memory_total") or 0) + int(metrics.get("feedback_eval_total") or 0)
    structured_inputs_total = (
        int(metrics.get("documents_total") or 0)
        + int(metrics.get("companions_total") or 0)
        + int(metrics.get("berth_profiles_total") or 0)
        + int(metrics.get("tug_rules_total") or 0)
        + int(metrics.get("safety_rules_total") or 0)
    )
    live_active = int(metrics.get("live_services_active") or 0)
    live_total = int(metrics.get("live_services_total") or 0)

    memory_state = _component_state(bool(trusted_memory_total), partial=bool(metrics.get("approved_messages_total") or 0))
    structured_state = _component_state(bool(structured_inputs_total), partial=bool(metrics.get("documents_total") or 0))
    live_state = _component_state(live_total > 0 and live_active == live_total, partial=bool(live_active))
    synthesis_state = _component_state(bool(online_sources_total), partial=bool(sources))
    governance_state = (
        "online"
        if quality.get("active_cases_total") and not quality.get("failed_total") and not exceptions.get("total")
        else "degraded"
        if quality.get("active_cases_total") or exceptions.get("total")
        else "offline"
    )

    return {
        "note": (
            "Cada etapa abaixo já existe no runtime. O estado mostra se a funcionalidade está só implementada "
            "ou se já tem dados/configuração suficientes para influenciar respostas reais neste momento."
        ),
        "summary_cards": [
            {
                "label": "Mecanismos implementados",
                "value": summary.get("implemented_total", 0),
                "detail": "pontos de afinação visíveis no painel",
            },
            {
                "label": "Com dados ou configuração ativa",
                "value": summary.get("verified_total", 0),
                "detail": f"{summary.get('online_total', 0)} online · {summary.get('degraded_total', 0)} parciais",
            },
            {
                "label": "Serviços live",
                "value": f"{live_active}/{live_total or 0}",
                "detail": "marés, meteo, onda e avisos",
            },
            {
                "label": "Evals a passar",
                "value": f"{quality.get('passed_total', 0)}/{quality.get('active_cases_total', 0)}",
                "detail": f"{quality.get('failed_total', 0)} falha(s) aberta(s)",
            },
        ],
        "steps": [
            {
                "label": "Leitura e routing do pedido",
                "state": "online",
                "state_label": _component_state_label("online"),
                "metric": "linguagem natural + slash commands",
                "detail": "O bot tenta perceber intenção, terminal, documento, follow-up curto e se a pergunta depende do dia atual.",
                "facts": [
                    "Percebe datas naturais para marés: hoje, amanhã, ontem e datas explícitas.",
                    "Distingue perguntas documentais, operacionais, live, memória supervisionada e continuidade de caso.",
                ],
            },
            {
                "label": "Ficha de contexto provável",
                "state": "online",
                "state_label": _component_state_label("online"),
                "metric": "histórico filtrado + factos extraídos",
                "detail": "Para mensagens como “E carga não IMO”, prepara o último caso provável sem misturar casos incompatíveis.",
                "facts": [
                    "Usa histórico recente filtrado em vez da conversa inteira.",
                    "Quando depende da continuidade, a resposta deve assumir a premissa em frase curta.",
                ],
            },
            {
                "label": "Memória supervisionada primeiro",
                "state": memory_state,
                "state_label": _component_state_label(memory_state),
                "metric": f"{trusted_memory_total} memória(s) supervisionada(s)",
                "detail": "Antes de improvisar, o runtime tenta reutilizar correções aprovadas e feedback já promovido.",
                "facts": [
                    f"{metrics.get('correction_memory_total', 0)} correção(ões) reaproveitável(eis)",
                    f"{metrics.get('feedback_eval_total', 0)} correção(ões) promovida(s) para eval",
                ],
            },
            {
                "label": "Consulta conhecimento estruturado",
                "state": structured_state,
                "state_label": _component_state_label(structured_state),
                "metric": f"{structured_inputs_total} input(s) estruturado(s)",
                "detail": "Companions, berth profiles, regras de reboque e limites de segurança entram como fonte forte.",
                "facts": [
                    f"{metrics.get('documents_total', 0)} documento(s) e {metrics.get('companions_total', 0)} companions",
                    f"{metrics.get('berth_profiles_total', 0)} perfil(is) + {metrics.get('tug_rules_total', 0) + metrics.get('safety_rules_total', 0)} regra(s)",
                ],
            },
            {
                "label": "Acrescenta contexto live",
                "state": live_state,
                "state_label": _component_state_label(live_state),
                "metric": f"{live_active}/{live_total or 0} serviço(s) ativo(s)",
                "detail": "Quando a pergunta depende do estado atual, o bot injeta marés, meteo, onda e avisos locais.",
                "facts": [
                    "Os dados live não substituem regras fixas; complementam ou bloqueiam a resposta.",
                    "A camada de marés já aceita datas relativas e comandos `/mares ...`.",
                ],
            },
            {
                "label": "Síntese, origem e reformulação",
                "state": synthesis_state,
                "state_label": _component_state_label(synthesis_state),
                "metric": f"{online_sources_total}/{len(sources)} fonte(s) base online",
                "detail": "O runtime escolhe a melhor origem, humaniza respostas reutilizadas e devolve a origem no playground.",
                "facts": [
                    "Evita FAQ copy-paste quando já existe uma resposta corrigida e confiável.",
                    "O playground mostra a origem e os snippets usados.",
                ],
            },
            {
                "label": "Evals e governação fecham o ciclo",
                "state": governance_state,
                "state_label": _component_state_label(governance_state),
                "metric": f"{quality.get('passed_total', 0)}/{quality.get('active_cases_total', 0)} eval(s)",
                "detail": "Feedback, thresholds e exceções determinam se o bot aprende sozinho ou pede revisão humana.",
                "facts": [
                    f"{exceptions.get('total', 0)} exceção(ões) pendente(s)",
                    f"{quality.get('feedback_cases_total', 0)} caso(s) vindos de feedback humano",
                ],
            },
        ],
    }


def compute_health_score(quality: dict, signals: dict, exceptions: dict, sources: list[dict]) -> dict:
    pass_rate = int(quality.get("pass_rate_pct") or 0)
    positives = int(signals.get("positives", {}).get("total") or 0)
    negatives = int(signals.get("negatives", {}).get("total") or 0)
    high_exceptions = int(exceptions.get("severity_counts", {}).get("high") or 0)
    coverage = 0
    coverage_total = 0
    for src in sources or []:
        coverage_total += 1 if src.get("state") == "online" else 0
    coverage_pct = round((coverage_total / max(len(sources), 1)) * 100)

    feedback_bias = 0
    if positives + negatives:
        feedback_bias = round((positives / (positives + negatives)) * 100)
    else:
        feedback_bias = 70  # neutro tende para positivo se não há sinais

    penalty = min(40, high_exceptions * 12)
    score = max(0, min(100, round((pass_rate * 0.5) + (coverage_pct * 0.25) + (feedback_bias * 0.25) - penalty)))

    if score >= 85:
        label = "Saudável"
        state = "online"
    elif score >= 65:
        label = "A operar com alertas"
        state = "degraded"
    else:
        label = "Precisa de atenção"
        state = "offline"

    return {
        "score": score,
        "label": label,
        "state": state,
        "pass_rate_pct": pass_rate,
        "coverage_pct": coverage_pct,
        "feedback_bias_pct": feedback_bias,
        "penalty": penalty,
    }


# ----------------------------------------------------------------------------
# Runtime monitor — compact operational picture for the admin bot cockpit


def _source_by_id(sources: list[dict], source_id: str) -> dict:
    for source in sources or []:
        if source.get("id") == source_id:
            return source
    return {}


def _service_status_detail(service: Any, fallback: str) -> str:
    if service is None:
        return "Serviço não inicializado."
    status_method = getattr(service, "status", None)
    if not callable(status_method):
        return fallback
    try:
        status = status_method()
    except Exception as exc:
        return f"Estado indisponível: {exc}"
    if status.get("error"):
        return str(status.get("error"))
    if status.get("count") is not None:
        return f"{status.get('count')} aviso(s); cache {status.get('cache_updated_at_label') or 'sem cache'}."
    if status.get("cache_updated_at_label"):
        return f"Cache {status.get('cache_updated_at_label')}."
    return fallback


def _runtime_card(card_id: str, label: str, value: str, detail: str, state: str) -> dict:
    return {
        "id": card_id,
        "label": label,
        "value": value,
        "detail": detail,
        "state": state,
    }


def _context_mix(sources: list[dict]) -> list[dict]:
    max_count = max([int(source.get("count") or 0) for source in sources or []] + [1])
    rows: list[dict] = []
    for source in sources or []:
        count = int(source.get("count") or 0)
        rows.append(
            {
                "id": source.get("id", ""),
                "label": source.get("label", ""),
                "count": count,
                "state": source.get("state", "offline"),
                "bar_pct": max(4, round((count / max_count) * 100)) if count else 2,
                "meta": source.get("meta", ""),
            }
        )
    return rows


def build_bot_monitor_snapshot(
    *,
    settings: dict,
    sources: list[dict],
    quality: dict,
    signals: dict,
    exceptions: dict,
    health: dict,
) -> dict:
    """Return real-time admin dashboard data for the bot operating loop."""
    rag = getattr(services, "rag", None)
    if rag:
        try:
            from core.knowledge_runtime import current_reindex_status_payload

            reindex = current_reindex_status_payload()
        except Exception as exc:
            logger.exception("Falha a obter estado de reindexação para monitor do bot.")
            reindex = {"state": "error", "message": str(exc), "semantic_chunk_coverage_pct": 0}
    else:
        reindex = {
            "state": "offline",
            "message": "Motor RAG não inicializado.",
            "semantic_chunk_coverage_pct": 0,
        }
    can_generate = bool(rag and rag.can_generate())
    generation_model = str(getattr(rag, "generation_model", "") or "sem modelo")
    provider = str(getattr(rag, "provider_name", "") or getattr(rag, "generation_provider_label", "") or "").strip()
    index_state = str(reindex.get("state") or "offline")
    semantic_coverage = int(float(reindex.get("semantic_chunk_coverage_pct") or reindex.get("progress_pct") or 0))
    rag_state = (
        "online"
        if index_state == "completed" and semantic_coverage >= 80
        else "degraded"
        if index_state in {"running", "pending", "completed"} and semantic_coverage > 0
        else "offline"
    )

    portal_source = _source_by_id(sources, "operational_data")
    live_items = _live_operational_service_items()
    live_online = sum(1 for _, enabled in live_items if enabled)
    live_state = (
        "online"
        if live_items and live_online == len(live_items)
        else "degraded"
        if live_online
        else "offline"
    )

    feedback_cases = int(quality.get("feedback_cases_total") or 0)
    feedback_enabled = bool(
        settings.get("auto_promote_corrections")
        or settings.get("auto_trust_positive_feedback")
        or feedback_cases
    )
    feedback_state = _enabled_state(
        feedback_enabled,
        degraded=bool(exceptions.get("severity_counts", {}).get("high")),
    )

    runtime_cards = [
        _runtime_card(
            "llm",
            "Motor de resposta",
            generation_model,
            provider or ("pronto para síntese" if can_generate else "provider sem chave ativa"),
            "online" if can_generate else "offline",
        ),
        _runtime_card(
            "rag",
            "RAG",
            f"{semantic_coverage}%",
            str(reindex.get("message") or reindex.get("query_embedding_summary") or "Índice documental."),
            rag_state,
        ),
        _runtime_card(
            "live",
            "Dados live",
            f"{live_online}/{len(live_items)}",
            " · ".join(name for name, enabled in live_items if enabled) or "sem fontes live ativas",
            live_state,
        ),
        _runtime_card(
            "feedback",
            "Memória",
            f"{feedback_cases}",
            "correções promovidas + feedback aprovado/revisto",
            feedback_state,
        ),
        _runtime_card(
            "quality",
            "Evals",
            f"{quality.get('passed_total', 0)}/{quality.get('active_cases_total', 0)}",
            f"{quality.get('failed_total', 0)} caso(s) a falhar",
            "online" if quality.get("failed_total", 0) == 0 and quality.get("active_cases_total", 0) else "degraded",
        ),
    ]

    pipeline_steps = [
        {
            "id": "input",
            "label": "Entrada",
            "detail": "Web ou WhatsApp; comandos operacionais seguem validação própria.",
            "state": "online",
        },
        {
            "id": "planner",
            "label": "Planner",
            "detail": "Classifica pergunta: live direto, RAG, síntese técnica, follow-up curto ou ação.",
            "state": "online",
        },
        {
            "id": "context",
            "label": "Contexto",
            "detail": "Seleciona histórico filtrado, ficha provável, documentos, perfis, live e casebooks relevantes.",
            "state": rag_state,
        },
        {
            "id": "synthesis",
            "label": "Síntese",
            "detail": "Cruza fontes; respostas simples de maré/meteo podem ser determinísticas.",
            "state": "online" if can_generate else "offline",
        },
        {
            "id": "guard",
            "label": "Guardas",
            "detail": "Feedback em revisão bloqueia repetições; critic reforça decisões operacionais.",
            "state": feedback_state,
        },
        {
            "id": "answer",
            "label": "Resposta",
            "detail": "Resposta final sem ids internos; fontes ficam disponíveis para auditoria.",
            "state": health.get("state", "degraded"),
        },
    ]

    health_breakdown = [
        {
            "id": "pass_rate_pct",
            "label": "Evals",
            "value": int(health.get("pass_rate_pct") or 0),
            "suffix": "%",
            "bar_pct": int(health.get("pass_rate_pct") or 0),
            "state": "online" if int(health.get("pass_rate_pct") or 0) >= 85 else "degraded",
        },
        {
            "id": "coverage_pct",
            "label": "Fontes",
            "value": int(health.get("coverage_pct") or 0),
            "suffix": "%",
            "bar_pct": int(health.get("coverage_pct") or 0),
            "state": "online" if int(health.get("coverage_pct") or 0) >= 80 else "degraded",
        },
        {
            "id": "feedback_bias_pct",
            "label": "Feedback",
            "value": int(health.get("feedback_bias_pct") or 0),
            "suffix": "%",
            "bar_pct": int(health.get("feedback_bias_pct") or 0),
            "state": "online" if int(health.get("feedback_bias_pct") or 0) >= 70 else "degraded",
        },
        {
            "id": "penalty",
            "label": "Penalização",
            "value": int(health.get("penalty") or 0),
            "suffix": "",
            "bar_pct": min(100, int(health.get("penalty") or 0) * 2),
            "state": "offline" if int(health.get("penalty") or 0) else "online",
        },
    ]

    config_profile = [
        {
            "label": "Aprendizagem",
            "value": "Automática" if settings.get("auto_promote_corrections") else "Manual",
            "state": "online" if settings.get("auto_promote_corrections") else "degraded",
        },
        {
            "label": "Feedback positivo",
            "value": "Ativo" if settings.get("auto_trust_positive_feedback") else "Conservador",
            "state": "online" if settings.get("auto_trust_positive_feedback") else "degraded",
        },
        {
            "label": "Validação admin",
            "value": "Obrigatória" if settings.get("require_admin_validation") else "Por exceção",
            "state": "degraded" if settings.get("require_admin_validation") else "online",
        },
    ]

    weather_enabled = _service_enabled(getattr(services, "weather_service", None))
    wave_enabled = _service_enabled(getattr(services, "wave_service", None))
    warning_enabled = _service_enabled(getattr(services, "local_warning_service", None))
    wave_detail = _service_status_detail(getattr(services, "wave_service", None), "Ondulação configurada.")
    warning_detail = _service_status_detail(
        getattr(services, "local_warning_service", None),
        "Avisos locais configurados.",
    )
    tide_enabled = _tide_service_enabled()
    live_details = [
        {
            "label": "Portal",
            "state": portal_source.get("state", "offline"),
            "detail": portal_source.get("meta", "") or "Sem escalas resolvidas no portal.",
        },
        {
            "label": "Marés",
            "state": _enabled_state(tide_enabled),
            "detail": "CSV local ativo." if tide_enabled else "Sem CSV local ativo.",
        },
        {
            "label": "Meteorologia",
            "state": _enabled_state(weather_enabled),
            "detail": "WeatherAPI configurada." if weather_enabled else "Sem chave/localização ativa.",
        },
        {"label": "Ondulação", "state": _enabled_state(wave_enabled), "detail": wave_detail},
        {"label": "Avisos", "state": _enabled_state(warning_enabled), "detail": warning_detail},
        {"label": "AIS", "state": "degraded", "detail": "Disponível como mapa/embed; não é fonte textual do chat."},
    ]

    checked_at = _now_utc()
    return {
        "checked_at": checked_at.isoformat(),
        "checked_at_label": checked_at.strftime("%d/%m/%Y %H:%M UTC"),
        "runtime_cards": runtime_cards,
        "pipeline_steps": pipeline_steps,
        "health_breakdown": health_breakdown,
        "context_mix": _context_mix(sources),
        "config_profile": config_profile,
        "live_details": live_details,
        "reindex": reindex,
    }
