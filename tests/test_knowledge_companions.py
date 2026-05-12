from __future__ import annotations

from domain.knowledge_companions import (
    build_companion_answer,
    find_best_global_companion_match,
    load_document_companion,
)


def test_sapec_example_calculation_is_explained_with_premises() -> None:
    companion = load_document_companion("IT-029_SAPEC.txt", "knowledge")

    answer = build_companion_answer(
        "Qual o calado praticável no TPS com altura de água de 1,8 m para carga não IMO?",
        companion or {},
    )

    assert "exemplo" in answer.lower()
    assert "7,6 m de base + altura de água" in answer
    assert "7,6 + 1,8 = 9,4 m" in answer
    assert "calado real do navio" in answer


def test_short_non_imo_follow_up_does_not_reuse_specific_water_height_example() -> None:
    match = find_best_global_companion_match("E carga não IMO", "knowledge")

    assert match is None or "1,8" not in match["answer"]
    assert match is None or "9,4" not in match["answer"]
