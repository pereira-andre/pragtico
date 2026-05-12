from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
QA_MEMORY_PATH = REPO_ROOT / "resources" / "qa" / "pragtico_test_questions_archive_20260512.json"
QA_MEMORY_AUDIT_PATH = REPO_ROOT / "resources" / "qa" / "qa_memory_knowledge_audit_20260512.json"
MAX_SNIPPET_CHARS = 2400
MIN_MATCH_SCORE = 0.42
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
KNOWLEDGE_AUDIT_EXCLUDED_PARTS = {"evals", "whatsapp_chats"}
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
VOLATILE_QUERY_RE = re.compile(
    r"\b("
    r"agora|neste momento|em porto|porto agora|quadro|navios?\s+em\s+porto|"
    r"manobras?\s+(?:planeadas|previstas)|planeamento\s+(?:atual|de hoje)|"
    r"chegadas?\s+previstas|sa[ií]das?\s+(?:recentes|previstas)|arquivo\s+de\s+manobras|"
    r"hoje|amanh[ãa]|meteorologia(?:\s+atual)?|condi[cç][oõ]es\s+meteorol[oó]gicas\s+atuais|"
    r"ondula[cç][aã]o|leitura\s+costeira|avisos?|anav"
    r")\b",
    flags=re.IGNORECASE,
)
SLASH_OR_COMMAND_RE = re.compile(r"^\s*/|\bcomandos?\s+slash\b", flags=re.IGNORECASE)


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


@dataclass(frozen=True)
class KnowledgeSupport:
    expected: str
    status: str
    score: float
    document: str
    evidence: str


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


def _tokens_with_small_numbers(value: object) -> frozenset[str]:
    return frozenset(
        token
        for token in _normalize(value).split()
        if (len(token) > 2 or token.isdigit()) and token not in STOPWORDS
    )


def _clean_text(value: object, *, max_chars: int = 700) -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "..."


def _split_expected_text(value: str) -> tuple[str, ...]:
    clean = _clean_text(value, max_chars=700)
    if not clean:
        return ()
    parts = [
        _clean_text(part, max_chars=220)
        for part in re.split(r"\s*(?:\||·|;)\s*", clean)
        if _clean_text(part, max_chars=220)
    ]
    return tuple(parts or [clean])


def _as_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_split_expected_text(str(item or "")))
        return tuple(dict.fromkeys(items))
    if isinstance(value, str) and value.strip():
        return _split_expected_text(value)
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


def _knowledge_files(base_dir: Path = KNOWLEDGE_DIR) -> tuple[Path, ...]:
    if not base_dir.exists():
        return ()
    files: list[Path] = []
    for path in base_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".txt", ".json", ".md"}:
            continue
        if any(part in KNOWLEDGE_AUDIT_EXCLUDED_PARTS for part in path.relative_to(base_dir).parts):
            continue
        if path.name.startswith("."):
            continue
        files.append(path)
    return tuple(sorted(files))


@lru_cache(maxsize=1)
def load_knowledge_audit_corpus(base_dir: str | None = None) -> tuple[dict, ...]:
    root = Path(base_dir) if base_dir else KNOWLEDGE_DIR
    corpus: list[dict] = []
    for path in _knowledge_files(root):
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = path.read_text(encoding="latin-1", errors="ignore")
        except Exception:
            continue
        text = re.sub(r"\s+", " ", raw).strip()
        if not text:
            continue
        corpus.append(
            {
                "document": str(path.relative_to(root)),
                "text": text,
                "normalized": _normalize(text),
                "tokens": _tokens_with_small_numbers(text),
            }
        )
    return tuple(corpus)


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


def _best_knowledge_support(
    expected: str,
    corpus: tuple[dict, ...],
    *,
    context: str = "",
) -> KnowledgeSupport:
    expected_clean = _clean_text(expected, max_chars=260)
    expected_norm = _normalize(expected_clean)
    expected_tokens = _tokens_with_small_numbers(expected_clean)
    if not expected_norm or not expected_tokens:
        return KnowledgeSupport(expected_clean, "unsupported", 0.0, "", "")
    context_tokens = _tokens_with_small_numbers(context)

    best_doc = ""
    best_score = 0.0
    best_evidence = ""
    for item in corpus:
        doc = str(item.get("document") or "")
        normalized = str(item.get("normalized") or "")
        tokens = set(item.get("tokens") or ())
        context_overlap = context_tokens & tokens
        context_denominator = min(len(context_tokens), 14) or 1
        context_score = min(len(context_overlap) / context_denominator, 1.0)
        if expected_norm in normalized:
            score = 0.9 + (0.1 * context_score)
            if score > best_score:
                best_score = score
                best_doc = doc
                evidence_parts = ["exact:" + expected_clean]
                if context_overlap:
                    evidence_parts.append("context:" + " ".join(sorted(context_overlap)[:10]))
                best_evidence = " | ".join(evidence_parts)
            continue
        overlap = expected_tokens & tokens
        if not overlap:
            continue
        coverage = len(overlap) / len(expected_tokens)
        jaccard = len(overlap) / len(expected_tokens | tokens) if tokens else 0.0
        score = (0.78 * coverage) + (0.12 * min(jaccard * 20, 1.0)) + (0.1 * context_score)
        if score > best_score:
            best_score = score
            best_doc = doc
            evidence_parts = ["expected:" + " ".join(sorted(overlap))]
            if context_overlap:
                evidence_parts.append("context:" + " ".join(sorted(context_overlap)[:10]))
            best_evidence = " | ".join(evidence_parts)

    status = "supported" if best_score >= 0.72 else "partial" if best_score >= 0.45 else "unsupported"
    return KnowledgeSupport(expected_clean, status, round(best_score, 3), best_doc, best_evidence)


def _is_volatile_or_command_record(record: QAMemoryRecord) -> bool:
    haystack = record.question
    return bool(VOLATILE_QUERY_RE.search(haystack) or SLASH_OR_COMMAND_RE.search(haystack))


def _has_known_knowledge_conflict(record: QAMemoryRecord) -> bool:
    haystack = _normalize(" ".join([record.question, record.validated_answer, " ".join(record.expected)]))
    expected = _normalize(" ".join(record.expected))
    south_terms = {
        "cais a sul",
        "lisnave",
        "mitrena",
        "tanquisado",
        "eco oil",
        "ecooil",
        "termitrena",
        "teporset",
    }
    mentions_fundeadouro_norte = "fundeadouro norte" in haystack
    mentions_south = any(term in haystack for term in south_terms)
    expects_one_hour = bool(re.search(r"\b1\s+hora\b", expected) or re.search(r"\b1h\b", expected))
    expects_ninety_minutes = bool(
        "1 hora e 30 minutos" in expected
        or "1h30" in expected
        or "90 minutos" in expected
    )
    return mentions_fundeadouro_norte and mentions_south and expects_one_hour and not expects_ninety_minutes


def audit_qa_memory_record(record: QAMemoryRecord, *, corpus: tuple[dict, ...] | None = None) -> dict:
    corpus = corpus if corpus is not None else load_knowledge_audit_corpus()
    context = " ".join([record.question, record.group])
    supports = [_best_knowledge_support(item, corpus, context=context) for item in record.expected]
    supported = [item for item in supports if item.status == "supported"]
    partial = [item for item in supports if item.status == "partial"]
    unsupported = [item for item in supports if item.status == "unsupported"]
    expected_with_numbers = [
        item
        for item in supports
        if re.search(r"\d", item.expected or "") and item.status != "supported"
    ]
    volatile_or_command = _is_volatile_or_command_record(record)
    known_conflict = _has_known_knowledge_conflict(record)

    if volatile_or_command:
        status = "out_of_scope"
        reason = "Caso volátil, live ou de comando; não deve treinar resposta factual estática."
    elif known_conflict:
        status = "review"
        reason = "Conflito conhecido: Fundeadouro Norte para cais a sul deve usar 1h30, não 1h."
    elif not supports:
        status = "review"
        reason = "Sem factos esperados para validar contra knowledge."
    elif unsupported or expected_with_numbers:
        status = "review"
        reason = "Há factos esperados sem suporte claro na knowledge atual."
    elif partial:
        status = "review"
        reason = "Alguns factos só têm suporte parcial na knowledge atual."
    else:
        status = "supported"
        reason = "Todos os factos esperados têm suporte lexical forte na knowledge atual."

    return {
        "question": record.question,
        "group": record.group,
        "risk": record.risk,
        "source": record.source,
        "answer_origin": record.answer_origin,
        "status": status,
        "reason": reason,
        "volatile_or_command": volatile_or_command,
        "known_conflict": known_conflict,
        "expected_count": len(supports),
        "supported_count": len(supported),
        "partial_count": len(partial),
        "unsupported_count": len(unsupported),
        "support": [
            {
                "expected": item.expected,
                "status": item.status,
                "score": item.score,
                "document": item.document,
                "evidence": item.evidence,
            }
            for item in supports
        ],
        "forbidden": list(record.forbidden),
    }


@lru_cache(maxsize=1)
def audit_qa_memory_records() -> tuple[dict, ...]:
    corpus = load_knowledge_audit_corpus()
    return tuple(audit_qa_memory_record(record, corpus=corpus) for record in load_qa_memory_records())


@lru_cache(maxsize=1)
def load_qa_memory_audit_report(path: str | None = None) -> dict:
    source_path = Path(path) if path else QA_MEMORY_AUDIT_PATH
    if not source_path.exists():
        return {}
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=1)
def qa_memory_supported_questions() -> frozenset[str]:
    report = load_qa_memory_audit_report()
    report_records = report.get("records") if isinstance(report, dict) else None
    if isinstance(report_records, list):
        supported = {
            _normalize(item.get("question"))
            for item in report_records
            if isinstance(item, dict) and item.get("status") == "supported"
        }
        if supported:
            return frozenset(supported)

    return frozenset(
        _normalize(item.get("question"))
        for item in audit_qa_memory_records()
        if item.get("status") == "supported"
    )


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
    return lines


def find_qa_memory_matches(question: str, *, limit: int = 2) -> list[tuple[QAMemoryRecord, float]]:
    query_tokens = _tokens(question)
    if len(query_tokens) < 2:
        return []
    supported_questions = qa_memory_supported_questions()
    matches = [
        (record, _score_record(question, query_tokens, record))
        for record in load_qa_memory_records()
        if _normalize(record.question) in supported_questions
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
