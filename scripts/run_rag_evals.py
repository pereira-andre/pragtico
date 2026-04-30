from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.chat_runtime import _contextual_lookup_question
from domain.document_processing import extract_text_from_path
from domain.knowledge_chunking import structured_chunk_document
from integrations.rag_engine import SimpleRAGEngine


ALLOWED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx", ".csv"}


@dataclass
class EvalCase:
    question: str
    expected_entities: list[str] = field(default_factory=list)
    forbidden_entities: list[str] = field(default_factory=list)
    expected_documents: list[str] = field(default_factory=list)
    expected_terms: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)


EVAL_CASES = [
    EvalCase(
        question="Que restrições existem na Eco-Oil?",
        expected_entities=["Eco-Oil"],
        forbidden_entities=["SAPEC solidos", "SAPEC liquidos", "Secil W", "Secil E"],
        expected_documents=["IT-008_EcoOil.txt"],
        expected_terms=["reponto", "calado"],
    ),
    EvalCase(
        question="Fala-me do cais da Tanquisado em termos gerais.",
        expected_entities=["Tanquisado"],
        forbidden_entities=["SAPEC solidos", "SAPEC liquidos", "Secil W", "Secil E"],
        expected_documents=["IT-010_Tanquisado.txt"],
    ),
    EvalCase(
        question="Que restrições existem na SAPEC líquidos?",
        expected_entities=["SAPEC liquidos"],
        forbidden_entities=["SAPEC solidos", "Secil W", "Secil E", "Eco-Oil", "Tanquisado"],
        expected_documents=["IT-029_SAPEC.txt"],
        expected_terms=["TGL", "calado"],
    ),
    EvalCase(
        question="E na SAPEC sólidos?",
        history=[{"role": "user", "content": "Que restrições existem na SAPEC líquidos?"}],
        expected_entities=["SAPEC solidos"],
        forbidden_entities=["SAPEC liquidos", "Secil W", "Secil E", "Eco-Oil", "Tanquisado"],
        expected_documents=["IT-029_SAPEC.txt"],
        expected_terms=["TPS", "calado"],
    ),
    EvalCase(
        question="O que diz o documento sobre a Secil W?",
        expected_entities=["Secil W"],
        forbidden_entities=["Secil E", "SAPEC solidos", "SAPEC liquidos"],
        expected_documents=["IT-009_Secil.txt"],
    ),
    EvalCase(
        question="E a Secil E?",
        history=[{"role": "user", "content": "O que diz o documento sobre a Secil W?"}],
        expected_entities=["Secil E"],
        forbidden_entities=["Secil W", "SAPEC solidos", "SAPEC liquidos"],
        expected_documents=["IT-009_Secil.txt"],
    ),
    EvalCase(
        question="Há alguma regra sobre calado?",
        expected_terms=["calado"],
    ),
    EvalCase(
        question="Preciso de rebocadores?",
        expected_documents=["IT-016_Rebocadores.txt"],
        expected_terms=["rebocador"],
    ),
]


def _load_chunks(knowledge_dir: str) -> list[dict]:
    chunks: list[dict] = []
    for name in sorted(os.listdir(knowledge_dir)):
        if os.path.splitext(name)[1].lower() not in ALLOWED_EXTENSIONS:
            continue
        path = os.path.join(knowledge_dir, name)
        if not os.path.isfile(path):
            continue
        text = extract_text_from_path(path)
        for chunk_id, chunk in enumerate(structured_chunk_document(text, document_name=name), start=1):
            chunks.append(
                {
                    "id": f"{name}:{chunk_id}",
                    "document": name,
                    "chunk_id": chunk_id,
                    **chunk,
                }
            )
    return chunks


def _run_case(engine: SimpleRAGEngine, chunks: list[dict], case: EvalCase, top_k: int) -> dict:
    rewritten = _contextual_lookup_question(case.question, case.history)
    query_entities = engine._query_entities(rewritten)
    lexical = engine._lexical_search(rewritten, chunks, max(top_k * 8, top_k), query_entities=query_entities)
    results = engine._rerank_candidates(rewritten, lexical, query_entities, top_k)
    top_entities = {
        entity
        for item in results
        for entity in (item.get("entity_names") or [])
    }
    top_documents = {item.get("document") for item in results}
    combined_text = " ".join(str(item.get("text") or "") for item in results).lower()

    checks = {
        "expected_entities": all(entity in top_entities for entity in case.expected_entities),
        "forbidden_entities": not any(entity in top_entities for entity in case.forbidden_entities),
        "expected_documents": all(doc in top_documents for doc in case.expected_documents),
        "expected_terms": all(term.lower() in combined_text for term in case.expected_terms),
    }
    passed = all(checks.values())
    return {
        "question": case.question,
        "rewritten_question": rewritten,
        "passed": passed,
        "checks": checks,
        "top_results": [
            {
                "document": item.get("document"),
                "chunk_id": item.get("chunk_id"),
                "section": item.get("section"),
                "entities": item.get("entity_names") or [],
                "content_type": item.get("content_type"),
                "content_scope": item.get("content_scope"),
                "score": round(float(item.get("score") or 0), 3),
                "mode": item.get("retrieval_mode"),
            }
            for item in results
        ],
    }


def run_evals(knowledge_dir: str, top_k: int) -> list[dict]:
    chunks = _load_chunks(knowledge_dir)
    engine = SimpleRAGEngine.__new__(SimpleRAGEngine)
    return [_run_case(engine, chunks, case, top_k) for case in EVAL_CASES]


def main() -> int:
    parser = argparse.ArgumentParser(description="Corre evals RAG em memória, sem LLM e sem tocar na BD.")
    parser.add_argument("--knowledge-dir", default=os.path.join(PROJECT_ROOT, "knowledge"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-fail", action="store_true")
    args = parser.parse_args()

    results = run_evals(args.knowledge_dir, max(args.top_k, 1))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            status = "PASS" if result["passed"] else "FAIL"
            print(f"\n[{status}] {result['question']}")
            if result["rewritten_question"] != result["question"]:
                print(f"  query: {result['rewritten_question']}")
            print(f"  checks: {result['checks']}")
            for item in result["top_results"][:3]:
                print(
                    "  - "
                    f"{item['document']} #{item['chunk_id']} | "
                    f"{item['section']} | "
                    f"{', '.join(item['entities']) or '--'} | "
                    f"{item.get('content_scope') or '--'} | "
                    f"{item['score']}"
                )
        passed = sum(1 for result in results if result["passed"])
        print(f"\nResumo: {passed}/{len(results)} evals passaram.")

    failed = [result for result in results if not result["passed"]]
    return 1 if args.fail_on_fail and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
