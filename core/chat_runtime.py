"""Channel-agnostic chat runtime shared by web chat and WhatsApp."""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from contextlib import contextmanager
from typing import Iterator

from flask import session

from core import services
from core.chat_planner import ChatExecutionPlan, build_chat_execution_plan
from core.chat_reasoning import (
    build_compound_message_analysis_source,
    build_conversation_reasoning_state,
)
from core.event_report_runtime import (
    build_event_report_photo_prompt,
    build_event_report_template,
    clear_pending_event_report,
    finalize_pending_event_report,
    format_event_report_answer,
    is_cancel_reply,
    is_no_photo_reply,
    load_pending_event_report,
    parse_event_report_command,
    save_pending_event_report,
)
from core.helpers import (
    answer_direct_operational_query,
    answer_slash_query,
    answer_slash_validation,
    build_live_operational_sources,
    build_operational_chat_sources,
    clear_pending_chat_action,
    current_resolvable_port_calls,
    execute_pending_operational_action,
    finalize_operational_proposal,
    load_pending_chat_action,
    looks_like_pending_confirmation,
    refine_pending_operational_action,
    refresh_knowledge_state,
    save_pending_chat_action,
)
from domain.chat_actions import (
    build_action_reply_template,
    looks_like_operational_command,
    looks_like_slash_command,
    parse_slash_command,
)
from domain.chat_response_formatting import add_contextual_response_emojis
from domain.berth_layout import canonicalize_berth_label
from domain.berth_profiles import (
    build_berth_profile_answer,
    build_berth_profile_sources,
    find_best_berth_profile,
)
from domain.knowledge_companions import (
    build_companion_answer,
    build_companion_sources,
    companion_lookup_terms,
    find_best_global_companion_match,
    load_document_companion,
)
from domain.operational_memory import (
    build_feedback_memory_sources,
    filter_feedback_for_synthesis,
)
from domain.port_entities import detect_port_entities, entity_names_from_matches, specific_entities
from integrations.rag_engine import chunk_text, lexical_score
from storage.utils import normalize_feedback_correction
from core.bot_settings import load_bot_settings

logger = logging.getLogger(__name__)

# Fallback defaults; actual values are read live from bot_settings via the helpers below.
REVIEW_GUARD_SIMILARITY = 0.9
REVIEW_BLOCK_SIMILARITY = 0.97
REVIEW_CORRECTION_SIMILARITY = 0.94
TRUSTED_DOCUMENT_HINT_SIMILARITY = 0.82
TRUSTED_DOCUMENT_HINT_GAP = 0.08


def _threshold(key: str, fallback: float) -> float:
    try:
        value = load_bot_settings().get(key)
        return float(value) if value is not None else fallback
    except Exception:
        return fallback


def _review_guard_threshold() -> float:
    return _threshold("review_guard_similarity", REVIEW_GUARD_SIMILARITY)


def _review_block_threshold() -> float:
    return _threshold("review_block_similarity", REVIEW_BLOCK_SIMILARITY)


def _review_correction_threshold() -> float:
    return _threshold("review_correction_similarity", REVIEW_CORRECTION_SIMILARITY)


def _trusted_document_hint_threshold() -> float:
    return _threshold("trusted_document_hint_similarity", TRUSTED_DOCUMENT_HINT_SIMILARITY)
DOCUMENT_FOLLOW_UP_RE = re.compile(
    r"\b(?:esse|essa|este|esta|o|a)\s+(?:documento|doc|ficheiro|regra|instrucao|instrução)\b"
    r"|\bo que diz(?:\s+(?:esse|essa|este|esta|o|a))?\s+"
    r"(?:documento|doc|ficheiro|regra|instrucao|instrução)\b"
    r"|\bresume(?:\s+(?:esse|essa|este|esta|o|a))?\s+"
    r"(?:documento|doc|ficheiro|regra|instrucao|instrução)?\b",
    flags=re.IGNORECASE,
)

ROUTE_DURATION_TOPIC_RE = re.compile(
    r"\b(quanto tempo|tempo|demora|leva|transito|trânsito|percurso|viagem|"
    r"marcar|marcacao|marcação|antecedencia|antecedência)\b"
)
ROUTE_FOLLOW_UP_RE = re.compile(
    r"^(?:e\s+)?(?:se\s+(?:fosse|for|fossemos|fôssemos|era)|para|ate|até|ao|a|à)\b"
    r"|^(?:e\s+)?(?:do|da|dos|das|desde)\b"
    r"|\be\s+se\s+(?:fosse|for|era)\b"
    r"|\be\s+para\b"
)
ROUTE_DESTINATION_RE = re.compile(
    r"\b(canal norte|canal sul|lisnave|mitrena|tanquisado|eco\s*oil|ecooil|ecoil|"
    r"teporset|tepor\s*set|termitrena|tms\s*1|tms1|tms\s*2|tms2|"
    r"autoeuropa|auto\s*europa|ro\s*ro|roro|cais\s+a\s+norte|cais\s+do\s+norte|"
    r"cais\s+norte|cais\s+a\s+sul|cais\s+do\s+sul|cais\s+sul|cais\s*10|cais\s*11|"
    r"praias|sapec|secil|fundeadouro|fundeadouros)\b"
)
ROUTE_ORIGIN_BARRA_RE = re.compile(r"\b(barra|entrada da barra|fora da barra|pilar\s*2|boia\s*2|bóia\s*2)\b")


DOCUMENT_MATCH_STOPWORDS = {
    "apss",
    "documento",
    "porto",
    "setubal",
    "sesimbra",
    "instrucao",
    "regras",
    "regra",
    "normas",
    "norma",
    "interna",
    "interno",
    "it",
    "txt",
    "pdf",
    "docx",
    "md",
}
TUG_DOCUMENT_QUERY_RE = re.compile(
    r"\b(reboque|reboques|rebocador|rebocadores)\b"
)


def _active_knowledge_dir() -> str:
    return (
        getattr(getattr(services, "store", None), "knowledge_dir", "") or getattr(services, "KNOWLEDGE_DIR", "") or ""
    )


@contextmanager
def chat_actor_context(username: str, role: str) -> Iterator[None]:
    """Temporarily project a chat actor into the Flask session-backed helpers."""
    previous_username = session.get("username")
    previous_role = session.get("role")
    had_username = "username" in session
    had_role = "role" in session
    session["username"] = username
    session["role"] = (role or "piloto").strip().lower() or "piloto"
    try:
        yield
    finally:
        if had_username:
            session["username"] = previous_username
        else:
            session.pop("username", None)
        if had_role:
            session["role"] = previous_role
        else:
            session.pop("role", None)


def _feedback_timestamp(value: dict | None) -> str:
    if not value:
        return ""
    return str(value.get("feedback_updated_at") or "")


def _select_review_guard_match(reviewed_answers: list[dict], trusted_answers: list[dict]) -> dict | None:
    """Return the reviewed match that should suppress blind reuse, if any."""
    guard_threshold = _review_guard_threshold()
    best_review = next(
        (
            item
            for item in reviewed_answers
            if item.get("similarity", 0) >= guard_threshold
            and not (item.get("feedback_correction") or "").strip()
        ),
        None,
    )
    if not best_review or best_review.get("similarity", 0) < guard_threshold:
        return None

    best_trusted = trusted_answers[0] if trusted_answers else None
    if not best_trusted:
        return best_review

    if (
        best_trusted.get("similarity", 0) >= best_review.get("similarity", 0)
        and _feedback_timestamp(best_trusted) >= _feedback_timestamp(best_review)
    ):
        return None
    return best_review


def _is_corrected_feedback_match(item: dict) -> bool:
    status = (item.get("feedback_status") or "").strip().lower()
    has_correction = bool((item.get("feedback_correction") or "").strip())
    return status == "corrected" or (status == "review" and has_correction)


def _select_review_correction_match(reviewed_answers: list[dict], trusted_answers: list[dict]) -> dict | None:
    correction_threshold = _review_correction_threshold()
    best_review = next(
        (
            item
            for item in reviewed_answers
            if item.get("similarity", 0) >= correction_threshold
            and (item.get("feedback_correction") or "").strip()
            and _is_corrected_feedback_match(item)
        ),
        None,
    )
    if not best_review or best_review.get("similarity", 0) < correction_threshold:
        return None

    best_trusted = trusted_answers[0] if trusted_answers else None
    if not best_trusted:
        return best_review

    if (
        best_trusted.get("similarity", 0) >= best_review.get("similarity", 0)
        and _feedback_timestamp(best_trusted) >= _feedback_timestamp(best_review)
    ):
        return None
    return best_review


def _build_review_correction_answer(review_match: dict) -> dict:
    correction = normalize_feedback_correction(
        review_match.get("question"),
        (review_match.get("feedback_correction") or "").strip(),
    )
    if not correction:
        raise ValueError("Correção vazia.")
    return {
        "answer": correction,
        "sources": review_match.get("citations") or [],
        "answer_origin": "review_correction_memory",
        "review_match": {
            "similarity": review_match.get("similarity", 0),
            "message_id": review_match.get("message_id", ""),
            "question": review_match.get("question", ""),
            "feedback_note": (review_match.get("feedback_note") or "").strip(),
            "feedback_correction": correction,
            "feedback_correction_document": (review_match.get("feedback_correction_document") or "").strip(),
        },
    }


def _review_correction_targets_document(
    review_match: dict | None,
    targeted_document_context: dict | None,
    global_companion_match: dict | None = None,
) -> bool:
    if not review_match:
        return False

    correction_document = str(review_match.get("feedback_correction_document") or "").strip()
    cited_documents = {
        str((citation or {}).get("document") or "").strip()
        for citation in (review_match.get("citations") or [])
        if str((citation or {}).get("document") or "").strip()
    }
    target_document = str(((targeted_document_context or {}).get("record") or {}).get("name") or "").strip()
    global_document = str((((global_companion_match or {}).get("companion") or {}).get("document") or "")).strip()

    if not correction_document and not cited_documents:
        return True
    if target_document and (correction_document == target_document or target_document in cited_documents):
        return True
    if global_document and (correction_document == global_document or global_document in cited_documents):
        return True
    return not target_document and not global_document


def _build_review_guard_answer(review_match: dict) -> dict:
    """Build a deterministic answer when a near-identical question is still under review."""
    feedback_note = (review_match.get("feedback_note") or "").strip()
    if feedback_note:
        answer = (
            "Uma resposta anterior muito semelhante ficou marcada para revisão, "
            "por isso não a vou repetir como validada. "
            f"Nota de revisão registada: {feedback_note}"
        )
    else:
        answer = (
            "Uma resposta anterior muito semelhante ficou marcada para revisão, "
            "por isso não a vou repetir como validada sem nova confirmação."
        )
    return {
        "answer": answer,
        "sources": [],
        "answer_origin": "review_guard",
        "review_match": {
            "similarity": review_match.get("similarity", 0),
            "message_id": review_match.get("message_id", ""),
            "question": review_match.get("question", ""),
            "feedback_note": feedback_note,
        },
    }


def _build_supplemental_sources(
    question: str,
    plan: ChatExecutionPlan | None = None,
    conversation_state: dict | None = None,
    trusted_answers: list[dict] | None = None,
    reviewed_answers: list[dict] | None = None,
) -> list[dict]:
    supplemental_sources = build_operational_chat_sources(question, plan=plan)
    supplemental_sources.extend(build_live_operational_sources(question, plan=plan))
    if conversation_state and conversation_state.get("source"):
        supplemental_sources.append(conversation_state["source"])
    supplemental_sources.extend(_build_approved_casebook_sources(question))
    supplemental_sources.extend(
        build_feedback_memory_sources(question, trusted_answers, reviewed_answers)
    )
    return supplemental_sources


def _clean_revision_text(value: object, *, max_chars: int = 1800) -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "..."


def _build_answer_revision_source(revision_context: dict | None) -> dict | None:
    if not revision_context:
        return None
    original_question = _clean_revision_text(revision_context.get("original_question"), max_chars=800)
    previous_answer = _clean_revision_text(revision_context.get("previous_answer"), max_chars=1800)
    user_note = _clean_revision_text(revision_context.get("user_note"), max_chars=600)
    if not original_question and not previous_answer:
        return None
    lines = [
        "Pedido de reanálise da resposta anterior.",
        "Objetivo: voltar a consultar o conhecimento disponível, procurar omissões ou confusões e produzir nova resposta.",
        "Não repetir literalmente a resposta anterior. Se a conclusão factual se mantiver, explicar que foi revalidada e reformular a síntese.",
    ]
    if original_question:
        lines.append(f"Pergunta original: {original_question}")
    if previous_answer:
        lines.append(f"Resposta anterior a rever: {previous_answer}")
    if user_note:
        lines.append(f"Observação do utilizador: {user_note}")
    snippet = "\n".join(lines)
    return {
        "source_id": "REV1",
        "document": "pedido_reanalise_resposta",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "answer_revision_context",
        "snippet": snippet,
        "text": snippet,
    }


def _answers_are_effectively_same(first: object, second: object) -> bool:
    first_norm = re.sub(r"\W+", "", unicodedata.normalize("NFKD", str(first or "")).lower())
    second_norm = re.sub(r"\W+", "", unicodedata.normalize("NFKD", str(second or "")).lower())
    return bool(first_norm and second_norm and first_norm == second_norm)


def _source_mode_counts(sources: list[dict] | None) -> list[dict]:
    counts: dict[str, int] = {}
    for source in sources or []:
        mode = (
            str(source.get("retrieval_mode") or source.get("type") or "sem_modo").strip()
            or "sem_modo"
        )
        counts[mode] = counts.get(mode, 0) + 1
    return [
        {"mode": mode, "count": count}
        for mode, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _compact_feedback_trace(items: list[dict] | None) -> list[dict]:
    compacted: list[dict] = []
    for item in (items or [])[:3]:
        compacted.append(
            {
                "similarity": round(float(item.get("similarity") or 0), 3),
                "question": str(item.get("question") or "")[:180],
                "message_id": str(item.get("message_id") or ""),
                "feedback_note": str(item.get("feedback_note") or "")[:180],
                "feedback_correction_document": str(item.get("feedback_correction_document") or ""),
            }
        )
    return compacted


def _compact_review_match(match: dict | None) -> dict | None:
    if not match:
        return None
    return {
        "similarity": round(float(match.get("similarity") or 0), 3),
        "question": str(match.get("question") or "")[:180],
        "message_id": str(match.get("message_id") or ""),
        "feedback_note": str(match.get("feedback_note") or "")[:180],
        "feedback_correction_document": str(match.get("feedback_correction_document") or ""),
    }


def _build_playground_trace(
    *,
    execution_plan: ChatExecutionPlan | None,
    answer_origin: str,
    sources: list[dict] | None,
    targeted_document_context: dict | None = None,
    berth_profile_match: dict | None = None,
    global_companion_match: dict | None = None,
    conversation_state: dict | None = None,
    trusted_answers: list[dict] | None = None,
    reviewed_answers: list[dict] | None = None,
    review_guard_match: dict | None = None,
    review_correction_match: dict | None = None,
    retrieval_validation: dict | None = None,
    direct_answer_used: bool = False,
) -> dict:
    target_record = (targeted_document_context or {}).get("record") or {}
    document_sources = (targeted_document_context or {}).get("document_sources") or []
    companion_sources = (targeted_document_context or {}).get("companion_sources") or []
    global_companion = (global_companion_match or {}).get("companion") or {}
    global_companion_sources = (global_companion_match or {}).get("sources") or []
    global_companion_document = (
        global_companion.get("document")
        or (global_companion_sources[0].get("document") if global_companion_sources else "")
        or ""
    )
    berth_label = (
        (berth_profile_match or {}).get("canonical_label")
        or (berth_profile_match or {}).get("label")
        or (berth_profile_match or {}).get("name")
        or ""
    )
    return {
        "execution_plan": execution_plan.to_dict() if execution_plan else {},
        "answer_origin": answer_origin,
        "source_count": len(sources or []),
        "source_mode_counts": _source_mode_counts(sources),
        "target_document": str(target_record.get("name") or ""),
        "target_document_hit": bool(target_record),
        "document_target_chunks": len(document_sources),
        "document_companion_hit": bool((targeted_document_context or {}).get("companion_answer") or companion_sources),
        "global_companion_hit": bool(global_companion_match),
        "global_companion_document": str(global_companion_document),
        "berth_profile_hit": bool(berth_profile_match),
        "berth_profile_label": str(berth_label),
        "conversation_state_hit": bool(conversation_state and conversation_state.get("source")),
        "trusted_matches": _compact_feedback_trace(trusted_answers),
        "review_matches": _compact_feedback_trace(reviewed_answers),
        "review_guard": _compact_review_match(review_guard_match),
        "review_correction": _compact_review_match(review_correction_match),
        "retrieval_validation": retrieval_validation or {},
        "used_llm": answer_origin == "llm",
        "used_direct_answer": direct_answer_used,
        "used_shortcut": answer_origin in {
            "berth_profile",
            "document_companion",
            "document_companion_global",
            "review_correction_memory",
            "review_guard",
        },
    }


_CASEBOOK_LOOKUP_TOKENS = {
    "entry": ("entrada", "entrar", "chegada", "chegar"),
    "departure": ("saida", "saída", "sair", "sair", "partida"),
    "shift": ("mudanca", "mudança", "shift", "reatracacao", "reatracação"),
}


def _build_approved_casebook_sources(question: str) -> list[dict]:
    """Inject up to three approved maneuver cases relevant to the question."""
    store = getattr(services, "store", None)
    if not store or not hasattr(store, "list_maneuver_cases"):
        return []
    try:
        cases = store.list_maneuver_cases(limit=80) or []
    except Exception:
        return []
    if not cases:
        return []

    clean_question = _normalize_lookup_text(question)
    if not clean_question:
        return []
    target_berths = _casebook_query_berth_labels(question)

    target_type = None
    for maneuver_type, tokens in _CASEBOOK_LOOKUP_TOKENS.items():
        if any(token in clean_question for token in tokens):
            target_type = maneuver_type
            break

    def _case_matches(case: dict) -> bool:
        if (case.get("feedback_status") or "").strip().lower() != "approved":
            return False
        if target_type and (case.get("maneuver_type") or "").strip().lower() != target_type:
            return False
        if target_berths:
            case_berths = {
                canonicalize_berth_label(case.get("origin_label")) or str(case.get("origin_label") or "").strip(),
                canonicalize_berth_label(case.get("destination_label")) or str(case.get("destination_label") or "").strip(),
            }
            if not target_berths & case_berths:
                return False
        searchable = " ".join(
            [
                str(case.get("vessel_name") or ""),
                str(case.get("origin_label") or ""),
                str(case.get("destination_label") or ""),
                str(case.get("maneuver_type_label") or ""),
                str(case.get("feedback_note") or ""),
            ]
        )
        normalized = _normalize_lookup_text(searchable)
        return any(token in normalized for token in clean_question.split() if len(token) > 3)

    sources: list[dict] = []
    for case in cases:
        if not _case_matches(case):
            continue
        snippet_bits = [
            f"{case.get('maneuver_type_label') or 'Manobra'}",
            f"{case.get('origin_label') or '--'} → {case.get('destination_label') or '--'}",
            (case.get('feedback_note') or '').strip(),
        ]
        snippet = " · ".join(bit for bit in snippet_bits if bit)
        sources.append(
            {
                "document": f"Casebook · {case.get('reference_code') or case.get('vessel_name') or 'Manobra aprovada'}",
                "source_id": f"CASEBOOK_{case.get('maneuver_id', '')[:8].upper()}",
                "retrieval_mode": "casebook_approved",
                "snippet": snippet[:300],
            }
        )
        if len(sources) >= 3:
            break
    return sources


def _normalize_lookup_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


def _looks_like_route_duration_topic(text: str) -> bool:
    clean = _normalize_lookup_text(text)
    if not clean:
        return False
    return bool(ROUTE_DURATION_TOPIC_RE.search(clean) and ROUTE_DESTINATION_RE.search(clean))


def _looks_like_route_duration_follow_up(question: str) -> bool:
    clean = _normalize_lookup_text(question)
    if not clean:
        return False
    return bool(ROUTE_DESTINATION_RE.search(clean) and ROUTE_FOLLOW_UP_RE.search(clean))


def _last_user_route_duration_question(history: list[dict]) -> str:
    for entry in reversed(history):
        if (entry.get("role") or "").strip().lower() != "user":
            continue
        content = str(entry.get("content") or "").strip()
        if _looks_like_route_duration_topic(content):
            return content
    return ""


def _strip_follow_up_lead_in(question: str) -> str:
    cleaned = re.sub(
        r"^\s*e\s+se\s+(?:fosse|for|fossemos|fôssemos|era)\s+",
        "",
        str(question or "").strip(),
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^\s*e\s+para\s+", "para ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*e\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or str(question or "").strip()


def _looks_like_entity_follow_up(question: str) -> bool:
    clean = _normalize_lookup_text(question)
    if not clean:
        return False
    if _starts_like_follow_up(clean):
        return True
    return len(clean.split()) <= 6 and bool(detect_port_entities(question))


def _starts_like_follow_up(question: str) -> bool:
    clean = _normalize_lookup_text(question)
    return bool(re.match(r"^(?:e|entao|então|mas|agora)\b", clean))


def _last_user_question_with_reusable_intent(history: list[dict]) -> str:
    for entry in reversed(history):
        if (entry.get("role") or "").strip().lower() != "user":
            continue
        content = str(entry.get("content") or "").strip()
        if content and not _starts_like_follow_up(content):
            return content
    return ""


def _intent_template_from_question(question: str) -> str:
    clean = _normalize_lookup_text(question)
    if any(token in clean for token in ("restricao", "restricoes", "restrições", "limite", "limites")):
        return "Que restrições operacionais documentadas existem para {entity}?"
    if any(token in clean for token in ("calado", "calados", "profundidade", "sonda")):
        return "Que regras documentadas de calado existem para {entity}?"
    if any(token in clean for token in ("rebocador", "rebocadores", "reboque", "reboques")):
        return "O que dizem os documentos sobre rebocadores para {entity}?"
    if any(token in clean for token in ("geral", "gerais", "fala", "resumo", "informacao", "informação")):
        return "Fala-me de {entity} em termos gerais, com base na documentação."
    return "O que diz a documentação sobre {entity}?"


def _contextual_entity_lookup_question(question: str, history: list[dict]) -> str:
    if not _looks_like_entity_follow_up(question):
        return question
    entities = specific_entities(detect_port_entities(question))
    if not entities:
        return question
    previous_question = _last_user_question_with_reusable_intent(history)
    if not previous_question:
        return question
    entity_name = entity_names_from_matches(entities)[0]
    return _intent_template_from_question(previous_question).format(entity=entity_name)


def _contextual_lookup_question(question: str, history: list[dict]) -> str:
    entity_question = _contextual_entity_lookup_question(question, history)
    if entity_question != question:
        return entity_question
    if not _looks_like_route_duration_follow_up(question):
        return question
    stripped_question = _strip_follow_up_lead_in(question)
    normalized_stripped = _normalize_lookup_text(stripped_question)
    if re.match(r"^(?:do|da|dos|das|desde|de)\s+(?:fundeadouro|fundeadouros|canal|barra|entrada|pilar)\b", normalized_stripped):
        return f"Quanto tempo leva {stripped_question}"
    previous_route_question = _last_user_route_duration_question(history)
    if not previous_route_question:
        return question
    origin = (
        "desde a entrada da Barra"
        if ROUTE_ORIGIN_BARRA_RE.search(_normalize_lookup_text(previous_route_question))
        else "desde o mesmo ponto de origem"
    )
    return f"Quanto tempo leva {origin} {stripped_question}"


def _casebook_query_berth_labels(question: str) -> set[str]:
    clean = _normalize_lookup_text(question)
    compact = re.sub(r"[^a-z0-9]+", "", clean)
    candidates = set()

    for match in re.finditer(r"\b(?:lisnave\s+)?(?:ponte\s+cais|cais|c)\s*([0-3])\s*([ab])\b", clean):
        candidates.add(f"Lisnave {match.group(1)}{match.group(2)}")
    for match in re.finditer(r"\blisnave\s+([0-3])\s*([ab])\b", clean):
        candidates.add(f"Lisnave {match.group(1)}{match.group(2)}")
    for match in re.finditer(r"\b([0-3])\s*([ab])\s+lisnave\b", clean):
        candidates.add(f"Lisnave {match.group(1)}{match.group(2)}")
    for match in re.finditer(r"\b([ab])\s*([0-3])\s+lisnave\b", clean):
        candidates.add(f"Lisnave {match.group(2)}{match.group(1)}")
    for match in re.finditer(r"\blisnave\s+([ab])\s*([0-3])\b", clean):
        candidates.add(f"Lisnave {match.group(2)}{match.group(1)}")

    for number in range(0, 4):
        has_numbered_lisnave_quay = bool(
            re.search(rf"\b(?:lisnave\s+)?(?:ponte\s+cais|cais|c)\s*{number}\b", clean)
            or f"lisnave{number}" in compact
        )
        if has_numbered_lisnave_quay and "setubal" in clean:
            candidates.add(f"Cais {number} lado Setubal")
        if has_numbered_lisnave_quay and "alcacer" in clean:
            candidates.add(f"Cais {number} lado Alcacer")
        if has_numbered_lisnave_quay and re.search(r"\b(?:w|west|oeste|lado w)\b", clean):
            candidates.add(f"Cais {number} W")
        if has_numbered_lisnave_quay and re.search(r"\b(?:east|leste|lado este|lado leste|lado e)\b", clean):
            candidates.add(f"Cais {number} E")
        if any(marker in compact for marker in (f"c{number}w", f"cais{number}w", f"lisnave{number}w")):
            candidates.add(f"Cais {number} W")
        if any(marker in compact for marker in (f"c{number}e", f"cais{number}e", f"lisnave{number}e")):
            candidates.add(f"Cais {number} E")
        for side in ("a", "b"):
            markers = (
                f"c{number}{side}",
                f"cais{number}{side}",
                f"pontecais{number}{side}",
                f"lisnavec{number}{side}",
                f"lisnavecais{number}{side}",
                f"lisnave{number}{side}",
                f"{number}{side}lisnave",
                f"{side}{number}lisnave",
                f"lisnave{side}{number}",
            )
            if any(marker in compact for marker in markers):
                candidates.add(f"Lisnave {number}{side}")

    return {label for candidate in candidates if (label := canonicalize_berth_label(candidate))}


def _extract_rule_codes(text: str) -> list[str]:
    codes: set[str] = set()
    raw_text = str(text or "")
    for pattern in (
        r"\bit[\s\-_]?0*(\d{1,3})\b",
        r"\b(?:regra|documento|doc|instrucao|instrução)\s*(?:it[\s\-_]?)?0*(\d{1,3})\b",
    ):
        for match in re.finditer(pattern, raw_text, flags=re.IGNORECASE):
            codes.add(match.group(1).zfill(3))
    return sorted(codes)


def _document_alias_texts(record: dict) -> list[str]:
    aliases: list[str] = []
    for raw_value in (record.get("name"), record.get("original_name")):
        value = str(raw_value or "").strip()
        if not value:
            continue
        aliases.append(value)
        stem = os.path.splitext(value)[0].replace("_", " ")
        aliases.append(stem)
        aliases.append(re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý])", " ", stem))
    return [item for item in dict.fromkeys(aliases) if item]


def _match_document_from_text(text: str, documents: list[dict]) -> dict | None:
    if not text or not documents:
        return None

    code_matches = set(_extract_rule_codes(text))
    if code_matches:
        for record in documents:
            name = str(record.get("name") or "")
            code_match = re.match(r"IT-(\d{3})_", name, flags=re.IGNORECASE)
            if code_match and code_match.group(1) in code_matches:
                return record

    normalized_text = _normalize_lookup_text(text)
    if not normalized_text:
        return None
    if TUG_DOCUMENT_QUERY_RE.search(normalized_text):
        for record in documents:
            name = str(record.get("name") or "")
            if re.match(r"IT-016_", name, flags=re.IGNORECASE):
                return record

    question_tokens = {
        token
        for token in normalized_text.split()
        if len(token) > 2 and token not in DOCUMENT_MATCH_STOPWORDS and not token.isdigit()
    }
    if not question_tokens:
        return None

    best_match: dict | None = None
    best_score = 0.0
    best_overlap: set[str] = set()
    for record in documents:
        alias_tokens: set[str] = set()
        for alias in _document_alias_texts(record):
            alias_tokens.update(
                token
                for token in _normalize_lookup_text(alias).split()
                if len(token) > 2 and token not in DOCUMENT_MATCH_STOPWORDS and not token.isdigit()
            )
        if not alias_tokens:
            continue
        overlap = question_tokens & alias_tokens
        if not overlap:
            continue
        overlap_score = len(overlap) / len(alias_tokens)
        if overlap_score > best_score:
            best_match = record
            best_score = overlap_score
            best_overlap = overlap

    if not best_match:
        return None
    longest_overlap = max((len(token) for token in best_overlap), default=0)
    if len(best_overlap) >= 2 or longest_overlap >= 6:
        return best_match
    return None


def _looks_like_document_follow_up(question: str) -> bool:
    return bool(DOCUMENT_FOLLOW_UP_RE.search(str(question or "")))


def _resolve_feedback_target_document(feedback_matches: list[dict], documents: list[dict]) -> dict | None:
    if not feedback_matches or not documents:
        return None

    documents_by_name = {str(item.get("name") or ""): item for item in documents}
    hint_threshold = _trusted_document_hint_threshold()
    candidates: list[dict] = []
    for match in feedback_matches:
        similarity = float(match.get("similarity") or 0.0)
        if similarity < hint_threshold:
            continue
        cited_documents = []
        explicit_document = str(match.get("feedback_correction_document") or "").strip()
        if explicit_document in documents_by_name:
            cited_documents.append(explicit_document)
        for citation in match.get("citations") or []:
            document_name = str(citation.get("document") or "")
            if document_name in documents_by_name:
                cited_documents.append(document_name)
        unique_cited_documents = list(dict.fromkeys(cited_documents))
        if len(unique_cited_documents) != 1:
            continue
        document_name = unique_cited_documents[0]
        candidates.append(
            {
                "record": documents_by_name[document_name],
                "score": similarity,
            }
        )

    if not candidates:
        return None

    candidates.sort(key=lambda item: item["score"], reverse=True)
    best_match = candidates[0]
    second_best = candidates[1] if len(candidates) > 1 else None
    if (
        second_best
        and str(second_best["record"].get("name") or "") != str(best_match["record"].get("name") or "")
        and (best_match["score"] - second_best["score"]) < TRUSTED_DOCUMENT_HINT_GAP
    ):
        return None
    return best_match["record"]


def _resolve_target_knowledge_document(
    question: str,
    history: list[dict],
    trusted_answers: list[dict] | None = None,
    reviewed_answers: list[dict] | None = None,
) -> dict | None:
    try:
        documents = services.store.list_documents()
    except Exception:
        return None
    if not documents:
        return None

    direct_match = _match_document_from_text(question, documents)
    if direct_match:
        return direct_match

    documents_by_name = {str(item.get("name") or ""): item for item in documents}
    if _looks_like_document_follow_up(question):
        for entry in reversed(history):
            for citation in reversed(entry.get("citations") or []):
                document_name = str(citation.get("document") or "")
                if document_name in documents_by_name:
                    return documents_by_name[document_name]
            history_match = _match_document_from_text(entry.get("content", ""), documents)
            if history_match:
                return history_match

    feedback_matches = list(trusted_answers or [])
    feedback_matches.extend(
        match
        for match in (reviewed_answers or [])
        if (match.get("feedback_correction") or "").strip()
    )
    return _resolve_feedback_target_document(feedback_matches, documents)


def _document_lookup_query(question: str, history: list[dict], record: dict) -> str:
    parts = [str(question or "").strip()]
    for entry in reversed(history):
        if (entry.get("role") or "").strip().lower() != "user":
            continue
        content = str(entry.get("content") or "").strip()
        if not content or content == question:
            continue
        parts.append(content)
        break
    parts.extend(_document_alias_texts(record))
    return "\n".join(part for part in parts if part)


def _build_targeted_document_context(
    question: str,
    history: list[dict],
    trusted_answers: list[dict] | None = None,
    reviewed_answers: list[dict] | None = None,
) -> dict:
    record = _resolve_target_knowledge_document(question, history, trusted_answers, reviewed_answers)
    if not record:
        return {
            "record": None,
            "retrieval_question": None,
            "document_sources": [],
            "companion_sources": [],
            "companion_answer": "",
        }

    companion = load_document_companion(record["name"], _active_knowledge_dir())
    companion_sources = build_companion_sources(companion, question) if companion else []
    companion_answer = build_companion_answer(question, companion) if companion else ""

    try:
        document_text = services.store.get_document_text(record["name"])
    except Exception:
        return {
            "record": record,
            "retrieval_question": None,
            "document_sources": [],
            "companion_sources": companion_sources,
            "companion_answer": companion_answer,
        }

    raw_chunks = chunk_text(document_text, chunk_size=900, overlap=160)
    if not raw_chunks:
        return {
            "record": record,
            "retrieval_question": None,
            "document_sources": [],
            "companion_sources": companion_sources,
            "companion_answer": companion_answer,
        }

    lookup_query = _document_lookup_query(question, history, record)
    if companion:
        companion_terms = companion_lookup_terms(companion)
        if companion_terms:
            lookup_query = "\n".join([lookup_query, *companion_terms[:6]])
    ranked_chunks: list[tuple[float, int, str]] = []
    for chunk_index, chunk in enumerate(raw_chunks, start=1):
        chunk_for_score = f"{' '.join(_document_alias_texts(record))}\n{chunk}"
        score = lexical_score(lookup_query, chunk_for_score)
        if chunk_index == 1:
            score += 0.05
        ranked_chunks.append((score, chunk_index, chunk))
    ranked_chunks.sort(key=lambda item: (item[0], -item[1]), reverse=True)

    selected = [item for item in ranked_chunks if item[0] > 0][:2]
    if not selected:
        selected = ranked_chunks[:2]

    supplemental_sources: list[dict] = []
    for source_index, (score, chunk_index, chunk) in enumerate(selected, start=1):
        supplemental_sources.append(
            {
                "source_id": f"KD{source_index}",
                "document": record["name"],
                "chunk_id": chunk_index,
                "score": round(score, 3) if score > 0 else 0.001,
                "retrieval_mode": "document_target",
                "snippet": chunk,
            }
        )
    retrieval_question = f"{lookup_query}\nDocumento alvo: {record['name']}"
    return {
        "record": record,
        "retrieval_question": retrieval_question,
        "document_sources": supplemental_sources,
        "companion_sources": companion_sources,
        "companion_answer": companion_answer,
    }


def _blocked_mutation_answer(channel: str) -> dict:
    channel_label = "WhatsApp" if channel == "whatsapp" else channel
    return {
        "answer": (
            f"No {channel_label} o PRAGtico está, por agora, em modo de consulta.\n\n"
            "Posso responder a perguntas, validar contexto e consultar operação, "
            "mas ações que alterem o portal continuam reservadas à interface web."
        ),
        "sources": [],
        "pending_action": None,
        "answer_origin": f"{channel}_mutations_blocked",
    }


def _should_prefer_berth_profile_answer(question: str, companion_answer: str) -> bool:
    clean_question = _normalize_lookup_text(question)
    asks_route_metric = bool(
        re.search(r"\b(quanto tempo|tempo|demora|leva|levo|distancia|distancias|milhas|percurso|viagem)\b", clean_question)
        and re.search(r"\b(barra|pilar|boia|canal|fundeadouro|fundeadouros|ate|para)\b", clean_question)
    )
    if asks_route_metric:
        return False

    clean_answer = re.sub(r"\s+", " ", str(companion_answer or "")).strip()
    if not clean_answer:
        return True
    if clean_answer.startswith(("A resposta direta:", "O valor a reter é")):
        return True
    asks_general_profile = bool(
        re.search(r"\b(fala|sabes|conheces|termos gerais|geral|restricoes|restrições|regras|limites)\b", clean_question)
    )
    if asks_general_profile and clean_answer.startswith("Segundo o ") and "Pontos principais:" in clean_answer:
        return True
    return False


def playground_answer(
    *,
    username: str,
    role: str,
    question: str,
) -> dict:
    """Lightweight answer pipeline for the admin playground — no persistence, no pending actions."""
    clean_question = (question or "").strip()
    if not clean_question:
        raise ValueError("Pergunta vazia.")

    with chat_actor_context(username=username, role=role):
        refresh_knowledge_state(force_reindex=False)
        execution_plan = build_chat_execution_plan(clean_question)

        settings = load_bot_settings()
        if settings.get("auto_trust_positive_feedback", True):
            trusted_answers = services.store.find_feedback_matches(
                username,
                clean_question,
                limit=3,
                feedback_statuses={"approved"},
            )
        else:
            trusted_answers = []
        reviewed_answers = services.store.find_feedback_matches(
            username,
            clean_question,
            limit=3,
            feedback_statuses={"corrected", "review"},
        )
        review_correction_match = _select_review_correction_match(reviewed_answers, trusted_answers)
        review_guard_match = _select_review_guard_match(reviewed_answers, trusted_answers)
        synthesis_trusted_answers, synthesis_reviewed_answers = filter_feedback_for_synthesis(
            trusted_answers,
            reviewed_answers,
        )

        direct_answer = answer_direct_operational_query(clean_question, plan=execution_plan)
        if direct_answer:
            direct_answer["trace"] = _build_playground_trace(
                execution_plan=execution_plan,
                answer_origin=direct_answer.get("answer_origin", "direct_operational"),
                sources=direct_answer.get("sources", []),
                trusted_answers=synthesis_trusted_answers,
                reviewed_answers=synthesis_reviewed_answers,
                review_guard_match=review_guard_match,
                review_correction_match=review_correction_match,
                direct_answer_used=True,
            )
            return direct_answer

        runtime_history: list[dict] = []
        conversation_state = build_conversation_reasoning_state(
            clean_question,
            runtime_history,
            execution_plan,
        )
        targeted_document_context = _build_targeted_document_context(
            clean_question,
            runtime_history,
            trusted_answers,
            reviewed_answers,
        )
        berth_profile_match = find_best_berth_profile(clean_question, _active_knowledge_dir())
        berth_profile_answer = build_berth_profile_answer(clean_question, berth_profile_match)
        supplemental_sources = _build_supplemental_sources(
            clean_question,
            plan=execution_plan,
            conversation_state=conversation_state,
            trusted_answers=synthesis_trusted_answers,
            reviewed_answers=synthesis_reviewed_answers,
        )
        compound_message_source = build_compound_message_analysis_source(clean_question)
        if compound_message_source:
            supplemental_sources.append(compound_message_source)
        supplemental_sources.extend(build_berth_profile_sources(berth_profile_match))
        supplemental_sources.extend(targeted_document_context["companion_sources"])
        supplemental_sources.extend(targeted_document_context["document_sources"])

        allow_companion_shortcut = not execution_plan.requires_llm_synthesis
        answer: dict | None = None
        global_companion_match: dict | None = None
        if berth_profile_answer and allow_companion_shortcut and _should_prefer_berth_profile_answer(
            clean_question,
            targeted_document_context["companion_answer"],
        ):
            answer = {
                "answer": berth_profile_answer,
                "sources": supplemental_sources,
                "answer_origin": "berth_profile",
            }
        elif targeted_document_context["companion_answer"] and allow_companion_shortcut:
            if _review_correction_targets_document(review_correction_match, targeted_document_context, None):
                answer = _build_review_correction_answer(review_correction_match)
                if supplemental_sources:
                    answer["sources"] = supplemental_sources
            else:
                answer = {
                    "answer": targeted_document_context["companion_answer"],
                    "sources": supplemental_sources,
                    "answer_origin": "document_companion",
                }
        else:
            global_companion_match = find_best_global_companion_match(
                clean_question,
                _active_knowledge_dir(),
            )
            if global_companion_match and allow_companion_shortcut:
                if _review_correction_targets_document(
                    review_correction_match,
                    targeted_document_context,
                    global_companion_match,
                ):
                    answer = _build_review_correction_answer(review_correction_match)
                    if global_companion_match.get("sources"):
                        answer["sources"] = global_companion_match["sources"]
                else:
                    answer = {
                        "answer": global_companion_match["answer"],
                        "sources": global_companion_match["sources"],
                        "answer_origin": "document_companion_global",
                    }
            elif review_correction_match and not execution_plan.requires_llm_synthesis:
                answer = _build_review_correction_answer(review_correction_match)
            else:
                if (
                    review_guard_match
                    and review_guard_match.get("similarity", 0) >= _review_block_threshold()
                    and not targeted_document_context["document_sources"]
                ):
                    answer = _build_review_guard_answer(review_guard_match)
                else:
                    if not services.rag.can_generate():
                        answer = {
                            "answer": "Define a API key do provider antes de usar o playground.",
                            "sources": [],
                            "answer_origin": "error",
                        }
                        answer["trace"] = _build_playground_trace(
                            execution_plan=execution_plan,
                            answer_origin=answer["answer_origin"],
                            sources=answer["sources"],
                            targeted_document_context=targeted_document_context,
                            berth_profile_match=berth_profile_match,
                            global_companion_match=global_companion_match,
                            conversation_state=conversation_state,
                            trusted_answers=synthesis_trusted_answers,
                            reviewed_answers=synthesis_reviewed_answers,
                            review_guard_match=review_guard_match,
                            review_correction_match=review_correction_match,
                        )
                        return answer
                    answer = services.rag.answer(
                        question=clean_question,
                        retrieval_question=targeted_document_context["retrieval_question"],
                        role=role,
                        history=[],
                        supplemental_sources=supplemental_sources,
                        trusted_answers=synthesis_trusted_answers,
                        reviewed_answers=synthesis_reviewed_answers,
                        execution_plan=execution_plan.to_dict(),
                        conversation_state=conversation_state,
                    )
                    answer["answer_origin"] = "llm"

        answer = add_contextual_response_emojis(
            answer or {"answer": "Sem resposta.", "sources": [], "answer_origin": "empty"},
            clean_question,
        )
        answer["trace"] = _build_playground_trace(
            execution_plan=execution_plan,
            answer_origin=answer.get("answer_origin", ""),
            sources=answer.get("sources", []),
            targeted_document_context=targeted_document_context,
            berth_profile_match=berth_profile_match,
            global_companion_match=global_companion_match,
            conversation_state=conversation_state,
            trusted_answers=synthesis_trusted_answers,
            reviewed_answers=synthesis_reviewed_answers,
            review_guard_match=review_guard_match,
            review_correction_match=review_correction_match,
            retrieval_validation=answer.get("retrieval_validation"),
        )
        return answer


def handle_chat_turn(
    *,
    username: str,
    role: str,
    question: str,
    conversation_id: str | None = None,
    channel: str = "web",
    allow_mutations: bool = True,
    channel_user_id: str = "",
    inbound_message_id: str = "",
    inbound_message_metadata: dict | None = None,
    pre_response_messages: list[dict] | None = None,
    revision_context: dict | None = None,
) -> dict:
    """Process a single chat turn and persist the resulting messages."""
    clean_question = (question or "").strip()
    if not clean_question:
        raise ValueError("Pergunta vazia.")

    with chat_actor_context(username=username, role=role):
        refresh_knowledge_state(force_reindex=False)
        revision_context = revision_context or {}
        is_revision_attempt = bool(revision_context)
        conversation = services.store.ensure_conversation(username=username, conversation_id=conversation_id)
        history = services.store.list_messages(username, conversation["id"])
        lookup_question = (
            str(revision_context.get("original_question") or "").strip()
            if is_revision_attempt
            else ""
        ) or _contextual_lookup_question(clean_question, history)
        execution_plan = build_chat_execution_plan(lookup_question)
        existing_pending = load_pending_chat_action(username, conversation["id"])
        if existing_pending and not allow_mutations:
            clear_pending_chat_action(username, conversation["id"])
            existing_pending = None

        if load_bot_settings().get("auto_trust_positive_feedback", True):
            trusted_answers = services.store.find_feedback_matches(
                username,
                lookup_question,
                limit=3,
                feedback_statuses={"approved"},
            )
        else:
            trusted_answers = []
        reviewed_answers = services.store.find_feedback_matches(
            username,
            lookup_question,
            limit=3,
            feedback_statuses={"corrected", "review"},
        )
        review_correction_match = _select_review_correction_match(reviewed_answers, trusted_answers)
        review_guard_match = _select_review_guard_match(reviewed_answers, trusted_answers)
        synthesis_trusted_answers, synthesis_reviewed_answers = filter_feedback_for_synthesis(
            trusted_answers,
            reviewed_answers,
        )
        user_message = services.store.append_chat_message(
            username=username,
            conversation_id=conversation["id"],
            role="user",
            content=clean_question,
            channel=channel,
            channel_user_id=channel_user_id,
            external_message_id=inbound_message_id,
            channel_metadata=inbound_message_metadata or {},
        )

        answer = None
        pending_event_report = load_pending_event_report(
            channel=channel,
            username=username,
            conversation_id=conversation["id"],
            channel_user_id=channel_user_id,
        )
        if pending_event_report and not looks_like_slash_command(clean_question):
            if is_cancel_reply(clean_question):
                clear_pending_event_report(
                    channel=channel,
                    username=username,
                    conversation_id=conversation["id"],
                    channel_user_id=channel_user_id,
                )
                answer = {
                    "answer": "Reporte de evento cancelado. Nada foi arquivado.",
                    "sources": [],
                    "answer_origin": "event_report_cancelled",
                }
            elif is_no_photo_reply(clean_question):
                event_report = finalize_pending_event_report(pending_event_report)
                answer = {
                    "answer": format_event_report_answer(event_report),
                    "sources": [],
                    "answer_origin": "event_report_registered",
                }
            else:
                answer = {
                    "answer": (
                        "Tenho um reporte de evento pendente. "
                        "Envia uma foto agora, responde `não` para arquivar sem anexo, "
                        "ou `cancelar` para desistir."
                    ),
                    "sources": [],
                    "answer_origin": "event_report_pending",
                }

        slash_command = None
        if answer is None and looks_like_slash_command(clean_question):
            try:
                slash_command = parse_slash_command(clean_question, role)
            except (PermissionError, ValueError) as exc:
                answer = {
                    "answer": f"Não consegui interpretar o comando. Motivo: {exc}",
                    "sources": [],
                    "answer_origin": "slash_error",
                }
            except Exception:
                logger.exception("Falha inesperada ao interpretar comando slash.")
                answer = {
                    "answer": (
                        "Falha inesperada ao interpretar o comando. "
                        "Confirma os campos obrigatórios com `/help` e volta a tentar."
                    ),
                    "sources": [],
                    "answer_origin": "slash_error",
                }
        if slash_command and slash_command.get("intent") == "help":
            answer = {
                "answer": slash_command["answer"],
                "sources": [],
                "answer_origin": "slash_help",
            }
        elif slash_command and slash_command.get("intent") == "query":
            answer = answer_slash_query(
                slash_command.get("command", ""),
                slash_command.get("argument", ""),
                role,
            )
        elif slash_command and slash_command.get("intent") == "validate":
            answer = answer_slash_validation(slash_command.get("target") or {}, role)
        elif slash_command and slash_command.get("intent") == "event_report":
            parsed_report = parse_event_report_command(slash_command.get("argument", ""))
            if not parsed_report.get("ok"):
                answer = {
                    "answer": build_event_report_template(parsed_report.get("missing") or []),
                    "sources": [],
                    "answer_origin": "event_report_template",
                }
            else:
                draft = parsed_report.get("draft") or {}
                save_pending_event_report(
                    username=username,
                    role=role,
                    conversation_id=conversation["id"],
                    channel=channel,
                    channel_user_id=channel_user_id,
                    inbound_message_id=inbound_message_id,
                    draft=draft,
                )
                answer = {
                    "answer": build_event_report_photo_prompt(draft),
                    "sources": [],
                    "answer_origin": "event_report_pending",
                }
        elif slash_command and slash_command.get("intent") == "template":
            proposal = slash_command.get("proposal") or {}
            if proposal.get("intent") == "action" and proposal.get("action"):
                if not allow_mutations:
                    answer = _blocked_mutation_answer(channel)
                else:
                    pending_action = save_pending_chat_action(
                        username=username,
                        conversation_id=conversation["id"],
                        proposal=proposal,
                        question=clean_question,
                    )
                    answer = {
                        "answer": pending_action["summary"],
                        "sources": [],
                        "pending_action": load_pending_chat_action(username, conversation["id"]),
                        "answer_origin": "slash_template",
                    }
            else:
                answer = {
                    "answer": slash_command["answer"],
                    "sources": [],
                    "answer_origin": "slash_template",
                }
        elif slash_command and slash_command.get("intent") == "unsupported":
            answer = {
                "answer": slash_command["answer"],
                "sources": [],
                "answer_origin": "slash_rejected",
            }
        elif slash_command and slash_command.get("intent") == "action":
            if not allow_mutations:
                answer = _blocked_mutation_answer(channel)
            else:
                try:
                    action_proposal = finalize_operational_proposal(
                        slash_command.get("proposal"),
                        current_resolvable_port_calls(),
                    )
                except (PermissionError, ValueError) as exc:
                    proposal = slash_command.get("proposal") or {}
                    template = build_action_reply_template(
                        proposal.get("action", ""),
                        proposal.get("missing_fields", []),
                    )
                    message = f"Não consegui preparar o comando. Motivo: {exc}"
                    if template:
                        message = f"{message}\n\n{template}"
                    answer = {
                        "answer": message,
                        "sources": [],
                        "answer_origin": "slash_error",
                    }
                    action_proposal = None
                except Exception:
                    logger.exception("Falha inesperada ao preparar comando slash operacional.")
                    answer = {
                        "answer": (
                            "Falha inesperada ao preparar o comando operacional. "
                            "Confirma a Ref da escala, o ID da manobra e os campos alterados."
                        ),
                        "sources": [],
                        "answer_origin": "slash_error",
                    }
                    action_proposal = None
                if answer is None and action_proposal and action_proposal.get("intent") == "action":
                    pending_action = save_pending_chat_action(
                        username=username,
                        conversation_id=conversation["id"],
                        proposal=action_proposal,
                        question=clean_question,
                    )
                    answer = {
                        "answer": pending_action["summary"],
                        "sources": [],
                        "pending_action": load_pending_chat_action(username, conversation["id"]),
                        "answer_origin": "slash_proposal",
                    }
                elif answer is None:
                    proposal = slash_command.get("proposal") or {}
                    template = build_action_reply_template(
                        proposal.get("action", ""),
                        proposal.get("missing_fields", []),
                    )
                    reason = (
                        (action_proposal or {}).get("reason")
                        or "Não consegui interpretar o comando com segurança."
                    )
                    if template:
                        reason = f"{reason}\n\n{template}"
                    answer = {
                        "answer": reason,
                        "sources": [],
                        "answer_origin": "slash_rejected",
                    }
        elif answer is None:
            answer = answer_direct_operational_query(
                lookup_question if is_revision_attempt else clean_question,
                plan=execution_plan,
            )

        if answer is None:
            if existing_pending:
                if not allow_mutations:
                    answer = _blocked_mutation_answer(channel)
                elif looks_like_pending_confirmation(clean_question):
                    proposal = existing_pending.get("proposal", {})
                    if proposal.get("missing_fields"):
                        answer = {
                            "answer": "Ainda faltam dados obrigatórios antes de confirmar esta ação.",
                            "sources": [],
                            "pending_action": load_pending_chat_action(username, conversation["id"]),
                            "answer_origin": "pending_action_block",
                        }
                    else:
                        try:
                            result, message = execute_pending_operational_action(
                                proposal,
                                username=username,
                                role=role,
                            )
                        except (PermissionError, ValueError) as exc:
                            clear_pending_chat_action(username, conversation["id"])
                            answer = {
                                "answer": f"Não consegui aplicar a ação operacional. Motivo: {exc}",
                                "sources": [],
                                "pending_action": None,
                                "answer_origin": "pending_action_error",
                            }
                        except Exception:
                            logger.exception(
                                "Falha inesperada na execução da ação operacional do chat."
                            )
                            clear_pending_chat_action(username, conversation["id"])
                            answer = {
                                "answer": "Falha inesperada ao aplicar a ação operacional no portal.",
                                "sources": [],
                                "pending_action": None,
                                "answer_origin": "pending_action_error",
                            }
                        else:
                            clear_pending_chat_action(username, conversation["id"])
                            citations = []
                            current_port_call = result if isinstance(result, dict) else None
                            if current_port_call and current_port_call.get("reference_code"):
                                citations.append(
                                    {
                                        "document": current_port_call.get("vessel_name", "Escala"),
                                        "source_id": current_port_call.get("reference_code", ""),
                                        "retrieval_mode": "operational_action",
                                        "snippet": message,
                                    }
                                )
                            answer = {
                                "answer": message,
                                "sources": citations,
                                "pending_action": None,
                                "answer_origin": "pending_action_confirmed",
                            }
                else:
                    try:
                        pending_update = refine_pending_operational_action(
                            clean_question,
                            existing_pending.get("proposal", {}),
                            role,
                        )
                    except (PermissionError, ValueError) as exc:
                        pending_update = {
                            "intent": "unsupported",
                            "reason": f"Não consegui atualizar a ação pendente. Motivo: {exc}",
                        }
                    except Exception:
                        logger.exception("Falha inesperada ao atualizar ação operacional pendente.")
                        pending_update = {
                            "intent": "unsupported",
                            "reason": (
                                "Falha inesperada ao atualizar a ação pendente. "
                                "Confirma os campos obrigatórios e volta a tentar."
                            ),
                        }
                    if (
                        pending_update
                        and pending_update.get("intent") == "update"
                        and pending_update.get("proposal", {}).get("intent") == "action"
                    ):
                        pending_action = save_pending_chat_action(
                            username=username,
                            conversation_id=conversation["id"],
                            proposal=pending_update["proposal"],
                            question=clean_question,
                        )
                        answer = {
                            "answer": (
                                "Atualizei a ação pendente com a tua resposta.\n\n"
                                + pending_action["summary"]
                            ),
                            "sources": [],
                            "pending_action": load_pending_chat_action(
                                username,
                                conversation["id"],
                            ),
                            "answer_origin": "operational_update",
                        }
                    elif (
                        pending_update
                        and pending_update.get("intent") == "replace"
                        and pending_update.get("proposal", {}).get("intent") == "action"
                    ):
                        pending_action = save_pending_chat_action(
                            username=username,
                            conversation_id=conversation["id"],
                            proposal=pending_update["proposal"],
                            question=clean_question,
                        )
                        answer = {
                            "answer": (
                                "Troquei a proposta pendente por uma nova ação operacional.\n\n"
                                + pending_action["summary"]
                            ),
                            "sources": [],
                            "pending_action": load_pending_chat_action(
                                username,
                                conversation["id"],
                            ),
                            "answer_origin": "operational_replace",
                        }
                    elif pending_update and pending_update.get("intent") == "cancel":
                        clear_pending_chat_action(username, conversation["id"])
                        answer = {
                            "answer": "A ação operacional pendente foi cancelada. O portal não foi alterado.",
                            "sources": [],
                            "pending_action": None,
                            "answer_origin": "pending_action_cancelled",
                        }
                    elif pending_update and pending_update.get("intent") == "question":
                        answer = None
                    else:
                        proposal = existing_pending.get("proposal", {})
                        template = (
                            build_action_reply_template(
                                proposal.get("action", ""),
                                proposal.get("missing_fields", []),
                            )
                            if proposal.get("missing_fields")
                            else ""
                        )
                        block_message = (
                            pending_update.get("reason")
                            if pending_update and pending_update.get("reason")
                            else "Não consegui atualizar a ação pendente com essa resposta."
                        )
                        if template:
                            block_message = f"{block_message}\n\n{template}"
                        answer = {
                            "answer": block_message,
                            "sources": [],
                            "pending_action": load_pending_chat_action(username, conversation["id"]),
                            "answer_origin": "pending_action_block",
                        }
            else:
                if looks_like_operational_command(clean_question):
                    if allow_mutations:
                        answer = {
                            "answer": (
                                "Para executar operações ou validações no portal usa um comando começado por `/`.\n\n"
                                "Exemplos: `/aprovar`, `/registar-manobra`, `/editar-manobra`, `/abortar`, `/validar-manobra`.\n"
                                "Usa `/help` para ver a lista completa."
                            ),
                            "sources": [],
                            "answer_origin": "slash_redirect",
                        }
                    else:
                        answer = _blocked_mutation_answer(channel)
                else:
                    answer = None

        if answer is None:
            runtime_history = history + [user_message]
            conversation_state = build_conversation_reasoning_state(
                lookup_question,
                runtime_history,
                execution_plan,
            )
            targeted_document_context = _build_targeted_document_context(
                lookup_question,
                history,
                trusted_answers,
                reviewed_answers,
            )
            berth_profile_match = find_best_berth_profile(lookup_question, _active_knowledge_dir())
            berth_profile_answer = build_berth_profile_answer(lookup_question, berth_profile_match)
            supplemental_sources = _build_supplemental_sources(
                lookup_question,
                plan=execution_plan,
                conversation_state=conversation_state,
                trusted_answers=synthesis_trusted_answers,
                reviewed_answers=synthesis_reviewed_answers,
            )
            revision_source = _build_answer_revision_source(revision_context)
            if revision_source:
                supplemental_sources.insert(0, revision_source)
            compound_message_source = build_compound_message_analysis_source(lookup_question)
            if compound_message_source:
                supplemental_sources.append(compound_message_source)
            supplemental_sources.extend(build_berth_profile_sources(berth_profile_match))
            supplemental_sources.extend(targeted_document_context["companion_sources"])
            supplemental_sources.extend(targeted_document_context["document_sources"])
            global_companion_match = None
            allow_companion_shortcut = not execution_plan.requires_llm_synthesis and not is_revision_attempt
            if berth_profile_answer and allow_companion_shortcut and _should_prefer_berth_profile_answer(
                lookup_question,
                targeted_document_context["companion_answer"],
            ):
                answer = {
                    "answer": berth_profile_answer,
                    "sources": supplemental_sources,
                    "answer_origin": "berth_profile",
                }
            elif targeted_document_context["companion_answer"] and allow_companion_shortcut:
                if _review_correction_targets_document(
                    review_correction_match,
                    targeted_document_context,
                    None,
                ):
                    answer = _build_review_correction_answer(review_correction_match)
                    if supplemental_sources:
                        answer["sources"] = supplemental_sources
                else:
                    answer = {
                        "answer": targeted_document_context["companion_answer"],
                        "sources": supplemental_sources,
                        "answer_origin": "document_companion",
                    }
            else:
                global_companion_match = find_best_global_companion_match(
                    lookup_question,
                    _active_knowledge_dir(),
                )
                if global_companion_match and not allow_companion_shortcut:
                    supplemental_sources.extend(global_companion_match.get("sources") or [])
                if global_companion_match and allow_companion_shortcut:
                    if _review_correction_targets_document(
                        review_correction_match,
                        targeted_document_context,
                        global_companion_match,
                    ):
                        answer = _build_review_correction_answer(review_correction_match)
                        if global_companion_match.get("sources"):
                            answer["sources"] = global_companion_match["sources"]
                    else:
                        answer = {
                            "answer": global_companion_match["answer"],
                            "sources": global_companion_match["sources"],
                            "answer_origin": "document_companion_global",
                        }
                elif review_correction_match and not execution_plan.requires_llm_synthesis and not is_revision_attempt:
                    answer = _build_review_correction_answer(review_correction_match)
                else:
                    if (
                        review_guard_match
                        and review_guard_match.get("similarity", 0) >= _review_block_threshold()
                        and not targeted_document_context["document_sources"]
                    ):
                        answer = _build_review_guard_answer(review_guard_match)
                    else:
                        if not services.rag.can_generate():
                            raise RuntimeError("Define a API key do provider antes de usar o chatbot.")
                        answer = services.rag.answer(
                            question=clean_question,
                            retrieval_question=targeted_document_context["retrieval_question"] or lookup_question,
                            role=role,
                            history=runtime_history[-10:],
                            supplemental_sources=supplemental_sources,
                            trusted_answers=synthesis_trusted_answers,
                            reviewed_answers=synthesis_reviewed_answers,
                            execution_plan=execution_plan.to_dict(),
                            conversation_state=conversation_state,
                        )
                        answer["answer_origin"] = "llm"
                        if synthesis_trusted_answers:
                            answer["feedback_match"] = {
                                "similarity": synthesis_trusted_answers[0]["similarity"],
                                "message_id": synthesis_trusted_answers[0]["message_id"],
                                "question": synthesis_trusted_answers[0]["question"],
                                "feedback_note": synthesis_trusted_answers[0].get("feedback_note", ""),
                                "feedback_correction": synthesis_trusted_answers[0].get("feedback_correction", ""),
                                "feedback_correction_document": synthesis_trusted_answers[0].get("feedback_correction_document", ""),
                            }

        if is_revision_attempt and _answers_are_effectively_same(
            (answer or {}).get("answer", ""),
            revision_context.get("previous_answer", ""),
        ):
            answer["answer"] = (
                "Reanalisei a pergunta e não encontrei base documental para alterar a conclusão. "
                "Reformulando de forma explícita: "
                + str(answer.get("answer") or "").strip()
            )

        answer = add_contextual_response_emojis(answer, clean_question)

        persisted_pre_response_messages: list[dict] = []
        for item in pre_response_messages or []:
            content = str((item or {}).get("content") or "").strip()
            if not content:
                continue
            pre_message = services.store.append_chat_message(
                username=username,
                conversation_id=conversation["id"],
                role="assistant",
                content=content,
                citations=(item or {}).get("citations") or [],
                channel=channel,
                channel_user_id=channel_user_id,
                external_reply_to_id=inbound_message_id,
                channel_metadata=(item or {}).get("channel_metadata") or {},
            )
            persisted_pre_response_messages.append(
                {
                    "message_id": pre_message["id"],
                    "content": pre_message["content"],
                    "created_at_label": pre_message.get("created_at_label", ""),
                    "channel_metadata": pre_message.get("channel_metadata", {}),
                }
            )

        assistant_message = services.store.append_chat_message(
            username=username,
            conversation_id=conversation["id"],
            role="assistant",
            content=answer["answer"],
            citations=answer.get("sources", []),
            channel=channel,
            channel_user_id=channel_user_id,
            external_reply_to_id=inbound_message_id,
        )
        return {
            **answer,
            "conversation_id": conversation["id"],
            "user_message_id": user_message["id"],
            "message_id": assistant_message["id"],
            "created_at_label": assistant_message.get("created_at_label", ""),
            "pre_response_messages": persisted_pre_response_messages,
        }
