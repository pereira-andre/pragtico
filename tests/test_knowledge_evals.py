from __future__ import annotations

import json
from pathlib import Path

from domain.knowledge_evals import evaluate_companion_case


def test_wind_suspension_eval_accepts_rigorous_threshold_wording() -> None:
    cases = json.loads(Path("knowledge/evals/golden_operational_companion_evals.json").read_text(encoding="utf-8"))
    case = next(
        item
        for item in cases
        if item.get("question") == "Se não fosse nevoeiro e estivesse 31 kts de vento, já podia sair desde que tivesse 4 reboques?"
    )

    result = evaluate_companion_case(case, "knowledge")

    assert result["passed"] is True
    assert result["missing_substrings"] == []
    assert result["answer_origin"] == "operational_safety_limit"
