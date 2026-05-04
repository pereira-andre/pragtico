from __future__ import annotations

import os
import re
from datetime import timedelta

from flask import Flask, jsonify, render_template, request, session
from markupsafe import Markup, escape
from werkzeug.exceptions import RequestEntityTooLarge

from core.helpers import current_user_profile
from core.runtime import Runtime, env_flag
from core.security import init_csrf
from domain.berth_layout import dropdown_berth_options


def configure_app(app: Flask) -> None:
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "64")) * 1024 * 1024
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV", "production") == "production"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        minutes=int(os.getenv("SESSION_IDLE_MINUTES", "45"))
    )
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True
    app.config["MANUAL_KNOWLEDGE_AUTHORING_ENABLED"] = env_flag(
        "MANUAL_KNOWLEDGE_AUTHORING_ENABLED",
        default="0",
    )
    app.jinja_env.filters["chat_markdown"] = render_chat_markdown


def render_chat_markdown(text: str) -> Markup:
    escaped = str(escape(text or ""))
    escaped = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"(?<!\*)\*(?=\S)(.+?)(?<=\S)\*(?!\*)", r"<em>\1</em>", escaped, flags=re.DOTALL)
    return Markup(escaped)


def register_blueprints(app: Flask) -> None:
    from blueprints.admin import bp as admin_bp
    from blueprints.api import bp as api_bp
    from blueprints.auth import bp as auth_bp
    from blueprints.chat import bp as chat_bp
    from blueprints.dashboard import bp as dashboard_bp
    from blueprints.port_calls import bp as port_calls_bp
    from blueprints.whatsapp import bp as whatsapp_bp

    init_csrf(app)
    for blueprint in (auth_bp, dashboard_bp, port_calls_bp, admin_bp, chat_bp, api_bp, whatsapp_bp):
        app.register_blueprint(blueprint)


def register_request_hooks(app: Flask, ensure_external_refresh_started) -> None:
    @app.before_request
    def refresh_authenticated_session():
        ensure_external_refresh_started()
        if not session.get("username"):
            return None
        session.permanent = True
        session.modified = True
        return None

    @app.after_request
    def audit_sensitive_request(response):
        try:
            from core.audit_log import audit_request_response

            audit_request_response(response)
        except Exception:
            app.logger.exception("Falha ao escrever audit log.")
        return response


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(RequestEntityTooLarge)
    def handle_file_too_large(_exc):
        from flask import flash, redirect, url_for

        flash(
            "#ERR-8080 Ficheiro demasiado grande. "
            f"Limite atual: {int(app.config['MAX_CONTENT_LENGTH'] / (1024 * 1024))} MB.",
            "error",
        )
        return redirect(request.referrer or url_for("dashboard_bp.dashboard")), 413

    @app.errorhandler(403)
    def handle_forbidden(_exc):
        if wants_json():
            return jsonify({"error": "#ERR-2020 Pedido inválido. Recarrega a página e tenta novamente.", "error_code": 2020, "error_ref": "#ERR-2020"}), 403
        return render_template("error.html", error_ref="#ERR-2020", error_title="Acesso negado", error_message="Pedido inválido ou sem permissão. Recarrega a página e tenta novamente."), 403

    @app.errorhandler(404)
    def handle_not_found(_exc):
        if wants_json():
            return jsonify({"error": "Recurso não encontrado.", "error_code": 404}), 404
        return render_template("error.html", error_ref="404", error_title="Página não encontrada", error_message="O recurso pedido não existe ou foi removido."), 404

    @app.errorhandler(429)
    def handle_rate_limited(_exc):
        if wants_json():
            return jsonify({"error": "#ERR-2021 Demasiados pedidos. Aguarda e tenta novamente.", "error_code": 2021, "error_ref": "#ERR-2021"}), 429
        return render_template("error.html", error_ref="#ERR-2021", error_title="Demasiados pedidos", error_message="Fizeste demasiados pedidos em pouco tempo. Aguarda uns segundos e tenta novamente."), 429

    @app.errorhandler(500)
    def handle_internal_error(_exc):
        if wants_json():
            return jsonify({"error": "#ERR-9000 Erro inesperado.", "error_code": 9000, "error_ref": "#ERR-9000"}), 500
        return render_template("error.html", error_ref="#ERR-9000", error_title="Erro interno", error_message="Ocorreu um erro inesperado. Contacta o suporte com este código."), 500

    @app.after_request
    def apply_security_headers(response):
        if session.get("username") or request.endpoint in {"auth.login", "auth.profile"}:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if os.getenv("FLASK_ENV", "production") == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def wants_json() -> bool:
    return (
        request.path.startswith("/api/")
        or request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") in {"fetch", "XMLHttpRequest"}
    )


def register_template_context(app: Flask, runtime: Runtime) -> None:
    @app.context_processor
    def inject_globals():
        chatbot_conversation = None
        chatbot_messages = []
        chatbot_conversations = []
        username = session.get("username")
        if username:
            try:
                requested_conv_id = request.args.get("conversation_id", "").strip() or None
                chatbot_conversation = runtime.store.ensure_conversation(
                    username=username,
                    conversation_id=requested_conv_id,
                )
                chatbot_messages = runtime.store.list_messages(username, chatbot_conversation["id"])
                chatbot_conversations = runtime.store.list_conversations(username)
            except Exception:
                pass
        return {
            "current_user": username,
            "current_role": session.get("role"),
            "provider": runtime.rag.generation_provider_label,
            "auth_backend": getattr(runtime.auth_service, "backend_name", "unknown"),
            "storage_backend": getattr(runtime.store, "backend_name", "unknown"),
            "rag_backend": getattr(runtime.index_store, "backend_name", "unknown"),
            "berth_options": dropdown_berth_options(runtime.berth_options),
            "terminal_options": runtime.terminal_options,
            "vessel_type_options": runtime.vessel_type_options,
            "constraint_options": runtime.constraint_options,
            "current_profile": current_user_profile(),
            "chatbot_conversation": chatbot_conversation,
            "chatbot_messages": chatbot_messages,
            "chatbot_conversations": chatbot_conversations,
            "chatbot_model": runtime.rag.generation_model,
            "manual_knowledge_authoring_enabled": app.config.get("MANUAL_KNOWLEDGE_AUTHORING_ENABLED", False),
        }
