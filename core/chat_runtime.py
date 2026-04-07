"""Channel-agnostic chat runtime shared by web chat and WhatsApp."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Iterator

from flask import session

from core import services
from core.helpers import (
    answer_direct_operational_query,
    answer_slash_query,
    answer_slash_validation,
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

logger = logging.getLogger(__name__)

APPROVED_MEMORY_SIMILARITY = 0.96
REVIEW_GUARD_SIMILARITY = 0.9
REVIEW_BLOCK_SIMILARITY = 0.97


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
    best_review = reviewed_answers[0] if reviewed_answers else None
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


def _build_supplemental_sources(question: str) -> list[dict]:
    supplemental_sources = build_operational_chat_sources(question)
    supplemental_sources.append(services.tide_service.context_for_question(question))
    if services.weather_service.enabled:
        try:
            weather_context = services.weather_service.context_for_question(question)
            if weather_context:
                supplemental_sources.append(weather_context)
        except Exception:
            pass
    if re.search(r"\b(aviso|avisos|anav|capitania)\b", question, flags=re.IGNORECASE):
        try:
            warnings_context = (
                services.local_warning_service.context_source()
                if getattr(services, "local_warning_service", None)
                else None
            )
            if warnings_context:
                supplemental_sources.append(warnings_context)
        except Exception:
            pass
    if re.search(
        r"\b(ondulacao|ondulação|leitura costeira|altura significativa|periodo medio|período médio|temp\.? agua|temperatura da agua|temperatura da água)\b",
        question,
        flags=re.IGNORECASE,
    ):
        try:
            wave_context = (
                services.wave_service.context_source()
                if getattr(services, "wave_service", None)
                else None
            )
            if wave_context:
                supplemental_sources.append(wave_context)
        except Exception:
            pass
    return supplemental_sources


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
            answer = answer_direct_operational_query(clean_question)

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

        if answer is None and review_guard_match and review_guard_match.get("similarity", 0) >= REVIEW_BLOCK_SIMILARITY:
            answer = _build_review_guard_answer(review_guard_match)
        elif (
            answer is None
            and trusted_answers
            and trusted_answers[0].get("similarity", 0) >= APPROVED_MEMORY_SIMILARITY
            and not review_guard_match
        ):
            best_match = trusted_answers[0]
            answer = {
                "answer": best_match["answer"],
                "sources": best_match.get("citations", []),
                "answer_origin": "approved_memory",
                "feedback_match": {
                    "similarity": best_match["similarity"],
                    "message_id": best_match["message_id"],
                    "question": best_match["question"],
                    "feedback_note": best_match.get("feedback_note", ""),
                },
            }
        elif answer is None:
            if not services.rag.can_generate():
                raise RuntimeError("Define a API key do LLM antes de usar o chatbot.")
            answer = services.rag.answer(
                question=clean_question,
                role=role,
                history=(history + [user_message])[-10:],
                supplemental_sources=_build_supplemental_sources(clean_question),
                trusted_answers=trusted_answers,
                reviewed_answers=reviewed_answers,
            )
            answer["answer_origin"] = "llm"
            if trusted_answers:
                answer["feedback_match"] = {
                    "similarity": trusted_answers[0]["similarity"],
                    "message_id": trusted_answers[0]["message_id"],
                    "question": trusted_answers[0]["question"],
                    "feedback_note": trusted_answers[0].get("feedback_note", ""),
                }

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
