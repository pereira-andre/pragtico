from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from domain.document_processing import extract_text_from_path
from domain.knowledge_chunking import structured_chunk_document


ALLOWED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx", ".csv"}


def _iter_documents(knowledge_dir: str):
    for name in sorted(os.listdir(knowledge_dir)):
        if os.path.splitext(name)[1].lower() not in ALLOWED_EXTENSIONS:
            continue
        path = os.path.join(knowledge_dir, name)
        if os.path.isfile(path):
            yield name, path


def _risky_mix(entity_names: list[str]) -> bool:
    pairs = (
        {"Secil W", "Secil E"},
        {"SAPEC solidos", "SAPEC liquidos"},
        {"Tanquisado", "Eco-Oil"},
        {"Fundeadouro Norte", "Fundeadouro Sul"},
    )
    names = set(entity_names or [])
    return any(pair <= names for pair in pairs)


def audit(knowledge_dir: str, sample: int, *, fail_on_risk: bool = False) -> int:
    total_docs = 0
    total_chunks = 0
    risky_chunks = []
    content_types = Counter()
    content_scopes = Counter()
    entities = Counter()

    for name, path in _iter_documents(knowledge_dir):
        total_docs += 1
        text = extract_text_from_path(path)
        chunks = structured_chunk_document(text, document_name=name)
        total_chunks += len(chunks)
        print(f"\n## {name} | chunks={len(chunks)}")
        for index, chunk in enumerate(chunks[:sample], start=1):
            entity_names = chunk.get("entity_names") or []
            print(
                f"- {index}: section={chunk.get('section') or '--'} | "
                f"entity={', '.join(entity_names) or '--'} | "
                f"type={chunk.get('content_type') or '--'}"
            )
        for chunk in chunks:
            content_types[chunk.get("content_type") or ""] += 1
            content_scopes[chunk.get("content_scope") or ""] += 1
            for entity in chunk.get("entity_names") or []:
                entities[entity] += 1
            if _risky_mix(chunk.get("entity_names") or []):
                risky_chunks.append((name, chunk.get("section"), chunk.get("entity_names") or []))

    print("\n== Resumo ==")
    print(f"Documentos: {total_docs}")
    print(f"Chunks estruturados: {total_chunks}")
    print("Tipos de conteudo:", dict(content_types.most_common()))
    print("Escopos:", dict(content_scopes.most_common()))
    print("Entidades:", dict(entities.most_common()))
    print(f"Chunks com mistura critica: {len(risky_chunks)}")
    for name, section, entity_names in risky_chunks[:20]:
        print(f"- {name} | {section} | {', '.join(entity_names)}")
    if len(risky_chunks) > 20:
        print(f"- ... +{len(risky_chunks) - 20}")
    return 1 if fail_on_risk and risky_chunks else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita chunks estruturados da pasta knowledge sem reindexar.")
    parser.add_argument("--knowledge-dir", default=os.path.join(PROJECT_ROOT, "knowledge"))
    parser.add_argument("--sample", type=int, default=3)
    parser.add_argument("--fail-on-risk", action="store_true")
    args = parser.parse_args()
    return audit(args.knowledge_dir, max(args.sample, 0), fail_on_risk=args.fail_on_risk)


if __name__ == "__main__":
    raise SystemExit(main())
