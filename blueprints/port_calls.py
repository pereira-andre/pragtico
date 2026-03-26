"""Port calls blueprint — CRUD, approvals, maneuver plans, reports."""

import logging
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import services
from helpers import (
    build_departure_plan_note,
    build_entry_request_note,
    build_pilot_report_note,
    build_scale_context,
    build_shift_plan_note,
    login_required,
    parse_local_datetime_input,
    port_call_scope_required,
    redirect_to_portal_target,
    require_form_text,
    role_required,
)
from storage import normalize_constraint_codes
from validators import (
    validate_datetime_range,
    validate_imo,
    validate_optional_text,
    validate_positive_number,
    validate_required_text,
    validate_tug_count,
    validate_vessel_dimensions,
)

logger = logging.getLogger(__name__)

bp = Blueprint("port_calls", __name__)


@bp.route("/port-calls/register")
@login_required
@role_required("admin", "agente")
def port_call_register():
    """Página de registo de nova escala portuária."""
    from helpers import build_tracked_scales, filter_port_activity_for_session
    port_activity = services.store.get_port_activity_snapshot(window_days=5)
    port_activity = filter_port_activity_for_session(port_activity)
    return render_template(
        "port_call_register.html",
        port_activity=port_activity,
        tracked_scales=build_tracked_scales(port_activity),
        title="Registo de Escalas",
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
        title=f"Escala {port_call['vessel_name']}",
    )


@bp.route("/port-calls", methods=["POST"])
@login_required
@role_required("admin", "agente")
def create_port_call():
    """Criar uma nova escala portuária a partir do formulário de registo."""
    form_data = {
        "vessel_name": request.form.get("vessel_name", "").strip(),
        "vessel_short_name": "",
        "vessel_imo": request.form.get("vessel_imo", "").strip(),
        "vessel_call_sign": request.form.get("vessel_call_sign", "").strip(),
        "vessel_flag": request.form.get("vessel_flag", "").strip(),
        "vessel_type": request.form.get("vessel_type", "").strip(),
        "vessel_loa_m": request.form.get("vessel_loa_m", "").strip(),
        "vessel_beam_m": request.form.get("vessel_beam_m", "").strip(),
        "vessel_gt_t": request.form.get("vessel_gt_t", "").strip(),
        "vessel_max_draft_m": request.form.get("vessel_max_draft_m", "").strip(),
        "vessel_dwt_t": request.form.get("vessel_dwt_t", "").strip(),
        "eta_local": request.form.get("eta_local", "").strip(),
        "berth": request.form.get("berth", "").strip(),
        "last_port": request.form.get("last_port", "").strip(),
        "next_port": request.form.get("next_port", "").strip(),
        "booking_local": request.form.get("booking_local", "").strip(),
        "draft_m": request.form.get("draft_m", "").strip(),
        "constraints": request.form.getlist("constraints"),
        "tug_count": request.form.get("tug_count", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }

    try:
        eta = parse_local_datetime_input(form_data["eta_local"], "ETA")
        parse_local_datetime_input(form_data["booking_local"], "Marcação")
        berth = require_form_text(form_data["berth"], "Cais previsto")
        last_port = require_form_text(form_data["last_port"], "Porto anterior")
        next_port = require_form_text(form_data["next_port"], "Próximo destino")
        draft_m = validate_positive_number(form_data["draft_m"], "Calado (m)", max_value=30.0)
        tug_count = validate_tug_count(form_data["tug_count"])
        validate_imo(form_data["vessel_imo"])
        validated_dims = validate_vessel_dimensions(form_data)
        form_data.update(validated_dims)
        port_call = services.store.create_port_call(
            vessel_name=form_data["vessel_name"], eta=eta,
            created_by=session["username"], constraints=form_data["constraints"],
            berth=berth, last_port=last_port, next_port=next_port,
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
            notes=build_entry_request_note({**form_data, "draft_m": draft_m, "tug_count": tug_count}),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard_bp.dashboard"))
    except Exception:
        logger.exception("Falha inesperada ao criar escala para %s.", session.get("username"))
        flash("Falha inesperada ao guardar a escala.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))

    flash(f"Manobra registada para {port_call['vessel_name']} com ETA {port_call['eta_label']}.", "success")
    return redirect(url_for("dashboard_bp.dashboard"))


@bp.route("/port-calls/<port_call_id>/approve", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def approve_port_call(port_call_id: str):
    """Aprovar a manobra de entrada ou saída pendente de uma escala."""
    try:
        port_call = services.store.approve_port_call(
            port_call_id=port_call_id, decided_by=session["username"],
            approval_note=request.form.get("approval_note", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Manobra aprovada para {port_call['vessel_name']}.", "success")
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
                "draft_m": draft_m, "constraints": request.form.getlist("constraints"),
                "tug_count": tug_count, "notes": request.form.get("departure_plan_note", "").strip(),
            }),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Saída planeada para {port_call['vessel_name']} às {port_call['planned_departure_label']}.", "success")
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
        planned_shift_at = parse_local_datetime_input(request.form.get("planned_shift_at_local", "").strip(), "Hora prevista da mudança")
        parse_local_datetime_input(request.form.get("booking_local", "").strip(), "Marcação")
        origin_berth = require_form_text(request.form.get("origin_berth", "").strip(), "Cais origem")
        destination_berth = require_form_text(request.form.get("destination_berth", "").strip(), "Cais destino")
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
        port_call = services.store.mark_shift_completed(
            port_call_id=port_call_id,
            shifted_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Mudança concluída para {port_call['vessel_name']} às {port_call['shift_label']}.", "success")
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/arrive", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def mark_port_call_arrived(port_call_id: str):
    """Registar a chegada do navio ao porto e confirmar a manobra de entrada."""
    try:
        port_call = services.store.mark_port_call_arrived(
            port_call_id=port_call_id,
            arrived_at=datetime.now().astimezone().isoformat(),
            updated_by=session["username"],
            berth=request.form.get("berth", "").strip(),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to_portal_target(port_call_id)
    flash(f"Entrada confirmada para {port_call['vessel_name']} às {port_call['ata_label']}.", "success")
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
@role_required("admin", "agente", "piloto")
@port_call_scope_required
def edit_maneuver_plan(port_call_id: str, maneuver_id: str):
    """Editar o planeamento de uma manobra existente e registar o motivo da alteração."""
    try:
        port_call = services.store.edit_maneuver_plan(
            port_call_id=port_call_id, maneuver_id=maneuver_id,
            updated_by=session["username"], actor_role=session.get("role", ""),
            planned_at=parse_local_datetime_input(request.form.get("planned_at_local", "").strip(), "Hora de marcação"),
            origin=require_form_text(request.form.get("origin", "").strip(), "Origem"),
            destination=require_form_text(request.form.get("destination", "").strip(), "Destino"),
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
    return redirect_to_portal_target(port_call_id)


@bp.route("/port-calls/<port_call_id>/maneuvers/<maneuver_id>/edit-report", methods=["POST"])
@login_required
@role_required("admin", "piloto")
def edit_maneuver_report(port_call_id: str, maneuver_id: str):
    """Rever o registo operacional de uma manobra concluída e registar o motivo da alteração."""
    try:
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
    return redirect_to_portal_target(port_call_id)
