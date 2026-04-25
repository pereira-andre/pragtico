"""Chat blueprint — API chat, conversations, feedback, pending actions."""

import json
import logging
import re
from datetime import datetime
from io import BytesIO
from textwrap import wrap
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for

from core import services
from core.chat_feedback import sync_feedback_correction_eval_case
from core.chat_runtime import handle_chat_turn
from domain.chat_actions import (
    build_action_reply_template,
    looks_like_operational_command,
    looks_like_operational_query,
    looks_like_slash_command,
    parse_slash_command,
)
from domain.document_processing import slugify
from domain.error_catalog import error_payload, flash_error_message, log_error_event
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
CONVERSATION_EXPORT_FORMATS = {"json", "txt", "pdf"}


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


def _conversation_export_json_payload(conversation: dict, messages: list[dict]) -> dict:
    return {
        "conversation": conversation,
        "messages": messages,
    }


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
            feedback_correction = (message.get("feedback_correction") or "").strip()
            parts = []
            if feedback_note:
                parts.append(f"Nota: {feedback_note}")
            if feedback_correction:
                parts.append(f"Correção: {feedback_correction}")
            suffix = f" | {' | '.join(parts)}" if parts else ""
            lines.append(f"Feedback: {feedback_label}{suffix}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _pdf_safe_text(value: str) -> str:
    encoded = str(value or "").encode("cp1252", "replace").decode("cp1252")
    return encoded.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _render_text_pdf(title: str, body: str) -> bytes:
    page_width = 595
    page_height = 842
    margin = 48
    font_size = 10
    line_height = 14
    usable_width = page_width - (margin * 2)
    # Keep wrapping conservative so Helvetica lines never clip on the right edge.
    avg_char_width = font_size * 0.62
    max_chars = max(40, int(usable_width / avg_char_width))

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

        stream_lines = ["BT", f"/F1 {font_size} Tf"]
        y = page_height - margin
        for line in page_lines:
            stream_lines.append(f"1 0 0 1 {margin} {y} Tm ({_pdf_safe_text(line)}) Tj")
            y -= line_height
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("cp1252", "replace")

        objects[content_id] = (
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")

    objects[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
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


def _conversation_export_filename(conversation: dict, extension: str) -> str:
    title = slugify(str(conversation.get("title") or "conversa"))
    conversation_id = slugify(str(conversation.get("id") or "conversa"))
    return f"conversa_{title}_{conversation_id}.{extension}"


def _conversation_export_bytes(conversation: dict, messages: list[dict], export_format: str) -> tuple[bytes, str, str]:
    export_format = str(export_format or "").strip().lower()
    if export_format not in CONVERSATION_EXPORT_FORMATS:
        raise ValueError("Formato de exportação inválido.")

    if export_format == "json":
        payload = _conversation_export_json_payload(conversation, messages)
        return (
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
            _conversation_export_filename(conversation, "json"),
        )
    if export_format == "txt":
        return (
            _conversation_export_text(conversation, messages).encode("utf-8-sig"),
            "text/plain; charset=utf-8",
            _conversation_export_filename(conversation, "txt"),
        )
    pdf = _render_text_pdf(
        title=f"Conversa · {conversation.get('title', 'Conversa')}",
        body=_conversation_export_text(conversation, messages),
    )
    return pdf, "application/pdf", _conversation_export_filename(conversation, "pdf")


def _selected_conversations_for_export(username: str, selected_ids: list[str]) -> list[dict]:
    conversations = services.store.list_conversations(username)
    if not selected_ids:
        return conversations
    selected_lookup = {str(item).strip() for item in selected_ids if str(item).strip()}
    return [conversation for conversation in conversations if conversation.get("id") in selected_lookup]


def _conversation_export_zip(username: str, conversations: list[dict], export_format: str) -> bytes:
    bundle = BytesIO()
    with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as archive:
        for conversation in conversations:
            messages = services.store.list_messages(username, conversation["id"])
            payload, _mimetype, filename = _conversation_export_bytes(conversation, messages, export_format)
            archive.writestr(filename, payload)
    return bundle.getvalue()


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
        return jsonify({"error": flash_error_message(str(exc))}), 404


@bp.route("/conversations/<conversation_id>/export.json")
@login_required
def export_conversation_json(conversation_id: str):
    """Exportar uma conversa e respetivas mensagens em JSON."""
    conversation, messages = _conversation_export_payload(session["username"], conversation_id)
    if not conversation:
        flash("Conversa não encontrada.", "error")
        return redirect(url_for("chat.chat_archive"))
    payload, mimetype, filename = _conversation_export_bytes(conversation, messages, "json")
    return Response(
        payload,
        mimetype=mimetype,
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
    payload, mimetype, filename = _conversation_export_bytes(conversation, messages, "txt")
    return Response(
        payload,
        mimetype=mimetype,
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
    payload, mimetype, filename = _conversation_export_bytes(conversation, messages, "pdf")
    return Response(
        payload,
        mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/conversations/export", methods=["GET", "POST"])
@login_required
def export_conversations_bundle():
    """Exportar conversas selecionadas, ou todas quando não há seleção, num ZIP por formato."""
    export_format = (request.values.get("export_format") or "").strip().lower()
    if export_format not in CONVERSATION_EXPORT_FORMATS:
        flash("Formato de exportação inválido.", "error")
        return redirect(url_for("chat.chat_archive"))

    selected_ids = request.values.getlist("conversation_ids")
    conversations = _selected_conversations_for_export(session["username"], selected_ids)
    if not conversations:
        flash("Nenhuma conversa encontrada para exportação.", "error")
        return redirect(url_for("chat.chat_archive"))

    payload = _conversation_export_zip(session["username"], conversations, export_format)
    selection_label = "selecionadas" if selected_ids else "todas"
    filename = f"conversas_{selection_label}_{export_format}_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
    return Response(
        payload,
        mimetype="application/zip",
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
        flash(flash_error_message(str(exc)), "error")
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
        return jsonify({"error": flash_error_message(str(exc))}), status_code


@bp.route("/conversations/<conversation_id>/clear", methods=["POST"])
@login_required
def clear_conversation(conversation_id: str):
    """Apagar todas as mensagens de uma conversa sem a eliminar."""
    try:
        services.store.clear_conversation(session["username"], conversation_id)
        flash("Mensagens da conversa removidas.", "success")
    except ValueError as exc:
        flash(flash_error_message(str(exc)), "error")
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
        flash(flash_error_message(str(exc)), "error")
        return _redirect_after_conversation_action(conversation_id)


@bp.route("/conversations/delete-all", methods=["POST"])
@login_required
def delete_all_conversations():
    """Eliminar todas as conversas do utilizador atual."""
    username = session["username"]
    conversations = services.store.list_conversations(username)
    removed_count = 0
    for conversation in list(conversations):
        conversation_id = str(conversation.get("id") or "").strip()
        if not conversation_id:
            continue
        clear_pending_chat_action(username, conversation_id)
        services.store.delete_conversation(username, conversation_id)
        removed_count += 1
    if removed_count:
        flash(f"{removed_count} conversa(s) eliminada(s).", "success")
    else:
        flash("Não existiam conversas para eliminar.", "success")
    return _redirect_after_conversation_action(None)


@bp.route("/api/messages/<message_id>/feedback", methods=["POST"])
@login_required
def api_message_feedback(message_id: str):
    """API para submeter feedback de aprovação ou revisão numa mensagem do assistente."""
    payload = request.get_json(silent=True) or {}
    conversation_id = (payload.get("conversation_id") or "").strip()
    feedback_status = (payload.get("feedback_status") or "").strip().lower()
    feedback_note = (payload.get("feedback_note") or "").strip()
    feedback_correction = (payload.get("feedback_correction") or "").strip()
    feedback_correction_document = (payload.get("feedback_correction_document") or "").strip()
    if not conversation_id:
        return jsonify({"error": flash_error_message("conversation_id em falta.")}), 400
    if feedback_status not in {"approved", "review"}:
        return jsonify({"error": flash_error_message("Estado de feedback inválido.")}), 400
    if feedback_status == "review" and not feedback_note and not feedback_correction:
        return jsonify({"error": flash_error_message("Ao pedir revisão indica o motivo ou a resposta corrigida.")}), 400
    try:
        message = services.store.update_message_feedback(
            username=session["username"], conversation_id=conversation_id,
            message_id=message_id,
            feedback_status=feedback_status,
            feedback_note=feedback_note,
            feedback_correction=feedback_correction,
            feedback_correction_document=feedback_correction_document,
            feedback_updated_by=session["username"],
        )
        sync_feedback_correction_eval_case(
            services.store,
            session["username"],
            conversation_id,
            message_id,
            source="web",
        )
    except ValueError as exc:
        return jsonify({"error": flash_error_message(str(exc))}), 400
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
        return jsonify({"error": flash_error_message("conversation_id em falta.")}), 400
    pending = load_pending_chat_action(session["username"], conversation_id)
    if not pending:
        return jsonify({"error": flash_error_message("Não existe ação pendente para cancelar.")}), 404
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
        return jsonify({"error": flash_error_message("conversation_id em falta.")}), 400

    username = session["username"]
    pending = load_pending_chat_action(username, conversation_id)
    if not pending:
        return jsonify({"error": flash_error_message("Não existe ação pendente para confirmar.")}), 404

    proposal = pending.get("proposal") or {}
    if proposal.get("missing_fields"):
        return jsonify({"error": flash_error_message("Ainda faltam dados obrigatórios antes de confirmar esta ação.")}), 400

    try:
        result, message = execute_pending_operational_action(proposal, username=username, role=session.get("role", ""))
    except (PermissionError, ValueError) as exc:
        clear_pending_chat_action(username, conversation_id)
        tagged = flash_error_message(str(exc))
        assistant_message = services.store.append_chat_message(username=username, conversation_id=conversation_id, role="assistant", content=f"Não consegui aplicar a ação operacional. Motivo: {tagged}")
        shell = _conversation_shell(username, conversation_id)
        return jsonify({
            "error": tagged,
            "answer": assistant_message["content"],
            "message_id": assistant_message["id"],
            "created_at_label": assistant_message.get("created_at_label", ""),
            "conversation_id": conversation_id,
            **shell,
        }), 400
    except Exception as exc:
        log_error_event(logger, "UNEXPECTED_BOT_ACTION", detail=str(exc))
        clear_pending_chat_action(username, conversation_id)
        assistant_message = services.store.append_chat_message(username=username, conversation_id=conversation_id, role="assistant", content=flash_error_message("Falha inesperada ao aplicar a ação operacional no portal."))
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
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    conversation_id = (payload.get("conversation_id") or "").strip() or None

    if not question:
        return jsonify(error_payload("EMPTY_QUESTION")), 400

    username = session["username"]
    try:
        result = handle_chat_turn(
            username=username,
            role=session.get("role", "piloto"),
            question=question,
            conversation_id=conversation_id,
            channel="web",
            allow_mutations=True,
        )
    except RuntimeError as exc:
        log_error_event(
            logger,
            "CHAT_RUNTIME_FAILED",
            detail=str(exc),
            channel="web",
            username=username,
            endpoint="/api/chat",
        )
        return jsonify(error_payload("CHAT_RUNTIME_FAILED", detail=str(exc), expose_detail=True)), 500

    shell = _conversation_shell(username, result["conversation_id"])
    return jsonify({
        **result,
        **shell,
    })
