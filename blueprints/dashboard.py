"""Dashboard blueprint — home, dashboard, health, AIS, archive."""

import csv
import os
from datetime import date, datetime, timedelta
from io import StringIO

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from core import services
from domain.cost_engine import (
    ManoeuvreInput,
    ManoeuvreType,
    ReductionType,
    SurchargeType,
    calculate_scale_cost,
)
from domain.dashboard_data import build_weather_charts
from core.helpers import (
    build_weather_timeline,
    filter_port_activity_for_session,
    login_required,
    refresh_knowledge_state,
)
from storage.constants import PT_MONTH_NAMES

bp = Blueprint("dashboard_bp", __name__)


ARCHIVE_MANEUVER_TYPES = {"entry", "departure", "shift"}
ARCHIVE_REPORT_SELECTIONS = {"scales", "maneuvers"}


def _parse_archive_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt


def _archive_month_name(month: int) -> str:
    if 1 <= month < len(PT_MONTH_NAMES):
        return PT_MONTH_NAMES[month]
    return "--"


def _format_currency_pt(value: float | None) -> str:
    if value is None:
        return "--"
    formatted = f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{formatted} €"


def _split_csv_values(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _coerce_archive_int(value: str | None, *, default: int = 0, min_value: int = 0, max_value: int | None = None) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    parsed = max(parsed, min_value)
    if max_value is not None:
        parsed = min(parsed, max_value)
    return parsed


def _coerce_non_negative_int(value, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def _coerce_non_negative_float(value, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0.0)


def _available_archive_years(scales: list[dict]) -> list[int]:
    years = {
        dt.year
        for dt in (_parse_archive_datetime(item.get("latest_activity_value")) for item in scales)
        if dt
    }
    return sorted(years, reverse=True)


def _latest_archive_datetime(scales: list[dict]) -> datetime | None:
    candidates = [
        dt
        for dt in (_parse_archive_datetime(item.get("latest_activity_value")) for item in scales)
        if dt
    ]
    if not candidates:
        return None
    return max(candidates)


def _build_archive_filters(scales: list[dict]) -> dict:
    years = _available_archive_years(scales)
    latest_dt = _latest_archive_datetime(scales)

    raw_year = (request.args.get("year") or "").strip().lower()
    raw_month = (request.args.get("month") or "").strip().lower()
    default_year = latest_dt.year if latest_dt else 0
    default_month = latest_dt.month if latest_dt else 0

    if raw_year in {"all", "0"}:
        selected_year = 0
    elif raw_year:
        selected_year = _coerce_archive_int(raw_year, default=default_year, min_value=0)
        if years and selected_year not in years:
            selected_year = default_year
    else:
        selected_year = default_year

    if raw_month in {"all", "0"}:
        selected_month = 0
    elif raw_month:
        selected_month = _coerce_archive_int(raw_month, default=default_month, min_value=0, max_value=12)
    else:
        selected_month = default_month if selected_year else 0

    maneuver_type = (request.args.get("maneuver_type") or "").strip().lower()
    if maneuver_type not in ARCHIVE_MANEUVER_TYPES:
        maneuver_type = ""

    selection = (request.args.get("selection") or "scales").strip().lower()
    if selection not in ARCHIVE_REPORT_SELECTIONS:
        selection = "scales"

    agent = (request.args.get("agent") or "").strip()
    q = " ".join((request.args.get("q") or "").strip().split())
    q_search = q.lower()

    if selected_year and not any(
        dt and dt.year == selected_year
        for dt in (_parse_archive_datetime(item.get("latest_activity_value")) for item in scales)
    ):
        selected_year = default_year
        selected_month = default_month if selected_year else 0

    if selected_month and selected_year and latest_dt and not any(
        dt and dt.year == selected_year and dt.month == selected_month
        for dt in (_parse_archive_datetime(item.get("latest_activity_value")) for item in scales)
    ):
        selected_month = 0

    if selected_year and selected_month:
        period_label = f"{_archive_month_name(selected_month)} {selected_year}"
        period_hint = "arquivo filtrado por mês e ano"
    elif selected_year:
        period_label = str(selected_year)
        period_hint = "arquivo filtrado por ano"
    else:
        period_label = "Histórico"
        period_hint = "arquivo completo"

    return {
        "year": selected_year,
        "month": selected_month,
        "agent": agent,
        "q": q,
        "q_search": q_search,
        "maneuver_type": maneuver_type,
        "selection": selection,
        "years": years,
        "months": [{"value": idx, "label": name} for idx, name in enumerate(PT_MONTH_NAMES) if idx],
        "agents": sorted(
            {
                item.get("agent_label", "").strip()
                for item in scales
                if item.get("agent_label", "").strip() and item.get("agent_label", "").strip() != "--"
            }
        ),
        "period_label": period_label,
        "period_hint": period_hint,
    }


def _scale_matches_archive_filters(scale: dict, filters: dict) -> bool:
    dt = _parse_archive_datetime(scale.get("latest_activity_value"))
    if filters.get("year") and (not dt or dt.year != filters["year"]):
        return False
    if filters.get("month") and (not dt or dt.month != filters["month"]):
        return False
    if filters.get("agent") and (scale.get("agent_label") or "") != filters["agent"]:
        return False
    if filters.get("maneuver_type") and not any(
        (item.get("maneuver_type") or "").strip().lower() == filters["maneuver_type"]
        for item in scale.get("maneuvers", [])
    ):
        return False
    if filters.get("q_search") and filters["q_search"] not in (scale.get("search_blob") or ""):
        return False
    return True


def _filter_archived_scales(scales: list[dict], filters: dict) -> list[dict]:
    return [item for item in scales if _scale_matches_archive_filters(item, filters)]


def _archive_summary(scales: list[dict], filters: dict) -> dict:
    scale_count = len(scales)
    maneuver_count = sum(int(item.get("maneuver_count") or len(item.get("maneuvers", []))) for item in scales)
    total_pilotage = round(sum(float(item.get("estimated_pilotage_total") or 0.0) for item in scales), 2)
    total_tup = round(sum(float(item.get("estimated_tup") or 0.0) for item in scales), 2)
    total_cost = round(sum(float(item.get("estimated_grand_total") or 0.0) for item in scales), 2)
    return {
        "scale_count": scale_count,
        "maneuver_count": maneuver_count,
        "total_pilotage": total_pilotage,
        "total_tup": total_tup,
        "total_cost": total_cost,
        "total_pilotage_label": _format_currency_pt(total_pilotage) if scale_count else "--",
        "total_tup_label": _format_currency_pt(total_tup) if scale_count else "--",
        "total_cost_label": _format_currency_pt(total_cost) if scale_count else "--",
        "period_label": filters.get("period_label") or "Histórico",
        "period_hint": filters.get("period_hint") or "arquivo completo",
    }


def _build_archive_context() -> tuple[dict, dict, list[dict], dict]:
    port_activity = services.store.get_port_activity_snapshot(window_days=3650)
    port_activity = filter_port_activity_for_session(port_activity)
    filters = _build_archive_filters(port_activity.get("archived_scales", []))
    filtered_scales = _filter_archived_scales(port_activity.get("archived_scales", []), filters)
    return port_activity, filters, filtered_scales, _archive_summary(filtered_scales, filters)


def _filtered_archive_maneuvers(scales: list[dict], filters: dict) -> list[dict]:
    rows = []
    for scale in scales:
        for item in scale.get("maneuvers", []):
            maneuver_type = (item.get("maneuver_type") or "").strip().lower()
            if filters.get("maneuver_type") and maneuver_type != filters["maneuver_type"]:
                continue
            rows.append(
                {
                    **item,
                    "port_call_id": scale.get("port_call_id"),
                    "reference_code": scale.get("reference_code"),
                    "vessel_name": scale.get("vessel_name"),
                    "agent_label": scale.get("agent_label"),
                    "scale_estimated_total": scale.get("estimated_grand_total"),
                    "scale_estimated_total_label": scale.get("estimated_grand_total_label"),
                }
            )
    return rows


def _selected_archive_scales(filtered_scales: list[dict]) -> list[dict]:
    selected_ids = set(_split_csv_values(request.args.get("scale_ids")))
    if not selected_ids:
        return filtered_scales
    return [item for item in filtered_scales if item.get("port_call_id") in selected_ids]


def _selected_archive_maneuvers(filtered_scales: list[dict], filters: dict) -> list[dict]:
    selected_ids = set(_split_csv_values(request.args.get("maneuver_ids")))
    maneuvers = _filtered_archive_maneuvers(filtered_scales, filters)
    if not selected_ids:
        return maneuvers
    return [
        item for item in maneuvers
        if (item.get("maneuver_id") or item.get("id") or "") in selected_ids
    ]


def _build_archive_report_context(filtered_scales: list[dict], filters: dict) -> dict:
    selection = (request.args.get("selection") or filters.get("selection") or "scales").strip().lower()
    if selection not in ARCHIVE_REPORT_SELECTIONS:
        selection = "scales"

    generated_at = datetime.now().astimezone()
    report_context = {
        "selection": selection,
        "generated_at_label": generated_at.strftime("%d/%m/%Y %H:%M"),
        "period_label": filters.get("period_label") or "Histórico",
        "agent_filter_label": filters.get("agent") or "Todas as agências",
        "maneuver_type_label": {
            "entry": "Entrada",
            "departure": "Saída",
            "shift": "Mudança",
        }.get(filters.get("maneuver_type") or "", "Todos os tipos"),
        "search_label": filters.get("q") or "Sem texto de pesquisa",
        "auto_print": (request.args.get("print") or "").strip() == "1",
    }

    if selection == "maneuvers":
        maneuvers = _selected_archive_maneuvers(filtered_scales, filters)
        unique_scale_ids = {item.get("port_call_id") for item in maneuvers if item.get("port_call_id")}
        total_cost = round(sum(float(item.get("estimated_cost") or 0.0) for item in maneuvers), 2)
        report_context.update(
            {
                "maneuvers": maneuvers,
                "scales": [],
                "summary": {
                    "item_count": len(maneuvers),
                    "scale_count": len(unique_scale_ids),
                    "pilotage_total": total_cost,
                    "pilotage_total_label": _format_currency_pt(total_cost) if maneuvers else "--",
                    "tup_total": None,
                    "tup_total_label": "N/A",
                    "grand_total": total_cost,
                    "grand_total_label": _format_currency_pt(total_cost) if maneuvers else "--",
                },
            }
        )
        return report_context

    scales = _selected_archive_scales(filtered_scales)
    summary = _archive_summary(scales, filters)
    report_context.update(
        {
            "scales": scales,
            "maneuvers": [],
            "summary": {
                "item_count": summary["scale_count"],
                "scale_count": summary["scale_count"],
                "pilotage_total": summary["total_pilotage"],
                "pilotage_total_label": summary["total_pilotage_label"],
                "tup_total": summary["total_tup"],
                "tup_total_label": summary["total_tup_label"],
                "grand_total": summary["total_cost"],
                "grand_total_label": summary["total_cost_label"],
                "maneuver_count": summary["maneuver_count"],
            },
        }
    )
    return report_context


def _build_estimate_from_query() -> dict:
    gt = _coerce_non_negative_float(request.args.get("gt", 0), 0.0)
    if gt <= 0:
        raise ValueError("GT tem de ser positivo.")

    vessel_name = (request.args.get("vessel_name") or "Navio").strip() or "Navio"
    vessel_type = (request.args.get("vessel_type") or "restantes").strip().lower()
    stay_days = max(_coerce_non_negative_float(request.args.get("stay_days", 1), 1.0), 0.5)
    include_tup = (request.args.get("include_tup", "1") or "1").strip() not in {"0", "false", "False"}
    standby_hours = _coerce_non_negative_float(request.args.get("standby_hours", 0), 0.0)
    regular_line_calls = _coerce_non_negative_int(request.args.get("regular_line_calls", 0), 0)
    manoeuvre_names = _split_csv_values(request.args.get("manoeuvres"))
    if not manoeuvre_names:
        manoeuvre_names = ["entry", "departure"]

    type_map = {
        "entry": ManoeuvreType.ENTRY,
        "entrada": ManoeuvreType.ENTRY,
        "departure": ManoeuvreType.DEPARTURE,
        "saida": ManoeuvreType.DEPARTURE,
        "saída": ManoeuvreType.DEPARTURE,
        "shift": ManoeuvreType.SHIFT,
        "mudanca": ManoeuvreType.SHIFT,
        "mudança": ManoeuvreType.SHIFT,
        "anchoring": ManoeuvreType.ANCHORING,
        "fundeadouro": ManoeuvreType.ANCHORING,
        "trials": ManoeuvreType.TRIALS,
        "standby": ManoeuvreType.STANDBY,
    }
    surcharge_map = {
        "no_propulsion": SurchargeType.NO_PROPULSION,
        "special_assistance": SurchargeType.SPECIAL_ASSISTANCE,
    }
    reduction_map = {
        "regular_line": ReductionType.REGULAR_LINE,
        "cabotage": ReductionType.CABOTAGE,
        "technical_call": ReductionType.TECHNICAL_CALL,
    }

    raw_surcharges = _split_csv_values(request.args.get("surcharges"))
    raw_reductions = _split_csv_values(request.args.get("reductions"))
    manoeuvres = []
    for raw_name in manoeuvre_names:
        manoeuvre_type = type_map.get(raw_name.strip().lower(), ManoeuvreType.ENTRY)
        manoeuvres.append(
            ManoeuvreInput(
                manoeuvre_type=manoeuvre_type,
                gt=gt,
                surcharges=[surcharge_map[item] for item in raw_surcharges if item in surcharge_map],
                reductions=[reduction_map[item] for item in raw_reductions if item in reduction_map],
                standby_hours=standby_hours if manoeuvre_type == ManoeuvreType.STANDBY else 0.0,
                regular_line_calls=regular_line_calls,
            )
        )

    estimate = calculate_scale_cost(
        vessel_name=vessel_name,
        gt=gt,
        vessel_type=vessel_type,
        manoeuvres=manoeuvres,
        stay_days=stay_days,
        include_tup=include_tup,
    )
    return {
        "estimate": estimate,
        "auto_print": (request.args.get("print") or "").strip() == "1",
        "share_query": request.query_string.decode("utf-8", errors="ignore"),
        "surcharges": raw_surcharges,
        "reductions": raw_reductions,
    }


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
    tide_window = services.tide_service.window_summary(today - timedelta(days=2), days=5)

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
    wave_status = {}
    if getattr(services, "wave_service", None) and services.wave_service.enabled:
        try:
            wave_conditions = services.wave_service.get_current_conditions()
        except Exception as exc:
            wave_error = str(exc)
        wave_status = services.wave_service.status()

    local_warnings = []
    local_warnings_error = ""
    local_warnings_status = {}
    if getattr(services, "local_warning_service", None) and services.local_warning_service.enabled:
        try:
            local_warnings = services.local_warning_service.list_warnings()
        except Exception as exc:
            local_warnings_error = str(exc)
        local_warnings_status = services.local_warning_service.status()

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
        wave_status=wave_status,
        local_warnings=local_warnings,
        local_warnings_error=local_warnings_error,
        local_warnings_status=local_warnings_status,
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
    port_activity, archive_filters, filtered_scales, archive_summary = _build_archive_context()
    return render_template(
        "maneuver_archive.html",
        port_activity=port_activity,
        archive_filters=archive_filters,
        archived_scales=filtered_scales,
        archive_summary=archive_summary,
        title="Arquivo de Escalas",
    )


@bp.route("/maneuvers/archive/export.csv")
@login_required
def maneuver_archive_export():
    """Exportar o arquivo de manobras para um ficheiro CSV."""
    port_activity, archive_filters, filtered_scales, _archive_summary_data = _build_archive_context()
    filtered_scale_ids = {item.get("port_call_id") for item in filtered_scales if item.get("port_call_id")}
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Data", "Escala", "Navio", "Tipo de navio", "Hora", "Situacao",
        "Manobra", "Origem", "Destino", "Restricoes", "Agente",
        "Validado por", "Executado por", "Observacoes",
    ])
    for item in port_activity.get("archived_maneuvers", []):
        if item.get("port_call_id") not in filtered_scale_ids:
            continue
        maneuver_type = (item.get("maneuver_type") or "").strip().lower()
        if archive_filters.get("maneuver_type") and maneuver_type != archive_filters["maneuver_type"]:
            continue
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


@bp.route("/maneuvers/archive/report")
@login_required
def maneuver_archive_report():
    """Relatório formal e imprimível do arquivo filtrado ou selecionado."""
    refresh_knowledge_state(force_reindex=False)
    _port_activity, archive_filters, filtered_scales, _archive_summary_data = _build_archive_context()
    report_context = _build_archive_report_context(filtered_scales, archive_filters)
    return render_template(
        "archive_billing_report.html",
        archive_filters=archive_filters,
        report=report_context,
        title="Relatório de Faturação",
    )


@bp.route("/maneuvers/archive/estimate-report")
@login_required
def maneuver_estimate_report():
    """Relatório formal e partilhável da estimativa de custos."""
    try:
        report_context = _build_estimate_from_query()
    except ValueError as exc:
        return render_template(
            "cost_estimate_report.html",
            estimate=None,
            estimate_error=str(exc),
            auto_print=False,
            title="Relatório de Estimativa",
        )
    return render_template(
        "cost_estimate_report.html",
        estimate=report_context["estimate"],
        estimate_error="",
        auto_print=report_context["auto_print"],
        estimate_share_query=report_context["share_query"],
        estimate_surcharges=report_context["surcharges"],
        estimate_reductions=report_context["reductions"],
        title="Relatório de Estimativa",
    )


@bp.route("/warnings/local")
@login_required
def local_warnings():
    warnings = []
    warnings_error = ""
    warnings_status = {}
    if getattr(services, "local_warning_service", None) and services.local_warning_service.enabled:
        try:
            warnings = services.local_warning_service.list_warnings()
        except Exception as exc:
            warnings_error = str(exc)
        warnings_status = services.local_warning_service.status()
    return render_template(
        "local_warnings.html",
        warnings=warnings,
        warnings_error=warnings_error,
        warnings_status=warnings_status,
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
