from __future__ import annotations

from argparse import Namespace

from scripts.pragtico_bug_hunter import (
    Scenario,
    TransportResult,
    budget_decision,
    build_scenarios,
    evaluate_answer,
    extract_csrf_token,
    record_budget_turn,
)


def _args(**overrides):
    defaults = {
        "force": False,
        "daily_turn_limit": 12,
        "monthly_turn_limit": 250,
        "estimated_cost_per_turn": 0.003,
        "daily_cost_cap": 0.06,
        "monthly_cost_cap": 1.50,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def test_build_scenarios_produces_hundreds_of_site_questions() -> None:
    scenarios = build_scenarios(limit=360)

    assert len(scenarios) >= 300
    assert any("LISNAVE" in item.question for item in scenarios)
    assert any("Secil" in item.question or "SECIL" in item.question for item in scenarios)
    assert any("reboc" in item.question.casefold() or "reboq" in item.question.casefold() for item in scenarios)
    assert any("mare" in item.question.casefold() for item in scenarios)


def test_evaluate_answer_flags_strict_missing_expected_tokens() -> None:
    scenario = Scenario(
        id="strict",
        group="Rebocadores",
        question="Quantos rebocadores para RORO de 230m?",
        expected_substrings=("4 rebocadores grandes",),
        strict=True,
    )

    checks = evaluate_answer(scenario, TransportResult(ok=True, answer="Usaria 2 rebocadores."))

    assert checks["verdict"] == "fail"
    assert checks["missing_expected"] == ["4 rebocadores grandes"]


def test_evaluate_answer_marks_non_strict_missing_as_review() -> None:
    scenario = Scenario(
        id="variant",
        group="Rebocadores",
        question="Qts reboques para RORO de 230m?",
        expected_substrings=("4 rebocadores grandes",),
        strict=False,
    )

    checks = evaluate_answer(scenario, TransportResult(ok=True, answer="Usaria 4 grandes."))

    assert checks["verdict"] == "review"
    assert checks["manual_review"] is True


def test_extract_csrf_token_from_login_form() -> None:
    html = '<input type="hidden" name="csrf_token" value="abc123">'

    assert extract_csrf_token(html) == "abc123"


def test_budget_guard_stops_after_daily_turn_limit() -> None:
    args = _args(daily_turn_limit=1)
    state = {"budget": {}}

    allowed, _reason, _budget = budget_decision(state, args)
    assert allowed is True

    record_budget_turn(state, args)
    allowed, reason, _budget = budget_decision(state, args)

    assert allowed is False
    assert reason == "daily_turn_limit_1"
