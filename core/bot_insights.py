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

from core import services
from core.bot_settings import load_bot_settings
from domain.knowledge_companions import companion_directory, load_document_companion
from domain.knowledge_evals import (
    default_eval_cases_path,
    evaluate_companion_cases,
    load_eval_cases_from_dir,
    load_eval_cases_from_store,
)

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
        feedback_status = (msg.get("feedback_status") or "").strip().lower()
        feedback_updated_at = _iso_to_datetime(msg.get("feedback_updated_at"))
        created_at = _iso_to_datetime(msg.get("created_at"))
        question = msg.get("question") or ""
        if question:
            for topic in _topics_from_question(question)[:4]:
                topic_counter[topic] += 1

        if feedback_status == "approved":
            if feedback_updated_at and window_start <= feedback_updated_at <= window_end:
                positives_current += 1
                recent_events.append(
                    {
                        "type": "positive",
                        "label": "Resposta aprovada",
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
        if (msg.get("feedback_status") or "").strip().lower() == "review":
            items.append(
                {
                    "type": "correction_review",
                    "severity": "high",
                    "label": "Correção bloqueada",
                    "detail": msg.get("question") or (msg.get("content") or "")[:120],
                    "url": f"/admin/bot#chat-{msg.get('id', '')}",
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
        "label": "Dados operacionais vivos",
        "description": "Escalas, manobras, maré, meteorologia e agulhas em tempo real.",
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
    try:
        if store:
            port_calls_total = len(store.get_port_activity_snapshot(window_days=3650).get("arrivals", []) or [])
    except Exception:
        port_calls_total = 0

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
        state="online" if port_calls_total else "offline",
        action_url="/port-calls",
        meta=f"{port_calls_total} escala(s) resolvidas",
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

    return {
        "pass_rate_pct": pass_rate,
        "passed_total": passed,
        "active_cases_total": len(results),
        "failed_total": len(failed),
        "static_cases_total": len(static_cases),
        "feedback_cases_total": len(feedback_cases),
        "type_rows": type_rows,
        "protected_total": protected_total,
        "protected_passed": protected_passed,
        "protected_failed": protected_total - protected_passed,
        "protected_rows": protected_rows,
        "document_rows": document_rows[:12],
        "failure_rows": failure_rows,
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


def _enabled_state(enabled: bool, *, degraded: bool = False) -> str:
    if not enabled:
        return "offline"
    return "degraded" if degraded else "online"


def _service_enabled(service: Any) -> bool:
    if service is None:
        return False
    enabled = getattr(service, "enabled", True)
    return bool(enabled() if callable(enabled) else enabled)


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
    live_items = [
        ("portal", portal_source.get("state") == "online"),
        ("marés", getattr(services, "tide_service", None) is not None),
        ("meteo", _service_enabled(getattr(services, "weather_service", None))),
        ("ondulação", _service_enabled(getattr(services, "wave_service", None))),
        ("avisos", _service_enabled(getattr(services, "local_warning_service", None))),
    ]
    live_online = sum(1 for _, enabled in live_items if enabled)
    live_state = "online" if live_online >= 4 else "degraded" if live_online else "offline"

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
            "LLM",
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
            "detail": "Classifica pergunta: live direto, RAG, síntese técnica ou ação.",
            "state": "online",
        },
        {
            "id": "context",
            "label": "Contexto",
            "detail": "Seleciona documentos, companions, berth profiles, live e casebooks relevantes.",
            "state": rag_state,
        },
        {
            "id": "synthesis",
            "label": "Síntese",
            "detail": "LLM cruza fontes; respostas simples de maré/meteo podem ser determinísticas.",
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
    live_details = [
        {"label": "Portal", "state": portal_source.get("state", "offline"), "detail": portal_source.get("meta", "")},
        {"label": "Marés", "state": "online" if getattr(services, "tide_service", None) else "offline", "detail": "CSV local ativo."},
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
