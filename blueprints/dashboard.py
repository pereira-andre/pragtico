"""Dashboard blueprint — home, dashboard, health, AIS, archive."""

import csv
import os
from datetime import date, datetime, timedelta
from io import StringIO

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

import services
from dashboard_data import build_weather_charts
from helpers import (
    build_weather_timeline,
    filter_port_activity_for_session,
    login_required,
    refresh_knowledge_state,
)

bp = Blueprint("dashboard_bp", __name__)


@bp.route("/")
def home():
    """Redirecionar para o dashboard ou para o login consoante o estado da sessão."""
    if session.get("username"):
        return redirect(url_for("dashboard_bp.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/img/<path:asset_path>")
def image_asset(asset_path: str):
    """Servir ficheiros de imagem estáticos a partir da pasta img."""
    return send_from_directory(os.path.join(services.BASE_DIR, "img"), asset_path)


@bp.route("/healthz")
def healthz():
    """Endpoint de health check que retorna o estado dos backends de armazenamento e RAG."""
    return jsonify({
        "ok": True,
        "auth_backend": getattr(services.auth_service, "backend_name", "unknown"),
        "storage_backend": getattr(services.store, "backend_name", "unknown"),
        "rag_backend": getattr(services.index_store, "backend_name", "unknown"),
        "startup_migration": services.startup_migration_status,
    })


@bp.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(_exc):
    """Mostrar mensagem de erro amigável quando o ficheiro excede o tamanho máximo permitido."""
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
    """Painel principal com atividade portuária, marés e condições meteorológicas."""
    refresh_knowledge_state(force_reindex=False)
    port_activity = services.store.get_port_activity_snapshot(window_days=5)
    port_activity = filter_port_activity_for_session(port_activity)

    today = date.today()
    tide_window = services.tide_service.window_summary(today - timedelta(days=1), days=3)

    weather_data = None
    weather_error = ""
    weather_timeline = []
    weather_charts = {}
    if services.weather_service.enabled:
        try:
            weather_data = services.weather_service.get_forecast(days=3)
            weather_timeline = build_weather_timeline(weather_data, max_hours=48)
            weather_charts = build_weather_charts(weather_timeline)
        except Exception as exc:
            weather_error = str(exc)

    wave_conditions = None
    wave_error = ""
    if getattr(services, "wave_service", None) and services.wave_service.enabled:
        try:
            wave_conditions = services.wave_service.get_current_conditions()
        except Exception as exc:
            wave_error = str(exc)

    local_warnings = []
    local_warnings_error = ""
    if getattr(services, "local_warning_service", None) and services.local_warning_service.enabled:
        try:
            local_warnings = services.local_warning_service.list_warnings()
        except Exception as exc:
            local_warnings_error = str(exc)

    ais_context = services.ais_service.dashboard_context()
    return render_template(
        "dashboard.html",
        port_activity=port_activity,
        tide_window=tide_window,
        weather_data=weather_data,
        weather_timeline=weather_timeline,
        weather_charts=weather_charts,
        weather_error=weather_error,
        wave_conditions=wave_conditions,
        wave_error=wave_error,
        local_warnings=local_warnings,
        local_warnings_error=local_warnings_error,
        ais=ais_context,
        title="PRAGtico",
    )


@bp.route("/embed/vesselfinder/setubal")
@login_required
def vesselfinder_embed_setubal():
    """Página com o mapa AIS embebido do VesselFinder para o Porto de Setúbal."""
    return render_template(
        "vesselfinder_embed.html",
        embed=services.ais_service.embed_context(),
        title="VesselFinder Setubal",
    )


@bp.route("/maneuvers/archive")
@login_required
def maneuver_archive():
    """Página de arquivo histórico de manobras concluídas."""
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
    """Exportar o arquivo de manobras para um ficheiro CSV."""
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


@bp.route("/warnings/local")
@login_required
def local_warnings():
    warnings = []
    warnings_error = ""
    if getattr(services, "local_warning_service", None) and services.local_warning_service.enabled:
        try:
            warnings = services.local_warning_service.list_warnings()
        except Exception as exc:
            warnings_error = str(exc)
    return render_template(
        "local_warnings.html",
        warnings=warnings,
        warnings_error=warnings_error,
        title="Avisos Locais",
    )


@bp.route("/warnings/local/<int:warning_id>")
@login_required
def local_warning_detail(warning_id: int):
    if not getattr(services, "local_warning_service", None) or not services.local_warning_service.enabled:
        abort(404)
    try:
        warning = services.local_warning_service.get_warning(warning_id)
    except Exception as exc:
        abort(502, description=str(exc))
    if not warning:
        abort(404)
    return render_template(
        "local_warning_detail.html",
        warning=warning,
        title=warning.get("display_code") or "Aviso Local",
    )
