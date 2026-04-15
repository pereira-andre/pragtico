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
from core.chat_reasoning import build_conversation_reasoning_state
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
from domain.knowledge_companions import (
    build_companion_answer,
    build_companion_sources,
    companion_lookup_terms,
    find_best_global_companion_match,
    load_document_companion,
)
from integrations.rag_engine import chunk_text, lexical_score
from storage.utils import normalize_feedback_correction

logger = logging.getLogger(__name__)

REVIEW_GUARD_SIMILARITY = 0.9
REVIEW_BLOCK_SIMILARITY = 0.97
REVIEW_CORRECTION_SIMILARITY = 0.94
TRUSTED_DOCUMENT_HINT_SIMILARITY = 0.82
TRUSTED_DOCUMENT_HINT_GAP = 0.08
DOCUMENT_FOLLOW_UP_RE = re.compile(
    r"\b(?:esse|essa|este|esta|o|a)\s+(?:documento|doc|ficheiro|regra|instrucao|instrução)\b"
    r"|\bo que diz(?:\s+(?:esse|essa|este|esta|o|a))?\s+"
    r"(?:documento|doc|ficheiro|regra|instrucao|instrução)\b"
    r"|\bresume(?:\s+(?:esse|essa|este|esta|o|a))?\s+"
    r"(?:documento|doc|ficheiro|regra|instrucao|instrução)?\b",
    flags=re.IGNORECASE,
)


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
    best_review = next(
        (
            item
            for item in reviewed_answers
            if item.get("similarity", 0) >= REVIEW_GUARD_SIMILARITY
            and not (item.get("feedback_correction") or "").strip()
        ),
        None,
    )
    if not best_review or best_review.get("similarity", 0) < REVIEW_GUARD_SIMILARITY:
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


def _select_review_correction_match(reviewed_answers: list[dict], trusted_answers: list[dict]) -> dict | None:
    best_review = next(
        (
            item
            for item in reviewed_answers
            if item.get("similarity", 0) >= REVIEW_CORRECTION_SIMILARITY
            and (item.get("feedback_correction") or "").strip()
        ),
        None,
    )
    if not best_review or best_review.get("similarity", 0) < REVIEW_CORRECTION_SIMILARITY:
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
) -> list[dict]:
    supplemental_sources = build_operational_chat_sources(question)
    supplemental_sources.extend(build_live_operational_sources(question, plan=plan))
    if conversation_state and conversation_state.get("source"):
        supplemental_sources.append(conversation_state["source"])
    return supplemental_sources


def _normalize_lookup_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


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
    candidates: list[dict] = []
    for match in feedback_matches:
        similarity = float(match.get("similarity") or 0.0)
        if similarity < TRUSTED_DOCUMENT_HINT_SIMILARITY:
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
) -> dict:
    """Process a single chat turn and persist the resulting messages."""
    clean_question = (question or "").strip()
    if not clean_question:
        raise ValueError("Pergunta vazia.")

    with chat_actor_context(username=username, role=role):
        refresh_knowledge_state(force_reindex=False)
        conversation = services.store.ensure_conversation(username=username, conversation_id=conversation_id)
        history = services.store.list_messages(username, conversation["id"])
        execution_plan = build_chat_execution_plan(clean_question)
        existing_pending = load_pending_chat_action(username, conversation["id"])
        if existing_pending and not allow_mutations:
            clear_pending_chat_action(username, conversation["id"])
            existing_pending = None

        trusted_answers = services.store.find_feedback_matches(
            username,
            clean_question,
            limit=3,
            feedback_statuses={"approved"},
        )
        reviewed_answers = services.store.find_feedback_matches(
            username,
            clean_question,
            limit=3,
            feedback_statuses={"review"},
        )
        review_correction_match = _select_review_correction_match(reviewed_answers, trusted_answers)
        review_guard_match = _select_review_guard_match(reviewed_answers, trusted_answers)
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
        slash_command = (
            parse_slash_command(clean_question, role)
            if looks_like_slash_command(clean_question)
            else None
        )
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
                action_proposal = finalize_operational_proposal(
                    slash_command.get("proposal"),
                    current_resolvable_port_calls(),
                )
                if action_proposal and action_proposal.get("intent") == "action":
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
                else:
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
        else:
            answer = answer_direct_operational_query(clean_question, plan=execution_plan)

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
                    pending_update = refine_pending_operational_action(
                        clean_question,
                        existing_pending.get("proposal", {}),
                        role,
                    )
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
                clean_question,
                runtime_history,
                execution_plan,
            )
            targeted_document_context = _build_targeted_document_context(
                clean_question,
                history,
                trusted_answers,
                reviewed_answers,
            )
            supplemental_sources = _build_supplemental_sources(
                clean_question,
                plan=execution_plan,
                conversation_state=conversation_state,
            )
            supplemental_sources.extend(targeted_document_context["companion_sources"])
            supplemental_sources.extend(targeted_document_context["document_sources"])
            global_companion_match = None
            allow_companion_shortcut = not execution_plan.requires_llm_synthesis
            if targeted_document_context["companion_answer"] and allow_companion_shortcut:
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
                    clean_question,
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
                elif review_correction_match and not execution_plan.requires_llm_synthesis:
                    answer = _build_review_correction_answer(review_correction_match)
                else:
                    if (
                        review_guard_match
                        and review_guard_match.get("similarity", 0) >= REVIEW_BLOCK_SIMILARITY
                        and not targeted_document_context["document_sources"]
                    ):
                        answer = _build_review_guard_answer(review_guard_match)
                    else:
                        if not services.rag.can_generate():
                            raise RuntimeError("Define a API key do LLM antes de usar o chatbot.")
                        answer = services.rag.answer(
                            question=clean_question,
                            retrieval_question=targeted_document_context["retrieval_question"],
                            role=role,
                            history=runtime_history[-10:],
                            supplemental_sources=supplemental_sources,
                            trusted_answers=trusted_answers,
                            reviewed_answers=reviewed_answers,
                            execution_plan=execution_plan.to_dict(),
                            conversation_state=conversation_state,
                        )
                        answer["answer_origin"] = "llm"
                        if trusted_answers:
                            answer["feedback_match"] = {
                                "similarity": trusted_answers[0]["similarity"],
                                "message_id": trusted_answers[0]["message_id"],
                                "question": trusted_answers[0]["question"],
                                "feedback_note": trusted_answers[0].get("feedback_note", ""),
                                "feedback_correction": trusted_answers[0].get("feedback_correction", ""),
                                "feedback_correction_document": trusted_answers[0].get("feedback_correction_document", ""),
                            }

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
