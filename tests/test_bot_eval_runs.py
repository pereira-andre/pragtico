from __future__ import annotations

from core import bot_eval_runs
from core.bot_eval_runs import load_bot_eval_run_history, record_bot_eval_run


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.state: dict[str, dict] = {}

    def get_runtime_state(self, key: str) -> dict:
        return self.state.get(key, {})

    def set_runtime_state(self, key: str, value: dict) -> dict:
        self.state[key] = value
        return value


def _quality(*, passed: int, total: int, failed: int, protected_failed: int = 0) -> dict:
    return {
        "pass_rate_pct": round((passed / total) * 100) if total else 0,
        "passed_total": passed,
        "active_cases_total": total,
        "failed_total": failed,
        "protected_total": 4,
        "protected_failed": protected_failed,
        "static_cases_total": total,
        "feedback_cases_total": 0,
        "scenario_pack_rows": [
            {
                "label": "Rebocadores e vento",
                "total": total,
                "passed": passed,
                "failed": failed,
                "coverage_pct": round((passed / total) * 100) if total else 0,
                "state": "online" if not failed else "degraded",
            }
        ],
    }


def test_record_bot_eval_run_persists_latest_first() -> None:
    store = FakeRuntimeStore()

    first = record_bot_eval_run(_quality(passed=8, total=10, failed=2), store=store, triggered_by="admin")
    second = record_bot_eval_run(_quality(passed=10, total=10, failed=0), store=store, triggered_by="admin")
    history = load_bot_eval_run_history(store)

    assert history["latest"]["run_id"] == second["run_id"]
    assert history["previous"]["run_id"] == first["run_id"]
    assert history["latest"]["delta"]["pass_rate"] == 20
    assert history["latest"]["delta"]["failed"] == -2
    assert history["latest"]["scenario_pack_rows"][0]["label"] == "Rebocadores e vento"


def test_record_bot_eval_run_limits_history(monkeypatch) -> None:
    store = FakeRuntimeStore()
    monkeypatch.setattr(bot_eval_runs, "BOT_EVAL_RUN_HISTORY_LIMIT", 3)

    for index in range(5):
        record_bot_eval_run(_quality(passed=index + 1, total=10, failed=9 - index), store=store)

    history = load_bot_eval_run_history(store, limit=10)
    assert len(history["runs"]) == 3
