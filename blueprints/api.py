"""API blueprint — cost estimation endpoints."""

import unicodedata

from flask import Blueprint, jsonify, request
from flask import session

from core import services
from core.helpers import current_user_profile
from domain.cost_engine import (
    ManoeuvreInput,
    ManoeuvreType,
    ReductionType,
    SurchargeType,
    calculate_scale_cost,
    format_cost_summary,
    quick_estimate,
)
from core.helpers import login_required
from core.portal_notifications import PORTAL_NOTIFICATION_CHANNEL

bp = Blueprint("api", __name__)


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


def _scope_key(value: str | None) -> str:
    clean = " ".join((value or "").strip().split())
    if not clean:
        return ""
    normalized = unicodedata.normalize("NFKD", clean)
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    return ascii_only.casefold()


@bp.route("/api/cost/estimate", methods=["POST"])
@login_required
def api_cost_estimate():
    """API que calcula a estimativa detalhada de custos de pilotagem para uma escala."""
    payload = request.get_json(silent=True) or {}
    gt = payload.get("gt", 0)
    try:
        gt = float(gt)
    except (TypeError, ValueError):
        return jsonify({"error": "GT inválido."}), 400
    if gt <= 0:
        return jsonify({"error": "GT tem de ser positivo."}), 400

    vessel_name = (payload.get("vessel_name") or "Navio").strip()
    vessel_type = (payload.get("vessel_type") or "restantes").strip().lower()
    stay_days = max(_coerce_non_negative_float(payload.get("stay_days", 1), 1.0), 0.5)
    include_tup = payload.get("include_tup", True)

    raw_manoeuvres = payload.get("manoeuvres", [])
    if not raw_manoeuvres:
        raw_manoeuvres = [{"type": "entry"}, {"type": "departure"}]

    type_map = {
        "entry": ManoeuvreType.ENTRY, "entrada": ManoeuvreType.ENTRY,
        "departure": ManoeuvreType.DEPARTURE, "saida": ManoeuvreType.DEPARTURE,
        "shift": ManoeuvreType.SHIFT, "mudanca": ManoeuvreType.SHIFT,
        "anchoring": ManoeuvreType.ANCHORING,
        "standby": ManoeuvreType.STANDBY,
        "trials": ManoeuvreType.TRIALS,
    }
    surcharge_map = {"no_propulsion": SurchargeType.NO_PROPULSION, "special_assistance": SurchargeType.SPECIAL_ASSISTANCE}
    reduction_map = {"regular_line": ReductionType.REGULAR_LINE, "cabotage": ReductionType.CABOTAGE, "technical_call": ReductionType.TECHNICAL_CALL}

    manoeuvre_inputs = []
    for raw in raw_manoeuvres:
        m_type = type_map.get((raw.get("type") or "entry").lower().strip(), ManoeuvreType.ENTRY)
        surcharges = [surcharge_map[s] for s in (raw.get("surcharges") or []) if s in surcharge_map]
        reductions = [reduction_map[r] for r in (raw.get("reductions") or []) if r in reduction_map]
        manoeuvre_inputs.append(ManoeuvreInput(
            manoeuvre_type=m_type, gt=gt, surcharges=surcharges, reductions=reductions,
            standby_hours=_coerce_non_negative_float(raw.get("standby_hours", 0), 0.0),
            regular_line_calls=_coerce_non_negative_int(raw.get("regular_line_calls"), 0),
        ))

    estimate = calculate_scale_cost(
        vessel_name=vessel_name, gt=gt, vessel_type=vessel_type,
        manoeuvres=manoeuvre_inputs, stay_days=stay_days, include_tup=include_tup,
    )

    return jsonify({
        "vessel_name": estimate.vessel_name, "gt": estimate.gt,
        "vessel_type": estimate.vessel_type,
        "pilotage_total": estimate.pilotage_total,
        "tup_estimate": estimate.tup_estimate,
        "stay_days": estimate.stay_days,
        "grand_total": estimate.grand_total,
        "manoeuvres": [
            {"type": m.manoeuvre_type, "base_cost": m.base_cost, "surcharge": m.surcharge_amount, "reduction": m.reduction_amount, "standby": m.standby_cost, "total": m.total_cost, "breakdown": m.breakdown}
            for m in estimate.manoeuvres
        ],
        "notes": estimate.notes,
        "summary": format_cost_summary(estimate),
        "currency": "EUR", "tariff_year": 2024,
    })


@bp.route("/api/cost/quick", methods=["GET"])
@login_required
def api_cost_quick():
    """API que retorna uma estimativa rápida do custo de uma manobra a partir do GT."""
    try:
        gt = float(request.args.get("gt", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "GT inválido."}), 400
    if gt <= 0:
        return jsonify({"error": "GT tem de ser positivo."}), 400
    m_type = request.args.get("type", "entry").strip()
    return jsonify(quick_estimate(gt, m_type))


@bp.route("/api/portal-live-feed", methods=["GET"])
@login_required
def api_portal_live_feed():
    """Return unseen portal live notifications for the current authenticated session."""
    since = (request.args.get("since") or "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit", 10)), 20))
    except (TypeError, ValueError):
        limit = 10

    items = []
    latest_created_at = ""
    agent_scope_key = ""
    if (session.get("role") or "").strip().lower() == "agente":
        profile = current_user_profile() or {}
        agent_scope_key = _scope_key(profile.get("organization"))

    for event in services.store.list_channel_events(
        channel=PORTAL_NOTIFICATION_CHANNEL,
        since=since,
        limit=limit,
    ):
        payload = dict(event.get("payload") or {})
        if agent_scope_key and payload.get("scope_organization_key") != agent_scope_key:
            continue
        created_at = event.get("created_at") or ""
        items.append(
            {
                "id": event.get("id", ""),
                "event_type": event.get("event_type", ""),
                "message": payload.get("message", ""),
                "url": payload.get("url", ""),
                "created_at": created_at,
            }
        )
        if created_at and created_at > latest_created_at:
            latest_created_at = created_at

    return jsonify({"items": items, "latest_created_at": latest_created_at})
