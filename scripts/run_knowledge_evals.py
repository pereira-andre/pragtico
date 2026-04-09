#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _dedupe_cases(cases: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for item in cases:
        key = (
            str(item.get("source_message_id") or "").strip()
            or f"{str(item.get('document') or '').strip().lower()}::{str(item.get('question') or '').strip().lower()}"
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from domain.knowledge_evals import (
        evaluate_companion_cases,
        load_eval_cases_from_dir,
        load_eval_cases_from_store,
    )
    from storage import create_store

    knowledge_dir = repo_root / "knowledge"
    cases_path = knowledge_dir / "evals"
    data_dir = repo_root / "data"

    cases = load_eval_cases_from_dir(cases_path)
    try:
        store = create_store(data_dir=str(data_dir), knowledge_dir=str(knowledge_dir))
    except Exception as exc:
        print(f"Knowledge evals warning: não consegui carregar casos persistidos do storage ({exc})")
        store_cases = []
    else:
        store_cases = load_eval_cases_from_store(store)
    cases = _dedupe_cases(cases + store_cases)
    results = evaluate_companion_cases(cases, knowledge_dir)
    passed = sum(1 for item in results if item["passed"])
    failed = [item for item in results if not item["passed"]]

    print(f"Knowledge evals: {passed}/{len(results)} passed")
    for item in results:
        status = "OK" if item["passed"] else "FAIL"
        print(f"[{status}] {item['document']} :: {item['question']}")
        if item["missing_substrings"]:
            print("  Missing:", "; ".join(item["missing_substrings"]))
            preview = (item["answer"] or "").strip().replace("\n", " ")
            print("  Answer:", preview[:220] or "<empty>")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
