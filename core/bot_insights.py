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
from urllib.parse import urlencode

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


def _feedback_source_label(source: str) -> str:
    clean_source = str(source or "").strip().lower()
    if clean_source == "whatsapp":
        return "WhatsApp"
    if clean_source == "web":
        return "Site"
    return "Operador"


def _preview_text(value: Any, limit: int = 220) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return f"{clean[: max(limit - 1, 0)].rstrip()}..."


def _build_relative_url(path: str, **params: Any) -> str:
    clean_params = [(key, value) for key, value in params.items() if value not in {"", None}]
    if not clean_params:
        return path
    return f"{path}?{urlencode(clean_params, doseq=True)}"


def _build_casebooks_focus_url(
    *,
    source_type: str,
    anchor: str,
    limit: int = 8,
    chat_feedback: str = "",
    case_feedback: str = "",
    q: str = "",
) -> str:
    focus = str(anchor or "").strip()
    params = {
        "source_type": source_type,
        "limit": limit,
        "focus": focus,
        "chat_feedback": chat_feedback,
        "case_feedback": case_feedback,
        "q": q,
    }
    base = _build_relative_url("/admin/bot", **params)
    return f"{base}#{focus}" if focus else f"{base}#casebooks"


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
                    "url": _build_casebooks_focus_url(
                        source_type="chat",
                        chat_feedback="review",
                        limit=16,
                        anchor=f"chat-{msg.get('id', '')}",
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

    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for case in static_cases + feedback_cases:
        key = (
            (case.get("document") or "").strip().lower(),
            (case.get("question") or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(case)

    results = evaluate_companion_cases(deduped, knowledge_dir) if (deduped and knowledge_dir) else []
    evaluated_rows = [{**case, **result} for case, result in zip(deduped, results)]
    passed = sum(1 for item in evaluated_rows if item.get("passed"))
    failed = [item for item in evaluated_rows if not item.get("passed")]
    pass_rate = round((passed / len(results)) * 100) if results else 0
    source_rows = [
        {"label": label, "count": count}
        for label, count in sorted(
            Counter(_feedback_source_label(item.get("source", "")) for item in feedback_cases).items(),
            key=lambda entry: (-entry[1], entry[0]),
        )
    ]

    document_summary: dict[str, dict] = {}
    for case in deduped:
        name = (case.get("document") or "").strip()
        if not name:
            continue
        bucket = document_summary.setdefault(name, {"document": name, "total": 0, "passed": 0, "failed": 0, "feedback": 0})
        bucket["total"] += 1
    for case in feedback_cases:
        name = (case.get("document") or "").strip()
        if not name:
            continue
        document_summary.setdefault(name, {"document": name, "total": 0, "passed": 0, "failed": 0, "feedback": 0})
        document_summary[name]["feedback"] += 1
    for result in evaluated_rows:
        name = (result.get("document") or "").strip()
        if not name:
            continue
        if name not in document_summary:
            document_summary[name] = {"document": name, "total": 0, "passed": 0, "failed": 0, "feedback": 0}
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
        source_message_id = (item.get("source_message_id") or "").strip()
        if source_message_id:
            origin_label = "Correção promovida"
            action_label = "Abrir correção"
            action_url = _build_casebooks_focus_url(
                source_type="chat",
                chat_feedback="all",
                limit=16,
                anchor=f"chat-{source_message_id}",
                q=item.get("question", ""),
            )
        else:
            origin_label = "Eval estático"
            action_label = "Abrir documento"
            action_url = _build_relative_url("/admin/documents", q=item.get("document", ""))
        failure_rows.append(
            {
                "document": item.get("document", ""),
                "question": item.get("question", ""),
                "origin_label": origin_label,
                "missing_summary": ", ".join(missing) or "Resposta vazia ou fora do esperado.",
                "action_label": action_label,
                "action_url": action_url,
            }
        )

    recent_feedback_cases = []
    for item in sorted(feedback_cases, key=lambda row: str(row.get("updated_at") or ""), reverse=True)[:8]:
        recent_feedback_cases.append(
            {
                "document": item.get("document", ""),
                "question": item.get("question", ""),
                "source_label": _feedback_source_label(item.get("source", "")),
                "expected_answer_preview": _preview_text(item.get("expected_answer", ""), limit=220),
                "feedback_note_preview": _preview_text(item.get("feedback_note", ""), limit=160),
            }
        )

    return {
        "pass_rate_pct": pass_rate,
        "passed_total": passed,
        "active_cases_total": len(results),
        "failed_total": len(failed),
        "static_cases_total": len(static_cases),
        "feedback_cases_total": len(feedback_cases),
        "source_rows": source_rows,
        "recent_feedback_cases": recent_feedback_cases,
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
