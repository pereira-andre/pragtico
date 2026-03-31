"""Admin blueprint — users, documents, status, migration, reindex."""

import logging
import os

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from core import services
from core.validators import validate_phone, validate_required_text, validate_role
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

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)


@bp.route("/admin/status")
@login_required
@role_required("admin")
def admin_status():
    """Painel de estado do sistema para administradores."""
    refresh_knowledge_state(force_reindex=False)
    return render_template("admin_status.html", admin=load_admin_status())


@bp.route("/admin/users")
@login_required
@role_required("admin")
def admin_users():
    """Página de gestão de utilizadores do sistema."""
    return render_template("admin_users.html", users=services.store.list_users(), title="Utilizadores")


@bp.route("/admin/users/<username>", methods=["POST"])
@login_required
@role_required("admin")
def admin_update_user(username: str):
    """Atualizar o role e os dados de perfil de um utilizador."""
    target_username = username.strip().lower()
    try:
        updated_role = validate_role(request.form.get("role", ""))
        full_name = validate_required_text(request.form.get("full_name", ""), "Nome completo")
        organization = validate_required_text(request.form.get("organization", ""), "Agência/entidade")
        phone = validate_phone(request.form.get("phone", ""))

        if updated_role == "admin" and target_username != session.get("username"):
            existing_admins = [
                u for u in services.store.list_users()
                if (u.get("role") or "").strip().lower() == "admin" and u.get("username") != target_username
            ]
            if existing_admins:
                flash("Já existe um administrador no sistema. Só pode haver 1 admin.", "error")
                return redirect(url_for("admin.admin_users"))

        services.store.update_user_profile(target_username, full_name=full_name, organization=organization, email=target_username, phone=phone)
        updated_user = services.store.set_user_role(target_username, updated_role)
        if session.get("username") == target_username:
            session["role"] = updated_user["role"]
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_users"))
    except Exception:
        logger.exception("Falha inesperada ao atualizar utilizador %s.", target_username)
        flash("Falha inesperada ao atualizar o utilizador.", "error")
        return redirect(url_for("admin.admin_users"))

    flash(f"Utilizador {target_username} atualizado.", "success")
    return redirect(url_for("admin.admin_users"))


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
    try:
        rag_stats = services.rag.index_summary()
    except Exception as exc:
        rag_stats = {
            "document_count": 0, "chunk_count": 0, "embedded_chunks": 0,
            "index_backend": getattr(services.index_store, "backend_name", "unknown"),
            "index_error": str(exc),
        }
    reindex_status = current_reindex_status_payload()
    return render_template("admin_documents.html", docs=docs, rag_stats=rag_stats, reindex_status=reindex_status, title="Gestão de Documentos")


@bp.route("/documents", methods=["POST"])
@login_required
@role_required("admin")
def add_document():
    """Guardar um novo documento de texto na base de conhecimento e reindexar."""
    try:
        title = validate_required_text(request.form.get("title", ""), "Título", max_length=200)
        content = validate_required_text(request.form.get("content", ""), "Conteúdo", max_length=50000)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard_bp.dashboard"))
    filename = services.store.save_document(title, content, created_by=session["username"])
    if safe_rebuild_index(force=False):
        flash(f"Documento {filename} indexado.", "success")
    else:
        flash(f"Documento {filename} guardado, mas a reindexação falhou: {services.rag.last_index_error}", "error")
    return redirect(url_for("dashboard_bp.dashboard"))


@bp.route("/documents/upload", methods=["POST"])
@login_required
@role_required("admin")
def upload_documents():
    """Fazer upload de um ou mais ficheiros para a base de conhecimento e reindexar."""
    uploaded_files = [item for item in request.files.getlist("files") if item and item.filename]
    if not uploaded_files:
        flash("Seleciona pelo menos um ficheiro.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))
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
    return redirect(url_for("dashboard_bp.dashboard"))


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
    try:
        document_text = services.store.get_document_text(name)
    except Exception as exc:
        document_text = f"Erro ao ler conteúdo extraído: {exc}"
    return render_template("document_detail.html", document=document, document_text=document_text)


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
    content = request.form.get("content", "").strip()
    try:
        services.store.update_document_text(name=name, content=content, updated_by=session["username"])
        if safe_rebuild_index(force=False):
            flash(f"Documento {name} atualizado e reindexado.", "success")
        else:
            flash(f"Documento {name} atualizado, mas a reindexação falhou: {services.rag.last_index_error}", "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.document_detail", name=name))


@bp.route("/documents/<name>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_document(name: str):
    """Remover um documento da base de conhecimento e reindexar."""
    try:
        services.store.delete_document(name)
        if safe_rebuild_index(force=False):
            flash(f"Documento {name} removido do conhecimento.", "success")
        else:
            flash(f"Documento {name} removido, mas a reindexação falhou: {services.rag.last_index_error}", "error")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.document_detail", name=name))
    return redirect(url_for("dashboard_bp.dashboard"))
