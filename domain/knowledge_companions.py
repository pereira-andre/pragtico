from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Dict, List


PORTUGUESE_STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "com",
    "como",
    "da",
    "das",
    "de",
    "dentro",
    "do",
    "dos",
    "e",
    "em",
    "essa",
    "esse",
    "esta",
    "este",
    "isso",
    "isto",
    "me",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "ou",
    "para",
    "por",
    "qual",
    "quais",
    "que",
    "se",
    "sem",
    "sff",
    "uma",
    "um",
}
SUMMARY_REQUEST_RE = re.compile(
    r"\b(o que diz|resume|resumo|sumario|sumário|explica|diz me|diz-me|quais sao|quais são|qual e a regra|qual é a regra)\b",
    flags=re.IGNORECASE,
)


def companion_directory(knowledge_dir: str) -> str:
    return os.path.join(knowledge_dir, "companions")


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", without_accents.lower())).strip()


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in _normalize_text(value).split()
        if len(token) > 2 and token not in PORTUGUESE_STOPWORDS and not token.isdigit()
    }


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in (_clean_text(item) for item in value) if item]
    clean = _clean_text(value)
    return [clean] if clean else []


def _document_stem(document_name: str) -> str:
    return os.path.splitext(str(document_name or ""))[0]


def _candidate_companion_paths(document_name: str, knowledge_dir: str) -> list[str]:
    companions_dir = companion_directory(knowledge_dir)
    stem = _document_stem(document_name)
    candidates = [os.path.join(companions_dir, f"{stem}.json")]
    code_match = re.match(r"IT-(\d{3})_", str(document_name or ""), flags=re.IGNORECASE)
    if code_match:
        candidates.append(os.path.join(companions_dir, f"IT-{code_match.group(1)}.json"))
    return list(dict.fromkeys(candidates))


def _normalize_faq_entry(item: dict) -> dict | None:
    question = _clean_text(item.get("question"))
    answer = _clean_text(item.get("answer"))
    if not question or not answer:
        return None
    return {
        "question": question,
        "answer": answer,
        "keywords": _clean_list(item.get("keywords")),
    }


def _normalize_companion(payload: dict, document_name: str) -> dict:
    title = _clean_text(payload.get("title")) or _document_stem(document_name).replace("_", " ")
    aliases = _clean_list(payload.get("aliases"))
    aliases.extend(
        item
        for item in (
            document_name,
            _document_stem(document_name),
            title,
        )
        if _clean_text(item)
    )
    faq_items = []
    for raw_item in payload.get("faq", []) or []:
        if not isinstance(raw_item, dict):
            continue
        normalized = _normalize_faq_entry(raw_item)
        if normalized:
            faq_items.append(normalized)
    return {
        "document": document_name,
        "title": title,
        "aliases": list(dict.fromkeys(item for item in aliases if item)),
        "summary": _clean_text(payload.get("summary")),
        "key_points": _clean_list(payload.get("key_points")),
        "faq": faq_items,
    }


def load_document_companion(document_name: str, knowledge_dir: str) -> dict | None:
    for path in _candidate_companion_paths(document_name, knowledge_dir):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return _normalize_companion(payload, document_name)
    return None


def build_companion_scaffold(document_name: str, *, title: str = "") -> dict:
    clean_title = _clean_text(title) or _document_stem(document_name).replace("_", " ")
    return {
        "document": document_name,
        "title": clean_title,
        "aliases": [
            _document_stem(document_name),
        ],
        "summary": "",
        "key_points": [
            "",
        ],
        "faq": [
            {
                "question": "",
                "answer": "",
                "keywords": [],
            }
        ],
    }


def is_companion_summary_request(question: str) -> bool:
    return bool(SUMMARY_REQUEST_RE.search(str(question or "")))


def find_best_companion_faq(question: str, companion: dict) -> dict | None:
    question_tokens = _tokenize(question)
    if not question_tokens:
        return None

    best_match = None
    best_score = 0.0
    for item in companion.get("faq", []) or []:
        faq_tokens = _tokenize(item.get("question", ""))
        keyword_tokens = set()
        for keyword in item.get("keywords", []) or []:
            keyword_tokens.update(_tokenize(keyword))
        overlap_score = len(question_tokens & faq_tokens) / max(len(question_tokens), 1)
        keyword_score = len(question_tokens & keyword_tokens) / max(len(question_tokens), 1) if keyword_tokens else 0.0
        total_score = overlap_score + (0.45 * keyword_score)
        if total_score > best_score:
            best_score = total_score
            best_match = {**item, "score": round(total_score, 3)}

    if not best_match or best_score < 0.34:
        return None
    return best_match


def build_companion_sources(companion: dict, question: str) -> list[dict]:
    sources: list[dict] = []
    faq_match = find_best_companion_faq(question, companion)
    if faq_match:
        sources.append(
            {
                "source_id": "KC1",
                "document": companion["document"],
                "chunk_id": 0,
                "score": faq_match["score"],
                "retrieval_mode": "document_companion",
                "snippet": f"FAQ canónica: {faq_match['question']} Resposta: {faq_match['answer']}",
            }
        )

    summary_bits = []
    if companion.get("summary"):
        summary_bits.append(companion["summary"])
    if companion.get("key_points"):
        summary_bits.append("Pontos-chave: " + "; ".join(item for item in companion["key_points"] if item))
    if summary_bits:
        sources.append(
            {
                "source_id": "KC2",
                "document": companion["document"],
                "chunk_id": 0,
                "score": 0.9 if is_companion_summary_request(question) else 0.4,
                "retrieval_mode": "document_companion",
                "snippet": " ".join(summary_bits),
            }
        )
    return sources


def build_companion_answer(question: str, companion: dict) -> str:
    faq_match = find_best_companion_faq(question, companion)
    if faq_match:
        return faq_match["answer"]

    if not is_companion_summary_request(question):
        return ""

    summary = _clean_text(companion.get("summary"))
    key_points = [item for item in companion.get("key_points", []) if _clean_text(item)]
    parts: list[str] = []
    if summary:
        parts.append(f"Segundo o {companion['title']}, {summary}")
    elif companion.get("title"):
        parts.append(f"Segundo o {companion['title']},")
    if key_points:
        parts.append("Pontos principais: " + "; ".join(key_points) + ".")
    return " ".join(part.strip() for part in parts if part.strip()).strip()


def companion_lookup_terms(companion: dict) -> list[str]:
    terms = []
    terms.extend(companion.get("aliases", []) or [])
    terms.extend(item.get("question", "") for item in companion.get("faq", []) or [])
    for item in companion.get("faq", []) or []:
        terms.extend(item.get("keywords", []) or [])
    if companion.get("summary"):
        terms.append(companion["summary"])
    return [item for item in dict.fromkeys(_clean_text(term) for term in terms) if item]
