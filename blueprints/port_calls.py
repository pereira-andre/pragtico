"""Port calls blueprint — CRUD, approvals, maneuver plans, reports."""

import json
import logging
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from core import services
from core.helpers import (
    build_departure_plan_note,
    build_entry_request_note,
    build_maneuver_context,
    build_pilot_report_note,
    build_scale_context,
    build_shift_plan_note,
    ensure_portal_berth_is_available,
    login_required,
    normalize_portal_berth,
    parse_local_datetime_input,
    port_call_scope_required,
    redirect_to_portal_target,
    require_form_text,
    role_required,
)
from core.portal_notifications import (
    latest_maneuver_by_type,
    maneuver_by_id,
    record_maneuver_notification,
)
from storage import normalize_constraint_codes
from core.validators import (
    validate_datetime_range,
    validate_operational_feedback_status,
    validate_imo,
    validate_not_past_datetime,
    normalize_thruster_state,
    validate_optional_text,
    validate_positive_number,
    validate_required_text,
    validate_tug_count,
    validate_vessel_dimensions,
)

logger = logging.getLogger(__name__)

bp = Blueprint("port_calls", __name__)

PORT_CALL_JSON_TEMPLATE = {
    "vessel_name": "MSC Lyria",
    "eta": "2026-04-09T14:30:00+01:00",
    "berth": "Secil W",
    "last_port": "Sines",
    "next_port": "Vigo",
    "vessel_imo": "9723345",
    "vessel_call_sign": "CQAN7",
    "vessel_flag": "Madeira",
    "vessel_type": "Graneis sólidos",
    "vessel_loa_m": "189.9",
    "vessel_beam_m": "32.2",
    "vessel_gt_t": "32540",
    "vessel_dwt_t": "38600",
    "vessel_max_draft_m": "11.8",
    "vessel_bow_thruster": "yes",
    "vessel_stern_thruster": "unknown",
    "booking": "2026-04-09T13:45:00+01:00",
    "draft_m": "11.2",
    "tug_count": 2,
    "constraints": ["daylight"],
    "notes": "Janela de maré confirmada com agente.",
}


def _emit_maneuver_notification(
    *,
    port_call: dict,
    maneuver: dict | None,
    event_type: str,
    actor_username: str,
    previous_maneuver: dict | None = None,
) -> None:
    try:
        record_maneuver_notification(
            port_call=port_call,
            maneuver=maneuver,
            event_type=event_type,
            actor_username=actor_username,
            previous_maneuver=previous_maneuver,
        )
    except Exception:
        logger.exception(
            "Falha inesperada ao publicar notificacao live para %s/%s.",
            port_call.get("id"),
            event_type,
        )


def _iso_to_local_input_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


def _build_scale_edit_defaults(port_call: dict) -> dict:
    return {
        "vessel_name": port_call.get("vessel_name", ""),
        "eta_local": _iso_to_local_input_value(port_call.get("eta")),
        "berth": port_call.get("berth", ""),
        "last_port": port_call.get("last_port", ""),
        "next_port": port_call.get("next_port", ""),
        "vessel_imo": port_call.get("vessel_imo", ""),
        "vessel_call_sign": port_call.get("vessel_call_sign", ""),
        "vessel_flag": port_call.get("vessel_flag", ""),
        "vessel_type": port_call.get("vessel_type", ""),
        "vessel_loa_m": port_call.get("vessel_loa_m", ""),
        "vessel_beam_m": port_call.get("vessel_beam_m", ""),
        "vessel_gt_t": port_call.get("vessel_gt_t", ""),
        "vessel_dwt_t": port_call.get("vessel_dwt_t", ""),
        "vessel_max_draft_m": port_call.get("vessel_max_draft_m", ""),
        "vessel_bow_thruster": port_call.get("vessel_bow_thruster", "unknown"),
        "vessel_stern_thruster": port_call.get("vessel_stern_thruster", "unknown"),
        "notes": port_call.get("notes", ""),
    }


def _first_payload_value(payload: dict, *keys: str, default=""):
    for key in keys:
        if key in payload and payload.get(key) is not None:
            return payload.get(key)
    return default


def _string_payload_value(payload: dict, *keys: str, default: str = "") -> str:
    value = _first_payload_value(payload, *keys, default=default)
    if value is None:
        return default
    return str(value).strip()


def _coerce_port_call_payload(payload: dict) -> dict:
    return {
        "vessel_name": _string_payload_value(payload, "vessel_name"),
        "vessel_short_name": _string_payload_value(payload, "vessel_short_name"),
        "vessel_imo": _string_payload_value(payload, "vessel_imo"),
        "vessel_call_sign": _string_payload_value(payload, "vessel_call_sign"),
        "vessel_flag": _string_payload_value(payload, "vessel_flag"),
        "vessel_type": _string_payload_value(payload, "vessel_type"),
        "vessel_loa_m": _string_payload_value(payload, "vessel_loa_m"),
        "vessel_beam_m": _string_payload_value(payload, "vessel_beam_m"),
        "vessel_gt_t": _string_payload_value(payload, "vessel_gt_t"),
        "vessel_max_draft_m": _string_payload_value(payload, "vessel_max_draft_m"),
        "vessel_dwt_t": _string_payload_value(payload, "vessel_dwt_t"),
        "vessel_bow_thruster": _first_payload_value(payload, "vessel_bow_thruster", default="unknown"),
        "vessel_stern_thruster": _first_payload_value(payload, "vessel_stern_thruster", default="unknown"),
        "eta_local": _string_payload_value(payload, "eta_local", "eta"),
        "berth": _string_payload_value(payload, "berth"),
        "last_port": _string_payload_value(payload, "last_port"),
        "next_port": _string_payload_value(payload, "next_port"),
        "booking_local": _string_payload_value(payload, "booking_local", "booking"),
        "draft_m": _string_payload_value(payload, "draft_m"),
        "constraints": _first_payload_value(payload, "constraints", default=[]),
        "tug_count": _string_payload_value(payload, "tug_count"),
        "notes": _string_payload_value(payload, "notes"),
    }


def _create_port_call_from_payload(form_data: dict, *, created_by: str) -> dict:
    eta = parse_local_datetime_input(form_data["eta_local"], "ETA")
    validate_not_past_datetime(eta, "ETA")
    parse_local_datetime_input(form_data["booking_local"], "Marcação")
    berth = normalize_portal_berth(form_data["berth"], "Cais previsto")
    last_port = require_form_text(form_data["last_port"], "Porto anterior")
    next_port = require_form_text(form_data["next_port"], "Próximo destino")
    draft_m = validate_positive_number(form_data["draft_m"], "Calado (m)", max_value=30.0)
    tug_count = validate_tug_count(form_data["tug_count"])
    validate_imo(form_data["vessel_imo"])
    form_data["constraints"] = normalize_constraint_codes(form_data.get("constraints"))
    validated_dims = validate_vessel_dimensions(form_data)
    form_data.update(validated_dims)
    form_data["vessel_bow_thruster"] = normalize_thruster_state(form_data.get("vessel_bow_thruster"), "Bow thruster")
    form_data["vessel_stern_thruster"] = normalize_thruster_state(form_data.get("vessel_stern_thruster"), "Stern thruster")
    return services.store.create_port_call(
        vessel_name=form_data["vessel_name"],
        eta=eta,
        created_by=created_by,
        constraints=form_data["constraints"],
        berth=berth,
        last_port=last_port,
        next_port=next_port,
        vessel_short_name=form_data["vessel_short_name"],
        vessel_imo=form_data["vessel_imo"],
        vessel_call_sign=form_data["vessel_call_sign"],
        vessel_flag=form_data["vessel_flag"],
        vessel_type=form_data["vessel_type"],
        vessel_loa_m=form_data["vessel_loa_m"],
        vessel_beam_m=form_data["vessel_beam_m"],
        vessel_gt_t=form_data["vessel_gt_t"],
        vessel_max_draft_m=form_data["vessel_max_draft_m"],
        vessel_dwt_t=form_data["vessel_dwt_t"],
        vessel_bow_thruster=form_data["vessel_bow_thruster"],
        vessel_stern_thruster=form_data["vessel_stern_thruster"],
        notes=build_entry_request_note({**form_data, "draft_m": draft_m, "tug_count": tug_count}),
    )


def _load_port_call_json_payload() -> dict:
    uploaded_file = request.files.get("payload_file")
    raw_payload = ""
    if uploaded_file and uploaded_file.filename:
        raw_payload = uploaded_file.read().decode("utf-8-sig")
    if not raw_payload.strip():
        raw_payload = request.form.get("payload_json", "")
    if not raw_payload.strip():
        raise ValueError("Indica o JSON da escala ou carrega um ficheiro .json.")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido na linha {exc.lineno}, coluna {exc.colno}.") from exc

    if isinstance(payload, list):
        if len(payload) != 1 or not isinstance(payload[0], dict):
            raise ValueError("O JSON tem de conter um único objeto de escala.")
        payload = payload[0]
    if isinstance(payload, dict) and isinstance(payload.get("scale"), dict):
        payload = payload["scale"]
    elif isinstance(payload, dict) and isinstance(payload.get("port_call"), dict):
        payload = payload["port_call"]
    if not isinstance(payload, dict):
        raise ValueError("O JSON da escala tem de ser um objeto.")
    return payload


@bp.route("/port-calls/register")
@login_required
@role_required("admin", "agente")
def port_call_register():
    """Página de registo de nova escala portuária."""
    from core.helpers import build_tracked_scales, filter_port_activity_for_session
    port_activity = services.store.get_port_activity_snapshot(window_days=5)
    port_activity = filter_port_activity_for_session(port_activity)
    return render_template(
        "port_call_register.html",
        port_activity=port_activity,
        tracked_scales=build_tracked_scales(port_activity),
        port_call_json_template=json.dumps(PORT_CALL_JSON_TEMPLATE, ensure_ascii=False, indent=2),
        title="Escalas",
    )


@bp.route("/port-calls/<port_call_id>")
@login_required
@port_call_scope_required
def port_call_detail(port_call_id: str):
    """Página de detalhe de uma escala portuária."""
    try:
        port_call = services.store.get_port_call(port_call_id)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard_bp.dashboard"))
    except Exception:
        logger.exception("Falha inesperada ao abrir a escala %s.", port_call_id)
        flash("Falha inesperada ao abrir a escala.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))
    return render_template(
        "port_call_detail.html",
        port_call=port_call,
        scale=build_scale_context(port_call),
        scale_edit_defaults=_build_scale_edit_defaults(port_call),
        title=f"Escala {port_call['vessel_name']}",
    )


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>")
@login_required
@port_call_scope_required
def maneuver_detail(port_call_id: str, maneuver_id: str):
    """Página dedicada ao detalhe operacional de uma manobra."""
    try:
        port_call = services.store.get_port_call(port_call_id)
        maneuver_context = build_maneuver_context(port_call, maneuver_id)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))
    except Exception:
        logger.exception("Falha inesperada ao abrir a manobra %s/%s.", port_call_id, maneuver_id)
        flash("Falha inesperada ao abrir a manobra.", "error")
        return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))
    return render_template(
        "maneuver_detail.html",
        port_call=port_call,
        scale=maneuver_context["scale"],
        maneuver_view=maneuver_context,
        title=f"Manobra {maneuver_context['maneuver']['title']} · {port_call['vessel_name']}",
    )


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/feedback", methods=["POST"])
@login_required
@role_required("admin", "piloto")
@port_call_scope_required
def update_maneuver_case_feedback(port_call_id: str, maneuver_id: str):
    """Guardar feedback operacional validado sobre um caso histórico de manobra."""
    try:
        feedback_status = validate_operational_feedback_status(request.form.get("feedback_status"))
        feedback_note = validate_optional_text(request.form.get("feedback_note", ""))
        if feedback_status in {"avoid", "review"} and not feedback_note:
            raise ValueError("Indica uma nota para justificar este feedback operacional.")
        updated = services.store.update_maneuver_case_feedback(
            maneuver_id=maneuver_id,
            feedback_status=feedback_status,
            feedback_note=feedback_note,
            feedback_by=session["username"],
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("port_calls.maneuver_detail", port_call_id=port_call_id, maneuver_id=maneuver_id))
    except Exception:
        logger.exception("Falha inesperada ao atualizar feedback do caso %s.", maneuver_id)
        flash("Falha inesperada ao guardar o feedback operacional.", "error")
        return redirect(url_for("port_calls.maneuver_detail", port_call_id=port_call_id, maneuver_id=maneuver_id))

    flash(
        f"Feedback operacional atualizado: {updated.get('feedback_status_label') or updated.get('feedback_status', '')}.",
        "success",
    )
    return redirect(url_for("port_calls.maneuver_detail", port_call_id=port_call_id, maneuver_id=maneuver_id))


@bp.route("/port-calls", methods=["POST"])
@login_required
@role_required("admin", "agente")
def create_port_call():
    """Criar uma nova escala portuária a partir do formulário de registo."""
    form_data = _coerce_port_call_payload(
        {
            "vessel_name": request.form.get("vessel_name", ""),
            "vessel_short_name": "",
            "vessel_imo": request.form.get("vessel_imo", ""),
            "vessel_call_sign": request.form.get("vessel_call_sign", ""),
            "vessel_flag": request.form.get("vessel_flag", ""),
            "vessel_type": request.form.get("vessel_type", ""),
            "vessel_loa_m": request.form.get("vessel_loa_m", ""),
            "vessel_beam_m": request.form.get("vessel_beam_m", ""),
            "vessel_gt_t": request.form.get("vessel_gt_t", ""),
            "vessel_max_draft_m": request.form.get("vessel_max_draft_m", ""),
            "vessel_dwt_t": request.form.get("vessel_dwt_t", ""),
            "vessel_bow_thruster": request.form.get("vessel_bow_thruster", "unknown"),
            "vessel_stern_thruster": request.form.get("vessel_stern_thruster", "unknown"),
            "eta_local": request.form.get("eta_local", ""),
            "berth": request.form.get("berth", ""),
            "last_port": request.form.get("last_port", ""),
            "next_port": request.form.get("next_port", ""),
            "booking_local": request.form.get("booking_local", ""),
            "draft_m": request.form.get("draft_m", ""),
            "constraints": request.form.getlist("constraints"),
            "tug_count": request.form.get("tug_count", ""),
            "notes": request.form.get("notes", ""),
        }
    )

    try:
        port_call = _create_port_call_from_payload(form_data, created_by=session["username"])
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard_bp.dashboard"))
    except Exception:
        logger.exception("Falha inesperada ao criar escala para %s.", session.get("username"))
        flash("Falha inesperada ao guardar a escala.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))

    flash(f"Escala registada para {port_call['vessel_name']} com ETA {port_call['eta_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "entry"),
        event_type="created",
        actor_username=session["username"],
    )
    return redirect(url_for("dashboard_bp.dashboard"))


@bp.route("/port-calls/import-json", methods=["POST"])
@login_required
@role_required("admin", "agente")
def import_port_call_json():
    """Criar uma nova escala a partir de um payload JSON colado ou carregado no browser."""
    try:
        payload = _load_port_call_json_payload()
        port_call = _create_port_call_from_payload(
            _coerce_port_call_payload(payload),
            created_by=session["username"],
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("port_calls.port_call_register"))
    except Exception:
        logger.exception("Falha inesperada ao importar escala por JSON para %s.", session.get("username"))
        flash("Falha inesperada ao importar a escala por JSON.", "error")
        return redirect(url_for("port_calls.port_call_register"))

    flash(f"Escala importada para {port_call['vessel_name']} com ETA {port_call['eta_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "entry"),
        event_type="created",
        actor_username=session["username"],
    )
    return redirect(url_for("port_calls.port_call_register"))


@bp.route("/port-calls/<port_call_id>/edit", methods=["POST"])
@login_required
@role_required("admin")
@port_call_scope_required
def edit_port_call(port_call_id: str):
    """Editar os dados da escala/navio a partir da página de detalhe."""
    try:
        current = services.store.get_port_call(port_call_id)
        form_data = {
            "vessel_name": request.form.get("vessel_name", "").strip(),
            "vessel_imo": request.form.get("vessel_imo", "").strip(),
            "vessel_call_sign": request.form.get("vessel_call_sign", "").strip(),
            "vessel_flag": request.form.get("vessel_flag", "").strip(),
            "vessel_type": request.form.get("vessel_type", "").strip(),
            "vessel_loa_m": request.form.get("vessel_loa_m", "").strip(),
            "vessel_beam_m": request.form.get("vessel_beam_m", "").strip(),
            "vessel_gt_t": request.form.get("vessel_gt_t", "").strip(),
            "vessel_max_draft_m": request.form.get("vessel_max_draft_m", "").strip(),
            "vessel_dwt_t": request.form.get("vessel_dwt_t", "").strip(),
            "vessel_bow_thruster": request.form.get("vessel_bow_thruster", "unknown").strip(),
            "vessel_stern_thruster": request.form.get("vessel_stern_thruster", "unknown").strip(),
            "eta_local": request.form.get("eta_local", "").strip(),
            "berth": request.form.get("berth", "").strip(),
            "last_port": request.form.get("last_port", "").strip(),
            "next_port": request.form.get("next_port", "").strip(),
            "notes": request.form.get("notes", "").strip(),
        }
        eta = parse_local_datetime_input(form_data["eta_local"], "ETA")
        if current.get("status") == "scheduled":
            validate_not_past_datetime(eta, "ETA")
        berth = (
            ensure_portal_berth_is_available(form_data["berth"], current_port_call_id=port_call_id, label="Cais")
            if current.get("status") == "in_port"
            else normalize_portal_berth(form_data["berth"], "Cais")
        )
        last_port = require_form_text(form_data["last_port"], "Porto anterior")
        next_port = require_form_text(form_data["next_port"], "Próximo destino")
        validate_imo(form_data["vessel_imo"])
        validated_dims = validate_vessel_dimensions(form_data)
        form_data.update(validated_dims)
        form_data["vessel_bow_thruster"] = normalize_thruster_state(form_data.get("vessel_bow_thruster"), "Bow thruster")
        form_data["vessel_stern_thruster"] = normalize_thruster_state(form_data.get("vessel_stern_thruster"), "Stern thruster")
        updated = services.store.edit_port_call(
            port_call_id=port_call_id,
            updated_by=session["username"],
            vessel_name=require_form_text(form_data["vessel_name"], "Nome do navio"),
            eta=eta,
            berth=berth,
            last_port=last_port,
            next_port=next_port,
            notes=form_data["notes"],
            vessel_imo=form_data["vessel_imo"],
            vessel_call_sign=require_form_text(form_data["vessel_call_sign"], "Indicativo"),
            vessel_flag=require_form_text(form_data["vessel_flag"], "Bandeira"),
            vessel_type=require_form_text(form_data["vessel_type"], "Tipo de navio"),
            vessel_loa_m=form_data["vessel_loa_m"],
            vessel_beam_m=form_data["vessel_beam_m"],
            vessel_gt_t=form_data["vessel_gt_t"],
            vessel_max_draft_m=form_data["vessel_max_draft_m"],
            vessel_dwt_t=form_data["vessel_dwt_t"],
            vessel_bow_thruster=form_data["vessel_bow_thruster"],
            vessel_stern_thruster=form_data["vessel_stern_thruster"],
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    except Exception:
        logger.exception("Falha inesperada ao editar a escala %s.", port_call_id)
        flash("Falha inesperada ao atualizar a escala.", "error")
        return redirect_to_portal_target(port_call_id)

    flash(f"Escala atualizada para {updated['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/approve", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def approve_port_call(port_call_id: str):
    """Aprovar a manobra de entrada ou saída pendente de uma escala."""
    try:
        current = services.store.get_port_call(port_call_id)
        target_type = "entry" if current.get("status") == "scheduled" else "departure"
        port_call = services.store.approve_port_call(
            port_call_id=port_call_id, decided_by=session["username"],
            approval_note=request.form.get("approval_note", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Manobra aprovada para {port_call['vessel_name']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, target_type),
        event_type="approved",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/abort", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def abort_port_call(port_call_id: str):
    """Abortar a escala portuária e registar o motivo."""
    try:
        aborted_reason = validate_required_text(request.form.get("aborted_reason", ""), "Motivo de aborto")
        port_call = services.store.abort_port_call(
            port_call_id=port_call_id, decided_by=session["username"],
            aborted_reason=aborted_reason,
            approval_note=validate_optional_text(request.form.get("approval_note", "")),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Manobra abortada para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/schedule-departure", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def schedule_departure_plan(port_call_id: str):
    """Planear a manobra de saída de um navio em porto."""
    try:
        current = services.store.get_port_call(port_call_id)
        planned_departure_at = parse_local_datetime_input(request.form.get("planned_departure_at_local", "").strip(), "Hora prevista de saída")
        parse_local_datetime_input(request.form.get("booking_local", "").strip(), "Marcação")
        next_port = require_form_text(request.form.get("next_port", "").strip(), "Próximo destino")
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        tug_count = validate_tug_count(request.form.get("tug_count", "").strip())
        port_call = services.store.schedule_departure_plan(
            port_call_id=port_call_id, planned_departure_at=planned_departure_at,
            updated_by=session["username"], next_port=next_port,
            constraints=request.form.getlist("constraints"),
            departure_plan_note=build_departure_plan_note({
                "origin_berth": current.get("berth", ""),
                "draft_m": draft_m, "constraints": request.form.getlist("constraints"),
                "tug_count": tug_count, "notes": request.form.get("departure_plan_note", "").strip(),
            }),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Saída planeada para {port_call['vessel_name']} às {port_call['planned_departure_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "departure"),
        event_type="created",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/abort-departure", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def abort_departure_plan(port_call_id: str):
    """Cancelar o planeamento de saída de um navio em porto."""
    try:
        aborted_reason = validate_required_text(request.form.get("aborted_reason", ""), "Motivo de aborto")
        port_call = services.store.abort_departure_plan(
            port_call_id=port_call_id, updated_by=session["username"],
            aborted_reason=aborted_reason,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Planeamento de saída removido para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/schedule-shift", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def schedule_shift_plan(port_call_id: str):
    """Planear uma mudança de cais para um navio em porto."""
    try:
        current = services.store.get_port_call(port_call_id)
        planned_shift_at = parse_local_datetime_input(request.form.get("planned_shift_at_local", "").strip(), "Hora prevista da mudança")
        parse_local_datetime_input(request.form.get("booking_local", "").strip(), "Marcação")
        origin_berth = normalize_portal_berth(current.get("berth", ""), "Cais origem")
        destination_berth = normalize_portal_berth(request.form.get("destination_berth", "").strip(), "Cais destino")
        if destination_berth == origin_berth:
            raise ValueError("O cais destino tem de ser diferente do local atual do navio.")
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        tug_count = validate_tug_count(request.form.get("tug_count", "").strip())
        port_call = services.store.schedule_shift_plan(
            port_call_id=port_call_id, planned_shift_at=planned_shift_at,
            updated_by=session["username"], destination_berth=destination_berth,
            constraints=request.form.getlist("constraints"),
            shift_plan_note=build_shift_plan_note({
                "origin_berth": origin_berth, "destination_berth": destination_berth,
                "draft_m": draft_m, "constraints": request.form.getlist("constraints"),
                "tug_count": tug_count, "notes": request.form.get("shift_plan_note", "").strip(),
            }),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança planeada para {port_call['vessel_name']} às {port_call['planned_shift_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "shift"),
        event_type="created",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/approve-shift", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def approve_shift_plan(port_call_id: str):
    """Aprovar o planeamento de mudança de cais pendente."""
    try:
        port_call = services.store.approve_shift_plan(
            port_call_id=port_call_id, decided_by=session["username"],
            approval_note=request.form.get("approval_note", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança aprovada para {port_call['vessel_name']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "shift"),
        event_type="approved",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/abort-shift", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def abort_shift_plan(port_call_id: str):
    """Cancelar o planeamento de mudança de cais de um navio em porto."""
    try:
        aborted_reason = validate_required_text(request.form.get("aborted_reason", ""), "Motivo de aborto")
        port_call = services.store.abort_shift_plan(
            port_call_id=port_call_id, updated_by=session["username"],
            aborted_reason=aborted_reason,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança removida para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/complete-shift", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def mark_shift_completed(port_call_id: str):
    """Confirmar a conclusão da mudança de cais e atualizar a localização do navio."""
    try:
        current = services.store.get_port_call(port_call_id)
        ensure_portal_berth_is_available(
            current.get("shift_destination_berth") or current.get("berth", ""),
            current_port_call_id=port_call_id,
            label="Cais destino",
        )
        port_call = services.store.mark_shift_completed(
            port_call_id=port_call_id,
            shifted_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança concluída para {port_call['vessel_name']} às {port_call['shift_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "shift"),
        event_type="completed",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/arrive", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def mark_port_call_arrived(port_call_id: str):
    """Registar a chegada do navio ao porto e confirmar a manobra de entrada."""
    try:
        current = services.store.get_port_call(port_call_id)
        berth = ensure_portal_berth_is_available(
            request.form.get("berth", "").strip() or current.get("berth", ""),
            current_port_call_id=port_call_id,
            label="Cais",
        )
        port_call = services.store.mark_port_call_arrived(
            port_call_id=port_call_id,
            arrived_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
            berth=berth,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Entrada confirmada para {port_call['vessel_name']} às {port_call['ata_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "entry"),
        event_type="completed",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/depart", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def mark_port_call_departed(port_call_id: str):
    """Registar a saída do navio do porto e encerrar a manobra de saída."""
    try:
        port_call = services.store.mark_port_call_departed(
            port_call_id=port_call_id,
            departed_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
            next_port=request.form.get("next_port", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Saída registada para {port_call['vessel_name']} às {port_call['departure_label']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=latest_maneuver_by_type(port_call, "departure"),
        event_type="completed",
        actor_username=session["username"],
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/entry-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def attach_entry_report(port_call_id: str):
    """Guardar o registo de pilotagem da manobra de entrada."""
    try:
        maneuver_started_at = parse_local_datetime_input(request.form.get("maneuver_started_local", "").strip(), "Início da manobra")
        maneuver_finished_at = parse_local_datetime_input(request.form.get("maneuver_finished_local", "").strip(), "Fim da manobra")
        validate_datetime_range(maneuver_started_at, maneuver_finished_at)
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        note = build_pilot_report_note({"maneuver_started_at": maneuver_started_at, "maneuver_finished_at": maneuver_finished_at, "draft_m": draft_m, "notes": request.form.get("notes", "").strip()}, "Entrada")
        port_call = services.store.attach_entry_report(port_call_id=port_call_id, updated_by=session["username"], maneuver_started_at=maneuver_started_at, maneuver_finished_at=maneuver_finished_at, draft_m=draft_m, notes=note)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Registo da entrada guardado para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/departure-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def attach_departure_report(port_call_id: str):
    """Guardar o registo de pilotagem da manobra de saída."""
    try:
        maneuver_started_at = parse_local_datetime_input(request.form.get("maneuver_started_local", "").strip(), "Início da manobra")
        maneuver_finished_at = parse_local_datetime_input(request.form.get("maneuver_finished_local", "").strip(), "Fim da manobra")
        validate_datetime_range(maneuver_started_at, maneuver_finished_at)
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        note = build_pilot_report_note({"maneuver_started_at": maneuver_started_at, "maneuver_finished_at": maneuver_finished_at, "draft_m": draft_m, "notes": request.form.get("notes", "").strip()}, "Saída")
        port_call = services.store.attach_departure_report(port_call_id=port_call_id, updated_by=session["username"], maneuver_started_at=maneuver_started_at, maneuver_finished_at=maneuver_finished_at, draft_m=draft_m, notes=note)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Registo da saída guardado para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/shift-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def attach_shift_report(port_call_id: str):
    """Guardar o registo de pilotagem da manobra de mudança de cais."""
    try:
        maneuver_started_at = parse_local_datetime_input(request.form.get("maneuver_started_local", "").strip(), "Início da manobra")
        maneuver_finished_at = parse_local_datetime_input(request.form.get("maneuver_finished_local", "").strip(), "Fim da manobra")
        validate_datetime_range(maneuver_started_at, maneuver_finished_at)
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        note = build_pilot_report_note({"maneuver_started_at": maneuver_started_at, "maneuver_finished_at": maneuver_finished_at, "draft_m": draft_m, "notes": request.form.get("notes", "").strip()}, "Mudança")
        port_call = services.store.attach_shift_report(port_call_id=port_call_id, updated_by=session["username"], maneuver_started_at=maneuver_started_at, maneuver_finished_at=maneuver_finished_at, draft_m=draft_m, notes=note)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Registo da mudança guardado para {port_call['vessel_name']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/edit-plan", methods=["POST"])
@login_required
@role_required("admin", "agente")
@port_call_scope_required
def edit_maneuver_plan(port_call_id: str, maneuver_id: str):
    """Editar o planeamento de uma manobra existente e registar o motivo da alteração."""
    try:
        port_call = services.store.get_port_call(port_call_id)
        previous_maneuver = dict(maneuver_by_id(port_call, maneuver_id) or {})
        maneuver_context = build_maneuver_context(port_call, maneuver_id)
        maneuver_type = maneuver_context["maneuver"]["type"]
        origin = require_form_text(request.form.get("origin", "").strip(), "Origem")
        destination = require_form_text(request.form.get("destination", "").strip(), "Destino")
        if maneuver_type == "entry":
            destination = normalize_portal_berth(destination, "Destino")
        elif maneuver_type in {"departure", "shift"}:
            origin = normalize_portal_berth(origin, "Origem")
            if maneuver_type == "shift":
                destination = normalize_portal_berth(destination, "Destino")
                if destination == origin:
                    raise ValueError("O cais destino tem de ser diferente do local atual do navio.")
        port_call = services.store.edit_maneuver_plan(
            port_call_id=port_call_id, maneuver_id=maneuver_id,
            updated_by=session["username"], actor_role=session.get("role", ""),
            planned_at=parse_local_datetime_input(request.form.get("planned_at_local", "").strip(), "Hora de marcação"),
            origin=origin,
            destination=destination,
            draft_m=validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0),
            tug_count=validate_tug_count(request.form.get("tug_count", "").strip()),
            constraints=request.form.getlist("constraints"),
            plan_note=request.form.get("plan_observations", "").strip(),
            change_reason=require_form_text(request.form.get("change_reason", "").strip(), "Motivo da alteração"),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    except Exception:
        logger.exception("Falha inesperada ao editar planeamento %s/%s.", port_call_id, maneuver_id)
        flash("Falha inesperada ao editar a manobra.", "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Planeamento atualizado para {port_call['vessel_name']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=maneuver_by_id(port_call, maneuver_id),
        event_type="updated",
        actor_username=session["username"],
        previous_maneuver=previous_maneuver,
    )
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/edit-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def edit_maneuver_report(port_call_id: str, maneuver_id: str):
    """Rever o registo operacional de uma manobra concluída e registar o motivo da alteração."""
    try:
        current = services.store.get_port_call(port_call_id)
        previous_maneuver = dict(maneuver_by_id(current, maneuver_id) or {})
        maneuver_started_at = parse_local_datetime_input(request.form.get("maneuver_started_local", "").strip(), "Início da manobra")
        maneuver_finished_at = parse_local_datetime_input(request.form.get("maneuver_finished_local", "").strip(), "Fim da manobra")
        validate_datetime_range(maneuver_started_at, maneuver_finished_at)
        draft_m = validate_positive_number(request.form.get("draft_m", "").strip(), "Calado (m)", max_value=30.0)
        port_call = services.store.edit_maneuver_report(
            port_call_id=port_call_id, maneuver_id=maneuver_id,
            updated_by=session["username"],
            maneuver_started_at=maneuver_started_at, maneuver_finished_at=maneuver_finished_at,
            draft_m=draft_m,
            notes=build_pilot_report_note({"maneuver_started_at": maneuver_started_at, "maneuver_finished_at": maneuver_finished_at, "draft_m": draft_m, "notes": request.form.get("notes", "").strip()}, require_form_text(request.form.get("maneuver_label", "").strip(), "Manobra")),
            change_reason=require_form_text(request.form.get("change_reason", "").strip(), "Motivo da alteração"),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Registo revisto para {port_call['vessel_name']}.", "success")
    _emit_maneuver_notification(
        port_call=port_call,
        maneuver=maneuver_by_id(port_call, maneuver_id),
        event_type="report_updated",
        actor_username=session["username"],
        previous_maneuver=previous_maneuver,
    )
    return redirect_to_portal_target(port_call_id)
