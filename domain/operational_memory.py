from __future__ import annotations

import re
from typing import Iterable


APPROVED_MEMORY_MIN_SIMILARITY = 0.5
REVIEW_MEMORY_MIN_SIMILARITY = 0.45
MAX_MEMORY_TEXT_CHARS = 700


def _clean_text(value: str | None, *, max_chars: int = MAX_MEMORY_TEXT_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _feedback_citation_hint(item: dict) -> str:
    documents: list[str] = []
    for citation in item.get("citations") or []:
        document = _clean_text(citation.get("document"), max_chars=120)
        if document and document not in documents:
            documents.append(document)
    return ", ".join(documents[:4])


def _approved_memory_lines(items: Iterable[dict]) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        similarity = float(item.get("similarity") or 0)
        if similarity < APPROVED_MEMORY_MIN_SIMILARITY:
            continue
        lines.append(f"Resposta aprovada {index} (semelhança {similarity:.3f}):")
        lines.append(f"- Pergunta original: {_clean_text(item.get('question'), max_chars=260)}")
        note = _clean_text(item.get("feedback_note"), max_chars=320)
        if note:
            lines.append(f"- Nota do operador: {note}")
        citation_hint = _feedback_citation_hint(item)
        if citation_hint:
            lines.append(f"- Documentos citados na resposta aprovada: {citation_hint}")
        answer = _clean_text(item.get("answer"))
        if answer:
            lines.append(f"- Síntese anterior a usar apenas como memória, não como texto final: {answer}")
    return lines


def _review_memory_lines(items: Iterable[dict]) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        similarity = float(item.get("similarity") or 0)
        status = _clean_text(item.get("feedback_status"), max_chars=40).lower()
        correction = _clean_text(item.get("feedback_correction"))
        note = _clean_text(item.get("feedback_note"), max_chars=320)
        if similarity < REVIEW_MEMORY_MIN_SIMILARITY and not correction and not note:
            continue
        if status == "corrected":
            lines.append(f"Correção validada {index} (semelhança {similarity:.3f}):")
        else:
            lines.append(f"Revisão pendente {index} (semelhança {similarity:.3f}):")
        lines.append(f"- Pergunta original: {_clean_text(item.get('question'), max_chars=260)}")
        if note:
            lines.append(f"- Nota do operador: {note}")
        correction_document = _clean_text(item.get("feedback_correction_document"), max_chars=180)
        if correction_document:
            lines.append(f"- Documento indicado pelo operador: {correction_document}")
        if correction:
            lines.append(f"- Correção sugerida pelo operador: {correction}")
        rejected_answer = _clean_text(item.get("answer"), max_chars=420)
        if rejected_answer:
            lines.append(f"- Resposta anterior a evitar repetir sem validação: {rejected_answer}")
    return lines


def build_feedback_memory_sources(
    question: str,
    trusted_answers: list[dict] | None,
    reviewed_answers: list[dict] | None,
    *,
    limit: int = 3,
) -> list[dict]:
    """Convert operator-reviewed chat feedback into structured RAG context."""
    sources: list[dict] = []

    approved_lines = _approved_memory_lines((trusted_answers or [])[:limit])
    if approved_lines:
        snippet = "\n".join(
            [
                "Memória operacional validada por feedback.",
                f"Pergunta atual: {_clean_text(question, max_chars=260)}",
                "Usar como referência de decisão/factos e reconciliar sempre com documentos e dados live.",
                "Não copiar literalmente respostas anteriores.",
                *approved_lines,
            ]
        )
        best_score = max(float(item.get("similarity") or 0) for item in (trusted_answers or [])[:limit])
        sources.append(
            {
                "source_id": "MEM1",
                "document": "memoria_feedback_aprovado",
                "chunk_id": 1,
                "score": round(best_score, 3),
                "retrieval_mode": "operational_feedback_memory",
                "snippet": snippet,
                "text": snippet,
            }
        )

    review_lines = _review_memory_lines((reviewed_answers or [])[:limit])
    if review_lines:
        snippet = "\n".join(
            [
                "Memória operacional corrigida ou em revisão.",
                f"Pergunta atual: {_clean_text(question, max_chars=260)}",
                "Usar correções validadas como referência forte; usar revisões pendentes como aviso de risco.",
                *review_lines,
            ]
        )
        best_score = max(float(item.get("similarity") or 0) for item in (reviewed_answers or [])[:limit])
        sources.append(
            {
                "source_id": "MEM2",
                "document": "memoria_feedback_revisao",
                "chunk_id": 1,
                "score": round(best_score, 3),
                "retrieval_mode": "operational_feedback_memory",
                "snippet": snippet,
                "text": snippet,
            }
        )

    return sources


def filter_feedback_for_synthesis(
    trusted_answers: list[dict] | None,
    reviewed_answers: list[dict] | None,
) -> tuple[list[dict], list[dict]]:
    """Keep only feedback close enough to influence answer synthesis."""
    trusted = [
        item
        for item in (trusted_answers or [])
        if float(item.get("similarity") or 0) >= APPROVED_MEMORY_MIN_SIMILARITY
    ]
    reviewed = [
        item
        for item in (reviewed_answers or [])
        if (
            float(item.get("similarity") or 0) >= REVIEW_MEMORY_MIN_SIMILARITY
            or _clean_text(item.get("feedback_correction"))
            or _clean_text(item.get("feedback_note"))
        )
    ]
    return trusted, reviewed
