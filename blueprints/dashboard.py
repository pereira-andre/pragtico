"""Dashboard blueprint — home, dashboard, health, AIS, archive."""

import csv
import os
import unicodedata
from datetime import date, datetime, timedelta
from io import StringIO
from textwrap import wrap

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
from integrations.tide_service import LISBON_TZ
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
WARNING_ATTACHMENT_FILTERS = {"", "with", "without"}


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


def _daily_report_datetime(value: str | None) -> datetime | None:
    return _parse_archive_datetime(value)


def _daily_report_date(value: str | None) -> date | None:
    dt = _daily_report_datetime(value)
    if not dt:
        return None
    return dt.astimezone().date() if dt.tzinfo else dt.date()


def _daily_report_timestamp(value: str | None) -> float:
    dt = _daily_report_datetime(value)
    if not dt:
        return float("inf")
    return dt.timestamp()


def _daily_report_agency_label(item: dict) -> str:
    profile = item.get("agent_profile") or {}
    return (
        profile.get("organization")
        or item.get("agent_organization")
        or item.get("agency")
        or item.get("agent_label")
        or "--"
    )


def _daily_report_is_today(item: dict, target_date: date, fields: tuple[str, ...]) -> bool:
    return any(_daily_report_date(item.get(field)) == target_date for field in fields)


def _daily_report_maneuver_type(item: dict) -> str:
    raw_type = (item.get("maneuver_type") or "").strip().casefold()
    raw_label = (item.get("maneuver_label") or "").strip().casefold()
    if raw_type in {"entry", "departure", "shift"}:
        return raw_type
    if raw_label in {"entrar", "entrada"}:
        return "entry"
    if raw_label in {"sair", "saida", "saída"}:
        return "departure"
    if raw_label in {"mudanca", "mudança", "mudança interna"}:
        return "shift"
    return raw_type


def _daily_report_movement_row(item: dict, *, movement_type: str) -> dict:
    time_value = item.get("planned_value") or item.get("eta") or item.get("eta_value") or item.get("date_value")
    return {
        "id": item.get("port_call_id") or item.get("id") or item.get("reference_code") or item.get("vessel_name") or "",
        "time_label": item.get("planned_label") or item.get("eta_label") or item.get("date_label") or "--",
        "sort_value": _daily_report_timestamp(time_value),
        "vessel_name": item.get("vessel_name") or "Navio",
        "reference_code": item.get("reference_code") or "--",
        "agency_label": _daily_report_agency_label(item),
        "origin": item.get("local_origin") or item.get("last_port") or "--",
        "destination": item.get("local_destination") or item.get("berth_label") or item.get("berth") or "--",
        "movement_type": movement_type,
        "situation_label": item.get("situation_label") or "",
    }


def _daily_report_position_groups(groups: list[dict]) -> list[dict]:
    position_groups = []
    for group in groups or []:
        vessels = []
        for vessel in group.get("vessels", []):
            departure_label = vessel.get("planned_departure_label") or ""
            note = f"Saída planeada: {departure_label}" if departure_label else ""
            vessels.append(
                {
                    "id": vessel.get("id") or "",
                    "vessel_name": vessel.get("vessel_name") or "Navio",
                    "reference_code": vessel.get("reference_code") or "--",
                    "agency_label": _daily_report_agency_label(vessel),
                    "ship_type_label": vessel.get("ship_type_label") or "--",
                    "note": note,
                }
            )
        position_groups.append(
            {
                "berth": group.get("berth") or "Sem posição",
                "count": len(vessels),
                "vessels": vessels,
            }
        )
    return position_groups


def _build_daily_position_report_context(
    port_activity: dict,
    *,
    target_date: date | None = None,
    generated_at: datetime | None = None,
    tide_day: dict | None = None,
) -> dict:
    target_date = target_date or datetime.now(LISBON_TZ).date()
    generated_at = generated_at or datetime.now().astimezone()

    planned_today = [
        item
        for item in port_activity.get("planned_maneuvers", [])
        if _daily_report_is_today(item, target_date, ("planned_value", "date_value", "actual_value"))
    ]
    planned_entries = [
        _daily_report_movement_row(item, movement_type="entry")
        for item in planned_today
        if _daily_report_maneuver_type(item) == "entry"
    ]
    planned_entry_ids = {item["id"] for item in planned_entries if item.get("id")}
    scheduled_entries = [
        _daily_report_movement_row(item, movement_type="entry")
        for item in port_activity.get("arrivals", [])
        if _daily_report_is_today(item, target_date, ("eta", "eta_value", "date_value"))
        and (item.get("id") or item.get("reference_code") or item.get("vessel_name")) not in planned_entry_ids
    ]
    arrivals = sorted(planned_entries + scheduled_entries, key=lambda item: (item["sort_value"], item["vessel_name"]))

    shifts = sorted(
        [
            _daily_report_movement_row(item, movement_type="shift")
            for item in planned_today
            if _daily_report_maneuver_type(item) == "shift"
        ],
        key=lambda item: (item["sort_value"], item["vessel_name"]),
    )

    berthed = _daily_report_position_groups(port_activity.get("berthed", []))
    anchorages = _daily_report_position_groups(port_activity.get("anchorages", []))
    position_vessel_count = sum(group["count"] for group in berthed + anchorages)
    departing_today_count = sum(
        1
        for group in berthed + anchorages
        for vessel in group["vessels"]
        if vessel.get("note")
    )

    return {
        "target_date": target_date,
        "date_label": target_date.strftime("%d/%m/%Y"),
        "generated_at_label": generated_at.strftime("%d/%m/%Y %H:%M"),
        "arrivals": arrivals,
        "shifts": shifts,
        "berthed": berthed,
        "anchorages": anchorages,
        "tide_day": tide_day or {},
        "summary": {
            "arrivals": len(arrivals),
            "shifts": len(shifts),
            "positions": position_vessel_count,
            "departures_planned": departing_today_count,
        },
    }


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


def _parse_warning_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _warning_search_blob(warning: dict) -> str:
    return " ".join(
        str(warning.get(field) or "")
        for field in ("display_code", "subject", "location", "description_text", "status_label")
    ).lower()


def _build_warning_filters(warnings: list[dict]) -> dict:
    q = " ".join((request.args.get("q") or "").strip().split())
    q_search = q.lower()
    statuses = sorted(
        {
            (item.get("status_label") or "").strip()
            for item in warnings
            if (item.get("status_label") or "").strip()
        }
    )
    locations = sorted(
        {
            (item.get("location") or "").strip()
            for item in warnings
            if (item.get("location") or "").strip() and (item.get("location") or "").strip() != "--"
        }
    )
    status = (request.args.get("status") or "").strip()
    if status not in statuses:
        status = ""
    location = (request.args.get("location") or "").strip()
    if location not in locations:
        location = ""
    attachments = (request.args.get("attachments") or "").strip().lower()
    if attachments not in WARNING_ATTACHMENT_FILTERS:
        attachments = ""
    return {
        "q": q,
        "q_search": q_search,
        "status": status,
        "location": location,
        "attachments": attachments,
        "statuses": statuses,
        "locations": locations,
    }


def _warning_matches_filters(warning: dict, filters: dict) -> bool:
    if filters.get("status") and (warning.get("status_label") or "") != filters["status"]:
        return False
    if filters.get("location") and (warning.get("location") or "") != filters["location"]:
        return False
    if filters.get("attachments") == "with" and not warning.get("has_attachments"):
        return False
    if filters.get("attachments") == "without" and warning.get("has_attachments"):
        return False
    if filters.get("q_search") and filters["q_search"] not in _warning_search_blob(warning):
        return False
    return True


def _filter_warnings(warnings: list[dict], filters: dict) -> list[dict]:
    filtered = [item for item in warnings if _warning_matches_filters(item, filters)]

    def warning_sort_key(item: dict) -> tuple[float, str]:
        start_dt = _parse_warning_datetime(item.get("start_date_iso"))
        return (start_dt.timestamp() if start_dt else 0.0, str(item.get("display_code") or ""))

    return sorted(
        filtered,
        key=warning_sort_key,
        reverse=True,
    )


def _selected_warnings(filtered_warnings: list[dict]) -> list[dict]:
    selected_ids = set(_split_csv_values(request.args.get("warning_ids")))
    if not selected_ids:
        return filtered_warnings
    return [item for item in filtered_warnings if str(item.get("id") or "") in selected_ids]


def _warning_filter_summary(filters: dict) -> str:
    parts = []
    if filters.get("status"):
        parts.append(f"Estado: {filters['status']}")
    if filters.get("location"):
        parts.append(f"Local: {filters['location']}")
    if filters.get("attachments") == "with":
        parts.append("Com anexos")
    elif filters.get("attachments") == "without":
        parts.append("Sem anexos")
    if filters.get("q"):
        parts.append(f"Pesquisa: {filters['q']}")
    return " | ".join(parts) if parts else "Sem filtros adicionais."


def _warning_runtime_status(
    warnings: list[dict],
    warnings_status: dict,
    warnings_error: str,
    *,
    fetch_ok: bool,
) -> dict:
    error_message = (warnings_error or warnings_status.get("error") or "").strip()
    is_stale = bool(warnings_status.get("stale"))
    has_cache = bool(warnings_status.get("cache_updated_at_label"))
    if error_message and not warnings:
        return {
            "state": "offline",
            "label": "Indisponível",
            "detail": error_message,
        }
    if is_stale:
        detail_parts = []
        if error_message:
            detail_parts.append(error_message)
        detail_parts.append(
            f"cache {warnings_status.get('cache_updated_at_label')}"
            if has_cache
            else "a usar último snapshot local"
        )
        return {
            "state": "degraded",
            "label": "Cache local",
            "detail": " · ".join(part for part in detail_parts if part),
        }
    if fetch_ok:
        return {
            "state": "online",
            "label": "Online",
            "detail": f"{len(warnings)} aviso(s) em vigor",
        }
    return {
        "state": "offline",
        "label": "Sem ligação",
        "detail": "sem dados remotos disponíveis",
    }


def _warning_report_text(warnings: list[dict], filters: dict) -> str:
    generated_at = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
    lines = [
        "Relatório de Avisos Locais",
        f"Gerado em: {generated_at}",
        f"Filtros: {_warning_filter_summary(filters)}",
        f"Avisos incluídos: {len(warnings)}",
        "",
    ]
    for warning in warnings:
        lines.extend(
            [
                f"{warning.get('display_code', '--')} | {warning.get('subject', '--')}",
                f"Estado: {warning.get('status_label', '--')}",
                f"Local: {warning.get('location', '--')}",
                f"Período: {warning.get('start_date_label', '--')} até {warning.get('end_date_label', '--')}",
            ]
        )
        description = str(warning.get("description_text") or "").strip()
        if description:
            lines.append("Descrição:")
            lines.extend(description.splitlines())
        attachments = warning.get("attachments") or []
        if attachments:
            lines.append("Anexos:")
            for attachment in attachments:
                lines.append(f"- {attachment.get('name', 'Anexo')}: {attachment.get('url', '')}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _pdf_safe_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", str(value or ""))
    encoded = normalized.encode("cp1252", "replace").decode("cp1252")
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
    })


@bp.route("/contact")
def contact():
    """Página simples de contacto e enquadramento académico do projeto."""
    return render_template(
        "contact.html",
        support_email="2202880@estudante.uab.pt",
        title="Contacto",
    )


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
    planning_activity = services.store.get_port_activity_snapshot(window_days=3650)
    # The dashboard is the shared operational picture for the whole port.
    # Agent scoping remains enforced on scale-specific pages and detail routes.
    port_activity["planned_maneuvers"] = planning_activity.get("planned_maneuvers", [])
    port_activity["planned_groups"] = planning_activity.get("planned_groups", [])
    port_activity["stats"] = {
        **(port_activity.get("stats") or {}),
        "planned_count": planning_activity.get("stats", {}).get("planned_count", 0),
        "pending_count": planning_activity.get("stats", {}).get("pending_count", 0),
    }

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


@bp.route("/dashboard/fotografia-dia")
@login_required
def daily_position_report():
    """Relatório diário imprimível do estado operacional."""
    refresh_knowledge_state(force_reindex=False)
    target_date = datetime.now(LISBON_TZ).date()
    port_activity = services.store.get_port_activity_snapshot(window_days=5)
    planning_activity = services.store.get_port_activity_snapshot(window_days=3650)
    port_activity["planned_maneuvers"] = planning_activity.get("planned_maneuvers", [])
    report_context = _build_daily_position_report_context(
        port_activity,
        target_date=target_date,
        tide_day=services.tide_service.summary_for_date(target_date),
    )
    return render_template(
        "daily_position_report.html",
        report=report_context,
        title="Relatório Diário",
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
    warnings_fetch_ok = False
    if getattr(services, "local_warning_service", None) and services.local_warning_service.enabled:
        try:
            warnings = services.local_warning_service.probe_warnings()
            warnings_fetch_ok = True
        except Exception as exc:
            warnings_error = str(exc)
        warnings_status = services.local_warning_service.status()
    warnings_runtime = _warning_runtime_status(
        warnings,
        warnings_status,
        warnings_error,
        fetch_ok=warnings_fetch_ok,
    )
    warning_filters = _build_warning_filters(warnings)
    filtered_warnings = _filter_warnings(warnings, warning_filters)
    return render_template(
        "local_warnings.html",
        warnings=filtered_warnings,
        warnings_total=len(warnings),
        warning_filters=warning_filters,
        warnings_error=warnings_error,
        warnings_status=warnings_status,
        warnings_runtime=warnings_runtime,
        title="Avisos Locais",
    )


@bp.route("/warnings/local/report.txt")
@login_required
def local_warnings_report_txt():
    if not getattr(services, "local_warning_service", None) or not services.local_warning_service.enabled:
        abort(404)
    warnings = services.local_warning_service.list_warnings()
    warning_filters = _build_warning_filters(warnings)
    filtered_warnings = _filter_warnings(warnings, warning_filters)
    selected_warnings = _selected_warnings(filtered_warnings)
    filename = f"avisos_locais_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    payload = ("\ufeff" + _warning_report_text(selected_warnings, warning_filters)).encode("utf-8")
    return Response(
        payload,
        content_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/warnings/local/report.pdf")
@login_required
def local_warnings_report_pdf():
    if not getattr(services, "local_warning_service", None) or not services.local_warning_service.enabled:
        abort(404)
    warnings = services.local_warning_service.list_warnings()
    warning_filters = _build_warning_filters(warnings)
    filtered_warnings = _filter_warnings(warnings, warning_filters)
    selected_warnings = _selected_warnings(filtered_warnings)
    pdf = _render_text_pdf(
        "Relatório de Avisos Locais",
        _warning_report_text(selected_warnings, warning_filters),
    )
    filename = f"avisos_locais_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
