"""Session, role, and agency-scope helpers."""

import unicodedata
from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for

from core import services
from domain.berth_layout import build_slot_occupancy
from domain.error_catalog import error_ref
from storage import is_user_profile_complete


def ensure_session_user_profile() -> bool:
    """Sync the session role with the stored user profile, clearing the session if user is gone."""
    username = session.get("username", "").strip().lower()
    if not username:
        return False
    profile = services.store.get_user_profile(username)
    if profile:
        session_role = (session.get("role") or "").strip().lower()
        profile_role = (profile.get("role") or "").strip().lower()
        if session_role in {"admin", "agente", "piloto"} and session_role != profile_role:
            if session_role == "admin":
                profile = services.store.set_user_role(username, "admin")
            else:
                session["role"] = profile_role
                return True
        session["role"] = profile.get("role", session_role or "piloto")
        return True
    session.clear()
    return False


def current_user_profile() -> dict | None:
    """Return the profile dict for the currently authenticated session user, or None."""
    username = session.get("username", "").strip().lower()
    if not username:
        return None
    return services.store.get_user_profile(username)


def session_profile_incomplete() -> bool:
    """Return True if the current session user has an incomplete profile."""
    profile = current_user_profile()
    if not profile:
        return False
    if (profile.get("role") or session.get("role") or "").strip().lower() == "admin":
        return False
    return not is_user_profile_complete(profile)


# ---------------------------------------------------------------------------
# Organization / scope helpers
# ---------------------------------------------------------------------------

def _organization_scope_key(value: str | None) -> str:
    collapsed = " ".join((value or "").strip().split())
    if not collapsed:
        return ""
    normalized = unicodedata.normalize("NFKD", collapsed)
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    return ascii_only.casefold()


def _item_organization_scope_key(item: dict | None) -> str:
    payload = item or {}
    profile = (
        payload.get("agent_profile")
        or payload.get("created_by_profile")
        or payload.get("reported_by_profile")
        or {}
    )
    username = (
        payload.get("created_by")
        or profile.get("username")
        or payload.get("agent_username")
        or payload.get("reported_by")
        or payload.get("decided_by")
        or ""
    )
    if username and hasattr(services.store, "get_user_profile"):
        try:
            live_profile = services.store.get_user_profile(username) or {}
        except Exception:
            live_profile = {}
        live_scope = _organization_scope_key(live_profile.get("organization"))
        if live_scope:
            return live_scope
    return _organization_scope_key(profile.get("organization"))


def _current_agent_scope_key() -> str | None:
    if (session.get("role") or "").strip().lower() != "agente":
        return None
    profile = current_user_profile() or {}
    scope_key = _organization_scope_key(profile.get("organization"))
    return scope_key or ""


def ensure_port_call_scope_access(port_call_id: str) -> None:
    """Raise PermissionError if the current agent session has no access to the given port call."""
    scope_key = _current_agent_scope_key()
    if scope_key is None:
        return
    if not scope_key:
        raise PermissionError(f"{error_ref('AGENCY_NOT_SET')} O perfil do agente tem de ter uma agência definida.")
    port_call = services.store.get_port_call(port_call_id)
    if _item_organization_scope_key(port_call) != scope_key:
        raise PermissionError(f"{error_ref('AGENCY_MISMATCH')} Esta escala pertence a outra agência.")


def _sanitize_public_activity_item(item: dict, scope_key: str) -> dict:
    """Keep public movement fields while hiding private agent/report context for other agencies."""
    if _item_organization_scope_key(item) == scope_key:
        return item
    sanitized = dict(item)
    for key in (
        "notes",
        "detail_note",
        "approval_note",
        "aborted_reason",
        "agent_profile",
        "pilot_profile",
        "reported_by_profile",
        "created_by_profile",
        "validated_by_profile",
        "executed_by_profile",
        "change_log",
        "vessel_gt",
        "vessel_gt_t",
        "vessel_loa_m",
        "vessel_beam_m",
        "vessel_dwt_t",
        "vessel_max_draft_m",
        "ship_loa_label",
        "ship_beam_label",
        "ship_gt_label",
        "ship_dwt_label",
        "ship_max_draft_label",
    ):
        if key in sanitized:
            sanitized[key] = {} if key.endswith("_profile") else "" if key != "change_log" else []
    for key in ("agent_label", "pilot_label", "validated_by_label", "executed_by_label", "reported_by_label"):
        if key in sanitized:
            sanitized[key] = "--"
    return sanitized


def filter_port_activity_for_session(port_activity: dict, *, public_operational: bool = False) -> dict:
    """Filter port activity down to entries visible to the current session's role and agency scope."""
    scope_key = _current_agent_scope_key()
    if scope_key is None:
        return port_activity

    arrivals = [item for item in port_activity.get("arrivals", []) if _item_organization_scope_key(item) == scope_key]
    in_port = [item for item in port_activity.get("in_port", []) if _item_organization_scope_key(item) == scope_key]
    departed = [item for item in port_activity.get("departed", []) if _item_organization_scope_key(item) == scope_key]
    aborted = [item for item in port_activity.get("aborted", []) if _item_organization_scope_key(item) == scope_key]
    planned_maneuvers = [
        item for item in port_activity.get("planned_maneuvers", [])
        if _item_organization_scope_key(item) == scope_key
    ]
    archived_maneuvers = [
        item for item in port_activity.get("archived_maneuvers", [])
        if _item_organization_scope_key(item) == scope_key
    ]
    archived_scales = [
        item for item in port_activity.get("archived_scales", [])
        if _item_organization_scope_key(item) == scope_key
    ]

    if public_operational:
        arrivals = [
            _sanitize_public_activity_item(item, scope_key)
            for item in port_activity.get("arrivals", []) or []
        ]
        in_port = [
            _sanitize_public_activity_item(item, scope_key)
            for item in port_activity.get("in_port", []) or []
        ]
        departed = [
            _sanitize_public_activity_item(item, scope_key)
            for item in port_activity.get("departed", []) or []
        ]
        planned_maneuvers = [
            _sanitize_public_activity_item(item, scope_key)
            for item in port_activity.get("planned_maneuvers", []) or []
        ]

    visible_port_call_ids = {
        item.get("id")
        for item in arrivals + in_port + departed + aborted
        if item.get("id")
    }
    visible_port_call_ids.update(
        item.get("port_call_id")
        for item in planned_maneuvers + archived_maneuvers
        if item.get("port_call_id")
    )

    slot_occupancy = build_slot_occupancy(in_port, berth_options=services.BERTH_OPTIONS)
    berthed = slot_occupancy["berthed"]
    anchorages = slot_occupancy["anchorages"]

    planned_groups_map = {}
    for item in planned_maneuvers:
        date_key = item.get("date_key")
        if not date_key:
            continue
        group = planned_groups_map.setdefault(
            date_key,
            {"date_key": date_key, "date_label": item.get("date_label", ""), "total": 0},
        )
        group["total"] += 1
    planned_groups = [planned_groups_map[key] for key in sorted(planned_groups_map.keys())]

    filtered_activity = {
        **port_activity,
        "arrivals": arrivals,
        "in_port": in_port,
        "berthed": berthed,
        "anchorages": anchorages,
        "departed": departed,
        "aborted": aborted,
        "planned_maneuvers": planned_maneuvers,
        "archived_maneuvers": archived_maneuvers,
        "archived_scales": archived_scales,
        "planned_groups": planned_groups,
        "departure_candidates": [
            _sanitize_public_activity_item(item, scope_key) if public_operational else item
            for item in port_activity.get("departure_candidates", [])
            if item.get("id") in visible_port_call_ids
        ],
        "maneuvers": [
            _sanitize_public_activity_item(item, scope_key)
            for item in port_activity.get("maneuvers", [])
        ] if public_operational else port_activity.get("maneuvers", []),
    }
    filtered_activity["stats"] = {
        **(port_activity.get("stats") or {}),
        "scheduled_count": len(arrivals),
        "in_port_count": len(in_port),
        "quay_vessel_count": slot_occupancy["quay_vessel_count"],
        "anchorage_vessel_count": slot_occupancy["anchorage_vessel_count"],
        "quadro_count": slot_occupancy["anchorage_vessel_count"],
        "departed_count": len(departed),
        "berth_count": slot_occupancy["occupied_slot_count"],
        "occupied_slot_count": slot_occupancy["occupied_slot_count"],
        "free_slot_count": slot_occupancy["free_slot_count"],
        "slot_capacity_count": slot_occupancy["slot_capacity_count"],
        "aborted_count": len(aborted),
        "planned_count": len(planned_maneuvers),
        "archive_count": len(archived_maneuvers),
        "archive_scale_count": len(archived_scales),
        "pending_count": sum(1 for item in planned_maneuvers if item.get("situation_class") == "pending"),
    }
    return filtered_activity


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def login_required(view):
    """Decorator that redirects unauthenticated requests to the login page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        wants_json = (
            request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") in {"fetch", "XMLHttpRequest"}
        )
        if not session.get("username"):
            if wants_json:
                return jsonify({"error": f"{error_ref('SESSION_EXPIRED')} Sessão expirada. Faz login novamente."}), 401
            return redirect(url_for("auth.login"))
        if not ensure_session_user_profile():
            if wants_json:
                return jsonify({"error": f"{error_ref('SESSION_EXPIRED')} Sessão expirada. Faz login novamente."}), 401
            flash(f"{error_ref('SESSION_EXPIRED')} Sessão expirada. Inicia sessão novamente.", "error")
            return redirect(url_for("auth.login"))
        if (
            session_profile_incomplete()
            and request.endpoint not in {"auth.profile", "auth.logout", "static", "dashboard_bp.image_asset"}
        ):
            if wants_json:
                return jsonify({"error": f"{error_ref('PROFILE_INCOMPLETE')} Completa o teu perfil antes de usar o sistema."}), 403
            return redirect(url_for("auth.profile", next=request.full_path if request.query_string else request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    """Decorator factory that restricts a view to users with one of the given roles."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                wants_json = (
                    request.path.startswith("/api/")
                    or request.accept_mimetypes.best == "application/json"
                    or request.headers.get("X-Requested-With") in {"fetch", "XMLHttpRequest"}
                )
                if wants_json:
                    return jsonify({"error": f"{error_ref('PERMISSION_DENIED')} Não tens permissão para esta ação."}), 403
                flash(f"{error_ref('PERMISSION_DENIED')} Não tens permissão para esta ação.", "error")
                return redirect(url_for("dashboard_bp.dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def redirect_to_portal_target(port_call_id: str):
    """Redirect to the scale detail, registration, or dashboard based on the form's redirect_to field."""
    target = request.form.get("redirect_to", "").strip().lower()
    maneuver_id = request.form.get("redirect_maneuver_id", "").strip()
    if target == "maneuver" and maneuver_id:
        return redirect(url_for("port_calls.maneuver_detail", port_call_id=port_call_id, maneuver_id=maneuver_id))
    if target == "scale":
        return redirect(url_for("port_calls.port_call_detail", port_call_id=port_call_id))
    if target == "register":
        return redirect(url_for("port_calls.port_call_register"))
    return redirect(url_for("dashboard_bp.dashboard"))


def port_call_scope_required(view):
    """Decorator that enforces agency-scope access control for port call views."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        port_call_id = kwargs.get("port_call_id")
        if not port_call_id:
            return view(*args, **kwargs)
        try:
            ensure_port_call_scope_access(port_call_id)
        except (ValueError, PermissionError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard_bp.dashboard")) if request.method == "GET" else redirect_to_portal_target(port_call_id)
        return view(*args, **kwargs)
    return wrapped
