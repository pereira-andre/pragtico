#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from domain.operational_qa_memory import (  # noqa: E402
    DEFAULT_QA_MEMORY_PATHS,
    KNOWLEDGE_DIR,
    QA_MEMORY_AUDIT_PATH,
    audit_qa_memory_records,
    load_knowledge_audit_corpus,
    load_qa_memory_audit_report,
    load_qa_memory_records,
    qa_memory_supported_questions,
)


def _escape_cell(value: object, *, max_chars: int = 180) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) > max_chars:
        clean = clean[: max_chars - 1].rstrip() + "..."
    return clean.replace("|", "\\|")


def _first_support_document(record: dict) -> str:
    for support in record.get("support") or []:
        if support.get("document"):
            return str(support.get("document"))
    return ""


def build_payload() -> dict:
    load_knowledge_audit_corpus.cache_clear()
    load_qa_memory_records.cache_clear()
    audit_qa_memory_records.cache_clear()
    load_qa_memory_audit_report.cache_clear()
    qa_memory_supported_questions.cache_clear()

    records = list(audit_qa_memory_records())
    counts = Counter(str(record.get("status") or "unknown") for record in records)
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "qa_sources": [
            str(source_path.relative_to(REPO_ROOT))
            for source_path in DEFAULT_QA_MEMORY_PATHS
            if source_path.exists()
        ],
        "knowledge_dir": str(KNOWLEDGE_DIR.relative_to(REPO_ROOT)),
        "runtime_rule": "Only records with status=supported are exposed to operational_qa_memory.",
        "summary": {
            "total": len(records),
            "supported": counts.get("supported", 0),
            "review": counts.get("review", 0),
            "out_of_scope": counts.get("out_of_scope", 0),
            "unknown": counts.get("unknown", 0),
        },
        "records": records,
    }


def write_markdown(payload: dict, path: Path) -> None:
    summary = payload.get("summary") or {}
    lines = [
        "# QA Memory Knowledge Audit",
        "",
        f"- Generated at: `{payload.get('generated_at')}`",
        "- QA sources: "
        + ", ".join(f"`{source}`" for source in payload.get("qa_sources") or []),
        f"- Knowledge dir: `{payload.get('knowledge_dir')}`",
        f"- Runtime rule: {payload.get('runtime_rule')}",
        "",
        "## Summary",
        "",
        f"- Total reviewed: {summary.get('total', 0)}",
        f"- Supported and active: {summary.get('supported', 0)}",
        f"- Needs review: {summary.get('review', 0)}",
        f"- Out of scope for static memory: {summary.get('out_of_scope', 0)}",
        "",
        "## Question Review",
        "",
        "| # | Status | Risk | Group | Question | Reason | Evidence document |",
        "|---:|---|---|---|---|---|---|",
    ]
    for index, record in enumerate(payload.get("records") or [], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    _escape_cell(record.get("status"), max_chars=24),
                    _escape_cell(record.get("risk"), max_chars=24),
                    _escape_cell(record.get("group"), max_chars=64),
                    _escape_cell(record.get("question"), max_chars=220),
                    _escape_cell(record.get("reason"), max_chars=140),
                    _escape_cell(_first_support_document(record), max_chars=90),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit curated QA memory against knowledge files.")
    parser.add_argument("--json", type=Path, default=QA_MEMORY_AUDIT_PATH)
    parser.add_argument(
        "--markdown",
        type=Path,
        default=QA_MEMORY_AUDIT_PATH.with_suffix(".md"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload()
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(payload, args.markdown)
    print(
        "QA memory audit: "
        f"{payload['summary']['supported']} supported, "
        f"{payload['summary']['review']} review, "
        f"{payload['summary']['out_of_scope']} out_of_scope "
        f"({payload['summary']['total']} total)"
    )
    print(args.json)
    print(args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
