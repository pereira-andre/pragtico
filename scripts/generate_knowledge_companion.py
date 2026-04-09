#!/usr/bin/env python3
"""Scaffold a structured knowledge companion JSON for a knowledge document."""

from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from domain.document_processing import extract_text_from_path
from domain.knowledge_companions import build_companion_scaffold, companion_directory


def _infer_title_from_document(path: str) -> str:
    try:
        text = extract_text_from_path(path)
    except Exception:
        return ""
    for line in text.splitlines():
        clean = " ".join(str(line).split()).strip()
        if clean:
            return clean[:160]
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a knowledge companion scaffold JSON.")
    parser.add_argument("document", help="Knowledge document filename, e.g. IT-036_RegulacaoAgulhas.txt")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing companion file.")
    args = parser.parse_args()

    knowledge_dir = os.path.join(PROJECT_ROOT, "knowledge")
    document_path = os.path.join(knowledge_dir, args.document)
    if not os.path.isfile(document_path):
        print(f"Documento não encontrado: {document_path}", file=sys.stderr)
        return 1

    companions_dir = companion_directory(knowledge_dir)
    os.makedirs(companions_dir, exist_ok=True)
    stem, _suffix = os.path.splitext(args.document)
    companion_path = os.path.join(companions_dir, f"{stem}.json")
    if os.path.exists(companion_path) and not args.force:
        print(f"O companion já existe: {companion_path}", file=sys.stderr)
        return 1

    scaffold = build_companion_scaffold(
        args.document,
        title=_infer_title_from_document(document_path),
    )
    with open(companion_path, "w", encoding="utf-8") as handle:
        json.dump(scaffold, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(companion_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
