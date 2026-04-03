"""Chat blueprint — API chat, conversations, feedback, pending actions."""

import logging
import json
import re
import unicodedata
from textwrap import wrap

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for

from core import services
from domain.chat_actions import (
    build_action_reply_template,
    looks_like_operational_command,
    looks_like_operational_query,
    looks_like_slash_command,
    parse_slash_command,
)
from core.security import api_limiter, rate_limit
from core.helpers import (
    answer_direct_operational_query,
    answer_slash_query,
    answer_slash_validation,
    build_operational_chat_sources,
    clear_pending_chat_action,
    current_resolvable_port_calls,
    execute_pending_operational_action,
    finalize_operational_proposal,
    get_current_conversation,
    load_pending_chat_action,
    login_required,
    looks_like_pending_confirmation,
    refresh_knowledge_state,
    refine_pending_operational_action,
    save_pending_chat_action,
)
from storage.utils import _utc_iso_to_label

logger = logging.getLogger(__name__)

bp = Blueprint("chat", __name__)

APPROVED_MEMORY_SIMILARITY = 0.96
REVIEW_GUARD_SIMILARITY = 0.9
REVIEW_BLOCK_SIMILARITY = 0.97


def _wants_archive_redirect() -> bool:
    return (request.form.get("redirect_to") or "").strip().lower() == "archive"


def _redirect_after_conversation_action(conversation_id: str | None = None):
    if _wants_archive_redirect():
        if conversation_id:
            return redirect(url_for("chat.chat_archive", conversation_id=conversation_id))
        return redirect(url_for("chat.chat_archive"))
    if conversation_id:
        return redirect(url_for("dashboard_bp.dashboard", conversation_id=conversation_id))
    return redirect(url_for("dashboard_bp.dashboard"))


def _conversation_export_payload(username: str, conversation_id: str) -> tuple[dict | None, list[dict] | None]:
    conversation = services.store.ensure_conversation(username, conversation_id)
    if conversation["id"] != conversation_id:
        return None, None
    return conversation, services.store.list_messages(username, conversation_id)


def _conversation_export_text(conversation: dict, messages: list[dict]) -> str:
    role_labels = {
        "user": "Utilizador",
        "assistant": "Assistente",
        "system": "Sistema",
    }
    lines = [
        f"Conversa: {conversation.get('title', 'Conversa')}",
        f"Criada: {conversation.get('created_at_label', '--')}",
        f"Atualizada: {conversation.get('updated_at_label', '--')}",
        "",
    ]
    for message in messages:
        lines.append(
            f"[{message.get('created_at_label', '--')}] "
            f"{role_labels.get(message.get('role'), str(message.get('role') or 'Mensagem').title())}"
        )
        content = str(message.get("content") or "").strip()
        if content:
            lines.extend(content.splitlines())
        citations = [item.get("document", "") for item in (message.get("citations") or []) if item.get("document")]
        if citations:
            lines.append("Fontes: " + ", ".join(citations))
        feedback_status = (message.get("feedback_status") or "").strip()
        if feedback_status:
            feedback_label = "Aprovada" if feedback_status == "approved" else "Rever"
            feedback_note = (message.get("feedback_note") or "").strip()
            suffix = f" | Nota: {feedback_note}" if feedback_note else ""
            lines.append(f"Feedback: {feedback_label}{suffix}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _pdf_safe_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    encoded = normalized.encode("latin-1", "replace").decode("latin-1")
    return encoded.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _render_text_pdf(title: str, body: str) -> bytes:
    page_width = 595
    page_height = 842
    margin = 48
    line_height = 14
    max_chars = 96

    raw_lines = [title, "", *str(body or "").splitlines()]
    wrapped_lines: list[str] = []
    for raw_line in raw_lines:
        clean = str(raw_line or "").replace("\t", "  ")
        if not clean:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            wrap(
                clean,
                width=max_chars,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )

    max_lines_per_page = max(1, int((page_height - (margin * 2)) / line_height))
    pages = [
        wrapped_lines[index:index + max_lines_per_page]
        for index in range(0, len(wrapped_lines), max_lines_per_page)
    ] or [["Sem conteúdo."]]

    objects: dict[int, bytes] = {}
    font_id = 3
    next_id = 4
    page_ids: list[int] = []

    for page_lines in pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)

        stream_lines = ["BT", "/F1 10 Tf"]
        y = page_height - margin
        for line in page_lines:
            stream_lines.append(f"1 0 0 1 {margin} {y} Tm ({_pdf_safe_text(line)}) Tj")
            y -= line_height
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1", "replace")

        objects[content_id] = (
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")

    objects[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for object_id in range(1, next_id):
        offsets[object_id] = len(pdf)
        pdf.extend(f"{object_id} 0 obj\n".encode("ascii"))
        pdf.extend(objects[object_id])
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {next_id}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for object_id in range(1, next_id):
        pdf.extend(f"{offsets[object_id]:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {next_id} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("ascii")
    )
    return bytes(pdf)


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


def _conversation_shell(username: str, conversation_id: str) -> dict:
    """Return lightweight metadata for a specific conversation owned by the user."""
    conversation = services.store.ensure_conversation(username, conversation_id)
    if conversation["id"] != conversation_id:
        raise ValueError("Conversa não encontrada.")
    return {
        "conversation": conversation,
        "conversations": services.store.list_conversations(username),
        "pending_action": load_pending_chat_action(username, conversation_id),
    }


def _conversation_payload(username: str, conversation_id: str) -> dict:
    """Return the full conversation payload for widget hydration."""
    payload = _conversation_shell(username, conversation_id)
    payload["messages"] = services.store.list_messages(username, conversation_id)
    return payload


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
    """Criar uma nova conversa e redirecionar para o destino adequado."""
    conversation = services.store.create_conversation(session["username"])
    flash("Nova conversa criada.", "success")
    return _redirect_after_conversation_action(conversation["id"])


@bp.route("/api/conversations", methods=["POST"])
@login_required
def api_create_conversation():
    """Criar uma conversa para o widget sem redirecionar a página atual."""
    conversation = services.store.create_conversation(session["username"])
    return jsonify(_conversation_payload(session["username"], conversation["id"])), 201


@bp.route("/api/conversations/<conversation_id>")
@login_required
def api_get_conversation(conversation_id: str):
    """Retornar uma conversa específica para o widget do chat."""
    try:
        return jsonify(_conversation_payload(session["username"], conversation_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@bp.route("/conversations/<conversation_id>/export.json")
@login_required
def export_conversation_json(conversation_id: str):
    """Exportar uma conversa e respetivas mensagens em JSON."""
    conversation, messages = _conversation_export_payload(session["username"], conversation_id)
    if not conversation:
        flash("Conversa não encontrada.", "error")
        return redirect(url_for("chat.chat_archive"))
    payload = {
        "conversation": conversation,
        "messages": messages,
    }
    filename = f"conversa_{conversation_id}.json"
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/conversations/<conversation_id>/export.txt")
@login_required
def export_conversation_txt(conversation_id: str):
    """Exportar uma conversa em texto simples."""
    conversation, messages = _conversation_export_payload(session["username"], conversation_id)
    if not conversation:
        flash("Conversa não encontrada.", "error")
        return redirect(url_for("chat.chat_archive"))
    filename = f"conversa_{conversation_id}.txt"
    return Response(
        _conversation_export_text(conversation, messages),
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/conversations/<conversation_id>/export.pdf")
@login_required
def export_conversation_pdf(conversation_id: str):
    """Exportar uma conversa em PDF simples e imprimível."""
    conversation, messages = _conversation_export_payload(session["username"], conversation_id)
    if not conversation:
        flash("Conversa não encontrada.", "error")
        return redirect(url_for("chat.chat_archive"))
    pdf = _render_text_pdf(
        title=f"Conversa · {conversation.get('title', 'Conversa')}",
        body=_conversation_export_text(conversation, messages),
    )
    filename = f"conversa_{conversation_id}.pdf"
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/conversations/<conversation_id>/rename", methods=["POST"])
@login_required
def rename_conversation(conversation_id: str):
    """Renomear uma conversa e redirecionar para o destino adequado."""
    title = request.form.get("title", "")
    try:
        conversation = services.store.rename_conversation(session["username"], conversation_id, title)
        flash("Conversa renomeada.", "success")
        return _redirect_after_conversation_action(conversation["id"])
    except ValueError as exc:
        flash(str(exc), "error")
        return _redirect_after_conversation_action(conversation_id)


@bp.route("/api/conversations/<conversation_id>/rename", methods=["POST"])
@login_required
def api_rename_conversation(conversation_id: str):
    """Renomear uma conversa sem recarregar a página atual."""
    payload = request.get_json(silent=True) or {}
    title = payload.get("title", "")
    try:
        services.store.rename_conversation(session["username"], conversation_id, title)
        return jsonify(_conversation_shell(session["username"], conversation_id))
    except ValueError as exc:
        status_code = 404 if "não encontrada" in str(exc).lower() else 400
        return jsonify({"error": str(exc)}), status_code


@bp.route("/conversations/<conversation_id>/clear", methods=["POST"])
@login_required
def clear_conversation(conversation_id: str):
    """Apagar todas as mensagens de uma conversa sem a eliminar."""
    try:
        services.store.clear_conversation(session["username"], conversation_id)
        flash("Mensagens da conversa removidas.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_after_conversation_action(conversation_id)


@bp.route("/conversations/<conversation_id>/delete", methods=["POST"])
@login_required
def delete_conversation(conversation_id: str):
    """Eliminar uma conversa e redirecionar para a próxima disponível."""
    try:
        next_conversation_id = services.store.delete_conversation(session["username"], conversation_id)
        flash("Conversa eliminada.", "success")
        return _redirect_after_conversation_action(next_conversation_id)
    except ValueError as exc:
        flash(str(exc), "error")
        return _redirect_after_conversation_action(conversation_id)


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
    if feedback_status == "review" and not feedback_note:
        return jsonify({"error": "Ao pedir revisão indica a correção ou o motivo."}), 400
    try:
        message = services.store.update_message_feedback(
            username=session["username"], conversation_id=conversation_id,
            message_id=message_id, feedback_status=feedback_status, feedback_note=feedback_note,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    payload = dict(message)
    feedback_updated_at = payload.get("feedback_updated_at")
    payload["feedback_updated_at_label"] = (
        _utc_iso_to_label(feedback_updated_at) if feedback_updated_at else ""
    )
    return jsonify(payload)


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
    shell = _conversation_shell(session["username"], conversation_id)
    return jsonify({
        "answer": assistant_message["content"],
        "message_id": assistant_message["id"],
        "created_at_label": assistant_message.get("created_at_label", ""),
        "conversation_id": conversation_id,
        **shell,
    })


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
        shell = _conversation_shell(username, conversation_id)
        return jsonify({
            "error": str(exc),
            "answer": assistant_message["content"],
            "message_id": assistant_message["id"],
            "created_at_label": assistant_message.get("created_at_label", ""),
            "conversation_id": conversation_id,
            **shell,
        }), 400
    except Exception as exc:
        logger.exception("Falha inesperada na execução da ação operacional do chat.")
        clear_pending_chat_action(username, conversation_id)
        assistant_message = services.store.append_chat_message(username=username, conversation_id=conversation_id, role="assistant", content="Falha inesperada ao aplicar a ação operacional no portal.")
        shell = _conversation_shell(username, conversation_id)
        return jsonify({
            "error": str(exc),
            "answer": assistant_message["content"],
            "message_id": assistant_message["id"],
            "created_at_label": assistant_message.get("created_at_label", ""),
            "conversation_id": conversation_id,
            **shell,
        }), 500

    clear_pending_chat_action(username, conversation_id)
    current_port_call = result if isinstance(result, dict) else None
    citations = []
    if current_port_call and current_port_call.get("reference_code"):
        citations.append({"document": current_port_call.get("vessel_name", "Escala"), "source_id": current_port_call.get("reference_code", ""), "retrieval_mode": "operational_action", "snippet": message})
    assistant_message = services.store.append_chat_message(username=username, conversation_id=conversation_id, role="assistant", content=message, citations=citations)
    shell = _conversation_shell(username, conversation_id)
    return jsonify({
        "answer": assistant_message["content"],
        "message_id": assistant_message["id"],
        "created_at_label": assistant_message.get("created_at_label", ""),
        "conversation_id": conversation_id,
        "sources": citations,
        "refresh_required": True,
        "port_call_id": current_port_call.get("id", "") if current_port_call else "",
        **shell,
    })


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
    trusted_answers = services.store.find_feedback_matches(
        username,
        question,
        limit=3,
        feedback_statuses={"approved"},
    )
    reviewed_answers = services.store.find_feedback_matches(
        username,
        question,
        limit=3,
        feedback_statuses={"review"},
    )
    review_guard_match = _select_review_guard_match(reviewed_answers, trusted_answers)
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
            warnings_context = services.local_warning_service.context_source() if getattr(services, "local_warning_service", None) else None
            if warnings_context:
                supplemental_sources.append(warnings_context)
        except Exception:
            pass
    if re.search(r"\b(ondulacao|ondulação|leitura costeira|altura significativa|periodo medio|período médio|temp\.? agua|temperatura da agua|temperatura da água)\b", question, flags=re.IGNORECASE):
        try:
            wave_context = services.wave_service.context_source() if getattr(services, "wave_service", None) else None
            if wave_context:
                supplemental_sources.append(wave_context)
        except Exception:
            pass
    user_message = services.store.append_chat_message(username=username, conversation_id=conversation["id"], role="user", content=question)

    answer = None
    slash_command = parse_slash_command(question, session.get("role", "piloto")) if looks_like_slash_command(question) else None
    if slash_command and slash_command.get("intent") == "help":
        answer = {"answer": slash_command["answer"], "sources": [], "answer_origin": "slash_help"}
    elif slash_command and slash_command.get("intent") == "query":
        answer = answer_slash_query(slash_command.get("command", ""), slash_command.get("argument", ""), session.get("role", "piloto"))
    elif slash_command and slash_command.get("intent") == "validate":
        answer = answer_slash_validation(slash_command.get("target") or {}, session.get("role", "piloto"))
    elif slash_command and slash_command.get("intent") == "template":
        proposal = slash_command.get("proposal") or {}
        if proposal.get("intent") == "action" and proposal.get("action"):
            pending_action = save_pending_chat_action(username=username, conversation_id=conversation["id"], proposal=proposal, question=question)
            answer = {"answer": pending_action["summary"], "sources": [], "pending_action": load_pending_chat_action(username, conversation["id"]), "answer_origin": "slash_template"}
        else:
            answer = {"answer": slash_command["answer"], "sources": [], "answer_origin": "slash_template"}
    elif slash_command and slash_command.get("intent") == "unsupported":
        answer = {"answer": slash_command["answer"], "sources": [], "answer_origin": "slash_rejected"}
    elif slash_command and slash_command.get("intent") == "action":
        action_proposal = finalize_operational_proposal(slash_command.get("proposal"), current_resolvable_port_calls())
        if action_proposal and action_proposal.get("intent") == "action":
            pending_action = save_pending_chat_action(username=username, conversation_id=conversation["id"], proposal=action_proposal, question=question)
            answer = {"answer": pending_action["summary"], "sources": [], "pending_action": load_pending_chat_action(username, conversation["id"]), "answer_origin": "slash_proposal"}
        else:
            proposal = slash_command.get("proposal") or {}
            template = build_action_reply_template(proposal.get("action", ""), proposal.get("missing_fields", []))
            reason = (action_proposal or {}).get("reason") or "Não consegui interpretar o comando com segurança."
            if template:
                reason = f"{reason}\n\n{template}"
            answer = {"answer": reason, "sources": [], "answer_origin": "slash_rejected"}
    else:
        answer = answer_direct_operational_query(question)
    if answer is None:
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
                    proposal = existing_pending.get("proposal", {})
                    template = build_action_reply_template(proposal.get("action", ""), proposal.get("missing_fields", [])) if proposal.get("missing_fields") else ""
                    block_message = pending_update.get("reason") if pending_update and pending_update.get("reason") else "Não consegui atualizar a ação pendente com essa resposta."
                    if template:
                        block_message = f"{block_message}\n\n{template}"
                    answer = {"answer": block_message, "sources": [], "pending_action": load_pending_chat_action(username, conversation["id"]), "answer_origin": "pending_action_block"}
        else:
            if looks_like_operational_command(question):
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
                answer = None

    if answer is None and review_guard_match and review_guard_match.get("similarity", 0) >= REVIEW_BLOCK_SIMILARITY:
        answer = _build_review_guard_answer(review_guard_match)
    elif answer is None and trusted_answers and trusted_answers[0].get("similarity", 0) >= APPROVED_MEMORY_SIMILARITY and not review_guard_match:
        best_match = trusted_answers[0]
        answer = {
            "answer": best_match["answer"], "sources": best_match.get("citations", []),
            "answer_origin": "approved_memory",
            "feedback_match": {"similarity": best_match["similarity"], "message_id": best_match["message_id"], "question": best_match["question"], "feedback_note": best_match.get("feedback_note", "")},
        }
    elif answer is None:
        if not services.rag.can_generate():
            return jsonify({"error": "Define a API key do LLM antes de usar o chatbot."}), 500
        answer = services.rag.answer(
            question=question, role=session.get("role", "piloto"),
            history=(history + [user_message])[-10:],
            supplemental_sources=supplemental_sources,
            trusted_answers=trusted_answers,
            reviewed_answers=reviewed_answers,
        )
        answer["answer_origin"] = "llm"
        if trusted_answers:
            answer["feedback_match"] = {"similarity": trusted_answers[0]["similarity"], "message_id": trusted_answers[0]["message_id"], "question": trusted_answers[0]["question"], "feedback_note": trusted_answers[0].get("feedback_note", "")}

    assistant_message = services.store.append_chat_message(username=username, conversation_id=conversation["id"], role="assistant", content=answer["answer"], citations=answer.get("sources", []))
    shell = _conversation_shell(username, conversation["id"])
    return jsonify({
        **answer,
        "conversation_id": conversation["id"],
        "message_id": assistant_message["id"],
        "created_at_label": assistant_message.get("created_at_label", ""),
        **shell,
    })
