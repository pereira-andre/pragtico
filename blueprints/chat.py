"""Chat blueprint — API chat, conversations, feedback, pending actions."""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

import services
from security import api_limiter, rate_limit
from helpers import (
    build_operational_chat_sources,
    clear_pending_chat_action,
    execute_pending_operational_action,
    get_current_conversation,
    load_pending_chat_action,
    login_required,
    looks_like_pending_confirmation,
    propose_operational_action,
    refresh_knowledge_state,
    refine_pending_operational_action,
    save_pending_chat_action,
)

logger = logging.getLogger(__name__)

bp = Blueprint("chat", __name__)


@bp.route("/conversations")
@login_required
def chat_archive():
    """Página de arquivo de conversas do utilizador atual."""
    username = session["username"]
    current_conversation = get_current_conversation(username)
    conversations = services.store.list_conversations(username)
    messages = services.store.list_messages(username, current_conversation["id"])
    return render_template(
        "chat_archive.html",
        conversations=conversations,
        current_conversation=current_conversation,
        messages=messages,
        title="Conversas",
    )


@bp.route("/conversations", methods=["POST"])
@login_required
def create_conversation():
    """Criar uma nova conversa e redirecionar para o dashboard."""
    conversation = services.store.create_conversation(session["username"])
    flash("Nova conversa criada.", "success")
    return redirect(url_for("dashboard_bp.dashboard", conversation_id=conversation["id"]))


@bp.route("/conversations/<conversation_id>/rename", methods=["POST"])
@login_required
def rename_conversation(conversation_id: str):
    """Renomear uma conversa e redirecionar para o dashboard."""
    title = request.form.get("title", "")
    try:
        conversation = services.store.rename_conversation(session["username"], conversation_id, title)
        flash("Conversa renomeada.", "success")
        return redirect(url_for("dashboard_bp.dashboard", conversation_id=conversation["id"]))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard_bp.dashboard", conversation_id=conversation_id))


@bp.route("/conversations/<conversation_id>/clear", methods=["POST"])
@login_required
def clear_conversation(conversation_id: str):
    """Apagar todas as mensagens de uma conversa sem a eliminar."""
    try:
        services.store.clear_conversation(session["username"], conversation_id)
        flash("Mensagens da conversa removidas.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard_bp.dashboard", conversation_id=conversation_id))


@bp.route("/conversations/<conversation_id>/delete", methods=["POST"])
@login_required
def delete_conversation(conversation_id: str):
    """Eliminar uma conversa e redirecionar para a próxima disponível."""
    try:
        next_conversation_id = services.store.delete_conversation(session["username"], conversation_id)
        flash("Conversa eliminada.", "success")
        if next_conversation_id:
            return redirect(url_for("dashboard_bp.dashboard", conversation_id=next_conversation_id))
        return redirect(url_for("dashboard_bp.dashboard"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard_bp.dashboard", conversation_id=conversation_id))


@bp.route("/api/messages/<message_id>/feedback", methods=["POST"])
@login_required
def api_message_feedback(message_id: str):
    """API para submeter feedback de aprovação ou revisão numa mensagem do assistente."""
    payload = request.get_json(silent=True) or {}
    conversation_id = (payload.get("conversation_id") or "").strip()
    feedback_status = (payload.get("feedback_status") or "").strip().lower()
    feedback_note = (payload.get("feedback_note") or "").strip()
    if not conversation_id:
        return jsonify({"error": "conversation_id em falta."}), 400
    if feedback_status not in {"approved", "review"}:
        return jsonify({"error": "Estado de feedback inválido."}), 400
    try:
        message = services.store.update_message_feedback(
            username=session["username"], conversation_id=conversation_id,
            message_id=message_id, feedback_status=feedback_status, feedback_note=feedback_note,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(message)


@bp.route("/api/chat/pending-action")
@login_required
def api_pending_chat_action():
    """API que retorna a ação operacional pendente para uma conversa."""
    conversation_id = (request.args.get("conversation_id") or "").strip()
    if not conversation_id:
        return jsonify({"pending_action": None})
    pending = load_pending_chat_action(session["username"], conversation_id)
    return jsonify({"pending_action": pending})


@bp.route("/api/chat/pending-action/cancel", methods=["POST"])
@login_required
def api_cancel_pending_chat_action():
    """API para cancelar a ação operacional pendente numa conversa."""
    payload = request.get_json(silent=True) or {}
    conversation_id = (payload.get("conversation_id") or "").strip()
    if not conversation_id:
        return jsonify({"error": "conversation_id em falta."}), 400
    pending = load_pending_chat_action(session["username"], conversation_id)
    if not pending:
        return jsonify({"error": "Não existe ação pendente para cancelar."}), 404
    clear_pending_chat_action(session["username"], conversation_id)
    assistant_message = services.store.append_chat_message(
        username=session["username"], conversation_id=conversation_id,
        role="assistant", content="Ação operacional cancelada. O portal não foi alterado.",
    )
    return jsonify({"answer": assistant_message["content"], "message_id": assistant_message["id"], "pending_action": None, "conversation_id": conversation_id})


@bp.route("/api/chat/pending-action/confirm", methods=["POST"])
@login_required
def api_confirm_pending_chat_action():
    """API para confirmar e executar a ação operacional pendente numa conversa."""
    payload = request.get_json(silent=True) or {}
    conversation_id = (payload.get("conversation_id") or "").strip()
    if not conversation_id:
        return jsonify({"error": "conversation_id em falta."}), 400

    username = session["username"]
    pending = load_pending_chat_action(username, conversation_id)
    if not pending:
        return jsonify({"error": "Não existe ação pendente para confirmar."}), 404

    proposal = pending.get("proposal") or {}
    if proposal.get("missing_fields"):
        return jsonify({"error": "Ainda faltam dados obrigatórios antes de confirmar esta ação."}), 400

    try:
        result, message = execute_pending_operational_action(proposal, username=username, role=session.get("role", ""))
    except (PermissionError, ValueError) as exc:
        clear_pending_chat_action(username, conversation_id)
        assistant_message = services.store.append_chat_message(username=username, conversation_id=conversation_id, role="assistant", content=f"Não consegui aplicar a ação operacional. Motivo: {exc}")
        return jsonify({"error": str(exc), "answer": assistant_message["content"], "message_id": assistant_message["id"], "pending_action": None, "conversation_id": conversation_id}), 400
    except Exception as exc:
        logger.exception("Falha inesperada na execução da ação operacional do chat.")
        clear_pending_chat_action(username, conversation_id)
        assistant_message = services.store.append_chat_message(username=username, conversation_id=conversation_id, role="assistant", content="Falha inesperada ao aplicar a ação operacional no portal.")
        return jsonify({"error": str(exc), "answer": assistant_message["content"], "message_id": assistant_message["id"], "pending_action": None, "conversation_id": conversation_id}), 500

    clear_pending_chat_action(username, conversation_id)
    current_port_call = result if isinstance(result, dict) else None
    citations = []
    if current_port_call and current_port_call.get("reference_code"):
        citations.append({"document": current_port_call.get("vessel_name", "Escala"), "source_id": current_port_call.get("reference_code", ""), "retrieval_mode": "operational_action", "snippet": message})
    assistant_message = services.store.append_chat_message(username=username, conversation_id=conversation_id, role="assistant", content=message, citations=citations)
    return jsonify({"answer": assistant_message["content"], "message_id": assistant_message["id"], "pending_action": None, "conversation_id": conversation_id, "sources": citations})


@bp.route("/api/chat", methods=["POST"])
@login_required
@rate_limit(api_limiter)
def api_chat():
    """API principal do chat que processa perguntas e ações operacionais do utilizador."""
    refresh_knowledge_state(force_reindex=False)
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    conversation_id = (payload.get("conversation_id") or "").strip() or None

    if not question:
        return jsonify({"error": "Pergunta vazia."}), 400

    username = session["username"]
    conversation = services.store.ensure_conversation(username=username, conversation_id=conversation_id)
    history = services.store.list_messages(username, conversation["id"])
    existing_pending = load_pending_chat_action(username, conversation["id"])
    trusted_answers = services.store.find_feedback_matches(username, question, limit=3)
    supplemental_sources = build_operational_chat_sources(question)
    supplemental_sources.append(services.tide_service.context_for_question(question))
    if services.weather_service.enabled:
        try:
            weather_context = services.weather_service.context_source()
            if weather_context:
                supplemental_sources.append(weather_context)
        except Exception:
            pass
    user_message = services.store.append_chat_message(username=username, conversation_id=conversation["id"], role="user", content=question)

    answer = None
    if existing_pending:
        if looks_like_pending_confirmation(question):
            proposal = existing_pending.get("proposal", {})
            if proposal.get("missing_fields"):
                answer = {"answer": "Ainda faltam dados obrigatórios antes de confirmar esta ação.", "sources": [], "pending_action": load_pending_chat_action(username, conversation["id"]), "answer_origin": "pending_action_block"}
            else:
                try:
                    result, message = execute_pending_operational_action(proposal, username=username, role=session.get("role", ""))
                except (PermissionError, ValueError) as exc:
                    clear_pending_chat_action(username, conversation["id"])
                    answer = {"answer": f"Não consegui aplicar a ação operacional. Motivo: {exc}", "sources": [], "pending_action": None, "answer_origin": "pending_action_error"}
                except Exception:
                    logger.exception("Falha inesperada na execução da ação operacional do chat.")
                    clear_pending_chat_action(username, conversation["id"])
                    answer = {"answer": "Falha inesperada ao aplicar a ação operacional no portal.", "sources": [], "pending_action": None, "answer_origin": "pending_action_error"}
                else:
                    clear_pending_chat_action(username, conversation["id"])
                    citations = []
                    current_port_call = result if isinstance(result, dict) else None
                    if current_port_call and current_port_call.get("reference_code"):
                        citations.append({"document": current_port_call.get("vessel_name", "Escala"), "source_id": current_port_call.get("reference_code", ""), "retrieval_mode": "operational_action", "snippet": message})
                    answer = {"answer": message, "sources": citations, "pending_action": None, "answer_origin": "pending_action_confirmed"}
        else:
            pending_update = refine_pending_operational_action(question, existing_pending.get("proposal", {}), session.get("role", "piloto"))
            if pending_update and pending_update.get("intent") == "update" and pending_update.get("proposal", {}).get("intent") == "action":
                pending_action = save_pending_chat_action(username=username, conversation_id=conversation["id"], proposal=pending_update["proposal"], question=question)
                answer = {"answer": "Atualizei a ação pendente com a tua resposta.\n\n" + pending_action["summary"], "sources": [], "pending_action": load_pending_chat_action(username, conversation["id"]), "answer_origin": "operational_update"}
            elif pending_update and pending_update.get("intent") == "replace" and pending_update.get("proposal", {}).get("intent") == "action":
                pending_action = save_pending_chat_action(username=username, conversation_id=conversation["id"], proposal=pending_update["proposal"], question=question)
                answer = {"answer": "Troquei a proposta pendente por uma nova ação operacional.\n\n" + pending_action["summary"], "sources": [], "pending_action": load_pending_chat_action(username, conversation["id"]), "answer_origin": "operational_replace"}
            elif pending_update and pending_update.get("intent") == "cancel":
                clear_pending_chat_action(username, conversation["id"])
                answer = {"answer": "A ação operacional pendente foi cancelada. O portal não foi alterado.", "sources": [], "pending_action": None, "answer_origin": "pending_action_cancelled"}
            elif pending_update and pending_update.get("intent") == "question":
                answer = None
            else:
                answer = {"answer": (pending_update.get("reason") if pending_update and pending_update.get("reason") else "Não consegui atualizar a ação pendente com essa resposta."), "sources": [], "pending_action": load_pending_chat_action(username, conversation["id"]), "answer_origin": "pending_action_block"}
    else:
        action_proposal = propose_operational_action(question, session.get("role", "piloto"))
        if action_proposal and action_proposal.get("intent") == "action":
            pending_action = save_pending_chat_action(username=username, conversation_id=conversation["id"], proposal=action_proposal, question=question)
            answer = {"answer": pending_action["summary"], "sources": [], "pending_action": load_pending_chat_action(username, conversation["id"]), "answer_origin": "operational_proposal"}
        elif action_proposal and action_proposal.get("intent") == "unsupported":
            answer = {"answer": action_proposal.get("reason") or "Essa ação não pode ser executada pelo bot nesta conta.", "sources": [], "answer_origin": "operational_rejected"}
        elif action_proposal and action_proposal.get("intent") == "question":
            answer = None
        else:
            answer = None

    if answer is None and trusted_answers and trusted_answers[0].get("similarity", 0) >= 0.96:
        best_match = trusted_answers[0]
        answer = {
            "answer": best_match["answer"], "sources": best_match.get("citations", []),
            "answer_origin": "approved_memory",
            "feedback_match": {"similarity": best_match["similarity"], "message_id": best_match["message_id"], "question": best_match["question"], "feedback_note": best_match.get("feedback_note", "")},
        }
    elif answer is None:
        if not services.rag.client:
            return jsonify({"error": "Define a API key do LLM antes de usar o chatbot."}), 500
        answer = services.rag.answer(
            question=question, role=session.get("role", "piloto"),
            history=(history + [user_message])[-10:],
            supplemental_sources=supplemental_sources, trusted_answers=trusted_answers,
        )
        answer["answer_origin"] = "llm"
        if trusted_answers:
            answer["feedback_match"] = {"similarity": trusted_answers[0]["similarity"], "message_id": trusted_answers[0]["message_id"], "question": trusted_answers[0]["question"], "feedback_note": trusted_answers[0].get("feedback_note", "")}

    assistant_message = services.store.append_chat_message(username=username, conversation_id=conversation["id"], role="assistant", content=answer["answer"], citations=answer.get("sources", []))
    return jsonify({**answer, "conversation_id": conversation["id"], "message_id": assistant_message["id"], "pending_action": answer.get("pending_action")})
