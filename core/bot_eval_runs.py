"""Persisted calibration run history for bot evaluation snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from core import services


BOT_EVAL_RUN_HISTORY_KEY = "bot_eval_run_history"
BOT_EVAL_RUN_HISTORY_LIMIT = 24


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _run_summary_from_quality(quality: dict, *, triggered_by: str = "", trigger: str = "manual") -> dict:
    scenario_rows = []
    for row in quality.get("scenario_pack_rows") or []:
        if not isinstance(row, dict):
            continue
        scenario_rows.append(
            {
                "label": str(row.get("label") or ""),
                "total": _as_int(row.get("total")),
                "passed": _as_int(row.get("passed")),
                "failed": _as_int(row.get("failed")),
                "coverage_pct": _as_int(row.get("coverage_pct")),
                "state": str(row.get("state") or ""),
            }
        )

    return {
        "run_id": uuid4().hex[:12],
        "ran_at": _now_iso(),
        "trigger": str(trigger or "manual").strip() or "manual",
        "triggered_by": str(triggered_by or "").strip(),
        "pass_rate_pct": _as_int(quality.get("pass_rate_pct")),
        "passed_total": _as_int(quality.get("passed_total")),
        "active_cases_total": _as_int(quality.get("active_cases_total")),
        "failed_total": _as_int(quality.get("failed_total")),
        "protected_total": _as_int(quality.get("protected_total")),
        "protected_failed": _as_int(quality.get("protected_failed")),
        "static_cases_total": _as_int(quality.get("static_cases_total")),
        "feedback_cases_total": _as_int(quality.get("feedback_cases_total")),
        "scenario_pack_rows": scenario_rows,
    }


def _delta(current: dict, previous: dict | None) -> dict:
    if not previous:
        return {"pass_rate": 0, "failed": 0, "cases": 0, "protected_failed": 0}
    return {
        "pass_rate": _as_int(current.get("pass_rate_pct")) - _as_int(previous.get("pass_rate_pct")),
        "failed": _as_int(current.get("failed_total")) - _as_int(previous.get("failed_total")),
        "cases": _as_int(current.get("active_cases_total")) - _as_int(previous.get("active_cases_total")),
        "protected_failed": _as_int(current.get("protected_failed")) - _as_int(previous.get("protected_failed")),
    }


def _normalize_run(raw: dict, previous: dict | None = None) -> dict:
    run = {
        "run_id": str(raw.get("run_id") or "") or uuid4().hex[:12],
        "ran_at": str(raw.get("ran_at") or ""),
        "trigger": str(raw.get("trigger") or "manual").strip() or "manual",
        "triggered_by": str(raw.get("triggered_by") or "").strip(),
        "pass_rate_pct": _as_int(raw.get("pass_rate_pct")),
        "passed_total": _as_int(raw.get("passed_total")),
        "active_cases_total": _as_int(raw.get("active_cases_total")),
        "failed_total": _as_int(raw.get("failed_total")),
        "protected_total": _as_int(raw.get("protected_total")),
        "protected_failed": _as_int(raw.get("protected_failed")),
        "static_cases_total": _as_int(raw.get("static_cases_total")),
        "feedback_cases_total": _as_int(raw.get("feedback_cases_total")),
        "scenario_pack_rows": list(raw.get("scenario_pack_rows") or []),
    }
    run["delta"] = _delta(run, previous)
    return run


def load_bot_eval_run_history(store=None, *, limit: int = BOT_EVAL_RUN_HISTORY_LIMIT) -> dict:
    store = store or getattr(services, "store", None)
    if not store or not hasattr(store, "get_runtime_state"):
        return {"runs": [], "latest": None, "previous": None}

    state = store.get_runtime_state(BOT_EVAL_RUN_HISTORY_KEY) or {}
    raw_runs = [item for item in state.get("runs") or [] if isinstance(item, dict)]
    raw_runs.sort(key=lambda item: str(item.get("ran_at") or ""), reverse=True)
    normalized = []
    for index, item in enumerate(raw_runs[: max(limit, 0)]):
        previous = raw_runs[index + 1] if index + 1 < len(raw_runs) else None
        normalized.append(_normalize_run(item, previous))
    return {
        "runs": normalized,
        "latest": normalized[0] if normalized else None,
        "previous": normalized[1] if len(normalized) > 1 else None,
    }


def record_bot_eval_run(
    quality: dict,
    *,
    store=None,
    triggered_by: str = "",
    trigger: str = "manual",
) -> dict:
    store = store or getattr(services, "store", None)
    if not store or not hasattr(store, "set_runtime_state"):
        return {}

    current = _run_summary_from_quality(quality, triggered_by=triggered_by, trigger=trigger)
    history = load_bot_eval_run_history(store, limit=BOT_EVAL_RUN_HISTORY_LIMIT)
    runs = [current, *(history.get("runs") or [])]
    runs = runs[:BOT_EVAL_RUN_HISTORY_LIMIT]
    store.set_runtime_state(BOT_EVAL_RUN_HISTORY_KEY, {"runs": runs})
    previous = runs[1] if len(runs) > 1 else None
    return _normalize_run(current, previous)
