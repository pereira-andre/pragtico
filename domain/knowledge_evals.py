from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from domain.knowledge_companions import build_companion_answer, load_document_companion

AUTO_FEEDBACK_EVALS_FILENAME = "operator_feedback_correction_evals.json"
EVAL_TOKEN_STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "é",
    "foi",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "ou",
    "para",
    "por",
    "que",
    "se",
    "um",
    "uma",
}


def default_eval_cases_path(knowledge_dir: str | Path) -> Path:
    return Path(knowledge_dir) / "evals" / AUTO_FEEDBACK_EVALS_FILENAME


def _normalize_eval_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _eval_identity_key(document: str, question: str) -> str:
    return f"{_normalize_eval_text(document)}::{_normalize_eval_text(question)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_expected_substrings(corrected_answer: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(corrected_answer or "").strip())
    if not text:
        return []

    anchors: list[str] = []
    patterns = (
        r"\b\d+(?:[.,]\d+)?\s*(?:milhas(?:\s+náuticas)?|metros?|m|horas?|dias?|minutos?|%)\b",
        r"\bbaliza(?:\s+número)?\s+\d+\b",
        r"\bloa\s+(?:igual ou superior|superior|inferior)\s+a?\s*\d+(?:[.,]\d+)?\s*metros?\b",
        r"\bit-\d{3}\b",
        r"\brg-\d+\b",
        r"\bp-\d+\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidate = re.sub(r"\s+", " ", match.group(0).strip())
            if candidate and candidate not in anchors:
                anchors.append(candidate)
    return anchors[:4]


def _expected_answer_terms(text: str) -> list[str]:
    tokens = re.findall(r"[a-zà-ÿ0-9][a-zà-ÿ0-9.,%-]*", _normalize_eval_text(text))
    terms: list[str] = []
    for token in tokens:
        clean = token.strip(".,")
        if not clean:
            continue
        if clean in EVAL_TOKEN_STOPWORDS:
            continue
        if len(clean) < 4 and not any(char.isdigit() for char in clean):
            continue
        if clean not in terms:
            terms.append(clean)
    return terms[:10]


def _term_coverage(answer: str, expected_answer: str) -> tuple[float, list[str]]:
    terms = _expected_answer_terms(expected_answer)
    if not terms:
        return 1.0, []
    answer_terms = set(_expected_answer_terms(answer))
    missing = [term for term in terms if term not in answer_terms]
    coverage = (len(terms) - len(missing)) / len(terms)
    return coverage, missing


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
        expected_answer = str(item.get("expected_answer") or "").strip()
        expected_substrings = [
            str(value).strip()
            for value in (item.get("expected_substrings") or [])
            if str(value or "").strip()
        ]
        if document and question and (expected_substrings or expected_answer):
            cases.append(
                {
                    "document": document,
                    "question": question,
                    "expected_substrings": expected_substrings,
                    "expected_answer": expected_answer,
                    "source": str(item.get("source") or "").strip(),
                    "updated_by": str(item.get("updated_by") or "").strip(),
                    "source_message_id": str(item.get("source_message_id") or "").strip(),
                    "eval_type": str(item.get("eval_type") or "companion").strip() or "companion",
                    "expected_answer_origin": str(item.get("expected_answer_origin") or "").strip(),
                }
            )
    return cases


def load_eval_cases_from_dir(path: str | Path) -> list[dict]:
    eval_dir = Path(path)
    if not eval_dir.exists():
        return []
    cases: list[dict] = []
    for file_path in sorted(eval_dir.glob("*.json")):
        cases.extend(load_eval_cases(file_path))
    return cases


def load_eval_cases_from_store(store, *, source: str = "") -> list[dict]:
    if not store or not hasattr(store, "list_feedback_eval_cases"):
        return []
    cases: list[dict] = []
    for item in store.list_feedback_eval_cases(source=source):
        document = str(item.get("document") or "").strip()
        question = str(item.get("question") or "").strip()
        expected_answer = str(item.get("expected_answer") or "").strip()
        expected_substrings = [
            str(value).strip()
            for value in (item.get("expected_substrings") or [])
            if str(value or "").strip()
        ]
        if document and question and (expected_substrings or expected_answer):
            cases.append(
                {
                    "document": document,
                    "question": question,
                    "expected_substrings": expected_substrings,
                    "expected_answer": expected_answer,
                    "source": str(item.get("source") or "").strip(),
                    "updated_by": str(item.get("updated_by") or "").strip(),
                    "source_message_id": str(item.get("source_message_id") or "").strip(),
                    "eval_type": str(item.get("eval_type") or "companion").strip() or "companion",
                    "expected_answer_origin": str(item.get("expected_answer_origin") or "").strip(),
                }
            )
    return cases


def register_feedback_correction_eval(
    knowledge_dir: str | Path,
    *,
    document: str,
    question: str,
    corrected_answer: str,
    feedback_note: str = "",
    updated_by: str = "",
    source: str = "",
    source_message_id: str = "",
) -> dict:
    document = str(document or "").strip()
    question = str(question or "").strip()
    corrected_answer = str(corrected_answer or "").strip()
    if not document or not question or not corrected_answer:
        raise ValueError("Documento, pergunta e resposta corrigida são obrigatórios.")

    target_path = default_eval_cases_path(knowledge_dir)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            payload = []
    else:
        payload = []

    case = {
        "document": document,
        "question": question,
        "expected_answer": corrected_answer,
        "expected_substrings": _extract_expected_substrings(corrected_answer),
        "feedback_note": str(feedback_note or "").strip(),
        "updated_by": str(updated_by or "").strip(),
        "source": str(source or "").strip(),
        "source_message_id": str(source_message_id or "").strip(),
        "updated_at": _now_iso(),
    }

    replacement_index = None
    identity_key = _eval_identity_key(document, question)
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        existing_source_message_id = str(item.get("source_message_id") or "").strip()
        if case["source_message_id"] and existing_source_message_id == case["source_message_id"]:
            replacement_index = index
            break
        if _eval_identity_key(item.get("document", ""), item.get("question", "")) == identity_key:
            replacement_index = index
            break

    if replacement_index is None:
        payload.append(case)
    else:
        payload[replacement_index] = case

    payload.sort(
        key=lambda item: (
            str(item.get("document") or ""),
            str(item.get("question") or ""),
        )
    )
    target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return case


def remove_feedback_correction_eval(
    knowledge_dir: str | Path,
    *,
    source_message_id: str = "",
    document: str = "",
    question: str = "",
) -> int:
    target_path = default_eval_cases_path(knowledge_dir)
    if not target_path.exists():
        return 0
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return 0

    clean_source_message_id = str(source_message_id or "").strip()
    identity_key = _eval_identity_key(document, question) if document and question else ""
    filtered = []
    removed = 0
    for item in payload:
        if not isinstance(item, dict):
            filtered.append(item)
            continue
        if clean_source_message_id and str(item.get("source_message_id") or "").strip() == clean_source_message_id:
            removed += 1
            continue
        if identity_key and _eval_identity_key(item.get("document", ""), item.get("question", "")) == identity_key:
            removed += 1
            continue
        filtered.append(item)

    if removed:
        target_path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return removed


def evaluate_companion_case(case: dict, knowledge_dir: str | Path) -> dict:
    knowledge_dir = str(knowledge_dir)
    eval_type = str(case.get("eval_type") or "companion").strip() or "companion"
    answer_origin = "document_companion"
    if eval_type == "direct_operational":
        from core.operational_sources import answer_direct_operational_query

        result = answer_direct_operational_query(case["question"]) or {}
        answer = str(result.get("answer") or "")
        answer_origin = str(result.get("answer_origin") or "")
    elif eval_type == "scope_guard":
        from core.chat_planner import build_chat_execution_plan
        from domain.scope_guard import build_scope_guard_answer, evaluate_scope_guard

        plan = build_chat_execution_plan(case["question"])
        decision = evaluate_scope_guard(case["question"], plan=plan)
        result = build_scope_guard_answer(decision) if decision.blocked else {}
        answer = str(result.get("answer") or "")
        answer_origin = str(result.get("answer_origin") or "")
    else:
        companion = load_document_companion(case["document"], knowledge_dir)
        answer = build_companion_answer(case["question"], companion) if companion else ""
    answer_lower = answer.casefold()
    missing = [item for item in case["expected_substrings"] if item.casefold() not in answer_lower]
    coverage, missing_terms = _term_coverage(answer, case.get("expected_answer", ""))
    expected_answer = str(case.get("expected_answer") or "").strip()
    expected_answer_origin = str(case.get("expected_answer_origin") or "").strip()
    if expected_answer and case["expected_substrings"]:
        semantic_pass = coverage >= 0.35
    elif expected_answer:
        semantic_pass = coverage >= 0.55
    else:
        semantic_pass = True
    origin_pass = not expected_answer_origin or answer_origin == expected_answer_origin
    return {
        "document": case["document"],
        "question": case["question"],
        "answer": answer,
        "eval_type": eval_type,
        "answer_origin": answer_origin,
        "expected_answer_origin": expected_answer_origin,
        "expected_answer": expected_answer,
        "expected_substrings": list(case.get("expected_substrings") or []),
        "source": str(case.get("source") or "").strip(),
        "updated_by": str(case.get("updated_by") or "").strip(),
        "source_message_id": str(case.get("source_message_id") or "").strip(),
        "origin": str(case.get("origin") or "").strip(),
        "origin_label": str(case.get("origin_label") or "").strip(),
        "missing_substrings": missing,
        "missing_terms": missing_terms,
        "term_coverage": round(coverage, 3),
        "passed": not missing and semantic_pass and origin_pass and bool(answer.strip()),
    }


def evaluate_companion_cases(cases: Iterable[dict], knowledge_dir: str | Path) -> list[dict]:
    return [evaluate_companion_case(case, knowledge_dir) for case in cases]
