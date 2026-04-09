#!/usr/bin/env python3
"""Generate structured knowledge companion JSON files from knowledge documents."""

from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from domain.document_processing import is_allowed_document
from domain.knowledge_companions import (
    auto_build_document_companion,
    build_companion_scaffold,
    companion_directory,
    load_document_companion,
)


def _write_companion(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _generate_companion(document_name: str, knowledge_dir: str, *, scaffold_only: bool) -> dict | None:
    if scaffold_only:
        existing = auto_build_document_companion(document_name, knowledge_dir)
        title = (existing or {}).get("title", "")
        return build_companion_scaffold(document_name, title=title)
    return auto_build_document_companion(document_name, knowledge_dir)


def _iter_documents(knowledge_dir: str) -> list[str]:
    return [
        entry
        for entry in sorted(os.listdir(knowledge_dir))
        if is_allowed_document(entry) and os.path.isfile(os.path.join(knowledge_dir, entry))
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate knowledge companion JSON files.")
    parser.add_argument("document", nargs="?", help="Knowledge document filename, e.g. IT-036_RegulacaoAgulhas.txt")
    parser.add_argument("--all", action="store_true", help="Generate companions for all knowledge documents.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing companion files.")
    parser.add_argument("--scaffold", action="store_true", help="Generate an empty scaffold instead of an auto-filled companion.")
    args = parser.parse_args()

    if not args.all and not args.document:
        parser.error("Indica um documento ou usa --all.")

    knowledge_dir = os.path.join(PROJECT_ROOT, "knowledge")
    companions_dir = companion_directory(knowledge_dir)
    os.makedirs(companions_dir, exist_ok=True)

    documents = _iter_documents(knowledge_dir) if args.all else [args.document]
    written_paths: list[str] = []

    for document_name in documents:
        if not document_name:
            continue
        document_path = os.path.join(knowledge_dir, document_name)
        if not os.path.isfile(document_path):
            print(f"Documento não encontrado: {document_path}", file=sys.stderr)
            return 1
        stem, _suffix = os.path.splitext(document_name)
        companion_path = os.path.join(companions_dir, f"{stem}.json")
        if os.path.exists(companion_path) and not args.force:
            continue
        payload = _generate_companion(document_name, knowledge_dir, scaffold_only=args.scaffold)
        if not payload:
            print(f"Não consegui gerar companion para: {document_name}", file=sys.stderr)
            return 1
        _write_companion(companion_path, payload)
        written_paths.append(companion_path)

    if args.all and not written_paths:
        for document_name in documents:
            companion = load_document_companion(document_name, knowledge_dir)
            if companion:
                written_paths.append(os.path.join(companions_dir, f"{os.path.splitext(document_name)[0]}.json"))

    for path in written_paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
