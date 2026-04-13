"""Helpers for building and ranking historical maneuver cases."""

from __future__ import annotations

import os
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from core import services
from domain.berth_layout import canonicalize_berth_label, is_anchorage_berth, is_known_berth_label
from domain.document_processing import iso_now

from .utils import _clean_text, _local_iso_to_label, _normalize_maneuver_type, _parse_iso_datetime


_COMPASS_TO_DEGREES = {
    "N": 0.0,
    "NNE": 22.5,
    "NE": 45.0,
    "ENE": 67.5,
    "E": 90.0,
    "ESE": 112.5,
    "SE": 135.0,
    "SSE": 157.5,
    "S": 180.0,
    "SSW": 202.5,
    "SW": 225.0,
    "WSW": 247.5,
    "W": 270.0,
    "WNW": 292.5,
    "NW": 315.0,
    "NNW": 337.5,
}


def _case_key(value: Optional[str]) -> str:
    normalized = unicodedata.normalize("NFKD", _clean_text(value).lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_value.split())


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", {}, []):
            return value
    return None


def _kph_to_kts(value: Any) -> Optional[float]:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return round(numeric / 1.852, 1)


def _mph_to_kts(value: Any) -> Optional[float]:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return round(numeric * 0.868976, 1)


def _direction_degrees(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) % 360
    clean = str(value).strip().upper().replace("º", "")
    if not clean:
        return None
    if clean in _COMPASS_TO_DEGREES:
        return _COMPASS_TO_DEGREES[clean]
    numeric = _safe_float(clean)
    return numeric % 360 if numeric is not None else None


def _direction_quadrant(value: Any) -> str:
    degrees = _direction_degrees(value)
    if degrees is None:
        return ""
    quadrants = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return quadrants[int(((degrees + 22.5) % 360) // 45)]


def _wind_force_kts(payload: Dict[str, Any]) -> Optional[float]:
    wind_kts = _safe_float(
        _first_present(
            payload.get("wind_kts"),
            payload.get("wind_kt"),
            payload.get("wind_knots"),
            payload.get("wind_speed_kts"),
            payload.get("wind_speed_kt"),
            payload.get("wind_speed_knots"),
        )
    )
    if wind_kts is None:
        wind_kts = _kph_to_kts(_first_present(payload.get("wind_kph"), payload.get("wind_speed_kph")))
    if wind_kts is None:
        wind_kts = _mph_to_kts(_first_present(payload.get("wind_mph"), payload.get("wind_speed_mph")))

    gust_kts = _safe_float(
        _first_present(
            payload.get("gust_kts"),
            payload.get("gust_kt"),
            payload.get("gust_knots"),
            payload.get("gust_speed_kts"),
            payload.get("gust_speed_kt"),
            payload.get("gust_speed_knots"),
        )
    )
    if gust_kts is None:
        gust_kts = _kph_to_kts(_first_present(payload.get("gust_kph"), payload.get("gust_speed_kph")))
    if gust_kts is None:
        gust_kts = _mph_to_kts(_first_present(payload.get("gust_mph"), payload.get("gust_speed_mph")))

    values = [value for value in (wind_kts, gust_kts) if value is not None]
    return round(max(values), 1) if values else None


def _wind_band(value: Optional[float]) -> str:
    if value is None:
        return ""
    if value < 10:
        return "light"
    if value <= 20:
        return "moderate"
    if value <= 30:
        return "strong"
    return "severe"


def _wave_height_m(payload: Dict[str, Any]) -> Optional[float]:
    return _safe_float(
        _first_present(
            payload.get("significant_height_m"),
            payload.get("significant_height"),
            payload.get("wave_height_m"),
            payload.get("wave_m"),
            payload.get("height_m"),
        )
    )


def _wave_period_s(payload: Dict[str, Any]) -> Optional[float]:
    return _safe_float(
        _first_present(
            payload.get("mean_period_s"),
            payload.get("period_s"),
            payload.get("mean_period"),
            payload.get("wave_period_s"),
            payload.get("max_observed_period_s"),
            payload.get("t02"),
        )
    )


def _wave_height_band(value: Optional[float]) -> str:
    if value is None:
        return ""
    if value < 1:
        return "lt_1m"
    if value < 2:
        return "1_2m"
    if value < 3:
        return "2_3m"
    return "gte_3m"


def _wave_period_band(value: Optional[float]) -> str:
    if value is None:
        return ""
    if value < 6:
        return "lt_6s"
    if value <= 10:
        return "6_10s"
    return "gt_10s"


def _environment_signature_from_snapshot(snapshot: Optional[Dict], *, wave_sensitive: bool) -> Dict:
    snapshot = snapshot or {}
    weather_block = snapshot.get("weather") or {}
    weather = weather_block.get("closest_hour") or weather_block.get("current") or {}
    wave_block = snapshot.get("wave") or {}
    wave = wave_block.get("current") or wave_block
    if not isinstance(weather, dict):
        weather = {}
    if not isinstance(wave, dict):
        wave = {}

    wind_force_kts = _wind_force_kts(weather)
    wave_height_m = _wave_height_m(wave) if wave_sensitive else None
    if wave_height_m is None and wave_sensitive:
        wave_height_m = _wave_height_m(weather)
    wave_period_s = _wave_period_s(wave) if wave_sensitive else None
    if wave_period_s is None and wave_sensitive:
        wave_period_s = _wave_period_s(weather)

    return {
        "available": bool(snapshot),
        "captured": (snapshot.get("status") or "").strip().lower() == "captured",
        "phase": snapshot.get("phase", ""),
        "wave_sensitive": bool(wave_sensitive),
        "wind_force_kts": wind_force_kts,
        "wind_band": _wind_band(wind_force_kts),
        "wind_quadrant": _direction_quadrant(
            _first_present(
                weather.get("wind_degree"),
                weather.get("wind_direction_deg"),
                weather.get("wind_direction"),
                weather.get("wind_dir"),
            )
        ),
        "wave_height_m": wave_height_m,
        "wave_height_band": _wave_height_band(wave_height_m) if wave_sensitive else "",
        "wave_period_s": wave_period_s,
        "wave_period_band": _wave_period_band(wave_period_s) if wave_sensitive else "",
        "wave_direction_quadrant": _direction_quadrant(
            _first_present(
                wave.get("direction_deg"),
                wave.get("wave_degree"),
                wave.get("direction"),
            )
        ),
    }


def build_case_environment_signature(case: Dict) -> Dict:
    existing = case.get("environment_signature")
    if isinstance(existing, dict) and existing:
        return existing

    features = case.get("feature_snapshot") or {}
    environment = case.get("environment_snapshot") or {}
    latest_snapshot = (
        environment.get("latest")
        or environment.get("execution")
        or environment.get("decision")
        or environment.get("planning")
        or {}
    )
    return _environment_signature_from_snapshot(
        latest_snapshot,
        wave_sensitive=bool(features.get("wave_sensitive")),
    )


def _is_wave_sensitive_maneuver(maneuver_type: Optional[str]) -> bool:
    return _normalize_maneuver_type(maneuver_type) in {"entry", "departure"}


def _feedback_status_label(value: Optional[str]) -> str:
    return {
        "observed": "Correlação observada",
        "approved": "Referência positiva",
        "avoid": "Evitar como padrão",
        "review": "Rever caso",
    }.get((value or "").strip().lower(), "")


def _should_capture_live_environment() -> bool:
    flag = os.getenv("MANEUVER_CASE_CAPTURE_ENVIRONMENT", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _capture_live_environment_sources() -> tuple[Optional[Dict], Optional[Dict]]:
    if not _should_capture_live_environment():
        return None, None

    weather = None
    wave = None

    try:
        weather_service = getattr(services, "weather_service", None)
        if weather_service and weather_service.enabled:
            weather = weather_service.get_forecast(days=3)
    except Exception:
        weather = None

    try:
        wave_service = getattr(services, "wave_service", None)
        if wave_service and wave_service.enabled:
            wave = wave_service.get_current_conditions()
    except Exception:
        wave = None

    return weather, wave


def _closest_weather_hour(weather_forecast: Optional[Dict], event_at: Optional[str]) -> Optional[Dict]:
    if not weather_forecast or not event_at:
        return None
    event_dt = _parse_iso_datetime(event_at)
    if not event_dt:
        return None

    closest: Optional[Dict] = None
    closest_gap: Optional[float] = None
    for group in weather_forecast.get("hourly_groups", []) or []:
        for hour in group.get("hours", []) or []:
            hour_dt = _parse_iso_datetime((hour.get("timestamp") or "").replace(" ", "T"))
            if not hour_dt:
                continue
            if hour_dt.tzinfo is None:
                hour_dt = hour_dt.replace(tzinfo=timezone.utc)
            gap = abs((hour_dt - event_dt).total_seconds())
            if closest_gap is None or gap < closest_gap:
                closest_gap = gap
                closest = {
                    "date": group.get("date"),
                    "date_label": group.get("date_label"),
                    **hour,
                    "gap_seconds": int(gap),
                }
    return closest


def _build_phase_environment_snapshot(
    *,
    event_at: Optional[str],
    phase: str,
    existing_snapshot: Optional[Dict],
    capture_live_environment: bool,
    include_wave: bool,
    weather_forecast: Optional[Dict],
    wave_conditions: Optional[Dict],
) -> Dict:
    if existing_snapshot and existing_snapshot.get("event_at") == event_at:
        return existing_snapshot

    if not event_at:
        return {}

    if not capture_live_environment:
        return {
            "status": "not_captured",
            "phase": phase,
            "event_at": event_at,
            "captured_at": existing_snapshot.get("captured_at") if existing_snapshot else None,
            "wave_relevance": "applicable" if include_wave else "not_applicable",
            "wave": (
                {}
                if include_wave
                else {
                    "status": "not_applicable",
                    "reason": "Ondulação só é tratada como fator operacional relevante nas entradas e saídas.",
                }
            ),
        }

    closest_hour = _closest_weather_hour(weather_forecast, event_at)
    payload = {
        "status": "captured",
        "phase": phase,
        "event_at": event_at,
        "captured_at": iso_now(),
        "wave_relevance": "applicable" if include_wave else "not_applicable",
        "weather": {
            "provider": "WeatherAPI" if weather_forecast else "",
            "location": (weather_forecast or {}).get("location", {}),
            "current": (weather_forecast or {}).get("current", {}),
            "closest_hour": closest_hour or {},
        },
        "wave": (
            {
                "provider": "wave_service" if wave_conditions else "",
                "current": wave_conditions or {},
            }
            if include_wave
            else {
                "status": "not_applicable",
                "reason": "Ondulação só é tratada como fator operacional relevante nas entradas e saídas.",
            }
        ),
    }
    if not weather_forecast and not wave_conditions:
        payload["status"] = "unavailable"
    return payload


def _latest_case_event_at(maneuver: Dict) -> Optional[str]:
    return (
        maneuver.get("reported_at")
        or maneuver.get("execution_finished_at")
        or maneuver.get("completed_at")
        or maneuver.get("decided_at")
        or maneuver.get("planned_at")
        or maneuver.get("created_at")
    )


def _state_label(state: str) -> str:
    return {
        "pending": "Pendente",
        "approved": "Aprovada",
        "aborted": "Abortada",
        "completed": "Realizada",
    }.get((state or "").strip().lower(), "Pendente")


def _case_summary(port_call: Dict, maneuver: Dict) -> str:
    parts = [
        maneuver.get("action_label") or maneuver.get("type_label") or maneuver.get("type") or "Manobra",
        f"{maneuver.get('origin') or '--'} -> {maneuver.get('destination') or '--'}",
        port_call.get("ship_type_label") or port_call.get("vessel_type") or "Navio",
    ]
    loa = port_call.get("vessel_loa_m") or port_call.get("ship_loa_label")
    if loa:
        parts.append(f"LOA {loa} m")
    tug_count = maneuver.get("tug_count")
    if tug_count:
        parts.append(f"rebocadores {tug_count}")
    return " | ".join(parts)


def _decision_flags(maneuver: Dict) -> List[str]:
    text = " ".join(
        value
        for value in (
            maneuver.get("approval_note", ""),
            maneuver.get("aborted_reason", ""),
            maneuver.get("report_note", ""),
            maneuver.get("plan_note", ""),
        )
        if value
    ).lower()
    flags: List[str] = []
    if any(token in text for token in ("ondul", "vaga", "mar grosso", "agitação marítima")):
        flags.append("wave_related")
    if any(token in text for token in ("suspens", "suspensa", "suspender")):
        flags.append("pilotage_suspended")
    if any(token in text for token in ("cancel", "cancelada", "cancelado")):
        flags.append("pilotage_cancelled")
    if (
        _normalize_maneuver_type(maneuver.get("type")) == "entry"
        and (maneuver.get("state") or "").strip().lower() == "aborted"
        and "wave_related" in flags
    ):
        flags.append("entry_aborted_by_sea_state")
    return flags


def _feature_snapshot(port_call: Dict, maneuver: Dict) -> Dict:
    canonical_origin = canonicalize_berth_label(maneuver.get("origin")) or _clean_text(maneuver.get("origin"))
    canonical_destination = canonicalize_berth_label(maneuver.get("destination")) or _clean_text(maneuver.get("destination"))
    return {
        "maneuver_type": _normalize_maneuver_type(maneuver.get("type")),
        "origin": canonical_origin,
        "destination": canonical_destination,
        "origin_key": _case_key(canonical_origin),
        "destination_key": _case_key(canonical_destination),
        "origin_is_anchorage": is_anchorage_berth(canonical_origin),
        "destination_is_anchorage": is_anchorage_berth(canonical_destination),
        "origin_is_known_berth": is_known_berth_label(canonical_origin),
        "destination_is_known_berth": is_known_berth_label(canonical_destination),
        "vessel_type": _clean_text(port_call.get("vessel_type") or port_call.get("ship_type_label")),
        "vessel_type_key": _case_key(port_call.get("vessel_type") or port_call.get("ship_type_label")),
        "vessel_loa_m": _safe_float(port_call.get("vessel_loa_m")),
        "vessel_beam_m": _safe_float(port_call.get("vessel_beam_m")),
        "vessel_gt_t": _safe_float(port_call.get("vessel_gt_t")),
        "planned_draft_m": _safe_float(maneuver.get("planned_draft_m") or port_call.get("vessel_max_draft_m")),
        "reported_draft_m": _safe_float(maneuver.get("reported_draft_m")),
        "bow_thruster": (port_call.get("vessel_bow_thruster") or "unknown").strip().lower(),
        "stern_thruster": (port_call.get("vessel_stern_thruster") or "unknown").strip().lower(),
        "tug_count": _clean_text(maneuver.get("tug_count")),
        "constraints": list(maneuver.get("constraints") or []),
        "wave_sensitive": _is_wave_sensitive_maneuver(maneuver.get("type")),
    }


def build_maneuver_case(
    port_call: Dict,
    maneuver: Dict,
    *,
    existing_case: Optional[Dict] = None,
    capture_live_environment: bool = True,
    weather_forecast: Optional[Dict] = None,
    wave_conditions: Optional[Dict] = None,
) -> Dict:
    """Build a normalized historical case from a decorated port call and maneuver."""
    existing_case = existing_case or {}
    features = _feature_snapshot(port_call, maneuver)
    include_wave = bool(features.get("wave_sensitive"))

    planning_event_at = maneuver.get("created_at") or maneuver.get("planned_at")
    decision_event_at = maneuver.get("decided_at")
    execution_event_at = maneuver.get("reported_at") or maneuver.get("execution_finished_at") or maneuver.get("completed_at")

    environment_snapshot = {
        "planning": _build_phase_environment_snapshot(
            event_at=planning_event_at,
            phase="planning",
            existing_snapshot=(existing_case.get("environment_snapshot") or {}).get("planning"),
            capture_live_environment=capture_live_environment,
            include_wave=include_wave,
            weather_forecast=weather_forecast,
            wave_conditions=wave_conditions,
        ),
        "decision": _build_phase_environment_snapshot(
            event_at=decision_event_at,
            phase="decision",
            existing_snapshot=(existing_case.get("environment_snapshot") or {}).get("decision"),
            capture_live_environment=capture_live_environment,
            include_wave=include_wave,
            weather_forecast=weather_forecast,
            wave_conditions=wave_conditions,
        ),
        "execution": _build_phase_environment_snapshot(
            event_at=execution_event_at,
            phase="execution",
            existing_snapshot=(existing_case.get("environment_snapshot") or {}).get("execution"),
            capture_live_environment=capture_live_environment,
            include_wave=include_wave,
            weather_forecast=weather_forecast,
            wave_conditions=wave_conditions,
        ),
    }
    environment_snapshot["latest"] = (
        environment_snapshot["execution"]
        or environment_snapshot["decision"]
        or environment_snapshot["planning"]
    )

    case = {
        "maneuver_id": maneuver.get("id"),
        "port_call_id": port_call.get("id"),
        "reference_code": port_call.get("reference_code", ""),
        "vessel_name": port_call.get("vessel_name", ""),
        "maneuver_type": features["maneuver_type"],
        "maneuver_type_label": maneuver.get("type_label") or maneuver.get("action_label") or features["maneuver_type"],
        "current_state": (maneuver.get("state") or "pending").strip().lower(),
        "current_state_label": _state_label(maneuver.get("state") or "pending"),
        "origin_label": features["origin"],
        "destination_label": features["destination"],
        "planned_at": maneuver.get("planned_at"),
        "decided_at": maneuver.get("decided_at"),
        "completed_at": maneuver.get("completed_at"),
        "reported_at": maneuver.get("reported_at"),
        "latest_event_at": _latest_case_event_at(maneuver),
        "case_summary": _case_summary(port_call, maneuver),
        "vessel_snapshot": {
            "name": port_call.get("vessel_name", ""),
            "short_name": port_call.get("ship_short_name_label") or port_call.get("vessel_short_name", ""),
            "imo": port_call.get("vessel_imo", ""),
            "call_sign": port_call.get("vessel_call_sign", ""),
            "flag": port_call.get("vessel_flag", ""),
            "type": port_call.get("ship_type_label") or port_call.get("vessel_type", ""),
            "loa_m": port_call.get("vessel_loa_m", ""),
            "beam_m": port_call.get("vessel_beam_m", ""),
            "gt_t": port_call.get("vessel_gt_t", ""),
            "max_draft_m": port_call.get("vessel_max_draft_m", ""),
            "dwt_t": port_call.get("vessel_dwt_t", ""),
            "bow_thruster": port_call.get("vessel_bow_thruster", "unknown"),
            "stern_thruster": port_call.get("vessel_stern_thruster", "unknown"),
        },
        "scale_snapshot": {
            "port_call_status": port_call.get("status", ""),
            "eta": port_call.get("eta"),
            "ata": port_call.get("ata"),
            "berth": port_call.get("berth", ""),
            "last_port": port_call.get("last_port", ""),
            "next_port": port_call.get("next_port", ""),
            "agent": port_call.get("agent_label", "--"),
            "pilot": port_call.get("pilot_label", "--"),
            "notes": port_call.get("notes", ""),
        },
        "planning_snapshot": {
            "planned_at": maneuver.get("planned_at"),
            "origin": features["origin"],
            "destination": features["destination"],
            "planned_draft_m": maneuver.get("planned_draft_m", ""),
            "tug_count": maneuver.get("tug_count", ""),
            "constraints": list(maneuver.get("constraints") or []),
            "plan_note": maneuver.get("plan_note", ""),
            "plan_observations": maneuver.get("plan_observations", ""),
            "created_by": maneuver.get("agent_label") or maneuver.get("created_by") or "--",
            "created_by_profile": maneuver.get("agent_profile") or maneuver.get("created_by_profile") or {},
            "created_at": maneuver.get("created_at"),
        },
        "decision_snapshot": {
            "decision": "aborted" if (maneuver.get("state") or "").strip().lower() == "aborted" else "approved" if maneuver.get("decided_at") else "",
            "state": maneuver.get("state", ""),
            "approval_note": maneuver.get("approval_note", ""),
            "aborted_reason": maneuver.get("aborted_reason", ""),
            "decided_by": maneuver.get("pilot_label") or maneuver.get("decided_by") or "--",
            "decided_by_profile": maneuver.get("pilot_profile") or maneuver.get("decided_by_profile") or {},
            "decided_at": maneuver.get("decided_at"),
        },
        "execution_snapshot": {
            "completed_at": maneuver.get("completed_at"),
            "execution_started_at": maneuver.get("execution_started_at"),
            "execution_finished_at": maneuver.get("execution_finished_at"),
            "reported_draft_m": maneuver.get("reported_draft_m", ""),
            "report_note": maneuver.get("report_note", ""),
            "reported_by": maneuver.get("reported_by_label") or maneuver.get("reported_by") or "--",
            "reported_by_profile": maneuver.get("reported_by_profile") or {},
            "reported_at": maneuver.get("reported_at"),
        },
        "outcome_snapshot": {
            "state": maneuver.get("state", ""),
            "state_label": _state_label(maneuver.get("state") or "pending"),
            "report_completed": bool((maneuver.get("report_note") or "").strip()),
            "resulting_port_call_status": port_call.get("status", ""),
            "resulting_location": port_call.get("berth", ""),
            "next_port": port_call.get("next_port", ""),
            "decision_flags": _decision_flags(maneuver),
        },
        "environment_snapshot": environment_snapshot,
        "feature_snapshot": features,
        "change_log": list(maneuver.get("change_log") or []),
        "feedback_status": existing_case.get("feedback_status", ""),
        "feedback_note": existing_case.get("feedback_note", ""),
        "feedback_updated_by": existing_case.get("feedback_updated_by", ""),
        "feedback_updated_at": existing_case.get("feedback_updated_at"),
        "created_at": existing_case.get("created_at") or iso_now(),
        "updated_at": iso_now(),
    }
    return case


def decorate_maneuver_case(case: Dict) -> Dict:
    latest_event_at = case.get("latest_event_at")
    return {
        **case,
        "latest_event_label": _local_iso_to_label(latest_event_at),
        "planned_label": _local_iso_to_label(case.get("planned_at")),
        "decided_label": _local_iso_to_label(case.get("decided_at")),
        "completed_label": _local_iso_to_label(case.get("completed_at")),
        "reported_label": _local_iso_to_label(case.get("reported_at")),
        "has_report": bool(((case.get("execution_snapshot") or {}).get("report_note") or "").strip()),
        "feedback_status_label": _feedback_status_label(case.get("feedback_status")),
        "feedback_updated_at_label": _local_iso_to_label(case.get("feedback_updated_at")),
        "has_validated_feedback": bool((case.get("feedback_status") or "").strip()),
        "environment_signature": build_case_environment_signature(case),
    }


def _primary_route_key(clean_type: str, *, origin: str, destination: str, origin_key: str, destination_key: str) -> tuple[str, str, bool]:
    if clean_type == "entry":
        return destination_key, destination, is_known_berth_label(destination)
    if clean_type == "departure":
        return origin_key, origin, is_known_berth_label(origin)
    if clean_type == "shift":
        if destination_key:
            return destination_key, destination, is_known_berth_label(destination)
        return origin_key, origin, is_known_berth_label(origin)
    if destination_key:
        return destination_key, destination, is_known_berth_label(destination)
    return origin_key, origin, is_known_berth_label(origin)


def _case_feedback_rank(value: str) -> int:
    clean = (value or "").strip().lower()
    if clean == "approved":
        return 3
    if clean == "observed":
        return 1
    if clean == "review":
        return -1
    if clean == "avoid":
        return -2
    return 0


def _experience_meta(
    *,
    feedback_status: str,
    route_match_strength: int,
    environment_match_count: int,
) -> tuple[str, str]:
    clean = (feedback_status or "").strip().lower()
    if clean == "approved":
        return "Experiência validada", "online"
    if clean == "observed":
        return "Correlação observada", "neutral"
    if clean == "avoid":
        return "Usar com reserva", "degraded"
    if clean == "review":
        return "Caso em revisão", "degraded"
    if route_match_strength >= 2 and environment_match_count > 0:
        return "Correlação forte", "neutral"
    if route_match_strength >= 1:
        return "Semelhança operacional", "neutral"
    return "Semelhança parcial", "neutral"


def rank_similar_maneuver_cases(
    cases: Iterable[Dict],
    *,
    maneuver_type: str,
    origin: str = "",
    destination: str = "",
    vessel_type: str = "",
    vessel_loa_m: str = "",
    bow_thruster: str = "",
    stern_thruster: str = "",
    tug_count: str = "",
    environment_signature: Optional[Dict] = None,
    strict_route: bool = True,
    limit: int = 5,
) -> List[Dict]:
    clean_type = _normalize_maneuver_type(maneuver_type)
    if not clean_type:
        return []

    origin_key = _case_key(canonicalize_berth_label(origin) or origin)
    destination_key = _case_key(canonicalize_berth_label(destination) or destination)
    vessel_type_key = _case_key(vessel_type)
    bow_key = (bow_thruster or "").strip().lower()
    stern_key = (stern_thruster or "").strip().lower()
    tug_key = _clean_text(tug_count)
    loa_value = _safe_float(vessel_loa_m)
    target_environment = environment_signature or {}
    target_primary_key, _target_primary_label, target_primary_is_berth = _primary_route_key(
        clean_type,
        origin=origin,
        destination=destination,
        origin_key=origin_key,
        destination_key=destination_key,
    )

    ranked: List[Dict] = []
    for raw_case in cases:
        case = decorate_maneuver_case(raw_case)
        if _normalize_maneuver_type(case.get("maneuver_type")) != clean_type:
            continue
        if case.get("current_state") not in {"completed", "aborted"}:
            continue

        features = case.get("feature_snapshot") or {}
        score = 0.0
        reasons: List[str] = []
        environment_match_count = 0

        case_origin_key = features.get("origin_key") or _case_key(features.get("origin"))
        case_destination_key = features.get("destination_key") or _case_key(features.get("destination"))
        case_primary_key, _case_primary_label, _case_primary_is_berth = _primary_route_key(
            clean_type,
            origin=features.get("origin") or case.get("origin_label", ""),
            destination=features.get("destination") or case.get("destination_label", ""),
            origin_key=case_origin_key,
            destination_key=case_destination_key,
        )
        same_origin = bool(origin_key and case_origin_key == origin_key)
        same_destination = bool(destination_key and case_destination_key == destination_key)
        primary_matches = bool(target_primary_key and case_primary_key == target_primary_key)
        route_match_strength = 0

        if strict_route and target_primary_key and target_primary_is_berth and not primary_matches:
            continue

        if same_origin and same_destination and origin_key and destination_key:
            score += 72
            reasons.append("mesma rota operacional")
            route_match_strength = 2
        else:
            if primary_matches:
                score += 42
                reasons.append("mesmo cais operacional" if target_primary_is_berth else "mesmo ponto operacional")
                route_match_strength = 1
            if same_destination and clean_type != "entry":
                score += 18
                reasons.append("mesmo destino")
                route_match_strength = max(route_match_strength, 1)
            if same_origin and clean_type != "departure":
                score += 16
                reasons.append("mesma origem")
                route_match_strength = max(route_match_strength, 1)

        if vessel_type_key and features.get("vessel_type_key") == vessel_type_key:
            score += 18
            reasons.append("mesmo tipo de navio")
        case_loa = _safe_float(features.get("vessel_loa_m"))
        if loa_value is not None and case_loa is not None:
            diff = abs(case_loa - loa_value)
            if diff <= 10:
                score += 12
                reasons.append("LOA muito próxima")
            elif diff <= 25:
                score += 6
                reasons.append("LOA próxima")
        if bow_key and bow_key != "unknown" and features.get("bow_thruster") == bow_key:
            score += 6
            reasons.append("mesmo bow thruster")
        if stern_key and stern_key != "unknown" and features.get("stern_thruster") == stern_key:
            score += 6
            reasons.append("mesmo stern thruster")
        if tug_key and features.get("tug_count") == tug_key:
            score += 5
            reasons.append("mesmos rebocadores")

        case_environment = build_case_environment_signature(case)
        if target_environment.get("wind_band") and case_environment.get("wind_band") == target_environment.get("wind_band"):
            score += 10
            reasons.append("mesma faixa de vento")
            environment_match_count += 1
        if target_environment.get("wind_quadrant") and case_environment.get("wind_quadrant") == target_environment.get("wind_quadrant"):
            score += 4
            reasons.append("mesmo quadrante de vento")
            environment_match_count += 1
        if target_environment.get("wave_sensitive") and target_environment.get("wave_height_band"):
            if case_environment.get("wave_height_band") == target_environment.get("wave_height_band"):
                score += 12
                reasons.append("mesma faixa de ondulação")
                environment_match_count += 1
        if target_environment.get("wave_sensitive") and target_environment.get("wave_period_band"):
            if case_environment.get("wave_period_band") == target_environment.get("wave_period_band"):
                score += 5
                reasons.append("mesmo período de ondulação")
                environment_match_count += 1
        if target_environment.get("wave_sensitive") and target_environment.get("wave_direction_quadrant"):
            if case_environment.get("wave_direction_quadrant") == target_environment.get("wave_direction_quadrant"):
                score += 3
                reasons.append("mesma direção de ondulação")
                environment_match_count += 1

        feedback_status = (case.get("feedback_status") or "").strip().lower()
        if feedback_status == "approved":
            score += 14
            reasons.append("referência validada")
        elif feedback_status == "observed":
            score += 5
            reasons.append("correlação observada")
        elif feedback_status == "review":
            score -= 8
            reasons.append("caso em revisão")
        elif feedback_status == "avoid":
            score -= 16
            reasons.append("caso marcado para evitar")
        if not destination_key and not origin_key and not vessel_type_key and loa_value is None:
            score += 1

        if score <= 0:
            continue

        experience_label, experience_badge = _experience_meta(
            feedback_status=feedback_status,
            route_match_strength=route_match_strength,
            environment_match_count=environment_match_count,
        )

        ranked.append(
            {
                **case,
                "similarity_score": round(score, 1),
                "similarity_reasons": reasons,
                "route_match_strength": route_match_strength,
                "environment_match_count": environment_match_count,
                "experience_label": experience_label,
                "experience_badge": experience_badge,
            }
        )

    ranked.sort(
        key=lambda item: (
            item.get("similarity_score", 0.0),
            _case_feedback_rank(item.get("feedback_status", "")),
            (_parse_iso_datetime(item.get("latest_event_at")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
        ),
        reverse=True,
    )
    return ranked[: max(limit, 0)]
