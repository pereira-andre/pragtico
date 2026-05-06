"""Authentication blueprint — login, register, profile, logout."""

import re

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from domain.error_catalog import flash_error_message

from core.audit_log import write_audit_event
from core import services
from core.whatsapp_support import verify_user_whatsapp
from core.helpers import (
    current_user_profile,
    login_required,
    session_profile_incomplete,
)
from storage import is_user_profile_complete
from core.security import login_limiter, rate_limit
from core.validators import validate_email, validate_password, validate_phone, validate_required_text, validate_role, validate_whatsapp_phone

bp = Blueprint("auth", __name__)


def _registration_form_data() -> dict:
    return {
        "role": request.form.get("role", "piloto"),
        "full_name": request.form.get("full_name", "").strip(),
        "organization": request.form.get("organization", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "whatsapp_number": request.form.get("whatsapp_number", "").strip(),
        "whatsapp_opt_in": request.form.get("whatsapp_opt_in", "") == "1",
    }


def _resolved_whatsapp_opt_in_at(existing_profile: dict | None, whatsapp_number: str, whatsapp_opt_in: bool) -> str:
    if not whatsapp_opt_in or not whatsapp_number:
        return ""
    existing = existing_profile or {}
    existing_number = re.sub(r"\D+", "", str(existing.get("whatsapp_number") or ""))
    if existing.get("whatsapp_opt_in") and existing_number == whatsapp_number:
        return str(existing.get("whatsapp_opt_in_at") or "").strip()
    return ""


@bp.route("/login", methods=["GET", "POST"])
@rate_limit(login_limiter)
def login():
    """Página de autenticação."""
    if session.get("username"):
        return redirect(url_for("dashboard_bp.dashboard"))

    if request.method == "POST":
        try:
            username = validate_email(request.form.get("email", ""))
            password = validate_password(request.form.get("password", ""))
        except ValueError as exc:
            flash(flash_error_message(str(exc)), "error")
            return render_template("login.html")
        try:
            user = services.auth_service.authenticate(username, password)
        except ValueError as exc:
            flash(flash_error_message(str(exc)), "error")
            return render_template("login.html")
        if not user:
            write_audit_event(
                "auth.login",
                category="seguranca",
                actor=username,
                severity="warning",
                result="failed",
                resource="session",
            )
            flash("Credenciais invalidas.", "error")
            return render_template("login.html")

        session["username"] = user["username"]
        session["role"] = user["role"]
        session.permanent = True
        write_audit_event(
            "auth.login",
            category="seguranca",
            actor=user["username"],
            actor_role=user["role"],
            severity="info",
            result="success",
            resource="session",
        )
        flash(f"Entraste como {user['role']}.", "success")
        if session_profile_incomplete():
            flash("Completa o teu perfil operacional antes de continuar.", "error")
            return redirect(url_for("auth.profile"))
        return redirect(url_for("dashboard_bp.dashboard"))

    return render_template("login.html")


@bp.route("/register", methods=["GET", "POST"])
@rate_limit(login_limiter)
def register():
    """Página de criação de conta."""
    if request.method == "POST":
        try:
            username = validate_email(request.form.get("email", ""))
            password = validate_password(request.form.get("password", ""))
            role = validate_role(request.form.get("role", "piloto"))
            whatsapp_number = validate_whatsapp_phone(request.form.get("whatsapp_number", ""), required=False)
            whatsapp_opt_in = request.form.get("whatsapp_opt_in", "") == "1"
            if whatsapp_opt_in and not whatsapp_number:
                raise ValueError("Se ativares WhatsApp, tens de indicar o respetivo número.")
        except ValueError as exc:
            flash(flash_error_message(str(exc)), "error")
            return render_template("register.html", form_data=_registration_form_data())
        profile_data = {
            "full_name": request.form.get("full_name", "").strip(),
            "organization": request.form.get("organization", "").strip(),
            "email": username,
            "phone": request.form.get("phone", "").strip(),
            "whatsapp_number": whatsapp_number,
            "whatsapp_opt_in": whatsapp_opt_in,
        }

        try:
            created_user = services.auth_service.register(
                username=username, password=password, role=role, profile_data=profile_data,
            )
        except ValueError as exc:
            flash(flash_error_message(str(exc)), "error")
            return render_template("register.html", form_data=_registration_form_data())
        write_audit_event(
            "auth.register",
            category="utilizadores",
            actor=username,
            actor_role=role,
            severity="warning",
            result="success",
            resource="app_user",
            resource_id=username,
            details={"role": role, "whatsapp_opt_in": whatsapp_opt_in},
        )

        if created_user.get("whatsapp_opt_in") and created_user.get("whatsapp_number"):
            whatsapp_result = verify_user_whatsapp(
                created_user,
                getattr(services, "whatsapp_service", None),
                services.store,
                source="register_auto_welcome",
            )
            if whatsapp_result.get("ok"):
                flash("Welcome WhatsApp enviada automaticamente para o número indicado.", "success")
            else:
                flash(
                    "Conta criada, mas a welcome WhatsApp falhou: "
                    + str(whatsapp_result.get("summary") or "erro desconhecido."),
                    "error",
                )

        flash("Conta criada. Ja podes iniciar sessao.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Página de edição do perfil operacional do utilizador."""
    existing_profile = current_user_profile() or {"username": session["username"], "role": session.get("role", "piloto")}
    profile_role = (existing_profile.get("role") or session.get("role") or "").strip().lower()
    phone_required = profile_role in {"agente", "piloto"}
    if request.method == "POST":
        try:
            full_name = validate_required_text(request.form.get("full_name", ""), "Nome completo")
            organization = validate_required_text(request.form.get("organization", ""), "Agência/entidade")
            phone = validate_phone(request.form.get("phone", ""), required=phone_required)
            whatsapp_number = validate_whatsapp_phone(request.form.get("whatsapp_number", ""), required=False)
            whatsapp_opt_in = request.form.get("whatsapp_opt_in", "") == "1"
            if whatsapp_opt_in and not whatsapp_number:
                raise ValueError("Se ativares WhatsApp, tens de indicar o respetivo número.")
            updated_profile = services.store.update_user_profile(
                session["username"],
                full_name=full_name,
                organization=organization,
                email=session["username"],
                phone=phone,
                whatsapp_number=whatsapp_number,
                whatsapp_opt_in=whatsapp_opt_in,
                whatsapp_opt_in_at=_resolved_whatsapp_opt_in_at(existing_profile, whatsapp_number, whatsapp_opt_in),
            )
            if not is_user_profile_complete(updated_profile):
                raise ValueError("Nome, agência/entidade, email e telefone são obrigatórios.")
        except ValueError as exc:
            flash(flash_error_message(str(exc)), "error")
            return render_template("profile.html", profile={**existing_profile, **request.form}, title="Perfil operacional")
        flash("Perfil operacional atualizado.", "success")
        next_target = request.form.get("next", "").strip()
        if next_target and next_target.startswith("/"):
            return redirect(next_target)
        return redirect(url_for("dashboard_bp.dashboard"))

    return render_template("profile.html", profile=existing_profile, title="Perfil operacional")


@bp.route("/logout")
def logout():
    """Terminar sessão e redirecionar para a página de login."""
    username = session.get("username", "")
    role = session.get("role", "")
    if username:
        write_audit_event(
            "auth.logout",
            category="seguranca",
            actor=username,
            actor_role=role,
            severity="info",
            result="success",
            resource="session",
        )
    session.clear()
    flash("Sessao terminada.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/logout-beacon", methods=["POST"])
def logout_beacon():
    """Endpoint de beacon para terminar sessão silenciosamente ao fechar a janela."""
    session.clear()
    return ("", 204)
