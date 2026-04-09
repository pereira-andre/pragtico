"""Admin blueprint — users, documents, status, migration, reindex."""

from collections import Counter
import logging
import os
import re
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from core import services
from core.whatsapp_support import build_user_whatsapp_view, verify_user_whatsapp
from core.validators import validate_email, validate_password, validate_phone, validate_required_text, validate_role, validate_whatsapp_phone
from domain.knowledge_companions import companion_directory, load_document_companion
from domain.knowledge_evals import evaluate_companion_cases, load_eval_cases_from_dir, load_eval_cases_from_store
from core.helpers import (
    current_reindex_status_payload,
    load_admin_status,
    login_required,
    refresh_knowledge_state,
    role_required,
    safe_rebuild_index,
    start_reindex_job,
)
from domain.migration_service import migrate_local_json_to_postgres
from storage.utils import _local_iso_to_label

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)


def _normalize_digits(value: str | None) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _resolved_whatsapp_opt_in_at(existing_user: dict | None, whatsapp_number: str, whatsapp_opt_in: bool) -> str:
    if not whatsapp_opt_in or not whatsapp_number:
        return ""
    existing = existing_user or {}
    if bool(existing.get("whatsapp_opt_in")) and _normalize_digits(existing.get("whatsapp_number")) == whatsapp_number:
        return str(existing.get("whatsapp_opt_in_at") or "").strip()
    return ""


def _admin_users_payload() -> list[dict]:
    service = getattr(services, "whatsapp_service", None)
    return [
        build_user_whatsapp_view(user, service, services.store)
        for user in services.store.list_users()
    ]


def _manual_knowledge_authoring_enabled() -> bool:
    return bool(current_app.config.get("MANUAL_KNOWLEDGE_AUTHORING_ENABLED", False))


def _dedupe_eval_cases(cases: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for item in cases:
        key = (
            str(item.get("source_message_id") or "").strip()
            or f"{str(item.get('document') or '').strip().lower()}::{str(item.get('question') or '').strip().lower()}"
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _feedback_source_label(source: str) -> str:
    clean_source = str(source or "").strip().lower()
    if clean_source == "whatsapp":
        return "WhatsApp"
    if clean_source == "web":
        return "Site"
    return "Operador"


def _preview_text(value: str, limit: int = 220) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _build_admin_bot_payload() -> dict:
    knowledge_dir = getattr(services.store, "knowledge_dir", "") or getattr(services, "KNOWLEDGE_DIR", "")
    documents = services.store.list_documents()
    manual_companion_files: list[str] = []
    companions_dir = companion_directory(knowledge_dir) if knowledge_dir else ""
    if companions_dir and os.path.isdir(companions_dir):
        manual_companion_files = sorted(
            name
            for name in os.listdir(companions_dir)
            if name.lower().endswith(".json")
        )

    resolved_companions_total = 0
    for document in documents:
        try:
            if load_document_companion(document.get("name", ""), knowledge_dir):
                resolved_companions_total += 1
        except Exception:
            logger.exception("Falha ao resolver companion para %s.", document.get("name", ""))

    static_cases = load_eval_cases_from_dir(os.path.join(knowledge_dir, "evals")) if knowledge_dir else []
    feedback_cases = load_eval_cases_from_store(services.store)
    active_cases = _dedupe_eval_cases(static_cases + feedback_cases)
    results = evaluate_companion_cases(active_cases, knowledge_dir) if active_cases else []
    passed_cases = sum(1 for item in results if item.get("passed"))
    failed_cases = [item for item in results if not item.get("passed")]
    pass_rate_pct = round((passed_cases / len(results)) * 100) if results else 0

    source_counter = Counter(_feedback_source_label(item.get("source", "")) for item in feedback_cases)
    source_rows = [
        {"label": label, "count": count}
        for label, count in sorted(source_counter.items(), key=lambda item: (-item[1], item[0]))
    ]

    latest_feedback_updated_at = ""
    if feedback_cases:
        latest_feedback_updated_at = max(
            (str(item.get("updated_at") or "").strip() for item in feedback_cases),
            default="",
        )

    document_summary: dict[str, dict] = {}
    for case in active_cases:
        name = str(case.get("document") or "").strip()
        if not name:
            continue
        bucket = document_summary.setdefault(
            name,
            {
                "document": name,
                "total_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
                "feedback_cases": 0,
            },
        )
        bucket["total_cases"] += 1
    for result in results:
        name = str(result.get("document") or "").strip()
        if not name:
            continue
        bucket = document_summary.setdefault(
            name,
            {
                "document": name,
                "total_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
                "feedback_cases": 0,
            },
        )
        if result.get("passed"):
            bucket["passed_cases"] += 1
        else:
            bucket["failed_cases"] += 1
    for case in feedback_cases:
        name = str(case.get("document") or "").strip()
        if not name:
            continue
        bucket = document_summary.setdefault(
            name,
            {
                "document": name,
                "total_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
                "feedback_cases": 0,
            },
        )
        bucket["feedback_cases"] += 1

    document_rows = []
    for item in document_summary.values():
        total_cases = item["total_cases"]
        passed = item["passed_cases"]
        failed = item["failed_cases"]
        coverage_pct = round((passed / total_cases) * 100) if total_cases else 0
        state = "online" if failed == 0 and total_cases else "degraded" if total_cases else "offline"
        document_rows.append(
            {
                **item,
                "coverage_pct": coverage_pct,
                "state": state,
            }
        )
    document_rows.sort(
        key=lambda item: (
            -item["failed_cases"],
            -item["feedback_cases"],
            item["document"],
        )
    )

    failure_rows = []
    for item in failed_cases[:12]:
        missing_bits = list(item.get("missing_substrings") or [])
        if not missing_bits:
            missing_bits = list(item.get("missing_terms") or [])[:4]
        failure_rows.append(
            {
                "document": item.get("document", ""),
                "question": item.get("question", ""),
                "missing_summary": ", ".join(missing_bits) or "Resposta vazia ou desalinhada com o esperado.",
                "answer_preview": _preview_text(item.get("answer", ""), limit=260) or "Sem resposta gerada.",
            }
        )

    recent_feedback_cases = []
    for item in sorted(
        feedback_cases,
        key=lambda record: str(record.get("updated_at") or ""),
        reverse=True,
    )[:12]:
        recent_feedback_cases.append(
            {
                **item,
                "source_label": _feedback_source_label(item.get("source", "")),
                "updated_at_label": _local_iso_to_label(item.get("updated_at")),
                "expected_answer_preview": _preview_text(item.get("expected_answer", ""), limit=260),
                "feedback_note_preview": _preview_text(item.get("feedback_note", ""), limit=160),
            }
        )

    if not results:
        state = "offline"
        state_label = "Sem régua"
        summary = "Ainda não existem casos de avaliação carregados para medir o comportamento do bot."
    elif failed_cases:
        state = "degraded"
        state_label = "Com desvios"
        summary = f"Existem {len(failed_cases)} caso(s) a rever no conjunto atual de evals."
    else:
        state = "online"
        state_label = "Conforme"
        summary = "Todos os casos ativos passam com os companions e correções atualmente carregados."

    return {
        "state": state,
        "state_label": state_label,
        "summary": summary,
        "knowledge_documents_total": len(documents),
        "manual_companions_total": len(manual_companion_files),
        "resolved_companions_total": resolved_companions_total,
        "static_cases_total": len(static_cases),
        "feedback_cases_total": len(feedback_cases),
        "active_cases_total": len(active_cases),
        "passed_cases_total": passed_cases,
        "failed_cases_total": len(failed_cases),
        "pass_rate_pct": pass_rate_pct,
        "documents_covered_total": len(document_rows),
        "latest_feedback_updated_at_label": (
            _local_iso_to_label(latest_feedback_updated_at) if latest_feedback_updated_at else "Nunca"
        ),
        "source_rows": source_rows,
        "recent_feedback_cases": recent_feedback_cases,
        "failure_rows": failure_rows,
        "document_rows": document_rows[:16],
    }


def _safe_return_to(value: str | None) -> str:
    target = (value or "").strip()
    if not target:
        return ""
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return ""
    rebuilt = parsed.path
    if parsed.query:
        rebuilt = f"{rebuilt}?{parsed.query}"
    return rebuilt


def _documents_return_to() -> str:
    if request.query_string:
        return f"{request.path}?{request.query_string.decode('utf-8', errors='ignore')}"
    return request.path


def _build_document_filters(docs: list[dict]) -> dict:
    q = " ".join((request.args.get("q") or "").strip().split())
    q_search = q.lower()
    doc_types = sorted(
        {
            (item.get("doc_type") or "").strip()
            for item in docs
            if (item.get("doc_type") or "").strip()
        }
    )
    uploaded_bys = sorted(
        {
            (item.get("uploaded_by") or "").strip()
            for item in docs
            if (item.get("uploaded_by") or "").strip()
        }
    )
    doc_type = (request.args.get("doc_type") or "").strip()
    if doc_type not in doc_types:
        doc_type = ""
    uploaded_by = (request.args.get("uploaded_by") or "").strip()
    if uploaded_by not in uploaded_bys:
        uploaded_by = ""
    editable = (request.args.get("editable") or "").strip().lower()
    if editable not in {"", "editable", "read_only"}:
        editable = ""
    return {
        "q": q,
        "q_search": q_search,
        "doc_type": doc_type,
        "uploaded_by": uploaded_by,
        "editable": editable,
        "doc_types": doc_types,
        "uploaded_bys": uploaded_bys,
        "has_active_filters": bool(q or doc_type or uploaded_by or editable),
    }


def _document_matches_filters(document: dict, filters: dict) -> bool:
    if filters.get("doc_type") and (document.get("doc_type") or "") != filters["doc_type"]:
        return False
    if filters.get("uploaded_by") and (document.get("uploaded_by") or "") != filters["uploaded_by"]:
        return False
    if filters.get("editable") == "editable" and not document.get("editable"):
        return False
    if filters.get("editable") == "read_only" and document.get("editable"):
        return False
    if filters.get("q_search"):
        haystack = " ".join(
            str(document.get(field) or "")
            for field in ("name", "original_name", "doc_type", "uploaded_by", "preview")
        ).lower()
        if filters["q_search"] not in haystack:
            return False
    return True


def _filter_documents(docs: list[dict], filters: dict) -> list[dict]:
    return [item for item in docs if _document_matches_filters(item, filters)]


@bp.route("/admin/status")
@login_required
@role_required("admin")
def admin_status():
    """Painel de estado do sistema para administradores."""
    refresh_knowledge_state(force_reindex=False)
    return render_template("admin_status.html", admin=load_admin_status())


@bp.route("/admin/bot")
@login_required
@role_required("admin")
def admin_bot():
    """Painel de acompanhamento do bot, evals e correções supervisionadas."""
    refresh_knowledge_state(force_reindex=False)
    return render_template("admin_bot.html", bot=_build_admin_bot_payload(), title="Bot e evals")


@bp.route("/admin/users")
@login_required
@role_required("admin")
def admin_users():
    """Página de gestão de utilizadores do sistema."""
    return render_template("admin_users.html", users=_admin_users_payload(), title="Utilizadores")


@bp.route("/admin/users/<username>", methods=["POST"])
@login_required
@role_required("admin")
def admin_update_user(username: str):
    """Atualizar o role e os dados de perfil de um utilizador."""
    target_username = username.strip().lower()
    try:
        existing_user = services.store.get_user_profile(target_username)
        if not existing_user:
            raise ValueError("Utilizador não encontrado.")
        login_email = validate_email(request.form.get("login_email", ""))
        updated_role = validate_role(request.form.get("role", ""))
        full_name = validate_required_text(request.form.get("full_name", ""), "Nome completo")
        organization = validate_required_text(request.form.get("organization", ""), "Agência/entidade")
        phone = validate_phone(request.form.get("phone", ""))
        whatsapp_number = validate_whatsapp_phone(request.form.get("whatsapp_number", ""), required=False)
        whatsapp_opt_in = request.form.get("whatsapp_opt_in", "") == "1"
        new_password_raw = request.form.get("new_password", "")
        new_password = validate_password(new_password_raw) if new_password_raw.strip() else ""
        if whatsapp_opt_in and not whatsapp_number:
            raise ValueError("Se ativares WhatsApp, tens de indicar o respetivo número.")

        effective_target_username = login_email

        if updated_role == "admin" and effective_target_username != session.get("username"):
            existing_admins = [
                u for u in services.store.list_users()
                if (u.get("role") or "").strip().lower() == "admin" and u.get("username") != target_username
            ]
            if existing_admins:
                flash("Já existe um administrador no sistema. Só pode haver 1 admin.", "error")
                return redirect(url_for("admin.admin_users"))

        if login_email != target_username:
            existing_user = services.store.rename_user(target_username, login_email)
            effective_target_username = existing_user["username"]

        services.store.update_user_profile(
            effective_target_username,
            full_name=full_name,
            organization=organization,
            email=effective_target_username,
            phone=phone,
            whatsapp_number=whatsapp_number,
            whatsapp_opt_in=whatsapp_opt_in,
            whatsapp_opt_in_at=_resolved_whatsapp_opt_in_at(existing_user, whatsapp_number, whatsapp_opt_in),
        )
        updated_user = services.store.set_user_role(effective_target_username, updated_role)
        if new_password:
            services.store.reset_user_password(effective_target_username, new_password)
        if session.get("username") == target_username:
            session["username"] = effective_target_username
            session["role"] = updated_user["role"]
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_users"))
    except Exception:
        logger.exception("Falha inesperada ao atualizar utilizador %s.", target_username)
        flash("Falha inesperada ao atualizar o utilizador.", "error")
        return redirect(url_for("admin.admin_users"))

    flash(f"Utilizador {effective_target_username} atualizado.", "success")
    return redirect(url_for("admin.admin_users"))


@bp.route("/admin/users/<username>/whatsapp-check", methods=["POST"])
@login_required
@role_required("admin")
def admin_check_user_whatsapp(username: str):
    target_username = username.strip().lower()
    profile = services.store.get_user_profile(target_username)
    if not profile:
        return jsonify({"ok": False, "summary": "Utilizador não encontrado."}), 404

    service = getattr(services, "whatsapp_service", None)
    result = verify_user_whatsapp(profile, service, services.store, source="admin_verify")
    refreshed_user = build_user_whatsapp_view(
        services.store.get_user_profile(target_username) or profile,
        service,
        services.store,
    )
    http_status = 200 if result.get("ok") else 400
    return jsonify({"ok": bool(result.get("ok")), "result": result, "user": refreshed_user}), http_status


@bp.route("/admin/users/<username>/delete", methods=["POST"])
@login_required
@role_required("admin")
def admin_delete_user(username: str):
    """Apagar a conta de um utilizador do sistema."""
    target_username = username.strip().lower()
    if session.get("username") == target_username:
        flash("Não podes apagar a tua própria conta enquanto estás autenticado.", "error")
        return redirect(url_for("admin.admin_users"))
    try:
        services.store.delete_user(target_username)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_users"))
    except Exception:
        logger.exception("Falha inesperada ao apagar utilizador %s.", target_username)
        flash("Falha inesperada ao apagar o utilizador.", "error")
        return redirect(url_for("admin.admin_users"))

    flash(f"Utilizador {target_username} apagado.", "success")
    return redirect(url_for("admin.admin_users"))


@bp.route("/admin/migrate-local-data", methods=["POST"])
@login_required
@role_required("admin")
def admin_migrate_local_data():
    """Migrar dados do armazenamento local JSON para o backend PostgreSQL."""
    if getattr(services.store, "backend_name", "") != "postgres":
        flash("A migração local -> Postgres só faz sentido com APP_STORAGE_BACKEND=postgres.", "error")
        return redirect(url_for("admin.admin_status"))

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        flash("DATABASE_URL em falta.", "error")
        return redirect(url_for("admin.admin_status"))

    force = request.form.get("force", "0") == "1"
    try:
        result = migrate_local_json_to_postgres(
            data_dir=services.DATA_DIR, knowledge_dir=services.KNOWLEDGE_DIR,
            database_url=database_url, force=force,
        )
        services.startup_migration_status = result
        refresh_knowledge_state(force_reindex=True)
        flash(f"Migração concluída com estado: {result['status']}.", "success")
    except Exception as exc:
        flash(f"Falha na migração: {exc}", "error")
    return redirect(url_for("admin.admin_status"))


@bp.route("/admin/documents")
@login_required
@role_required("admin")
def admin_documents():
    """Página de gestão de documentos da base de conhecimento."""
    refresh_knowledge_state(force_reindex=False)
    docs = services.store.list_documents()
    document_filters = _build_document_filters(docs)
    filtered_docs = _filter_documents(docs, document_filters)
    try:
        rag_stats = services.rag.index_summary()
    except Exception as exc:
        rag_stats = {
            "document_count": 0, "chunk_count": 0, "embedded_chunks": 0,
            "index_backend": getattr(services.index_store, "backend_name", "unknown"),
            "index_error": str(exc),
        }
    reindex_status = current_reindex_status_payload()
    return render_template(
        "admin_documents.html",
        docs=filtered_docs,
        docs_total=len(docs),
        document_filters=document_filters,
        documents_return_to=_documents_return_to(),
        rag_stats=rag_stats,
        reindex_status=reindex_status,
        title="Gestão de Documentos",
    )


@bp.route("/documents", methods=["POST"])
@login_required
@role_required("admin")
def add_document():
    """Guardar um novo documento de texto na base de conhecimento e reindexar."""
    if not _manual_knowledge_authoring_enabled():
        flash(
            "Criação manual de documentos desativada. Usa upload de ficheiros oficiais ou a pasta knowledge/.",
            "error",
        )
        return redirect(url_for("admin.admin_documents"))
    try:
        title = validate_required_text(request.form.get("title", ""), "Título", max_length=200)
        content = validate_required_text(request.form.get("content", ""), "Conteúdo", max_length=50000)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_documents"))
    filename = services.store.save_document(title, content, created_by=session["username"])
    if safe_rebuild_index(force=False):
        flash(f"Documento {filename} indexado.", "success")
    else:
        flash(f"Documento {filename} guardado, mas a reindexação falhou: {services.rag.last_index_error}", "error")
    return redirect(url_for("admin.admin_documents"))


@bp.route("/documents/upload", methods=["POST"])
@login_required
@role_required("admin")
def upload_documents():
    """Fazer upload de um ou mais ficheiros para a base de conhecimento e reindexar."""
    uploaded_files = [item for item in request.files.getlist("files") if item and item.filename]
    if not uploaded_files:
        flash("Seleciona pelo menos um ficheiro.", "error")
        return redirect(url_for("admin.admin_documents"))
    stored = []
    failed = []
    for uploaded_file in uploaded_files:
        try:
            filename = services.store.save_uploaded_document(uploaded_file, created_by=session["username"])
            stored.append(filename)
        except Exception as exc:
            failed.append(f"{uploaded_file.filename}: {exc}")
    if stored:
        if safe_rebuild_index(force=False):
            flash(f"Foram indexados {len(stored)} ficheiro(s): {', '.join(stored)}.", "success")
        else:
            flash("Os ficheiros foram guardados, mas a reindexação falhou: " + services.rag.last_index_error, "error")
    if failed:
        flash("Falhas no upload: " + " | ".join(failed), "error")
    return redirect(url_for("admin.admin_documents"))


@bp.route("/knowledge/reindex", methods=["POST"])
@login_required
@role_required("admin")
def reindex_knowledge():
    """Iniciar uma reindexação incremental da base de conhecimento."""
    started = start_reindex_job(force=False)
    status_payload = current_reindex_status_payload()
    wants_json = (
        request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "fetch"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    if wants_json:
        if started and status_payload.get("state") != "running":
            status_payload = {
                **status_payload, "state": "running", "phase": "queued",
                "message": "A iniciar reindexação...",
                "progress_pct": 1.0,
                "error": "",
            }
        return jsonify({"started": started, "status": status_payload, "message": "Reindexação incremental iniciada." if started else "Já existe uma reindexação em curso."}), 202 if started else 200
    if started:
        flash("Reindexação incremental iniciada. O progresso aparece no painel documental.", "success")
    else:
        flash("Já existe uma reindexação em curso.", "error")
    return redirect(request.referrer or url_for("dashboard_bp.dashboard"))


@bp.route("/api/knowledge/reindex-status")
@login_required
def reindex_status():
    """API que retorna o estado atual da reindexação do conhecimento."""
    return jsonify(current_reindex_status_payload())


@bp.route("/documents/<name>")
@login_required
def document_detail(name: str):
    """Página de detalhe de um documento da base de conhecimento."""
    refresh_knowledge_state(force_reindex=False)
    document = services.store.get_document(name)
    if not document:
        abort(404)
    document_return_to = _safe_return_to(request.args.get("return_to")) or url_for("admin.admin_documents")
    try:
        document_text = services.store.get_document_text(name)
    except Exception as exc:
        document_text = f"Erro ao ler conteúdo extraído: {exc}"
    return render_template(
        "document_detail.html",
        document=document,
        document_text=document_text,
        document_return_to=document_return_to,
    )


@bp.route("/documents/<name>/download")
@login_required
def download_document(name: str):
    """Descarregar o ficheiro original de um documento da base de conhecimento."""
    refresh_knowledge_state(force_reindex=False)
    try:
        file_path = services.store.get_document_file_path(name)
    except Exception:
        abort(404)
    return send_file(file_path, as_attachment=True, download_name=os.path.basename(file_path))


@bp.route("/documents/<name>/edit", methods=["POST"])
@login_required
@role_required("admin")
def edit_document(name: str):
    """Guardar o conteúdo editado de um documento de texto e reindexar."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_documents")
    if not _manual_knowledge_authoring_enabled():
        flash(
            "Edição manual de documentos desativada. Atualiza o ficheiro original e volta a indexar.",
            "error",
        )
        return redirect(url_for("admin.document_detail", name=name, return_to=return_to))
    content = request.form.get("content", "").strip()
    try:
        services.store.update_document_text(name=name, content=content, updated_by=session["username"])
        if safe_rebuild_index(force=False):
            flash(f"Documento {name} atualizado e reindexado.", "success")
        else:
            flash(f"Documento {name} atualizado, mas a reindexação falhou: {services.rag.last_index_error}", "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(
        url_for(
            "admin.document_detail",
            name=name,
            return_to=return_to,
        )
    )


@bp.route("/documents/<name>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_document(name: str):
    """Remover um documento da base de conhecimento e reindexar."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_documents")
    try:
        services.store.delete_document(name)
        if safe_rebuild_index(force=False):
            flash(f"Documento {name} removido do conhecimento.", "success")
        else:
            flash(f"Documento {name} removido, mas a reindexação falhou: {services.rag.last_index_error}", "error")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(return_to)
    return redirect(return_to)


@bp.route("/documents/bulk-delete", methods=["POST"])
@login_required
@role_required("admin")
def bulk_delete_documents():
    """Remover vários documentos filtrados/selecionados da base de conhecimento."""
    return_to = _safe_return_to(request.form.get("return_to")) or url_for("admin.admin_documents")
    selected_names = []
    for raw_name in request.form.getlist("document_names"):
        clean_name = raw_name.strip()
        if clean_name and clean_name not in selected_names:
            selected_names.append(clean_name)
    if not selected_names:
        flash("Seleciona pelo menos um ficheiro para eliminar.", "error")
        return redirect(return_to)

    removed = []
    failed = []
    for name in selected_names:
        try:
            services.store.delete_document(name)
            removed.append(name)
        except ValueError as exc:
            failed.append(f"{name}: {exc}")

    if removed:
        if safe_rebuild_index(force=False):
            flash(f"Foram removidos {len(removed)} ficheiro(s) da base documental.", "success")
        else:
            flash(
                f"Os ficheiros foram removidos, mas a reindexação falhou: {services.rag.last_index_error}",
                "error",
            )
    if failed:
        flash("Falhas na eliminação: " + " | ".join(failed), "error")
    return redirect(return_to)
