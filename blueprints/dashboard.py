"""Dashboard blueprint — home, dashboard, health, AIS, archive."""

import csv
import os
from datetime import date, datetime, timedelta
from io import StringIO

from flask import Blueprint, Response, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

import services
from helpers import (
    build_tracked_scales,
    build_weather_timeline,
    filter_port_activity_for_session,
    login_required,
    refresh_knowledge_state,
)

bp = Blueprint("dashboard_bp", __name__)


@bp.route("/")
def home():
    if session.get("username"):
        return redirect(url_for("dashboard_bp.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/img/<path:asset_path>")
def image_asset(asset_path: str):
    return send_from_directory(os.path.join(services.BASE_DIR, "img"), asset_path)


@bp.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "auth_backend": getattr(services.auth_service, "backend_name", "unknown"),
        "storage_backend": getattr(services.store, "backend_name", "unknown"),
        "rag_backend": getattr(services.index_store, "backend_name", "unknown"),
        "startup_migration": services.startup_migration_status,
    })


@bp.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(_exc):
    from flask import current_app, flash
    flash(
        "Ficheiro demasiado grande para este rascunho local. "
        f"Limite atual: {int(current_app.config['MAX_CONTENT_LENGTH'] / (1024 * 1024))} MB.",
        "error",
    )
    return redirect(request.referrer or url_for("dashboard_bp.dashboard")), 413


@bp.route("/dashboard")
@login_required
def dashboard():
    refresh_knowledge_state(force_reindex=False)
    port_activity = services.store.get_port_activity_snapshot(window_days=5)
    port_activity = filter_port_activity_for_session(port_activity)

    today = date.today()
    tomorrow = today + timedelta(days=1)
    tides_today = services.tide_service.summary_for_date(today)
    tides_tomorrow = services.tide_service.summary_for_date(tomorrow)

    weather_data = None
    weather_error = ""
    weather_timeline = []
    if services.weather_service.enabled:
        try:
            weather_data = services.weather_service.get_forecast(days=3)
            weather_timeline = build_weather_timeline(weather_data, max_hours=48)
        except Exception as exc:
            weather_error = str(exc)
    ais_context = services.ais_service.dashboard_context()
    return render_template(
        "dashboard.html",
        port_activity=port_activity,
        tides_today=tides_today,
        tides_tomorrow=tides_tomorrow,
        weather_data=weather_data,
        weather_timeline=weather_timeline,
        weather_error=weather_error,
        ais=ais_context,
        title="PRAGtico",
    )


@bp.route("/embed/vesselfinder/setubal")
@login_required
def vesselfinder_embed_setubal():
    return render_template(
        "vesselfinder_embed.html",
        embed=services.ais_service.embed_context(),
        title="VesselFinder Setubal",
    )


@bp.route("/maneuvers/archive")
@login_required
def maneuver_archive():
    refresh_knowledge_state(force_reindex=False)
    port_activity = services.store.get_port_activity_snapshot(window_days=30)
    port_activity = filter_port_activity_for_session(port_activity)
    return render_template(
        "maneuver_archive.html",
        port_activity=port_activity,
        title="Arquivo de Manobras",
    )


@bp.route("/maneuvers/archive/export.csv")
@login_required
def maneuver_archive_export():
    port_activity = services.store.get_port_activity_snapshot(window_days=3650)
    port_activity = filter_port_activity_for_session(port_activity)
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Data", "Escala", "Navio", "Tipo de navio", "Hora", "Situacao",
        "Manobra", "Origem", "Destino", "Restricoes", "Agente",
        "Validado por", "Executado por", "Observacoes",
    ])
    for item in port_activity.get("archived_maneuvers", []):
        writer.writerow([
            item.get("date_label", ""), item.get("reference_code", ""),
            item.get("vessel_name", ""), item.get("vessel_type", ""),
            item.get("execution_window_label") or item.get("actual_label") or item.get("planned_label") or "",
            item.get("situation_label", ""), item.get("maneuver_label", ""),
            item.get("local_origin", ""), item.get("local_destination", ""),
            ", ".join(badge.get("label", "") for badge in item.get("constraint_badges", []) if badge.get("label")),
            item.get("agent_label", ""), item.get("validated_by_label", ""),
            item.get("executed_by_label", ""),
            (item.get("detail_note") or "").replace("\n", " ").strip(),
        ])

    filename = f"arquivo_manobras_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
