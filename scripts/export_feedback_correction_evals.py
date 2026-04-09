#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from domain.knowledge_evals import load_eval_cases_from_store
    from storage import create_store

    knowledge_dir = repo_root / "knowledge"
    data_dir = repo_root / "data"
    output_path = Path(argv[0]).expanduser().resolve() if argv else (data_dir / "exports" / "operator_feedback_correction_evals.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    store = create_store(data_dir=str(data_dir), knowledge_dir=str(knowledge_dir))
    cases = load_eval_cases_from_store(store)
    output_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(cases)} feedback eval case(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
