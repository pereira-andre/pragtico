from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from domain.knowledge_companions import build_companion_answer, load_document_companion


def load_eval_cases(path: str | Path) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("O ficheiro de evals deve conter uma lista de casos.")
    cases: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        document = str(item.get("document") or "").strip()
        question = str(item.get("question") or "").strip()
        expected_substrings = [
            str(value).strip()
            for value in (item.get("expected_substrings") or [])
            if str(value or "").strip()
        ]
        if document and question and expected_substrings:
            cases.append(
                {
                    "document": document,
                    "question": question,
                    "expected_substrings": expected_substrings,
                }
            )
    return cases


def evaluate_companion_case(case: dict, knowledge_dir: str | Path) -> dict:
    knowledge_dir = str(knowledge_dir)
    companion = load_document_companion(case["document"], knowledge_dir)
    answer = build_companion_answer(case["question"], companion) if companion else ""
    answer_lower = answer.casefold()
    missing = [item for item in case["expected_substrings"] if item.casefold() not in answer_lower]
    return {
        "document": case["document"],
        "question": case["question"],
        "answer": answer,
        "missing_substrings": missing,
        "passed": not missing and bool(answer.strip()),
    }


def evaluate_companion_cases(cases: Iterable[dict], knowledge_dir: str | Path) -> list[dict]:
    return [evaluate_companion_case(case, knowledge_dir) for case in cases]
