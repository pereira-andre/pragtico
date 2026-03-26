"""Authentication blueprint — login, register, profile, logout."""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import services
from helpers import (
    current_user_profile,
    login_required,
    session_profile_incomplete,
)
from storage import is_user_profile_complete
from validators import validate_email, validate_password, validate_phone, validate_required_text, validate_role

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("username"):
        return redirect(url_for("dashboard_bp.dashboard"))

    if request.method == "POST":
        try:
            username = validate_email(request.form.get("email", ""))
            password = validate_password(request.form.get("password", ""))
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("login.html")
        try:
            user = services.auth_service.authenticate(username, password)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("login.html")
        if not user:
            flash("Credenciais invalidas.", "error")
            return render_template("login.html")

        session["username"] = user["username"]
        session["role"] = user["role"]
        flash(f"Entraste como {user['role']}.", "success")
        if session_profile_incomplete():
            flash("Completa o teu perfil operacional antes de continuar.", "error")
            return redirect(url_for("auth.profile"))
        return redirect(url_for("dashboard_bp.dashboard"))

    return render_template("login.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        try:
            username = validate_email(request.form.get("email", ""))
            password = validate_password(request.form.get("password", ""))
            role = validate_role(request.form.get("role", "piloto"))
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("register.html", form_data={
                "role": request.form.get("role", "piloto"),
                "full_name": request.form.get("full_name", "").strip(),
                "organization": request.form.get("organization", "").strip(),
                "phone": request.form.get("phone", "").strip(),
            })
        profile_data = {
            "full_name": request.form.get("full_name", "").strip(),
            "organization": request.form.get("organization", "").strip(),
            "email": username,
            "phone": request.form.get("phone", "").strip(),
        }

        try:
            services.auth_service.register(
                username=username, password=password, role=role, profile_data=profile_data,
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("register.html", form_data={"role": role, "full_name": profile_data["full_name"], "organization": profile_data["organization"], "phone": profile_data["phone"]})

        flash("Conta criada. Ja podes iniciar sessao.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    existing_profile = current_user_profile() or {"username": session["username"], "role": session.get("role", "piloto")}
    if request.method == "POST":
        try:
            full_name = validate_required_text(request.form.get("full_name", ""), "Nome completo")
            organization = validate_required_text(request.form.get("organization", ""), "Agência/entidade")
            phone = validate_phone(request.form.get("phone", ""))
            updated_profile = services.store.update_user_profile(
                session["username"],
                full_name=full_name,
                organization=organization,
                email=session["username"],
                phone=phone,
            )
            if not is_user_profile_complete(updated_profile):
                raise ValueError("Nome, agência/entidade, email e telefone são obrigatórios.")
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("profile.html", profile={**existing_profile, **request.form}, title="Perfil operacional")
        flash("Perfil operacional atualizado.", "success")
        next_target = request.form.get("next", "").strip()
        if next_target and next_target.startswith("/"):
            return redirect(next_target)
        return redirect(url_for("dashboard_bp.dashboard"))

    return render_template("profile.html", profile=existing_profile, title="Perfil operacional")


@bp.route("/logout")
def logout():
    session.clear()
    flash("Sessao terminada.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/logout-beacon", methods=["POST"])
def logout_beacon():
    session.clear()
    return ("", 204)
