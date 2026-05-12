from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable


QA_MEMORY_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "qa"
    / "pragtico_test_questions_archive_20260512.json"
)
MAX_SNIPPET_CHARS = 2400
MIN_MATCH_SCORE = 0.42
STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "às",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "ou",
    "para",
    "por",
    "pode",
    "qual",
    "quais",
    "quanto",
    "quantos",
    "que",
    "sao",
    "são",
    "se",
    "tem",
    "tenho",
    "um",
    "uma",
}


@dataclass(frozen=True)
class QAMemoryRecord:
    question: str
    group: str
    risk: str
    source: str
    expected: tuple[str, ...]
    forbidden: tuple[str, ...]
    validated_answer: str
    answer_origin: str
    tokens: frozenset[str]


def _normalize(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()


def _tokens(value: object) -> frozenset[str]:
    return frozenset(
        token
        for token in _normalize(value).split()
        if len(token) > 2 and token not in STOPWORDS
    )


def _clean_text(value: object, *, max_chars: int = 700) -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "..."


def _as_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(_clean_text(item, max_chars=220) for item in value if _clean_text(item))
    if isinstance(value, str) and value.strip():
        return (_clean_text(value, max_chars=420),)
    return ()


def _iter_raw_records(payload: dict) -> Iterable[tuple[str, dict]]:
    for record in (payload.get("railway_150") or {}).get("records") or []:
        yield "railway_150", record
    for record in payload.get("complementary_questions") or []:
        yield "complementary_questions", record
    for record in payload.get("critical_matrix") or []:
        yield "critical_matrix", record
    for eval_file in payload.get("knowledge_evals") or []:
        label = f"knowledge_eval:{eval_file.get('file') or ''}".rstrip(":")
        for record in eval_file.get("records") or []:
            yield label, record
    for record in (payload.get("page_export") or {}).get("records") or []:
        yield "page_export", record


def _record_quality(record: QAMemoryRecord) -> tuple[int, int, int]:
    return (
        1 if record.validated_answer else 0,
        len(record.expected),
        1 if record.risk.strip().lower() == "critico" else 0,
    )


@lru_cache(maxsize=1)
def load_qa_memory_records(path: str | None = None) -> tuple[QAMemoryRecord, ...]:
    source_path = Path(path) if path else QA_MEMORY_PATH
    if not source_path.exists():
        return ()
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception:
        return ()

    records_by_question: dict[str, QAMemoryRecord] = {}
    for source, raw in _iter_raw_records(payload):
        question = _clean_text(raw.get("question"), max_chars=360)
        question_key = _normalize(question)
        if not question or not question_key:
            continue
        expected = _as_text_tuple(raw.get("expected_substrings")) or _as_text_tuple(raw.get("expected_tokens"))
        expected = expected or _as_text_tuple(raw.get("expected_summary"))
        forbidden = _as_text_tuple(raw.get("forbidden_substrings")) or _as_text_tuple(raw.get("forbidden_tokens"))
        forbidden = forbidden or _as_text_tuple(raw.get("forbidden_summary"))
        verdict = str(raw.get("verdict") or "").strip().lower()
        answer = ""
        if verdict in {"", "pass"} and not raw.get("manual_review"):
            answer = _clean_text(raw.get("answer_excerpt") or raw.get("answer"), max_chars=850)
        record = QAMemoryRecord(
            question=question,
            group=_clean_text(raw.get("group") or raw.get("suite") or raw.get("document"), max_chars=120),
            risk=_clean_text(raw.get("risk"), max_chars=40),
            source=source,
            expected=expected,
            forbidden=forbidden,
            validated_answer=answer,
            answer_origin=_clean_text(raw.get("answer_origin") or raw.get("expected_origin"), max_chars=80),
            tokens=_tokens(" ".join([question, " ".join(expected)])),
        )
        previous = records_by_question.get(question_key)
        if previous is None or _record_quality(record) > _record_quality(previous):
            records_by_question[question_key] = record

    return tuple(records_by_question.values())


def _score_record(query: str, query_tokens: frozenset[str], record: QAMemoryRecord) -> float:
    if not query_tokens or not record.tokens:
        return 0.0
    query_norm = _normalize(query)
    record_question_norm = _normalize(record.question)
    if query_norm == record_question_norm:
        return 1.0
    if len(query_norm) >= 18 and (query_norm in record_question_norm or record_question_norm in query_norm):
        return 0.92

    overlap = query_tokens & record.tokens
    if not overlap:
        return 0.0
    query_coverage = len(overlap) / len(query_tokens)
    record_coverage = len(overlap) / max(len(_tokens(record.question)), 1)
    jaccard = len(overlap) / len(query_tokens | record.tokens)
    rare_bonus = 0.05 if any(any(char.isdigit() for char in token) for token in overlap) else 0.0
    return min(0.9, (0.62 * query_coverage) + (0.23 * record_coverage) + (0.15 * jaccard) + rare_bonus)


def _format_case_lines(record: QAMemoryRecord, *, index: int, score: float) -> list[str]:
    lines = [
        f"Caso QA {index} (semelhança {score:.3f}; origem {record.source}; grupo {record.group or '--'}; risco {record.risk or '--'}):",
        f"- Pergunta de teste: {record.question}",
    ]
    if record.expected:
        lines.append("- Pontos/factos que a resposta deve preservar: " + " | ".join(record.expected[:8]))
    if record.forbidden:
        lines.append("- Pontos a evitar: " + " | ".join(record.forbidden[:6]))
    if record.answer_origin:
        lines.append(f"- Origem esperada/validada no teste: {record.answer_origin}")
    if record.validated_answer:
        lines.append(
            "- Resposta validada anterior, apenas como memória factual e não como texto final: "
            + record.validated_answer
        )
    return lines


def find_qa_memory_matches(question: str, *, limit: int = 2) -> list[tuple[QAMemoryRecord, float]]:
    query_tokens = _tokens(question)
    if len(query_tokens) < 2:
        return []
    matches = [
        (record, _score_record(question, query_tokens, record))
        for record in load_qa_memory_records()
    ]
    matches = [(record, score) for record, score in matches if score >= MIN_MATCH_SCORE]
    matches.sort(key=lambda item: item[1], reverse=True)
    return matches[:limit]


def build_qa_memory_sources(question: str, *, limit: int = 2) -> list[dict]:
    """Return curated QA memory as RAG context, never as a ready-to-send answer."""
    matches = find_qa_memory_matches(question, limit=limit)
    if not matches:
        return []

    lines = [
        "Memória QA operacional proveniente dos testes curados do bot.",
        f"Pergunta atual: {_clean_text(question, max_chars=300)}",
        "Usar como experiência prática: extrair factos, riscos e forma de raciocínio; reconciliar com IT, perfis de cais, dados live e regras determinísticas.",
        "Nunca copiar literalmente a resposta validada. Não responder de forma curta ou telegráfica; dar conclusão e fundamentação mínima.",
    ]
    for index, (record, score) in enumerate(matches, start=1):
        lines.extend(_format_case_lines(record, index=index, score=score))

    snippet = "\n".join(lines)
    if len(snippet) > MAX_SNIPPET_CHARS:
        snippet = snippet[: MAX_SNIPPET_CHARS - 1].rstrip() + "..."
    best_score = matches[0][1]
    return [
        {
            "source_id": "QAMEM1",
            "document": "memoria_qa_operacional",
            "chunk_id": 1,
            "score": round(best_score, 3),
            "retrieval_mode": "operational_qa_memory",
            "snippet": snippet,
            "text": snippet,
        }
    ]
