"""Deterministic operational chat sources and live answers."""

import logging
import math
import re
from datetime import date, datetime, timedelta

from flask import has_request_context, session

from core import services
from core.access_control import filter_port_activity_for_session
from core.chat_planner import (
    CURRENT_WEATHER_RE,
    ChatExecutionPlan,
    WEATHER_TIMELINE_RE,
    build_chat_execution_plan,
)
from core.form_helpers import _local_iso_to_label
from core.maneuver_context import _match_port_call_from_question, build_maneuver_case_context_source
from core.operational_diagnostics import build_operational_diagnostic
from core.operational_common import _operational_lookup_key, current_resolvable_port_calls
from core.rule_catalog import _active_knowledge_dir
from integrations.tide_service import LISBON_TZ
from domain.berth_layout import is_anchorage_berth, slot_berth_options
from domain.berth_profiles import load_berth_profiles
from domain.chat_actions import visible_port_calls_from_activity
from domain.colreg_rules import answer_colreg_interpretation_direct
from domain.cost_engine import UP_NORMAL, UP_SHIFT_ALONG
from domain.lisnave_rules import lisnave_rule_snippet, should_include_lisnave_rule_source
from domain.navigation_basics import answer_navigation_basics_direct, build_navigation_basics_source
from domain.navigation_lights import build_navigation_lights_source
from domain.operational_safety import (
    build_fog_underway_procedure_source,
    build_emergency_response_source,
    build_operational_safety_source,
    build_weather_safety_status_lines,
)
from domain.route_transit import route_transit_answer
from domain.tug_guidance import build_tug_operational_guidance_source

logger = logging.getLogger(__name__)

PORTAL_ACTIVITY_CONTEXT_RE = re.compile(
    r"\b(navio|navios|escala|escalas|planead\w*|previst\w*|programad\w*|"
    r"marcad\w*|arquivo|historico|histórico|eta|etd|recent\w*|ultim\w*|"
    r"em porto|em cais|quadro|ocupad\w*|ocupac\w*|agent\w*|piloto|pilotos)\b"
)
PORTAL_MOVEMENT_CONTEXT_RE = re.compile(
    r"\b(chegada|chegadas|entrada|entradas|saida|saída|saidas|saídas|partida|partidas)\b"
    r".*\b(navio|navios|escala|escalas|previst\w*|planead\w*|recent\w*|ultim\w*|hoje|amanha|amanhã|eta|etd)\b"
    r"|"
    r"\b(navio|navios|escala|escalas|previst\w*|planead\w*|recent\w*|ultim\w*|hoje|amanha|amanhã|eta|etd)\b"
    r".*\b(chegada|chegadas|entrada|entradas|saida|saída|saidas|saídas|partida|partidas)\b"
)
PORTAL_MANEUVER_CONTEXT_RE = re.compile(
    r"\bmanobras?\b.*\b(planead\w*|previst\w*|programad\w*|marcad\w*|"
    r"arquivo|historico|histórico|hoje|amanha|amanhã|ontem)\b"
    r"|"
    r"\b(planead\w*|previst\w*|programad\w*|marcad\w*|arquivo|historico|histórico|"
    r"hoje|amanha|amanhã|ontem)\b.*\bmanobras?\b"
)
DAYLIGHT_QUERY_RE = re.compile(
    r"\b(luz do dia|periodo luminoso|periodos luminosos|periodo de luz|periodos de luz|"
    r"nascer do sol|por do sol|poe se o sol|pôr do sol|daylight)\b"
)
MOON_QUERY_RE = re.compile(r"\b(lua|fase da lua|fase lunar|moon)\b")
WEATHER_FORECAST_TODAY_RE = re.compile(
    r"\b(previsao|previsoes|previsao meteorologica|previsoes meteorologicas|metrologia|metrologica|metereologia|metereologica|prognostico|"
    r"como vai estar|vai estar|meteo)\b.*\b(hoje|resto do dia|proximas horas|próximas horas)\b"
    r"|"
    r"\b(hoje|resto do dia|proximas horas|próximas horas)\b.*\b(previsao|previsoes|prognostico|meteorologia|metrologia|metereologia|meteo|tempo)\b"
)
WEATHER_FORECAST_DAYS_RE = re.compile(
    r"\b(proximos dias|próximos dias|dias seguintes|amanha|amanhã|depois de amanha|depois de amanhã|"
    r"previsao geral|previsões gerais|previsoes gerais)\b"
)
TUG_LIVE_WEATHER_RE = re.compile(
    r"\b(meteorolog\w*|metereolog\w*|metrolog\w*|meteo|condicoes meteorologicas|"
    r"condicoes do tempo|estado do tempo|tempo|atual|atuais|actual|actuais|"
    r"agora|neste momento|previst\w*|previs\w*|proximas horas|próximas horas)\b"
)
LOCAL_WARNING_CODE_RE = re.compile(r"\b(?:anav\s*)?(?:n[.ºo]*\s*)?(\d{1,3}/\d{2,4})\b", re.IGNORECASE)
BERTHED_VESSELS_QUERY_RE = re.compile(
    r"\b(navios?|embarcacoes|embarcações)\b.*\b(em cais|atracad\w*|amarrad\w*)\b"
    r"|"
    r"\b(em cais|atracad\w*|amarrad\w*)\b.*\b(navios?|embarcacoes|embarcações)\b"
)
PLANNED_MANEUVER_SUBJECT_RE = re.compile(
    r"\b(navios?|manobras?|entradas?|saidas?|saídas|partidas?|mudancas?|mudanças)\b"
)
PLANNED_MANEUVER_MARKER_RE = re.compile(
    r"\b(planeamento|planead\w*|previst\w*|programad\w*|agendad\w*|marcad\w*|"
    r"agenda|futur\w*|proxim\w*)\b"
)
VESSEL_DETAIL_QUERY_RE = re.compile(
    r"\b(dados|detalhes|informacao|informação|caracteristicas|características|ficha|perfil)\b"
    r".*\b(navio|embarcacao|embarcação|imo|indicativo|call sign)\b"
    r"|"
    r"\b(navio|embarcacao|embarcação|imo|indicativo|call sign)\b"
    r".*\b(dados|detalhes|informacao|informação|caracteristicas|características|ficha|perfil)\b"
)
OPERATIONAL_FRAGMENT_TERMS_RE = re.compile(
    r"\b(navio|embarcacao|embarcação|reboques?|rebocadores?|fundear|fundeadouro|ferro|"
    r"entrada|saida|saída|atracar|desatracar|manobra)\b",
    re.IGNORECASE,
)
OPERATIONAL_DECISION_TERMS_RE = re.compile(
    r"\b(quantos|quantas|onde|como|quando|qual|quais|pode|posso|devo|deve|"
    r"aconselha|aconselhas|recomenda|recomendas|observa|observacao|observação|"
    r"precisa|necess[aá]rio|suficiente|meter|colocar|posicionar|o que)\b",
    re.IGNORECASE,
)
MANEUVER_APPROVER_QUERY_RE = re.compile(
    r"\b(quem|qual)\b.*\b(aprovou|aprovado|aprovada|validou|validado|validada|validador)\b.*\b(manobra|entrada|saida|saída|mudanca|mudança)\b"
    r"|"
    r"\b(aprovou|validou)\b.*\b(manobra|entrada|saida|saída|mudanca|mudança)\b"
)
AGENT_AGENCY_QUERY_RE = re.compile(
    r"\b(agencia|agência)\b.*\b(agent\w*|trabalha|pertence|qual|que)\b"
    r"|"
    r"\b(agent\w*|trabalha|pertence)\b.*\b(agencia|agência)\b"
)
AGENT_LOOKUP_QUERY_RE = re.compile(r"\b(qual|quem)\b.*\bagente\b|\bagente\b.*\b(navio|escala|manobra)\b")
MANEUVER_TIME_RE = re.compile(
    r"\b(?:as|às|para as|para às|para|pelas)\s*(\d{1,2}(?::\d{2}|h\d{0,2})|\d{3,4})\b"
    r"|\b(\d{1,2}(?::\d{2}|h\d{2}))\b",
    flags=re.IGNORECASE,
)
VESSEL_CATALOG_STATE_KEY = "port_call_vessel_catalog"
VESSEL_CATALOG_DELETED_KEYS_KEY = "deleted_keys"


def _attach_operational_diagnostic(answer: dict | None, question: str) -> dict | None:
    if not answer:
        return answer
    try:
        diagnostic = build_operational_diagnostic(
            question,
            answer=answer,
            knowledge_dir=_active_knowledge_dir() or "knowledge",
        )
    except Exception:
        logger.exception("Falha ao construir diagnostico operacional.")
        diagnostic = {}
    if diagnostic.get("present"):
        answer["operational_diagnostic"] = diagnostic
    return answer


def build_weather_timeline(weather_data: dict | None, max_hours: int = 48) -> list[dict]:
    """Flatten hourly weather groups into a single ordered timeline list up to max_hours entries."""
    if not weather_data:
        return []
    timeline = []
    for group in weather_data.get("hourly_groups", []):
        for hour in group.get("hours", []):
            timeline.append({
                **hour,
                "date": group.get("date", ""),
                "date_label": group.get("date_label") or group.get("date", ""),
                "day_label": group.get("date", ""),
                "slot_label": f"{group.get('date', '')} {hour.get('time', '')}".strip(),
            })
            if len(timeline) >= max_hours:
                return timeline
    return timeline


def build_operational_snapshot_source(port_activity: dict, max_rows: int = 12) -> dict:
    """Build a chat supplemental source summarizing the current planned maneuvers."""
    lines = [
        "Resumo operacional das manobras planeadas e referências do quadro:",
        "- O quadro operacional conta ocupação apenas por slots de cais; fundeadouros são quadro e não ocupam slots.",
        (
            f"- Chegadas previstas: {port_activity['stats']['scheduled_count']} | "
            f"Navios em porto: {port_activity['stats']['in_port_count']} | "
            f"em cais: {port_activity['stats'].get('quay_vessel_count', 0)} | "
            f"em quadro: {port_activity['stats'].get('quadro_count', 0)} | "
            f"slots ocupados: {port_activity['stats'].get('occupied_slot_count', 0)}/"
            f"{port_activity['stats'].get('slot_capacity_count', 0)} | "
            f"Saídas recentes: {port_activity['stats']['departed_count']} | "
            f"Manobras planeadas: {port_activity['stats'].get('planned_count', 0)}"
        ),
    ]
    for item in port_activity.get("planned_maneuvers", [])[:max_rows]:
        maneuver_id = item.get("maneuver_id") or "--"
        lines.append(
            f"- {item['date_label']} | escala {item['reference_code']} | manobra {maneuver_id} | {item['vessel_name']} | "
            f"{item['maneuver_label']} | situação {item['situation_label']} | "
            f"Hora {item['planned_label']} | "
            f"{item['local_origin']} -> {item['local_destination']} | "
            f"agente {_agent_display(item)} | piloto {_pilot_display(item)}"
        )
        if item.get("detail_note"):
            lines.append(f"  observações: {item['detail_note']}")
    return {
        "source_id": "OPS1", "document": "estado_operacional_planeadas",
        "chunk_id": 1, "score": 1.0, "retrieval_mode": "operational_snapshot",
        "snippet": "\n".join(lines),
    }


def _operational_query_terms(question: str) -> list[str]:
    seen = set()
    ordered = []
    for token in re.findall(r"[a-z0-9À-ÿ/.-]+", (question or "").lower()):
        clean = token.strip(".-")
        if len(clean) < 2 or clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return ordered


def _score_operational_text(question: str, text: str) -> int:
    haystack = (text or "").lower()
    score = 0
    for token in _operational_query_terms(question):
        if token in haystack:
            score += 2 if len(token) >= 5 else 1
    return score


def _constraint_labels_from_badges(item: dict) -> str:
    labels = [badge.get("label", "") for badge in item.get("constraint_badges", []) if badge.get("label")]
    return ", ".join(labels) or "--"


def _profile_organization(profile: dict | None) -> str:
    return " ".join(str((profile or {}).get("organization") or "").split())


def _actor_display_from_profile(label: str | None, profile: dict | None, *, include_missing_agency: bool = False) -> str:
    clean_label = " ".join(str(label or "").strip().split()) or "--"
    organization = _profile_organization(profile)
    if organization and clean_label not in {"--", organization}:
        return f"{clean_label} ({organization})"
    if organization:
        return organization
    if include_missing_agency and clean_label != "--":
        return f"{clean_label} (agência não registada)"
    return clean_label


def _agent_display(item: dict, *, include_missing_agency: bool = True) -> str:
    return _actor_display_from_profile(
        item.get("agent_label"),
        item.get("agent_profile"),
        include_missing_agency=include_missing_agency,
    )


def _pilot_display(item: dict, label_key: str = "pilot_label", profile_key: str = "pilot_profile") -> str:
    return _actor_display_from_profile(item.get(label_key), item.get(profile_key), include_missing_agency=False)


def _format_measure(value: object, suffix: str = "") -> str:
    clean = " ".join(str(value if value is not None else "").strip().split())
    if not clean:
        return "--"
    return f"{clean}{suffix}" if suffix and clean != "--" else clean


def _format_weather_slot(hour: dict) -> str:
    return (
        f"{hour.get('time', '--')} | {hour.get('condition', '--')} | "
        f"{hour.get('temp_c', '--')} °C | vento {hour.get('wind_kts', '--')} kts "
        f"{hour.get('wind_dir', '--')} | rajadas {hour.get('gust_kts', '--')} kts | "
        f"chuva {hour.get('chance_of_rain', '--')}%"
    )


def _weather_wind_summary(hours: list[dict]) -> dict:
    wind_values = [float(item["wind_kts"]) for item in hours if item.get("wind_kts") is not None]
    gust_values = [float(item["gust_kts"]) for item in hours if item.get("gust_kts") is not None]
    return {
        "avg_wind_kts": round(sum(wind_values) / len(wind_values), 1) if wind_values else None,
        "max_wind_kts": round(max(wind_values), 1) if wind_values else None,
        "max_gust_kts": round(max(gust_values), 1) if gust_values else None,
    }


def _safe_weather_float(value: object) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _format_weather_kts(value: object) -> str:
    numeric = _safe_weather_float(value)
    if numeric is None:
        return "--"
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.1f}"


def _select_weather_days(forecast: dict, weather_service, question: str, *, default_count: int = 1) -> list[dict]:
    days = list(forecast.get("forecast_days") or [])
    if not days:
        return []
    reference_dt = _parse_weather_reference_datetime(forecast)
    target_dates: list[str] = []
    if reference_dt and hasattr(weather_service, "_resolve_query_dates"):
        try:
            target_dates = list(weather_service._resolve_query_dates(question, reference_dt.date()))
        except Exception:
            target_dates = []
    if target_dates:
        selected = [item for item in days if item.get("date") in target_dates]
        if selected:
            return selected
    return days[:default_count]


def _hours_for_weather_day(forecast: dict, day: dict) -> list[dict]:
    target_date = day.get("date")
    for group in forecast.get("hourly_groups", []) or []:
        if group.get("date") == target_date:
            return list(group.get("hours") or [])
    return []


PT_MONTH_QUERY = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}


def _question_date_parts(question: str) -> list[tuple[int, int, int | None]]:
    clean = str(question or "").lower()
    dates: list[tuple[int, int, int | None]] = []
    for match in re.finditer(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", clean):
        year = int(match.group(3)) if match.group(3) else None
        if year is not None and year < 100:
            year += 2000
        dates.append((int(match.group(1)), int(match.group(2)), year))
    month_pattern = "|".join(PT_MONTH_QUERY)
    for match in re.finditer(rf"\b(\d{{1,2}})\s+de\s+({month_pattern})(?:\s+de\s+(\d{{2,4}}))?\b", clean):
        year = int(match.group(3)) if match.group(3) else None
        if year is not None and year < 100:
            year += 2000
        dates.append((int(match.group(1)), PT_MONTH_QUERY[match.group(2)], year))
    return dates


def _row_datetime(row: dict) -> datetime | None:
    for key in ("actual_value", "completed_at", "planned_value", "date_value", "departure_at", "eta", "ata"):
        value = row.get(key)
        if not value:
            continue
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _row_timestamp(row: dict) -> float:
    row_dt = _row_datetime(row)
    return row_dt.timestamp() if row_dt else 0.0


def _short_maneuver_id(value: object) -> str:
    clean = str(value or "").strip()
    return clean[:8].upper() if clean else "--"


def _arrival_entry_maneuver_id(item: dict) -> str:
    direct_id = str(item.get("maneuver_id") or "").strip()
    if direct_id:
        return direct_id
    entry_maneuvers = [
        maneuver
        for maneuver in item.get("maneuver_history", []) or []
        if str(maneuver.get("type") or "").strip().lower() == "entry"
    ]
    if not entry_maneuvers:
        return ""
    entry_maneuvers.sort(
        key=lambda maneuver: (
            maneuver.get("planned_at")
            or maneuver.get("completed_at")
            or maneuver.get("created_at")
            or "",
            maneuver.get("id") or "",
        )
    )
    return str(entry_maneuvers[-1].get("id") or "").strip()


def _row_matches_question_date(row: dict, date_parts: list[tuple[int, int, int | None]]) -> bool:
    if not date_parts:
        return True
    row_dt = _row_datetime(row)
    if not row_dt:
        return False
    local_dt = row_dt.astimezone()
    return any(
        local_dt.day == day
        and local_dt.month == month
        and (year is None or local_dt.year == year)
        for day, month, year in date_parts
    )


def _vessel_match_score(question: str, item: dict) -> int:
    clean_question = f" {_operational_lookup_key(question)} "
    score = 0
    for key, weight in (("vessel_name", 12), ("vessel_imo", 12), ("vessel_call_sign", 10), ("reference_code", 8)):
        value = _operational_lookup_key(item.get(key))
        if value and f" {value} " in clean_question:
            score += weight
    return score


def _catalog_vessel_key(record: dict) -> str:
    imo = re.sub(r"\D", "", str(record.get("vessel_imo") or ""))
    if imo:
        return f"imo:{imo}"
    name = re.sub(r"\s+", " ", str(record.get("vessel_name") or "").strip()).casefold()
    return f"name:{name}" if name else ""


def _catalog_vessel_rows() -> list[dict]:
    if has_request_context() and (session.get("role") or "").strip().lower() == "agente":
        return []
    if not hasattr(services.store, "get_runtime_state"):
        return []
    try:
        state = services.store.get_runtime_state(VESSEL_CATALOG_STATE_KEY) or {}
    except Exception:
        logger.exception("Falha ao ler catálogo de navios para resposta operacional.")
        return []
    if not isinstance(state, dict):
        return []
    deleted_keys = {
        str(key)
        for key in (state.get(VESSEL_CATALOG_DELETED_KEYS_KEY) or [])
        if str(key).strip()
    }
    rows = []
    for record in state.get("items") or []:
        if not isinstance(record, dict):
            continue
        key = record.get("key") or _catalog_vessel_key(record)
        if not key or key in deleted_keys:
            continue
        rows.append({**record, "key": key, "catalog_only": True})
    return rows


def _find_catalog_vessel(question: str) -> dict | None:
    candidates = []
    for item in _catalog_vessel_rows():
        score = _vessel_match_score(question, item)
        if score:
            candidates.append((score, item.get("updated_at") or item.get("created_at") or "", item))
    candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return candidates[0][2] if candidates else None


def build_vessel_catalog_source(question: str) -> dict | None:
    vessel = _find_catalog_vessel(question)
    if not vessel:
        return None
    lines = [
        "Ficha de navio guardada no catálogo PRAGtico:",
        (
            f"- {vessel.get('vessel_name') or '--'} | IMO {vessel.get('vessel_imo') or '--'} | "
            f"indicativo {vessel.get('vessel_call_sign') or '--'} | bandeira {vessel.get('vessel_flag') or '--'}"
        ),
        (
            f"- Tipo {vessel.get('vessel_type') or '--'} | LOA {vessel.get('vessel_loa_m') or '--'} m | "
            f"boca {vessel.get('vessel_beam_m') or '--'} m | GT {vessel.get('vessel_gt_t') or '--'} | "
            f"DWT {vessel.get('vessel_dwt_t') or '--'} | calado max. {vessel.get('vessel_max_draft_m') or '--'} m"
        ),
        (
            f"- Bow thruster {_format_thruster_label(vessel.get('vessel_bow_thruster'))}; "
            f"stern thruster {_format_thruster_label(vessel.get('vessel_stern_thruster'))}"
        ),
    ]
    if vessel.get("service_rate_profile") or vessel.get("service_notes"):
        lines.append(
            f"- Serviços/taxas: {vessel.get('service_rate_profile') or '--'}; "
            f"base linha regular {vessel.get('regular_line_calls_365d') or '0'}; "
            f"{vessel.get('service_notes') or 'sem notas'}"
        )
    return {
        "source_id": "OPS_VESSEL_CATALOG",
        "document": f"Ficha de navio · {vessel.get('vessel_name') or 'Catálogo'}",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "vessel_catalog",
        "snippet": "\n".join(lines),
        "text": "\n".join(lines),
    }


def _visible_activity(window_days: int = 3650) -> dict:
    return filter_port_activity_for_session(
        services.store.get_port_activity_snapshot(window_days=window_days),
        public_operational=True,
    )


def _visible_port_call_rows(port_activity: dict) -> list[dict]:
    return visible_port_calls_from_activity(port_activity)


def _find_visible_vessel(question: str, port_activity: dict) -> dict | None:
    candidates = []
    for item in _visible_port_call_rows(port_activity):
        score = _vessel_match_score(question, item)
        if score:
            candidates.append((score, item.get("departure_at") or item.get("eta") or item.get("date_value") or "", item))
    candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return candidates[0][2] if candidates else None


def _activity_maneuver_rows(question: str, clean_question: str, port_activity: dict, maneuver_type: str = "") -> list[dict]:
    date_parts = _question_date_parts(question)
    rows = list(port_activity.get("planned_maneuvers") or []) + list(port_activity.get("archived_maneuvers") or [])
    if maneuver_type:
        rows = [item for item in rows if (item.get("maneuver_type") or "").strip().lower() == maneuver_type]
    vessel_scored = [(score, item) for item in rows if (score := _vessel_match_score(question, item))]
    if vessel_scored:
        rows = [item for _, item in vessel_scored]
    if date_parts:
        rows = [item for item in rows if _row_matches_question_date(item, date_parts)]
    if "saida" in clean_question or "saidas" in clean_question or "saída" in question.lower():
        rows = [item for item in rows if (item.get("maneuver_type") or "").strip().lower() == "departure"]
    elif "entrada" in clean_question:
        rows = [item for item in rows if (item.get("maneuver_type") or "").strip().lower() == "entry"]
    elif "mudanca" in clean_question or "mudança" in question.lower():
        rows = [item for item in rows if (item.get("maneuver_type") or "").strip().lower() == "shift"]
    rows.sort(
        key=lambda item: (
            _row_timestamp(item),
            item.get("vessel_name") or "",
        ),
        reverse=True,
    )
    return rows


def _maneuver_route_label(row: dict) -> str:
    origin = row.get("local_origin") or "--"
    destination = row.get("local_destination") or "--"
    return f"{origin} -> {destination}"


def _maneuver_noun_label(row: dict) -> str:
    maneuver_type = (row.get("maneuver_type") or "").strip().lower()
    return {
        "entry": "entrada",
        "departure": "saída",
        "shift": "mudança",
    }.get(maneuver_type, (row.get("maneuver_label") or "manobra").lower())


def build_maneuver_archive_source(question: str, port_activity: dict, max_rows: int = 12) -> dict:
    """Build a chat supplemental source from archived maneuvers ranked by relevance to the question."""
    archive_rows = port_activity.get("archived_maneuvers", [])
    scored_rows = []
    for index, item in enumerate(archive_rows):
        row_text = " | ".join([
            item.get("date_label", ""), item.get("reference_code", ""),
            item.get("vessel_name", ""), item.get("maneuver_label", ""),
            item.get("local_origin", ""), item.get("local_destination", ""),
            item.get("validated_by_label", ""), item.get("executed_by_label", ""),
            item.get("agent_label", ""), item.get("detail_note", ""),
            _constraint_labels_from_badges(item),
        ])
        scored_rows.append((_score_operational_text(question, row_text), index, item))
    scored_rows.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    selected = [item for score, _, item in scored_rows if score > 0][:max_rows]
    if not selected:
        selected = archive_rows[-max_rows:]

    lines = [
        "Arquivo operacional de manobras concluídas:",
        f"- Total no arquivo disponível para consulta: {port_activity['stats'].get('archive_count', 0)}",
    ]
    for item in selected:
        maneuver_id = item.get("maneuver_id") or "--"
        lines.append(
            f"- {item.get('date_label', '--')} | escala {item.get('reference_code', '--')} | manobra {maneuver_id} | {item.get('vessel_name', '--')} | "
            f"{item.get('maneuver_label', '--')} | Hora {item.get('execution_window_label') or item.get('actual_label') or item.get('planned_label') or '--'} | "
            f"{item.get('local_origin', '--')} -> {item.get('local_destination', '--')} | "
            f"agente {_agent_display(item)} | validado por {_pilot_display(item, 'validated_by_label', 'validated_by_profile')} | "
            f"executado por {_pilot_display(item, 'executed_by_label', 'executed_by_profile')} | rebocadores {item.get('tug_count_label', '--')} | "
            f"restrições {_constraint_labels_from_badges(item)}"
        )
        if item.get("detail_note"):
            lines.append(f"  observações: {item['detail_note']}")
    return {
        "source_id": "OPS2", "document": "arquivo_maneuvers_concluidas",
        "chunk_id": 1, "score": 1.0, "retrieval_mode": "operational_archive",
        "snippet": "\n".join(lines),
    }


def build_scale_registry_source(question: str, port_activity: dict, max_rows: int = 12) -> dict:
    """Build a chat supplemental source from the port call registry ranked by relevance to the question."""
    scale_rows = []
    for group_name in ("arrivals", "in_port", "departed", "aborted"):
        for item in port_activity.get(group_name, []):
            scale_rows.append(item)

    deduped = []
    seen_ids = set()
    for item in scale_rows:
        if item.get("id") in seen_ids:
            continue
        seen_ids.add(item.get("id"))
        deduped.append(item)

    scored_rows = []
    for index, item in enumerate(deduped):
        row_text = " | ".join([
            item.get("reference_code", ""), item.get("vessel_name", ""),
            item.get("berth_label", ""), item.get("last_port", ""),
            item.get("next_port", ""), item.get("status", ""),
            item.get("eta_label", ""), item.get("departure_label", ""),
            item.get("agent_label", ""), item.get("pilot_label", ""),
            item.get("notes", ""),
        ])
        scored_rows.append((_score_operational_text(question, row_text), index, item))
    scored_rows.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    selected = [item for score, _, item in scored_rows if score > 0][:max_rows]
    if not selected:
        selected = deduped[:max_rows]

    lines = [
        "Registo de escalas do portal:",
        "- Fundeadouros representam navios em quadro/espera e não contam como slots de cais ocupados.",
        (
            f"- Escalas em porto: {port_activity['stats'].get('in_port_count', 0)} | "
            f"em cais: {port_activity['stats'].get('quay_vessel_count', 0)} | "
            f"em quadro: {port_activity['stats'].get('quadro_count', 0)} | "
            f"slots ocupados: {port_activity['stats'].get('occupied_slot_count', 0)}/"
            f"{port_activity['stats'].get('slot_capacity_count', 0)} | "
            f"chegadas previstas: {port_activity['stats'].get('scheduled_count', 0)} | "
            f"escalas com saída recente: {port_activity['stats'].get('departed_count', 0)}"
        ),
    ]
    for item in selected:
        status_label = (
            "Em quadro" if item.get("status") == "in_port" and is_anchorage_berth(item.get("berth_label"))
            else "Em porto" if item.get("status") == "in_port"
            else "Concluída" if item.get("status") == "departed"
            else "Abortada" if item.get("approval_status") == "aborted"
            else "Prevista"
        )
        lines.append(
            f"- {item.get('reference_code', '--')} | {item.get('vessel_name', '--')} | estado {status_label} | "
            f"ETA {item.get('eta_label', '--')} | cais {item.get('berth_label', '--')} | "
            f"porto anterior {item.get('last_port', '--') or '--'} | próximo destino {item.get('next_port', '--') or '--'} | "
            f"agente {_agent_display(item)} | piloto {_pilot_display(item)} | "
            f"IMO {item.get('vessel_imo') or item.get('ship_imo_label') or '--'} | indicativo {item.get('vessel_call_sign') or item.get('ship_call_sign_label') or '--'}"
        )
        if item.get("notes"):
            lines.append(f"  observações: {item['notes']}")
    return {
        "source_id": "OPS3", "document": "registo_escalas_portal",
        "chunk_id": 1, "score": 1.0, "retrieval_mode": "operational_scales",
        "snippet": "\n".join(lines),
    }


def _looks_like_cost_question(question: str) -> bool:
    clean = (question or "").lower()
    cost_keywords = {
        "custo", "custos", "preço", "preco", "precos", "preços",
        "tarifa", "tarifas", "fatura", "faturação", "faturacao",
        "pilotagem", "taxa", "taxas", "up", "cobrar", "cobrado",
        "pagar", "pagamento", "valor", "estimativa", "orçamento",
        "orcamento", "simulação", "simulacao", "simular",
    }
    return any(kw in clean for kw in cost_keywords)


def build_cost_context_source(question: str, port_activity: dict) -> dict | None:
    """Build a pilotage cost context source if the question appears cost-related, else return None."""
    if not _looks_like_cost_question(question):
        return None
    lines = [
        "Motor de cálculo de custos de pilotagem do Porto de Setúbal (tarifário 2024):",
        f"- UP serviços normais (entrada, saída, atracar): {UP_NORMAL} €/√GT",
        f"- UP mudança ao longo do cais: {UP_SHIFT_ALONG} €/√GT",
        "- Fórmula: Taxa = UP × √GT (raiz quadrada da arqueação bruta, Art. 15º)",
        "- Agravamento +25%: navio sem propulsão ou assistência especial",
        "- Reduções linha regular (Art. 16º): 6-24 escalas -10%, 25-52 -15%, 53-100 -20%, >100 -25%",
        "- Redução -10% cabotagem, -30% escala técnica (só a melhor aplica)",
        "- Pilotagem à ordem: 74.64 €/hora + 25% da taxa base",
        "- Cancelamentos: 30% (2h antes), 50% (1h depois), 100% (no-show), 25% (meteo c/ piloto)",
        "- TUP por tipo: contentores 0.1144/0.0263, RoRo 0.1186/0.0274, passag. 0.0620/0.0263, "
        "tanque/restantes 0.1459/0.0274 (€/GT, 1ºdia/restantes)",
        "- Não inclui rebocadores (privados), amarração, lanchas ou resíduos.",
        "",
    ]
    in_port = port_activity.get("in_port", [])[:3]
    for vessel in in_port:
        gt_str = vessel.get("vessel_gt_t") or vessel.get("vessel_gt") or ""
        gt_clean = gt_str.replace(".", "").replace(",", ".").strip()
        try:
            gt = float(gt_clean)
        except (ValueError, TypeError):
            continue
        if gt <= 0:
            continue
        name = vessel.get("vessel_name", "Navio")
        cost_entry = round(UP_NORMAL * math.sqrt(gt), 2)
        cost_departure = round(UP_NORMAL * math.sqrt(gt), 2)
        lines.append(
            f"- Exemplo {name} (GT {gt:.0f}): entrada ~{cost_entry:.2f}€, "
            f"saída ~{cost_departure:.2f}€, total ~{cost_entry + cost_departure:.2f}€"
        )
    lines.append("")
    lines.append("O utilizador pode pedir estimativas ao bot. Usa a API /api/cost/estimate ou /api/cost/quick para cálculos detalhados.")
    return {
        "source_id": "COST1", "document": "motor_custos_pilotagem",
        "chunk_id": 1, "score": 1.0, "retrieval_mode": "cost_engine",
        "snippet": "\n".join(lines),
    }


def build_berth_catalog_source(question: str) -> dict | None:
    """Build a berth catalog source for terminal/berth questions, with explicit Lisnave aliases."""
    clean = _operational_lookup_key(question)
    if not clean:
        return None
    if not re.search(r"\b(lisnave|doca|cais|fundeadouro|teporset|autoeuropa|sapec|tms)\b", clean):
        return None

    lisnave_berths = [item for item in services.BERTH_OPTIONS if item.startswith("Lisnave - ")]
    berth_slot_count = len(slot_berth_options(services.BERTH_OPTIONS))
    lines = [
        "Catálogo canónico de cais/fundeadouros do portal:",
        f"- O catálogo operacional tem {berth_slot_count} slots de cais/berço/manobra, excluindo fundeadouros.",
        "- TMS 2 conta como 4 posições operacionais: A, B, C e D.",
        "- 'Lisnave' identifica o terminal/estaleiro; para registo operacional usa-se um cais ou doca específicos.",
        "- Aliases Lisnave reconhecidos pelo sistema: 'Doca 21' e 'Doca seca 21' -> 'Lisnave - Doca 21'; 'Cais 2 A', 'Lisnave 2A', 'Cais 2 W' e 'Cais 2 lado Setúbal' são interpretados como 'Lisnave - Cais 2 A'.",
        "- Na Lisnave, a designação operacional mantém sempre A/B. W/E e Setúbal/Alcácer são apenas referências laterais: A = W/oeste; B = E/este.",
        "- D31/D32/D33 são Docas secas Lisnave com acesso por um único Hidrolift/mini eclusa.",
        "- Cais/docas Lisnave disponíveis no sistema:",
    ]
    for item in lisnave_berths:
        lines.append(f"  {item}")
    return {
        "source_id": "OPS4",
        "document": "catalogo_cais_portal",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "berth_catalog",
        "snippet": "\n".join(lines),
    }


def build_lisnave_operational_rule_source(question: str) -> dict | None:
    """Expose high-confidence Lisnave manoeuvre rules as structured operational context."""
    if not should_include_lisnave_rule_source(question):
        return None
    return {
        "source_id": "OPS5",
        "document": "regras_operacionais_lisnave",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "operational_rule",
        "snippet": lisnave_rule_snippet(),
    }


SOURCE_COVERAGE_QUERY_RE = re.compile(
    r"\b(fonte|fontes|documento|base|cobre|cobrem|inclui|incluem|conhecimento|indexavel|indexável|incorporad\w*)\b",
    re.IGNORECASE,
)


def _direct_source(document: str, source_id: str, snippet: str, retrieval_mode: str = "operational_rule") -> dict:
    return {
        "document": document,
        "source_id": source_id,
        "chunk_id": 0,
        "score": 1.0,
        "retrieval_mode": retrieval_mode,
        "snippet": snippet,
        "text": snippet,
    }


def _answer_spring_tide_definition_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\b(mare\s+viva|mares\s+vivas|mar[eé]\s+viva|mar[eé]s\s+vivas)\b", question, re.IGNORECASE):
        return None
    if not re.search(r"\b(quando|considera|criterio|critério|define|definicao|definição|o que e|o que é)\b", clean_question):
        return None
    answer = (
        "Considera-se maré viva operacional em Setúbal quando, no mesmo ciclo/dia de referência, "
        "a baixa-mar é inferior a 1,0 m e a preia-mar é superior a 3,0 m.\n"
        "- Critério prático: BM < 1,0 m e PM > 3,0 m.\n"
        "- Isto não substitui a tabela de marés do dia; serve para classificar a amplitude antes de aplicar regras de cais, corrente, calado e reponto."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("Condicoes_Meteorologicas_Prioridades.txt", "SPRING_TIDE_SETUBAL_DEFINITION", answer, "operational_tide_rule")],
        "answer_origin": "operational_tide_rule",
    }


def _extract_length_m(question: str) -> float | None:
    text = str(question or "").lower().replace(",", ".")
    patterns = (
        r"\b(?:loa|comprimento|navio|ro-?ro|roro|graneleiro)\D{0,80}?(\d{2,3}(?:\.\d+)?)\s*m\b",
        r"\b(\d{2,3}(?:\.\d+)?)\s*m(?:etros?)?\s*(?:de\s+)?(?:loa|comprimento)\b",
        r"\b(?:com|de)\s+(\d{2,3}(?:\.\d+)?)\s*m\b",
        r"\b(\d{2,3}(?:\.\d+)?)\s*m(?:etros?)?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _answer_source_coverage_direct(question: str, clean_question: str) -> dict | None:
    history_context_query = bool(
        re.search(r"\b(historia|história|historico|histórico|cultura|cultural|setubal|setúbal)\b", question or "", re.IGNORECASE)
        and re.search(r"\b(contexto|sem\s+misturar|regras?\s+tecnicas?|regras?\s+técnicas?)\b", question or "", re.IGNORECASE)
    )
    if not SOURCE_COVERAGE_QUERY_RE.search(question or "") and not history_context_query:
        return None
    if re.search(r"\b(colreg|rieam|anti[-\s]?colis[aã]o|abalroamento|visibilidade\s+reduzida)\b", question, re.IGNORECASE):
        return answer_colreg_interpretation_direct(question)
    if re.search(r"\b(luzes?|balizagem|balizas?|boias?|b[oó]ias?|farol|far[oó]is|enfiamento|iala)\b", question, re.IGNORECASE):
        return _answer_navigation_lights_direct(question, clean_question)
    if re.search(r"\b(shiphandling|manobra\s+pratica|manobra\s+prática|bow\s*thruster|squat|efeito\s+de\s+margem)\b", question, re.IGNORECASE):
        answer = (
            "Sim. A fonte indexavel de shiphandling prático está disponível em Shiphandling_Pratico.txt.\n"
            "- Cobre pivot point, uso de reboques/rebocadores, Bow thruster, squat, efeito de margem, vento, corrente e aproximação ao cais.\n"
            "- Inclui regra prática 1 a proa + 1 a popa, prontidão para largar ferro e comunicações VHF 73 quando aplicável.\n"
            "- Deve ser usada como apoio prático; regras locais de cais, maré e Piloto Coordenador continuam a prevalecer."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("Shiphandling_Pratico.txt", "SOURCE_SHIPHANDLING_PRACTICAL", answer, "source_coverage")],
            "answer_origin": "source_coverage",
        }
    if re.search(r"\b(historia|história|cultura|setubal|setúbal|moura|troia|tróia|sado)\b", question, re.IGNORECASE):
        answer = (
            "Sim. A fonte indexavel de história e cultura de Setúbal está disponível em Historia_Cultura_Setubal.txt.\n"
            "- Cobre referências históricas e culturais locais, incluindo Cetobriga, Forte de Sao Filipe, Outao, Sado, Troia/Tróia e choco frito.\n"
            "- Nao deve ser usado para validar manobras nem para substituir regras operacionais, meteorologia, marés ou condicionantes de cais."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("Historia_Cultura_Setubal.txt", "SOURCE_HISTORY_CULTURE_SETUBAL", answer, "source_coverage")],
            "answer_origin": "source_coverage",
        }
    return None


def _answer_alstom_direct(question: str, clean_question: str) -> dict | None:
    if "alstom" not in clean_question:
        return None
    if not re.search(r"\b(vento|kts?|nos|n[oó]s|pode|avancar|avançar|entrad|atrac|reponto|barra|quando|hora|regras?)\b", question, re.IGNORECASE):
        return None
    wind_kts = _extract_wind_kts_from_question(question)
    blocked = wind_kts is not None and wind_kts >= 15
    status = (
        f"Não deve avançar: vento {wind_kts:g} kt atinge/excede o limite prático da ALSTOM."
        if blocked
        else "Só deve avançar se o vento no canal for inferior a 15 kt e as restantes condições estiverem cumpridas."
    )
    answer = (
        f"{status}\n"
        "Fonte: vento indicado na pergunta do utilizador.\n"
        "Local: ALSTOM.\n"
        "Regras críticas IT-038_Alstom.txt:\n"
        "- Navios atracam apenas por estibordo.\n"
        "- Manobra apenas de dia e no reponto de preia-mar; não usar baixa-mar como reponto operacional.\n"
        "- Desde a Barra, marcar 1h30 antes da preia-mar para chegar ao cais no reponto.\n"
        "- Trânsito/manobra apenas com vento inferior a 15 kt; vento que atinge/excede o limite prático bloqueia a manobra.\n"
        "- Confirmar LOA máximo 120 m e calado aplicável pela regra LOA/calado."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("IT-038_Alstom.txt", "ALSTOM_WIND_REPONTO_RULE", answer)],
        "answer_origin": "alstom_operational_rule",
    }


def _answer_barra_draft_direct(question: str, clean_question: str) -> dict | None:
    if "barra" not in clean_question:
        return None
    if not re.search(r"\b(calado|calados|maximo|máximo|draft)\b", question, re.IGNORECASE):
        return None
    answer = (
        "Na barra do Porto de Setúbal há duas referências que não devem ser confundidas:\n"
        "- Calado máximo absoluto: 12,0 m.\n"
        "- Calado operacional pela barra: 10,30 m + altura da maré no momento da entrada/saída, limitado ao máximo absoluto de 12,0 m.\n"
        "- Esta referência pressupõe ondulação inferior a 1 m; com ondulação superior, deve haver validação operacional conservadora."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("Calados_Barra_Setubal.txt", "BARRA_MAX_DRAFT_RULE", answer)],
        "answer_origin": "barra_draft_rule",
    }


def _answer_visibility_threshold_direct(question: str, clean_question: str) -> dict | None:
    if "visibilidade" not in clean_question and "visibility" not in clean_question:
        return None
    if not re.search(r"\b(1[,.]0|1\s*km|reduzida|nevoeiro|bot|trata|limite|limiar|threshold|referencia|referência)\b", question, re.IGNORECASE):
        return None
    value_match = re.search(r"\b(\d+(?:[,.]\d+)?)\s*km\b", question, flags=re.IGNORECASE)
    value_note = ""
    if value_match:
        try:
            value = float(value_match.group(1).replace(",", "."))
            if value <= 1.0:
                value_note = f"- {value_match.group(1)} km está abaixo ou no limiar técnico de 1,0 km.\n"
            else:
                value_note = f"- {value_match.group(1)} km está acima do limiar técnico de 1,0 km, sem bloquear automaticamente só por este valor.\n"
        except ValueError:
            value_note = ""
    answer = (
        "Sim. Para segurança operacional do porto, a referência fog_visibility_km_reference é 1.0 km.\n"
        f"{value_note}"
        "- Se o live feed indicar visibilidade 1,0 km ou inferior, o bot deve tratar como visibilidade operacional reduzida.\n"
        "- Com visibilidade operacional reduzida/nevoeiro em porto, a regra é suspender manobras e só retomar com visibilidade restaurada.\n"
        "- Se um navio já estiver a navegar, aplicar velocidade de segurança, vigia reforçada e sinais de visibilidade reduzida."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("operational_safety_limits.json", "FOG_VISIBILITY_THRESHOLD", answer, "operational_safety")],
        "answer_origin": "operational_safety",
    }


def _answer_tup_formula_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\btup\b|taxa\s+de\s+uso|tarifa", clean_question):
        return None
    if not re.search(r"\b(formula|fórmula|calcular|calculo|cálculo|contentores|navio)\b", question, re.IGNORECASE):
        return None
    answer = (
        "Fórmula TUP para navio de contentores:\n"
        "- TUP = GT x UP/taxa aplicável por período, conforme o tarifário em vigor.\n"
        "- Contentores: 0,1144 €/GT no primeiro período/dia e 0,0263 €/GT nos períodos/dias seguintes.\n"
        f"- Referências internas disponíveis: UP normal {UP_NORMAL} e UP de mudança ao longo do cais {UP_SHIFT_ALONG}.\n"
        "- Para fechar o valor real faltam GT do navio, tipo exato de escala/serviço, isenções/descontos e eventuais serviços adicionais."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("Tarifario_APSS_TUP.txt", "TUP_CONTAINER_FORMULA", answer, "cost_formula")],
        "answer_origin": "cost_formula",
    }


def _answer_tms1_defenses_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\btms\s*1\b|\btms1\b|fontainhas", clean_question):
        return None
    if not re.search(r"\b(defensa|defensas|yokohama|borracha)\b", question, re.IGNORECASE):
        return None
    answer = "No TMS 1 são utilizadas defensas de borracha do tipo Yokohama."
    return {
        "answer": answer,
        "sources": [_direct_source("IT-005_TMS1.txt", "TMS1_DEFENSES_YOKOHAMA", answer)],
        "answer_origin": "berth_profile_fact",
    }


def _berth_profile_by_id(profile_id: str) -> dict:
    try:
        profiles = load_berth_profiles(_active_knowledge_dir() or "knowledge")
    except Exception:
        logger.exception("Falha ao carregar perfis de cais para resposta direta.")
        return {}
    for profile in profiles:
        if str(profile.get("id") or "").strip().lower() == profile_id:
            return profile
    return {}


def _format_rule_number(value: object, default: int | float) -> str:
    raw_value = default if value is None or value == "" else value
    try:
        number = float(str(raw_value).replace(",", "."))
    except (TypeError, ValueError):
        number = float(default)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}".replace(".", ",")


def _answer_tms1_large_vessel_capacity_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\btms\s*1\b|\btms1\b|fontainhas", clean_question):
        return None
    if not re.search(r"\bnavios?\b.*\bgrandes?\b|\bgrandes?\b.*\bnavios?\b", clean_question):
        return None
    if not re.search(r"\b(quantos?|maxim[oa]s?|limite|podem?|cabem|atracad\w*|amarrad\w*|ao mesmo tempo|simultaneamente)\b", clean_question):
        return None

    profile = _berth_profile_by_id("tms1")
    rules = profile.get("berth_capacity_rules") if isinstance(profile, dict) else {}
    rules = rules if isinstance(rules, dict) else {}
    max_large = _format_rule_number(rules.get("max_large_vessels_alongside"), 3)
    main_front_max = _format_rule_number(rules.get("main_front_max_large_vessels"), 2)
    main_front_length = _format_rule_number(rules.get("main_front_available_length_m"), 605)
    cais8_max = _format_rule_number(rules.get("cais8_max_loa_m"), 215)
    large_loa = _format_rule_number(rules.get("large_vessel_loa_m"), 200)
    clearance = _format_rule_number(rules.get("shared_clearance_m"), 30)
    large_rule = str(rules.get("large_vessel_rule") or "").strip()
    slot_lengths = rules.get("slot_lengths_m") if isinstance(rules.get("slot_lengths_m"), dict) else {}
    slot_summary_parts = []
    for label in ("TMS 1 - Cais 4", "TMS 1 - Cais 5", "TMS 1 - Cais 6", "TMS 1 - Cais 7", "TMS 1 - Cais 8"):
        value = slot_lengths.get(label)
        if value is not None:
            slot_summary_parts.append(f"{label.replace('TMS 1 - ', '')}: {_format_rule_number(value, 0)} m")
    slot_summary = "; ".join(slot_summary_parts) if slot_summary_parts else "Cais 4 a 8 conforme IT-005"

    answer = (
        f"No TMS 1, a regra operacional é no máximo {max_large} navios grandes ao cais ao mesmo tempo.\n"
        f"- Para esta regra, o perfil do cais considera navio grande a partir de cerca de {large_loa} m de LOA.\n"
        f"- Distribuição máxima: {main_front_max} navios grandes na frente principal do TMS 1 mais 1 navio no Cais 8.\n"
        f"- Comprimentos do IT-005: {slot_summary}.\n"
        f"- Frente principal contínua: Cais 4 a 7, com {main_front_length} m no total; o navio pode ocupar cais adjacentes se o LOA exceder o cais atribuído.\n"
        f"- Cais 8: máximo físico de {cais8_max} m.\n"
        f"- Quando navios partilham a frente principal do TMS 1, manter pelo menos {clearance} m de separação para cruzar cabos e evitar contacto.\n"
        "- O Cais 8 faz parte do TMS 1, mas é isolado: não serve como continuação do Cais 7.\n"
        "- O TMS 1 já não tem Cais 3 no modelo operacional; os cais são 4, 5, 6, 7 e 8.\n\n"
        "Isto é uma regra de capacidade do TMS 1, não a lista de navios atualmente em porto."
    )
    snippet = large_rule or answer
    return {
        "answer": answer,
        "sources": [_direct_source("berth_profiles.json", "TMS1_LARGE_VESSEL_CAPACITY", snippet, "berth_capacity_rule")],
        "answer_origin": "berth_capacity_rule",
    }


def _answer_tms2_capacity_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\btms\s*2\b|\btms2\b", clean_question):
        return None
    if not re.search(r"\b(posicoes?|posições?|posicao|posição|adjacent\w*|ocupar|ocupa|cais|slot|slots|comprimento|maior|logica|lógica)\b", question, re.IGNORECASE):
        return None

    profile = _berth_profile_by_id("tms2")
    rules = profile.get("berth_capacity_rules") if isinstance(profile, dict) else {}
    rules = rules if isinstance(rules, dict) else {}
    slot_labels = [str(item) for item in (rules.get("slot_labels") or []) if str(item).strip()]
    slot_names = ", ".join(label.replace("TMS 2 - ", "") for label in slot_labels) or "Posição A, Posição B, Posição C e Posição D"
    total_length = _format_rule_number(rules.get("total_length_m"), 723)
    slot_length = _format_rule_number(float(str(rules.get("total_length_m") or 723).replace(",", ".")) / 4, 180.75)
    clearance = _format_rule_number(rules.get("shared_clearance_m"), 30)

    if re.search(r"\b(quantas?|numero|número)\b.*\b(posicoes?|posições?|posicao|posição|slots?)\b|\b(posicoes?|posições?|posicao|posição|slots?)\b.*\b(quantas?|numero|número)\b", clean_question):
        answer = (
            f"O TMS 2 tem 4 posições no modelo atual: {slot_names}.\n"
            f"- O IT-006 trata o terminal como uma frente contínua de {total_length} m entre os cabeços 40 e 63.\n"
            f"- No portal, essas 4 posições são referências operacionais de cerca de {slot_length} m cada, não cais físicos separados."
        )
    else:
        answer = (
            "Sim. No TMS 2 a lógica de comprimento é semelhante à frente principal do TMS 1: se o navio for maior do que a posição atribuída, pode ocupar posições adjacentes desde que haja espaço livre.\n"
            f"- Frente contínua TMS 2: {total_length} m, representada no portal por 4 posições: {slot_names}.\n"
            f"- Quando houver mais do que um navio no TMS 2, manter pelo menos {clearance} m entre navios para cruzar cabos e evitar contacto, além da leitura formal do IT-006 sobre intervalos proporcionais ao comprimento.\n"
            "- O TMS 2 não continua para o Cais 8/TMS 1; o Cais 8 é isolado e não serve para prolongar uma ocupação do TMS 2.\n"
            "- Se as posições adjacentes necessárias estiverem ocupadas, a validação deve bloquear por falta de comprimento disponível."
        )
    return {
        "answer": answer,
        "sources": [_direct_source("berth_profiles.json / IT-006_TMS2.txt", "TMS2_CAPACITY_POSITIONS", answer, "berth_capacity_rule")],
        "answer_origin": "berth_capacity_rule",
    }


def _answer_operational_priority_direct(question: str, clean_question: str) -> dict | None:
    priority_terms = re.search(r"\b(prioridade|priorizo|priorizar|ordem geral|ordem operacional|quem priorizo|quem passa primeiro|fila)\b", clean_question)
    cargo_list_terms = re.search(r"\b(passageiros?|gado vivo|animais vivos|reefers?|perecivel|perecível|ro-?ro|roro|contentores?|outros)\b", question, re.IGNORECASE)
    if not priority_terms and not (cargo_list_terms and re.search(r"\bordem\b", clean_question)):
        return None
    if re.search(r"\b(mesmo\s+cais|mesmo\s+destino|mesma\s+acostagem|destino\s+de\s+acostagem)\b", clean_question):
        answer = (
            "Quando dois navios disputam o mesmo cais/destino de acostagem, aplica-se primeiro a regra P-13: "
            "tem prioridade o primeiro navio a cruzar o arco de 8 milhas náuticas centrado na Baliza número 2 da Barra, "
            "salvo impedimento técnico ou decisão operacional justificada.\n"
            "- Esta é a referência de chegada em frente da barra para prioridade de acostagem.\n"
            "- A ordem passageiros/gado/reefers/Ro-Ro/contentores e saídas > mudanças > entradas fica para planeamento geral, janelas concorrentes ou desempate operacional quando a regra do mesmo cais não resolver tudo."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("P-13_PlaneamentoGestao.txt", "P13_SAME_BERTH_PRIORITY_8NM", answer, "operational_priority")],
            "answer_origin": "operational_priority",
        }
    answer = (
        "A ordem operacional é esta:\n"
        "1. Manobras com reponto de maré ou janela crítica de profundidade.\n"
        "2. Passageiros, animais vivos/gado vivo, reefers e carga perecível.\n"
        "3. Ro-Ro.\n"
        "4. Contentores.\n"
        "5. Outros navios.\n"
        "Dentro da mesma prioridade e da mesma janela operacional, aplicar saídas > mudanças > entradas."
    )
    if re.search(r"\bpassageiros?\b", clean_question) and re.search(r"\bentrada normal|carga geral|normal\b", clean_question):
        answer += (
            "\n\nNo caso que descreves, eu priorizo o navio de passageiros, salvo se a entrada de carga geral tiver uma janela crítica de maré/profundidade que se perca nesse reponto."
        )
    return {
        "answer": answer,
        "sources": [_direct_source("Condicoes_Meteorologicas_Prioridades.txt", "OPERATIONAL_MANEUVER_PRIORITIES", answer, "operational_priority")],
        "answer_origin": "operational_priority",
    }


def _answer_setubal_culture_direct(question: str, clean_question: str) -> dict | None:
    if re.search(r"\b(distancia|distância|milhas?|quanto tempo|demora|rumo|canal|boia|bóia|pilar|percurso|rota)\b", question, re.IGNORECASE):
        return None
    explicit_culture = re.search(
        r"\b(forte\s+(?:do|de|da)|fortaleza|outao|outão|sao filipe|são filipe|cetobriga|choco|roazes|golfinhos|orcas|tubaroes|tubarões|animais|biodiversidade|curiosidade|historia|história|cultura)\b",
        question,
        re.IGNORECASE,
    )
    if not explicit_culture:
        return None
    if re.search(r"\b(manobra|manobrar|calado|rebocador|reboque|reponto|vento|cais|atracar|desatracar|validar)\b", clean_question) and not re.search(r"\b(curiosidade|historia|história|cultura|sem misturar|forte\s+(?:do|de|da)|fortaleza|outao|outão)\b", question, re.IGNORECASE):
        return None
    if re.search(r"\boutao|outão\b", question, re.IGNORECASE):
        answer = (
            "O Outão é um ponto estratégico da entrada da barra do Sado. A fortificação começou como atalaia de 1390, ganhou importância militar na defesa da foz, recebeu farol em 1880 e acabou ligada à saúde como sanatório e depois Hospital Ortopédico de Sant'Iago do Outão."
        )
    elif re.search(r"\broazes|golfinhos|animais|biodiversidade|orcas|tubaroes|tubarões\b", question, re.IGNORECASE):
        answer = (
            "No Sado destacam-se os roazes-corvineiros, a comunidade residente de golfinhos costeiros/estuarinos. A zona também tem aves de sapal, peixes, moluscos, crustáceos, macroalgas e fauna da Arrábida; orcas e tubarões-martelo são ocorrências ocasionais no largo/costa, não residentes do estuário."
        )
    elif re.search(r"\bchoco\b", question, re.IGNORECASE):
        answer = (
            "O choco frito é uma das imagens de marca de Setúbal: choco panado/frito, normalmente com batata frita e salada, muito ligado à identidade marítima e gastronómica da cidade."
        )
    else:
        answer = (
            "Setúbal é uma cidade de mar e rio: Sado, Arrábida, porto, pesca, sal, indústria e gastronomia ligada ao peixe, ao choco e ao marisco. Historicamente cruza Cetóbriga romana, Ordem de Santiago, Forte de São Filipe e Outão."
        )
    return {
        "answer": answer,
        "sources": [_direct_source("Setubal_Historia_Cultura.txt", "SETUBAL_LOCAL_CULTURE", answer, "local_culture")],
        "answer_origin": "local_culture",
    }


def _answer_lisnave_face_side_direct(question: str, clean_question: str) -> dict | None:
    if "lisnave" not in clean_question and "mitrena" not in clean_question:
        return None
    if not re.search(r"\bface\s*b\b|\blado\b", clean_question):
        return None
    answer = (
        "Na LISNAVE, a face B corresponde ao lado E/este, referência Alcácer do Sal. "
        "A face A corresponde ao lado W/oeste, referência Setúbal."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("IT-014_Lisnave.txt", "LISNAVE_FACE_B_SIDE", answer)],
        "answer_origin": "berth_profile_fact",
    }


def _answer_lisnave_doca21_depth_direct(question: str, clean_question: str) -> dict | None:
    if "doca 21" not in clean_question and "d21" not in clean_question:
        return None
    if not re.search(r"\b(profundidade|sonda|soleira|comporta|aberta|fechada|calado)\b", question, re.IGNORECASE):
        return None
    answer = (
        "Doca 21 / LISNAVE, soleira ao ZH:\n"
        "- Com comporta aberta: 6,10 metros ao ZH (20 pés).\n"
        "- Com comporta fechada: 5,49 metros ao ZH (18 pés).\n"
        "- Para calado praticável, somar a altura de água e aplicar margem operacional; não tratar como calado único global da LISNAVE."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("IT-014_Lisnave.txt", "LISNAVE_DOCA21_THRESHOLD_DEPTH", answer)],
        "answer_origin": "berth_profile_fact",
    }


def _answer_lisnave_doca_tug_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\b(reboque|reboques|rebocador|rebocadores)\b", clean_question):
        return None
    if not re.search(r"\bdoca\s*2[012]\b|\bd2[012]\b", clean_question):
        return None
    if not re.search(r"\b(quantos|necessarios|necessários|usar|entrar|entrada|manobra|manobrar|tem de|deve)\b", clean_question):
        return None
    loa = _extract_length_m(question)
    if loa is not None and loa > 250:
        loa_label = f"{loa:g}".replace(".", ",")
        answer = (
            "Recomendo 6 rebocadores.\n"
            f"Regra prática aplicável: Lisnave acima de 250 m: 6 rebocadores. LOA indicado: {loa_label} m.\n"
            "Para docas Lisnave, nunca descer abaixo do mínimo de 4 rebocadores, mas o escalão por comprimento agrava para 6 neste caso.\n"
            "Confirmar doca/cais concreto, reponto, vento, calado e validação do Piloto Coordenador."
        )
    else:
        answer = (
            "Para entrada em doca Lisnave, usar pelo menos 4 rebocadores.\n"
            "Se o navio tiver LOA acima de 250 m, a regra prática sobe para 6 rebocadores.\n"
            "Confirmar doca/cais concreto, reponto, vento, calado e validação do Piloto Coordenador."
        )
    return {
        "answer": answer,
        "sources": [_direct_source("tug_operational_guidance.json", "LISNAVE_DOCA_TUG_COUNT", answer, "operational_tug_guidance")],
        "answer_origin": "operational_tug_guidance",
    }


def _answer_lisnave_night_length_direct(question: str, clean_question: str) -> dict | None:
    if "lisnave" not in clean_question and "mitrena" not in clean_question:
        return None
    if not re.search(r"\b(noite|noturn|loa|comprimento|metros?|maximo|máximo|pode|manobrar)\b", question, re.IGNORECASE):
        return None
    if not re.search(r"\b(noite|noturn)\b", clean_question):
        return None
    loa = _extract_length_m(question)
    if loa is not None and loa <= 280:
        loa_label = f"{loa:g}".replace(".", ",")
        answer = (
            f"Sim. Na LISNAVE, LOA até 280 metros pode manobrar de dia e de noite, desde que seja no reponto de maré. "
            f"O navio indicado tem {loa_label} m, portanto fica dentro do limite noturno."
        )
    elif loa is not None:
        loa_label = f"{loa:g}".replace(".", ",")
        answer = (
            f"Não. Na LISNAVE, {loa_label} m é superior a 280 metros; acima desse limite a manobra fica limitada ao período diurno. "
            "Ou seja, só de dia / no período diurno; mesmo assim, a manobra deve ser no reponto de maré e validada para o cais/doca concreto."
        )
    else:
        answer = (
            "Na LISNAVE, o comprimento máximo para manobra de noite é 280 metros de LOA. "
            "Até esse limite pode manobrar de dia e de noite; acima de 280 metros, a manobra fica limitada ao período diurno. "
            "Em ambos os casos, usar reponto de maré."
        )
    return {
        "answer": answer,
        "sources": [_direct_source("IT-014_Lisnave.txt", "LISNAVE_NIGHT_LOA_LIMIT", answer)],
        "answer_origin": "berth_profile_fact",
    }


def _answer_lisnave_profile_direct(question: str, clean_question: str) -> dict | None:
    if "lisnave" not in clean_question and "mitrena" not in clean_question:
        return None
    if re.search(r"\b(reboque|reboques|rebocador|rebocadores)\b", clean_question):
        return None

    if re.search(r"\b(porque|por\s+que|razao|razão)\b.*\breponto\b|\breponto\b.*\b(porque|por\s+que|razao|razão)\b", question, re.IGNORECASE):
        answer = (
            "Na LISNAVE, as manobras devem ser feitas nos repontos de mare porque os cais estão dispostos perpendicularmente à corrente de maré. "
            "No reponto a corrente nula ou praticamente nula reduz o esforço lateral no navio e dá controlo para atracar, largar ou entrar em doca."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("IT-014_Lisnave.txt", "LISNAVE_REPONTO_REASON", answer)],
            "answer_origin": "berth_profile_fact",
        }

    if re.search(r"\b(dois|2)\b.*\bnavios?\b.*\b(grandes?|simultaneo|simultaneamente|mesmo tempo)\b", clean_question):
        answer = (
            "Na LISNAVE, em marés vivas, quando estão em causa navios com LOA superior a 200 metros, "
            "a orientação operacional é uma manobra por reponto. "
            "Assim, dois navios grandes não devem ser tratados como rotina para manobrar ao mesmo tempo no mesmo reponto; "
            "só com validação expressa do Piloto Coordenador para a bacia, cais/docas, maré, vento e meios disponíveis."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("IT-014_Lisnave.txt", "LISNAVE_ONE_LARGE_MANEUVER_PER_REPONTO", answer)],
            "answer_origin": "berth_profile_fact",
        }

    if re.search(r"\b(maior|mais)\b.*\b(profundidade|sonda)\b|\b(profundidade|sonda)\b.*\b(maior|mais)\b", clean_question):
        answer = (
            "Na LISNAVE, o cais com maior profundidade disponível indicada no IT-014 é o Cais 3 B, "
            "com sonda de referência 8,60 m ao ZH a 10 m da face."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("IT-014_Lisnave.txt", "LISNAVE_DEEPEST_QUAY", answer)],
            "answer_origin": "berth_profile_fact",
        }

    if not re.search(r"\b(regras?|limites?|perfil|docas?|cais|calado|orientacao|orientação)\b", question, re.IGNORECASE):
        return None
    answer = (
        "LISNAVE / Estaleiros Mitrena (IT-014_Lisnave.txt):\n"
        "- Todas as manobras devem ser feitas proximo dos repontos de mare.\n"
        "- Noite: permitida apenas até 280 m de LOA; acima de 280 m, só período diurno e sempre junto ao reponto.\n"
        "- Calado: não existe um valor único para toda a LISNAVE; depende do cais/doca, sonda ao ZH, maré e margem operacional.\n"
        "- Orientação: D20/D21/D22 ficam com proa a norte; cais Lisnave e D31/D32/D33 via Hidrolift ficam com proa a sul.\n"
        "- Pontes-Cais: faces 1 A, 1 B, 2 A, 2 B, 3 A e 3 B; W/E e Setúbal/Alcácer são referências laterais, não substituem A/B."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("IT-014_Lisnave.txt", "LISNAVE_PROFILE_DIRECT", answer)],
        "answer_origin": "berth_profile_fact",
    }


def _answer_lisnave_dimensions_direct(question: str, clean_question: str) -> dict | None:
    if re.search(r"\b(reboque|reboques|rebocador|rebocadores)\b", clean_question):
        return None
    if "lisnave" not in clean_question and "mitrena" not in clean_question and not re.search(r"\bcais\s+3\s*[ab]\b|\bdoca\s+2[012]\b|\bd3[123]\b", clean_question):
        return None
    if not re.search(r"\b(comprimento|metros?|cabe|cabem|duque|duques|d'alba|dalba|faces?|pontes?-?cais|cais\s+[123]|doca\s+2[012]|d3[123]|hidrolift|sonda)\b", question, re.IGNORECASE):
        return None
    loa = _extract_length_m(question)
    lines = ["Comprimentos/dimensões críticas da LISNAVE (IT-014_Lisnave.txt):"]
    if re.search(r"\bcais\s*3\s*a\b|3a", clean_question):
        lines.append("- Cais 3 A: 240 m de ponte-cais + 115 m até ao Duque d'Alba = 366 metros de comprimento operacional; sonda 7,0 m ao ZH a 10 m da face.")
        if loa is not None:
            if loa <= 366:
                lines.append(f"- Um navio de {loa:g} m fica dentro da referência de comprimento operacional do Cais 3 A, mas ainda exige validação de calado, reponto, amarração, vento e Piloto Coordenador.")
            else:
                lines.append(f"- Um navio de {loa:g} m excede os 366 metros de comprimento operacional do Cais 3 A.")
    elif re.search(r"\bcais\s*3\s*b\b|3b", clean_question):
        lines.append("- Cais 3 B: 134 m de ponte-cais + 115 m até ao Duque d'Alba = 259 metros de comprimento operacional; sonda 8,60 m ao ZH a 10 m da face.")
    elif re.search(r"\bcais\s*1\b", clean_question):
        lines.append("- Cais 1: comprimento operacional total 260 metros; Cais 1 A/W/Oeste/Setúbal tem sonda 7,14 m ao ZH e Cais 1 B/E/Este/Alcácer tem sonda 7,40 m ao ZH.")
        lines.append("- Faces Pontes-Cais 1, 2 e 3: 1 A, 1 B, 2 A, 2 B, 3 A e 3 B.")
    elif re.search(r"\bcais\s*2\b", clean_question):
        lines.append("- Cais 2: comprimento operacional total 276 metros; Cais 2 A e Cais 2 B têm sonda de referência 7,0 m ao ZH.")
    elif re.search(r"\bdoca\s*20\b|\bd20\b", clean_question):
        lines.append("- Doca 20: comprimento 420 metros; orientação operacional proa a norte.")
    elif re.search(r"\bdoca\s*21\b|\bd21\b", clean_question):
        lines.append("- Doca 21: comprimento 450 metros; soleira 6,10 metros ao ZH com comporta aberta e 5,49 metros ao ZH com comporta fechada; orientação proa a norte.")
    elif re.search(r"\bdoca\s*22\b|\bd22\b", clean_question):
        lines.append("- Doca 22: comprimento 350 metros; aplica a mesma lógica operacional de doca seca da Doca 21; orientação proa a norte.")
    elif re.search(r"\bd3[123]\b|doca\s*3[123]|hidrolift", clean_question):
        lines.append("- D31/D32/D33 via Hidrolift: boca maxima 32 m no acesso; orientação operacional proa a sul.")
    else:
        lines.extend(
            [
                "- Cais 1: 260 metros de comprimento operacional.",
                "- Cais 2: 276 metros de comprimento operacional.",
                "- Cais 3 A: 240 m + 115 m até ao Duque d'Alba = 366 metros de comprimento operacional.",
                "- Cais 3 B: 134 m + 115 m até ao Duque d'Alba = 259 metros de comprimento operacional.",
                "- Faces Pontes-Cais 1, 2 e 3: 1 A, 1 B, 2 A, 2 B, 3 A e 3 B.",
                "- Doca 20: 420 m; Doca 21: 450 m; Doca 22: 350 m.",
                "- D31/D32/D33 via Hidrolift: boca maxima 32 m.",
            ]
        )
    lines.append("Nota: o site pode usar slots para o quadro, mas a resposta operacional não deve ignorar os duques d'alba nem reduzir tudo ao comprimento físico da ponte-cais.")
    answer = "\n".join(lines)
    return {
        "answer": answer,
        "sources": [_direct_source("IT-014_Lisnave.txt", "LISNAVE_BERTH_LENGTHS_DOLPHINS", answer)],
        "answer_origin": "berth_profile_fact",
    }


def _answer_cross_reponto_scheduling_direct(question: str, clean_question: str) -> dict | None:
    if "lisnave" not in clean_question or "tanquisado" not in clean_question:
        return None
    if not re.search(r"\b(quando|marco|marcar|saida|saída|entrada|reponto)\b", clean_question):
        return None
    answer = (
        "Para uma saída da Doca 22 da LISNAVE e uma entrada para Tanquisado, trata como duas manobras dependentes de reponto:\n"
        "- Saída Doca 22 / LISNAVE: marcar 2h antes da preia-mar; D20/D21/D22 ficam com proa a norte e a fase crítica deve cair na janela de reponto/maior água.\n"
        "- Entrada Tanquisado: marcar para chegar ao cais no reponto de maré; aplicar IT-010_Tanquisado.txt, calado e meios no momento.\n"
        "- Se forem no mesmo ciclo, deixa margem de trânsito entre bacias/canais e confirma com o Piloto Coordenador a ordem das manobras."
    )
    return {
        "answer": answer,
        "sources": [
            _direct_source("IT-014_Lisnave.txt", "LISNAVE_REPONTO_SCHEDULING", answer),
            _direct_source("IT-010_Tanquisado.txt", "TANQUISADO_REPONTO_SCHEDULING", answer),
        ],
        "answer_origin": "berth_profile_fact",
    }


def _extract_draft_m_from_question(question: str) -> float | None:
    text = str(question or "").lower().replace(",", ".")
    draft_list_match = re.search(r"\bcalados?\b(.{0,80})", text, flags=re.IGNORECASE)
    if draft_list_match:
        values = [
            float(match.group(1))
            for match in re.finditer(r"\b(\d{1,2}(?:\.\d+)?)\b", draft_list_match.group(1))
            if float(match.group(1)) <= 25
        ]
        if values:
            return max(values)
    patterns = (
        r"\b(?:calado|draft)\D{0,30}?(\d{1,2}(?:\.\d+)?)\s*m\b",
        r"\b(\d{1,2}(?:\.\d+)?)\s*m(?:etros?)?\s*(?:de\s+)?(?:calado|draft)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return None
    return None


def _time_minus_label_from_question(question: str, minutes: int) -> tuple[str, str]:
    parsed = _parse_maneuver_time(question)
    if not parsed:
        return "o reponto pretendido", f"{minutes} min antes"
    hour, minute, time_label = parsed
    total = (hour * 60 + minute - minutes) % (24 * 60)
    return time_label, f"{total // 60:02d}:{total % 60:02d}"


def _time_range_before_label_from_question(question: str, min_minutes: int, max_minutes: int) -> tuple[str, str]:
    parsed = _parse_maneuver_time(question)
    if not parsed:
        return "a preia-mar/reponto pretendido", f"{min_minutes}-{max_minutes} min antes"
    hour, minute, time_label = parsed
    total = hour * 60 + minute
    early = (total - max_minutes) % (24 * 60)
    late = (total - min_minutes) % (24 * 60)
    return time_label, f"{early // 60:02d}:{early % 60:02d}-{late // 60:02d}:{late % 60:02d}"


def _answer_tide_scheduling_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\b(reponto|preia|baixa|mare|antecedencia|antecedência|marca\w*|marco|quando|hora|entrada|saida|saída|sair|atracar|desatracar)\b", clean_question):
        return None
    wind_kts = _extract_wind_kts_from_question(question)
    if wind_kts is not None and wind_kts > 25:
        return None

    draft = _extract_draft_m_from_question(question)
    high_draft = draft is not None and draft >= 9
    mentions_high_draft = high_draft or re.search(r"\b(grande\s+calado|calado\s+alto|calado\s+condicionante|proximo\s+do\s+maximo|próximo\s+do\s+máximo)\b", clean_question)
    explicit_departure = bool(re.search(r"\b(saida|saída|sair|largada|desatracar|desatracacao|desatracação)\b", clean_question))
    explicit_entry = bool(re.search(r"\b(entrada|entrar|atracar|atracacao|atracação)\b", clean_question))
    is_departure = explicit_departure
    is_entry = explicit_entry or not is_departure

    if re.search(r"\b(tms\s*1|tms1|tms\s*2|tms2)\b", clean_question) and mentions_high_draft:
        if is_departure:
            reponto_label, mark_label = _time_minus_label_from_question(question, 30)
            answer = (
                "🌕 Para TMS 1/TMS 2 com navio de grande calado, a saída deve ser marcada cerca de 30 min antes do reponto.\n"
                f"Se o reponto de referência é {reponto_label}, marca por volta das {mark_label}.\n"
                "A ideia é apanhar a água parada/maior margem na fase crítica de largada e passagem inicial. "
                "Confirmar calado real, altura de água, vento, corrente, ocupação do cais e rebocadores."
            )
        elif is_entry:
            reponto_label, range_label = _time_range_before_label_from_question(question, 60, 90)
            draft_note = f" Para calado {_format_measure(draft, ' m')}, eu puxava para o lado conservador da janela." if draft is not None else ""
            answer = (
                "🌕 Para TMS 1/TMS 2 com calado entre 9 m e 12 m, a entrada deve ser marcada 1h a 1h30 antes da preia-mar.\n"
                f"Se a preia-mar de referência é {reponto_label}, a janela prática é {range_label}.{draft_note}\n"
                "A validação final deve cruzar altura de maré, calado praticável, vento/rajadas, corrente, LOA/ocupação de slots e rebocadores."
            )
        else:
            return None
        return {
            "answer": answer,
            "sources": [_direct_source("Marcar_manobra_repontos_mare.txt", "TMS_HIGH_DRAFT_TIDE_SCHEDULING", answer, "operational_tide_scheduling")],
            "answer_origin": "operational_tide_scheduling",
        }

    if (
        "lisnave" in clean_question
        and re.search(r"\bfora\s+da\s+barra\b|\bbarra\b|\bentrada\s+da\s+barra\b", clean_question)
        and is_entry
        and re.search(r"\b(antecedencia|antecedência|marca\w*|marco|requisit\w*)\b", clean_question)
    ):
        reponto_label, mark_label = _time_minus_label_from_question(question, 120)
        if mark_label.endswith("min antes"):
            timing_line = "Sem hora concreta indicada, usa cerca de 2 horas antes do reponto pretendido."
        else:
            timing_line = f"Se o reponto de referência é {reponto_label}, a marcação deve ficar por volta das {mark_label}."
        answer = (
            "🌕 Para uma entrada de fora da Barra para a LISNAVE/Mitrena, marca 2 horas antes do reponto de maré pretendido.\n"
            f"{timing_line}\n"
            "Esta é uma regra de marcação para chegar ao estaleiro na fase crítica de corrente nula; depois confirma cais/doca concreta, calado, LOA, vento, rebocadores e validação do Piloto Coordenador."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("Marcar_manobra_repontos_mare.txt / IT-014_Lisnave.txt", "LISNAVE_BARRA_ENTRY_TIDE_SCHEDULING", answer, "operational_tide_scheduling")],
            "answer_origin": "operational_tide_scheduling",
        }

    if "sapec" in clean_question and mentions_high_draft:
        is_tgl = bool(re.search(r"\b(liquidos|líquidos|tgl)\b", clean_question))
        if is_departure:
            reponto_label, mark_label = _time_minus_label_from_question(question, 30)
            timing = (
                f"Para saída SAPEC com grande calado, marcar cerca de 30 min antes do reponto; "
                f"se o reponto é {reponto_label}, marca por volta das {mark_label}."
            )
        elif is_entry:
            reponto_label, mark_label = _time_minus_label_from_question(question, 90)
            terminal = "TGL/SAPEC Líquidos" if is_tgl else "TPS/SAPEC Sólidos"
            timing = (
                f"Para entrada {terminal} com calado condicionante, marcar cerca de 1h30 antes da preia-mar; "
                f"se a preia-mar é {reponto_label}, marca por volta das {mark_label}."
            )
        else:
            return None
        tgl_note = (
            " No TGL, distinguir carga IMO e não-IMO: referência 9,5 m para IMO e 10,0 m para não-IMO, sempre com fórmula de calado praticável e altura de água."
            if is_tgl or re.search(r"\b(imo|nao\s+imo|não\s+imo)\b", clean_question)
            else " Confirmar se é TPS ou TGL; no TGL, a carga IMO/não-IMO muda o limite de referência."
        )
        answer = (
            f"🌕 {timing}\n"
            f"{tgl_note}\n"
            "Antes de aprovar, cruzar calado real, altura de maré, vento/ondulação, LOA, defensas/regras SAPEC e rebocadores."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("IT-029_SAPEC.txt / Marcar_manobra_repontos_mare.txt", "SAPEC_HIGH_DRAFT_TIDE_SCHEDULING", answer, "operational_tide_scheduling")],
            "answer_origin": "operational_tide_scheduling",
        }

    if re.search(r"\b(teporset|tepor\s*set|termitrena)\b", clean_question):
        if not explicit_entry and not explicit_departure and re.search(r"\b(reponto|mare|preia|baixa|acertar)\b", clean_question):
            answer = (
                "🌕 Sim, para Teporset/Termitrena deves tratar a maré como condicionante quando o calado ou a janela de maior água forem críticos.\n"
                "- Entrada de fora da Barra: marcar cerca de 2 h antes do reponto/preia-mar.\n"
                "- Entrada do Fundeadouro Norte: marcar cerca de 1 h 30 min antes.\n"
                "- Entrada de Tróia/Fundeadouro Sul: marcar cerca de 1 h antes.\n"
                "- Saída: marcar cerca de 15 min antes do reponto; na Teporset, considerar que o reponto local acontece cerca de 15 min depois da hora nominal.\n"
                "Confirmar sempre calado praticável, altura de água, vento, corrente, rebocadores e margem junto ao baixo entre Termitrena e Teporset."
            )
        elif is_departure:
            reponto_label, mark_label = _time_minus_label_from_question(question, 15)
            answer = (
                "🌕 Para saída Teporset/Termitrena, marca cerca de 15 min antes do reponto.\n"
                f"Se o reponto de referência é {reponto_label}, marca por volta das {mark_label}. "
                "Na Teporset, considerar que o reponto local acontece cerca de 15 min depois da hora de referência, deixando margem prática em torno da água parada.\n"
                "Confirmar calado, vento, corrente, rebocadores e janela de segurança."
            )
        elif is_entry:
            if "fundeadouro norte" in clean_question:
                lead = 90
                origin = "do Fundeadouro Norte"
            elif re.search(r"\b(troia|tróia|fundeadouro\s+sul)\b", clean_question):
                lead = 60
                origin = "de Tróia/Fundeadouro Sul"
            else:
                lead = 120
                origin = "da Barra/fora da Barra"
            reponto_label, mark_label = _time_minus_label_from_question(question, lead)
            answer = (
                f"🌕 Para entrada Teporset/Termitrena com calado condicionante {origin}, marca cerca de {lead} min antes do reponto/preia-mar.\n"
                f"Se o reponto de referência é {reponto_label}, marca por volta das {mark_label}.\n"
                "Confirmar calado praticável, altura de água, vento, corrente, rebocadores e se a janela de maior água é suficiente."
            )
        else:
            return None
        return {
            "answer": answer,
            "sources": [_direct_source("Marcar_manobra_repontos_mare.txt", "TEPORSET_TERMITRENA_TIDE_SCHEDULING", answer, "operational_tide_scheduling")],
            "answer_origin": "operational_tide_scheduling",
        }

    if "lisnave" in clean_question and re.search(r"\b(doca\s*21|doca\s*22|d21|d22)\b", clean_question) and is_departure:
        reponto_label, mark_label = _time_minus_label_from_question(question, 120)
        answer = (
            "🌕 Para saída das Docas 21/22 da Lisnave, marca 2h antes da preia-mar.\n"
            f"Se a preia-mar é {reponto_label}, a marcação deve ficar por volta das {mark_label}.\n"
            "Esta regra é específica das docas secas; confirmar LOA, calado, vento, rebocadores, orientação e validação do Piloto Coordenador."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("Marcar_manobra_repontos_mare.txt / IT-014_Lisnave.txt", "LISNAVE_DOCKS_21_22_DEPARTURE_TIDE_SCHEDULING", answer, "operational_tide_scheduling")],
            "answer_origin": "operational_tide_scheduling",
        }

    return None


def _answer_sapec_non_imo_followup_direct(question: str, clean_question: str) -> dict | None:
    if "carga nao imo" not in clean_question and "carga não imo" not in str(question or "").lower():
        return None
    answer = (
        "Ficha de contexto provável\n"
        "- Premissa de continuidade: SAPEC / TPS-TGL.\n"
        "- Calado: 9,2 m.\n"
        "- Carga: não IMO.\n"
        "- Para carga não IMO na SAPEC, confirmar o terminal concreto e aplicar a fórmula de calado praticável com a altura de água no momento, respeitando o limite absoluto/documental aplicável.\n"
        "- Se o contexto anterior não era SAPEC, confirma o cais/terminal antes de validar."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("IT-029_SAPEC.txt", "SAPEC_NON_IMO_CONTEXT_FOLLOWUP", answer)],
        "answer_origin": "operational_context_followup",
    }


def _answer_tanquisado_dimensions_direct(question: str, clean_question: str) -> dict | None:
    if "tanquisado" not in clean_question:
        return None
    if not re.search(r"\b(comprimento|metros?|cabe|cabem|duque|duques|d'alba|dalba|slot|fisico|físico|limite|limites|regra|regras|restri[cç][oõ]es?|calado|noite|noturn)\b", question, re.IGNORECASE):
        return None
    loa = _extract_length_m(question)
    lines = [
        "Tanquisado (IT-010_Tanquisado.txt):",
        "- Comprimento operacional total: 463 m.",
        "- Esse valor inclui cais físico de 75 m e dois duques d'alba; não deve ser avaliado só pelo slot ou pelo comprimento físico do cais.",
        "- Calado diurno praticavel: 6,3 m + altura da mare no momento, limitado a 9,5 m.",
        "- As manobras devem ser planeadas nos repontos de mare; calado máximo absoluto 9,5 m, regime noturno e validação do Piloto Coordenador.",
        "- Saida fora de reponto apenas em vazante quando a preia-mar precedente tiver altura <= 3 m.",
    ]
    if loa is not None:
        if loa <= 463:
            lines.append(f"- Em comprimento operacional, um navio de {loa:g} m cabe na referência dos 463 m; isso não dispensa validação de calado, maré, amarração, vento e rebocadores.")
        else:
            lines.append(f"- Um navio de {loa:g} m excede a referência operacional de 463 m.")
    answer = "\n".join(lines)
    return {
        "answer": answer,
        "sources": [_direct_source("IT-010_Tanquisado.txt", "TANQUISADO_LENGTH_DOLPHINS", answer)],
        "answer_origin": "berth_profile_fact",
    }


def _answer_eco_oil_limits_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\beco\s*oil\b|\becooil\b|\becoil\b", clean_question):
        return None
    if not re.search(r"\b(comprimento|metros?|cabe|cabem|duque|duques|d'alba|dalba|slot|noite|noturn|limite|regra|regras)\b", question, re.IGNORECASE):
        return None
    answer = (
        "Eco-Oil (IT-008_EcoOil.txt):\n"
        "- A base estruturada atual não fixa o comprimento físico/duques d'alba como limite principal; por isso não devo reduzir a resposta a um slot de cais.\n"
        "- Calado maximo absoluto estacionado: 7,5 m; calado maximo para manobra em preia-mar: 7,0 m; em baixa-mar: 5,5 m.\n"
        "- As manobras devem ser planeadas proximo dos repontos de mare, com corrente minima.\n"
        "- Atracação diurna em preia-mar: sem limite documental de comprimento.\n"
        "- Atracação/desatracação diurna em baixa-mar: até 250 m, ou até 255 m se a baixa-mar for >= 0,9 m.\n"
        "- Atracacao noturna proibida em qualquer condição.\n"
        "- Desatracação noturna só em preia-mar e até 255 m de LOA.\n"
        "- Se quiseres que o bot responda também pelo comprimento físico com duques d'alba, esse dado deve ficar explícito na base Eco-Oil."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("IT-008_EcoOil.txt", "ECO_OIL_LENGTH_LIMITS", answer)],
        "answer_origin": "berth_profile_fact",
    }


def _answer_lisnave_checklist_direct(question: str, clean_question: str) -> dict | None:
    if "checklist" not in clean_question:
        return None
    if re.search(r"\beco\s*oil\b|\becooil\b|\becoil\b", clean_question):
        answer = (
            "Sim. Para uma entrada Eco-Oil, a checklist deve puxar IT-008_EcoOil.txt: Calado maximo, repontos de mare, defensas, limites dia/noite e validação do Piloto Coordenador. "
            "Ponto crítico: Atracacao noturna proibida."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("IT-008_EcoOil.txt", "CHECKLIST_ECO_OIL_RULES", answer, "checklist_rule")],
            "answer_origin": "checklist_rule",
        }
    if "tanquisado" in clean_question:
        answer = (
            "Sim. Para Tanquisado, a checklist deve puxar IT-010_Tanquisado.txt: calado maximo absoluto 9,5 m, reponto de maré, regime noturno, defensas e validação do Piloto Coordenador. "
            "Saida fora de reponto: só admitir a regra documental de vazante quando a preia-mar precedente tiver altura <= 3 m."
        )
        return {
            "answer": answer,
            "sources": [_direct_source("IT-010_Tanquisado.txt", "CHECKLIST_TANQUISADO_RULES", answer, "checklist_rule")],
            "answer_origin": "checklist_rule",
        }
    if "lisnave" in clean_question or re.search(r"\bdoca\s*2[012]\b|\bd3[123]\b|hidrolift", clean_question):
        if re.search(r"\bd3[123]\b|doca\s*3[123]|hidrolift", clean_question):
            answer = (
                "Sim. A checklist distingue D31, D32 e D33 das docas 20/21/22: D31/D32/D33 usam Hidrolift, têm boca maxima 32 m e ficam com proa a sul. "
                "As docas 20/21/22 são docas secas com orientação proa a norte."
            )
        elif re.search(r"\bcais\b.*\bdoca\b|\bdistingue\b", clean_question):
            answer = (
                "Sim. A checklist deve distinguir cais LISNAVE de doca seca. Orientação Lisnave: cais e D31/D32/D33 via Hidrolift ficam com proa a sul; "
                "D20/D21/D22 ficam com proa a norte. A regra de proa a norte não se aplica às docas/plataformas Hidrolift nem aos cais."
            )
        elif re.search(r"\b3\s+rebocadores|tres\s+rebocadores|tr[eê]s\s+rebocadores", question, re.IGNORECASE):
            answer = (
                "Sim. Para Lisnave - doca, a checklist deve avisar quando só houver 3 rebocadores: entradas em docas Lisnave exigem pelo menos 4 rebocadores. "
                "Nas docas 20/21/22 confirmar também orientação proa a norte, reponto de maré e Piloto Coordenador."
            )
        else:
            answer = (
                "Sim. Para LISNAVE, a checklist deve puxar IT-014_Lisnave.txt: reponto obrigatório, limite noturno de 280 m, calado por cais/doca, orientação, rebocadores e validação do Piloto Coordenador."
            )
        return {
            "answer": answer,
            "sources": [_direct_source("IT-014_Lisnave.txt", "CHECKLIST_LISNAVE_RULES", answer, "checklist_rule")],
            "answer_origin": "checklist_rule",
        }
    return None


def _extract_hidrolift_beam_m(question: str) -> float | None:
    text = str(question or "").lower().replace(",", ".")
    patterns = (
        r"\b(\d+(?:\.\d+)?)\s*m(?:etros?)?\s+de\s+(?:boca|largura)\b",
        r"\b(?:boca|largura)\s*(?:de|=|:)?\s*(\d+(?:\.\d+)?)\s*m\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _answer_lisnave_hidrolift_hard_limit(question: str, clean_question: str) -> dict | None:
    mentions_hidrolift = "hidrolift" in clean_question
    mentions_lisnave_hidrolift_area = any(
        token in clean_question
        for token in ("doca 31", "doca 32", "doca 33", "d31", "d32", "d33")
    )
    mentions_lisnave_eclusa = "eclusa" in clean_question and (
        "lisnave" in clean_question or "doca" in clean_question or "mitrena" in clean_question
    )
    if not (mentions_hidrolift or mentions_lisnave_hidrolift_area or mentions_lisnave_eclusa):
        return None
    beam_m = _extract_hidrolift_beam_m(question)
    if beam_m is None or beam_m <= 32:
        return None

    beam_label = f"{beam_m:g}".replace(".", ",")
    answer = (
        "Não. Há um bloqueio dimensional antes de discutir a hora da manobra: "
        f"o Hidrolift/Docas 31-33 da LISNAVE admite boca máxima de 32 m e o navio indicado tem {beam_label} m de boca. "
        "Assim, a manobra não deve seguir para o Hidrolift como está marcada; será preciso escolher outro cais/doca ou obter validação operacional específica.\n\n"
        f"Boca: {beam_label} m. Limite documental: boca maxima 32 m.\n\n"
        "O que ainda deve ser confirmado:\n"
        "- Calado: o acesso ao Hidrolift tem sonda de 5,5 m ao ZH, somando a altura de água disponível e margem de segurança.\n"
        "- Meios: entradas em docas Lisnave exigem pelo menos 4 rebocadores.\n"
        "- Hora: marcar 2 h antes do reponto/preia-mar para um navio que vem de fora da Barra pode estar coerente, mas não resolve a incompatibilidade da boca."
    )
    return {
        "answer": answer,
        "sources": [
            {
                "document": "regras_operacionais_lisnave",
                "source_id": "OPS_HIDROLIFT_LIMIT",
                "chunk_id": 1,
                "score": 1.0,
                "retrieval_mode": "operational_rule",
                "snippet": (
                    "Hidrolift/Docas 31-33: boca máxima admissível 32 m; "
                    "sonda de acesso 5,5 m ao ZH; docas Lisnave exigem pelo menos 4 rebocadores."
                ),
            }
        ],
        "answer_origin": "operational_rule",
    }


def _parse_maneuver_time(question: str) -> tuple[int, int, str] | None:
    value = ""
    for match in MANEUVER_TIME_RE.finditer(str(question or "")):
        value = next((group for group in match.groups() if group), "")
    clean_value = value.strip().lower()
    if not clean_value:
        return None
    if re.fullmatch(r"\d{3,4}", clean_value):
        digits = clean_value.zfill(4)
        hour = int(digits[:2])
        minute = int(digits[2:])
    else:
        clean_value = clean_value.replace("h", ":")
        if clean_value.endswith(":"):
            clean_value += "00"
        parts = clean_value.split(":", 1)
        if len(parts) != 2:
            return None
        hour = int(parts[0])
        minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute, f"{hour:02d}:{minute:02d}"


def _planned_datetime_for_question(question: str, hour: int, minute: int, tide_service) -> datetime | None:
    target_dates: list[date] = []
    try:
        if tide_service and hasattr(tide_service, "resolve_query_dates"):
            target_dates = list(tide_service.resolve_query_dates(question))
    except Exception:
        logger.exception("Falha ao resolver data de maré para hora de manobra.")
    if not target_dates:
        target_dates = [datetime.now(LISBON_TZ).date()]
    target_date = target_dates[0]
    return datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=LISBON_TZ)


def _nearest_tide_event(planned_dt: datetime, tide_service):
    if not tide_service or not hasattr(tide_service, "events_for_date"):
        return None
    events = []
    try:
        for offset in (-1, 0, 1):
            events.extend(tide_service.events_for_date((planned_dt + timedelta(days=offset)).date()))
    except Exception:
        logger.exception("Falha ao obter marés para validação horária SECIL.")
        return None
    if not events:
        return None
    return min(events, key=lambda item: abs((item.timestamp - planned_dt).total_seconds()))


def _is_operational_spring_tide(tide_event, tide_service) -> bool | None:
    if not tide_event or not tide_service or not hasattr(tide_service, "events_for_date"):
        return None
    try:
        day_events = tide_service.events_for_date(tide_event.date_value)
    except Exception:
        return None
    high_tides = [item.height for item in day_events if getattr(item, "tide_type", "") == "preia-mar"]
    low_tides = [item.height for item in day_events if getattr(item, "tide_type", "") == "baixa-mar"]
    if not high_tides or not low_tides:
        return None
    return max(high_tides) > 3.0 and min(low_tides) < 1.0


def _format_tide_context(tide_event) -> str:
    if not tide_event:
        return ""
    return (
        f"{tide_event.tide_type} às {tide_event.timestamp.strftime('%H:%M')} "
        f"({tide_event.height:.1f} m)"
    )


def _answer_secil_confirmation_direct(question: str, clean_question: str) -> dict | None:
    if "secil" not in clean_question:
        return None
    if not re.search(r"\b(o que|que|quais|qual)\b.*\b(precis\w*|falta\w*)\b.*\b(confirmar|validar|saber)\b", clean_question):
        return None
    answer = (
        "Para validar uma entrada na SECIL Este às 19:25, eu preciso confirmar:\n"
        "- hora do reponto de maré aplicável e se a chegada ao cais fica dentro da janela certa;\n"
        "- proveniência do navio: Barra/Fundeadouro Norte usa 30-45 min antes do reponto; Tróia/outro cais usa 45 min a 1 h antes;\n"
        "- se é maré viva, porque na SECIL E a atracação deve ficar junto do reponto em marés vivas;\n"
        "- LOA, calado, cais Este/Oeste, vento/corrente e validação do Piloto Coordenador.\n"
        "Com esses dados, dá para dizer se as 19:25 estão bem marcadas ou se convém ajustar."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("IT-009_Secil.txt / Marcar_manobra_repontos_mare.txt", "SECIL_ENTRY_CONFIRMATION_FIELDS", answer, "secil_entry_timing")],
        "answer_origin": "secil_entry_timing",
    }


def _answer_secil_entry_timing_direct(question: str, clean_question: str) -> dict | None:
    if "secil" not in clean_question:
        return None
    if not re.search(r"\b(e|este|east|cais e|cais este|cais b)\b", clean_question):
        return None
    if not re.search(r"\b(entrada|entrar|atracar|atracacao|atracação|marquei|marcada|marcar)\b", clean_question):
        return None
    if not re.search(r"\b(hora|horario|horário|corret|correct|certo|certa|marquei|marcada|marcar)\b", clean_question):
        return None
    parsed_time = _parse_maneuver_time(question)
    if not parsed_time:
        return None

    hour, minute, time_label = parsed_time
    tide_service = getattr(services, "tide_service", None)
    planned_dt = _planned_datetime_for_question(question, hour, minute, tide_service)
    tide_event = _nearest_tide_event(planned_dt, tide_service) if planned_dt else None
    tide_context = _format_tide_context(tide_event)
    spring_tide = _is_operational_spring_tide(tide_event, tide_service)

    if tide_event and planned_dt:
        signed_minutes = int(round((tide_event.timestamp - planned_dt).total_seconds() / 60))
        abs_minutes = abs(signed_minutes)
        if signed_minutes >= 0:
            if 30 <= signed_minutes <= 45:
                conclusion = (
                    f"Sim, a marcação das {time_label} está alinhada com a prática para entrada na SECIL E "
                    f"vinda de fora da Barra ou do Fundeadouro Norte: fica {signed_minutes} min antes do reponto, "
                    f"dentro da janela 30-45 min "
                    f"({tide_context})."
                )
            elif 45 < signed_minutes <= 60:
                conclusion = (
                    f"Sim, a marcação das {time_label} fica {signed_minutes} min antes do reponto ({tide_context}), "
                    "o que encaixa melhor na janela 45 min a 1 h para uma entrada vinda de Tróia ou de outro cais."
                )
            elif signed_minutes < 30:
                conclusion = (
                    f"Eu ajustava: {time_label} fica só {signed_minutes} min antes do reponto ({tide_context}). "
                    "Para entrada na Secil, a prática é marcar 30-45 min antes se vier de fora da Barra/Fundeadouro Norte, "
                    "ou 45 min a 1 h se vier de Tróia/outro cais."
                )
            else:
                conclusion = (
                    f"Eu ajustava: {time_label} fica {signed_minutes} min antes do reponto ({tide_context}), "
                    "mais cedo do que a prática normal de entrada para a Secil."
                )
        else:
            conclusion = (
                f"Não. {time_label} fica {abs_minutes} min depois do reponto mais próximo ({tide_context}). "
                "Para entrada na Secil E, em especial se forem marés vivas, a referência deve ser chegar ao cais junto do reponto."
            )
    else:
        conclusion = (
            f"Não valido a hora só por não haver proibição noturna no Cais de Este. Para entrada na Secil E, "
            f"a marcação das {time_label} tem de ser cruzada com o reponto de maré: 30-45 min antes se vier "
            "de fora da Barra/Fundeadouro Norte, ou 45 min a 1 h se vier de Tróia/outro cais."
        )

    tide_note = ""
    if spring_tide is True:
        tide_note = "Pelo critério operacional disponível, trata-se de maré viva; no Cais de Este a atracação deve ficar junto do reponto."
    elif spring_tide is False:
        tide_note = "Pelo critério operacional disponível, não parece maré viva; mesmo assim a prática local de marcação continua a usar o reponto como referência."
    else:
        tide_note = "Confirma se a janela é de marés vivas; no Cais de Este, se for maré viva, a atracação deve ficar junto do reponto."

    context_note = (
        "Contexto: apesar da menção a LISNAVE, esta pergunta é tratada como novo caso SECIL, sem herdar regras da LISNAVE.\n"
        if "lisnave" in clean_question
        else ""
    )

    answer = (
        "Local: SECIL.\n"
        "Doca/cais: SECIL E/Este.\n"
        f"Hora referida: {time_label}.\n\n"
        f"{context_note}"
        f"{conclusion}\n\n"
        "Atenção: o critério principal aqui não é apenas ser dia/noite. "
        "A IT-009 diz que a Secil E atraca no reponto em marés vivas, e as notas práticas indicam a antecedência "
        "de marcação para entradas: 30-45 min antes se vier de fora da Barra/Fundeadouro Norte, ou 45 min a 1 h "
        "se vier de Tróia/outro cais.\n\n"
        f"{tide_note}\n\n"
        "Antes de fechar, confirmar ainda LOA de referência 140 m, calado de referência 8,0 m, origem da entrada e validação do Piloto Coordenador."
    )
    sources = [
        {
            "document": "IT-009_Secil.txt",
            "source_id": "SECIL_ENTRY_TIMING_RULE",
            "chunk_id": 0,
            "score": 1.0,
            "retrieval_mode": "operational_rule",
            "snippet": (
                "Secil E/Cais de Este: em marés vivas, atracar próximo do reponto. "
                "Entradas para a Secil: de fora da Barra/Fundeadouro Norte marcar 30-45 min antes do reponto; "
                "de Tróia ou outro cais marcar 45 min a 1 h antes."
            ),
        }
    ]
    if tide_event:
        sources.append(
            {
                "document": "Marés Setúbal / Troia",
                "source_id": "SECIL_ENTRY_TIMING_TIDE",
                "chunk_id": 0,
                "score": 1.0,
                "retrieval_mode": "structured",
                "snippet": f"Reponto usado para validação: {tide_context}. Hora marcada: {time_label}.",
            }
        )
    return {"answer": answer, "sources": sources, "answer_origin": "secil_entry_timing"}


def _answer_secil_reponto_direct(question: str, clean_question: str) -> dict | None:
    if "secil" not in clean_question:
        return None
    if not re.search(r"\b(reponto|mare|mar[eé]|preia|baixa|marcada|marcado|marca\w*|marco|hora|horario|horário)\b", clean_question):
        return None
    if not re.search(r"\b(entrada|entrar|atracar|atracacao|atracação|saida|saída|sair|largada|manobra|marcada|marca\w*|marco)\b", clean_question):
        return None

    is_west = bool(re.search(r"\b(w|oeste|west|cais\s+w|cais\s+oeste|cais\s+a)\b", clean_question))
    is_east = bool(re.search(r"\b(e|este|east|cais\s+e|cais\s+este|cais\s+b)\b", clean_question))
    is_departure = bool(re.search(r"\b(saida|saída|sair|largada|desatracar|desatracacao|desatracação)\b", clean_question))
    is_entry = bool(re.search(r"\b(entrada|entrar|atracar|atracacao|atracação)\b", clean_question))

    if is_west:
        berth_line = (
            "SECIL W/Oeste: todos os navios devem atracar próximo do reponto de maré; "
            "esta regra aplica-se a todos os navios sem exceção. "
            "Formulação operacional: todos os navios atracam proximo do reponto."
        )
        limits_line = (
            "No Cais de Oeste, LOA máximo 200 m e calado de referência 9,5 m; "
            "se LOA > 170 m, a manobra tem de ser junto da preia-mar e com luz do dia."
        )
    elif is_east:
        berth_line = (
            "SECIL E/Este: no Cais de Este, os navios atracam próximo do reponto em marés vivas; "
            "em marés mortas a boa prática continua a favorecer a menor corrente."
        )
        limits_line = "No Cais de Este, usar LOA de referência 140 m e calado de referência 8,0 m."
    else:
        berth_line = (
            "Na SECIL é obrigatório distinguir SECIL W/Oeste de SECIL E/Este: "
            "o Oeste atraca sempre próximo do reponto; o Este exige reponto em marés vivas."
        )
        limits_line = "Confirma o cais, LOA, calado, origem da manobra e validação do Piloto Coordenador."

    if is_entry and is_departure:
        timing_line = (
            "Entradas para a SECIL: de fora da Barra ou Fundeadouro Norte, marcar 30-45 min antes do reponto; "
            "de Tróia ou de outro cais, marcar 45 min a 1 h antes.\n"
            "Saídas da SECIL: marcar cerca de 15 minutos antes do reponto; usam-se repontos de preia-mar e de baixa-mar, "
            "e a saída normalmente deixa o cais livre em 10 a 15 minutos."
        )
    elif is_departure:
        timing_line = (
            "Saídas da SECIL: marcar cerca de 15 minutos antes do reponto; usam-se repontos de preia-mar "
            "e de baixa-mar, e a saída normalmente deixa o cais livre em 10 a 15 minutos."
        )
    else:
        timing_line = (
            "Entradas para a SECIL: de fora da Barra ou Fundeadouro Norte, marcar 30-45 min antes do reponto; "
            "de Tróia ou de outro cais, marcar 45 min a 1 h antes."
        )

    answer = (
        "Local: SECIL.\n"
        f"Doca/cais: {'SECIL W/Oeste' if is_west else 'SECIL E/Este' if is_east else 'SECIL'}.\n"
        f"Sim, tens de tratar a manobra pela janela de reponto.\n"
        f"{berth_line}\n"
        f"{timing_line}\n"
        f"{limits_line}"
    )
    return {
        "answer": answer,
        "sources": [
            {
                "document": "IT-009_Secil.txt",
                "source_id": "SECIL_REPONTO_RULE",
                "chunk_id": 0,
                "score": 1.0,
                "retrieval_mode": "operational_rule",
                "snippet": (
                    "SECIL W/Oeste: todos os navios atracam próximo do reponto. "
                    "SECIL E/Este: reponto em marés vivas. Entradas 30-45 min antes do reponto "
                    "de fora da Barra/Fundeadouro Norte; saídas cerca de 15 min antes."
                ),
            }
        ],
        "answer_origin": "secil_reponto_rule",
    }


def _extract_wind_kts_from_question(question: str) -> float | None:
    text = str(question or "").lower().replace(",", ".")
    patterns = (
        r"\b(\d+(?:\.\d+)?)\s*(?:kt|kts|n[oó]s)\b",
        r"\bvento\s*(?:de|a|=|:)?\s*(\d+(?:\.\d+)?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _asks_wind_operational_limit(clean_question: str) -> bool:
    if "vento" not in clean_question:
        return False
    asks_limit = bool(
        re.search(
            r"\b(maxim\w*|admiss\w*|permitid\w*|limite|limites|"
            r"condicionad\w*|suspens\w*|suspend\w*|impossivel|impraticavel)\b",
            clean_question,
        )
        or re.search(r"\ba partir de que\b|\bate quanto\b|\bquanto vento\b", clean_question)
    )
    if not asks_limit:
        return False
    return bool(
        re.search(
            r"\b(manobr\w*|pilotagem|operacional|operacao|opera[cç][aã]o|porto|"
            r"navio|entrada|saida|saída|sair|atracar|desatracar)\b",
            clean_question,
        )
    )


def _answer_safety_hard_limit(question: str, clean_question: str) -> dict | None:
    if _asks_wind_operational_limit(clean_question):
        answer = (
            "🌬️ O limite máximo prático para manobrar é 25 kt. "
            "Acima de 20 kt a manobra já fica condicionada e deve ser avaliada caso a caso pelo Piloto e pelo Comandante, "
            "com atenção ao tipo de navio, área vélica, direção do vento, rajadas, corrente, local e rebocadores disponíveis. "
            "Aos 25 kt está-se no limite e a decisão deve ser muito conservadora; com vento sustentado ou rajada superior a 25 kt, "
            "as manobras ficam suspensas por segurança. 30 kt fica muito para lá do limite praticável."
        )
        return {
            "answer": answer,
            "sources": [
                {
                    "document": "operational_safety_limits.json",
                    "source_id": "SAFE_WIND_LIMIT",
                    "chunk_id": 1,
                    "score": 1.0,
                    "retrieval_mode": "operational_safety_limits",
                    "snippet": (
                        "Acima de 20 kt a manobra fica condicionada; 25 kt é o limite máximo prático; "
                        "vento sustentado ou rajada superior a 25 kt suspende manobras."
                    ),
                }
            ],
            "answer_origin": "operational_safety_limit",
        }

    if not re.search(r"\b(manobr\w*|sair|saida|saída|atracar|entrar|navio|reboque|reboques|rebocador|rebocadores)\b", clean_question):
        return None

    wind_kts = _extract_wind_kts_from_question(question)
    if wind_kts is not None and wind_kts > 25:
        wind_label = f"{wind_kts:g}".replace(".", ",")
        answer = (
            f"🌬️ Não. Com vento sustentado ou rajada superior a 25 kt ({wind_label} kt no caso indicado), "
            "as manobras ficam suspensas por segurança. Ter mais rebocadores não anula este limite. "
            "Se a suspensão foi acionada por vento, a retoma só deve ser considerada quando o vento baixar para menos de 25 kt. "
            "Fonte: limite operacional de segurança para vento."
        )
        return {
            "answer": answer,
            "sources": [
                {
                    "document": "operational_safety_limits.json",
                    "source_id": "SAFE_WIND_LIMIT",
                    "chunk_id": 1,
                    "score": 1.0,
                    "retrieval_mode": "operational_safety_limits",
                    "snippet": "Com vento superior a 25 kt, todas as manobras ficam suspensas; retoma apenas abaixo de 25 kt.",
                }
            ],
            "answer_origin": "operational_safety_limit",
        }

    if re.search(r"\b(nevoeiro|nevoa|neblina|fog|mist)\b", clean_question):
        local_note = "Local: Autoeuropa. " if re.search(r"\bauto\s*europa\b|\bautoeuropa\b", clean_question) else ""
        answer = (
            f"{local_note}Não. Com nevoeiro em porto / visibilidade reduzida, as manobras ficam suspensas até a visibilidade operacional ser restaurada. "
            "O número de rebocadores não elimina esta restrição; depois da visibilidade voltar, reavalia-se a manobra e os meios necessários."
        )
        return {
            "answer": answer,
            "sources": [
                {
                    "document": "operational_safety_limits.json",
                    "source_id": "SAFE_FOG_LIMIT",
                    "chunk_id": 1,
                    "score": 1.0,
                    "retrieval_mode": "operational_safety_limits",
                    "snippet": "Com nevoeiro em porto, todas as manobras ficam suspensas até que a visibilidade seja restaurada.",
                }
            ],
            "answer_origin": "operational_safety_limit",
        }

    return None


def _answer_fog_port_procedure_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\b(nevoeiro|nevoa|neblina|fog|mist)\b", clean_question):
        return None
    if re.search(r"\b(colreg|rieam|regra\s*19|regra\s*35|sinais?|sonor\w*|apito)\b", clean_question):
        return None
    source = build_operational_safety_source(question, _active_knowledge_dir() or "knowledge", force=True)
    answer = (
        "Com nevoeiro em porto:\n"
        "- A pilotagem é suspensa e as manobras não se executam até a visibilidade operacional ser reposta.\n"
        "- Os navios aguardam em fila de prioridade; quando levantar, retoma-se por reponto de maré/janela crítica, passageiros/animais vivos/reefers/carga perecível, Ro-Ro, contentores e restantes.\n"
        "- Dentro da mesma prioridade e janela operacional, ordenar por tipo de manobra: saídas > mudanças > entradas.\n"
        "- As requisições continuam a ser registadas no sistema, mas não autorizam a execução enquanto a pilotagem não declarar o levantamento.\n"
        "- Se o navio já estiver a navegar no meio do nevoeiro, aí aplica-se também RIEAM/COLREG: velocidade de segurança, máquinas prontas, radar/vigia reforçados e coordenação VTS."
    )
    return {
        "answer": answer,
        "sources": [source] if source else [],
        "answer_origin": "operational_safety_limit",
    }


def _weather_slot_datetime(slot: dict) -> datetime | None:
    timestamp = str(slot.get("timestamp") or "").strip()
    if timestamp:
        try:
            return datetime.strptime(timestamp, "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    slot_date = str(slot.get("date") or slot.get("day_label") or "").strip()
    slot_time = str(slot.get("time") or "").strip()
    if not slot_date or not slot_time:
        return None
    try:
        return datetime.strptime(f"{slot_date} {slot_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _target_weather_date(weather_service, question: str, reference_dt: datetime) -> date:
    try:
        if hasattr(weather_service, "_resolve_query_dates"):
            dates = list(weather_service._resolve_query_dates(question, reference_dt.date()))
            if dates:
                first = dates[0]
                if isinstance(first, date):
                    return first
                return datetime.strptime(str(first), "%Y-%m-%d").date()
    except Exception:
        logger.exception("Falha ao resolver data para meteorologia da manobra.")
    return reference_dt.date()


def _nearest_weather_slot(forecast: dict, target_dt: datetime) -> dict | None:
    selected: tuple[float, dict] | None = None
    for slot in build_weather_timeline(forecast, max_hours=72):
        slot_dt = _weather_slot_datetime(slot)
        if not slot_dt:
            continue
        distance = abs((slot_dt - target_dt).total_seconds())
        if selected is None or distance < selected[0]:
            selected = (distance, slot)
    return selected[1] if selected else None


def _guidance_weather_direction(direction: object) -> str:
    label = re.sub(r"[^A-Z]", "", str(direction or "").upper())
    if not label:
        return ""
    if "SW" in label or label in {"SSW", "WSW"}:
        return "SW"
    if label.startswith("S"):
        return "S"
    if label.startswith("N"):
        return "N"
    if label.startswith("E"):
        return "E"
    if label.startswith("W"):
        return "W"
    return ""


def _build_tug_weather_source(question: str, weather_service, summary: str) -> dict:
    context = None
    try:
        if hasattr(weather_service, "context_for_question"):
            context = weather_service.context_for_question(question)
    except Exception:
        context = None
    source = dict(context or {})
    source.update(
        {
            "document": source.get("document") or "Meteorologia operacional",
            "source_id": "WEATHER_TUG_CONTEXT",
            "chunk_id": source.get("chunk_id", 0),
            "score": source.get("score", 1.0),
            "retrieval_mode": "live_weather_context",
            "snippet": summary,
            "text": summary,
        }
    )
    return source


def _tug_live_weather_context(question: str, clean_question: str) -> dict:
    if not TUG_LIVE_WEATHER_RE.search(clean_question):
        return {"guidance_question": question, "lines": [], "sources": []}

    weather_service = getattr(services, "weather_service", None)
    if not weather_service or not getattr(weather_service, "enabled", False):
        return {
            "guidance_question": question,
            "lines": [
                "Meteorologia: não consegui confirmar vento/rajadas atuais ou previstos. "
                "Não fechar a decisão operacional sem essa confirmação."
            ],
            "sources": [],
        }

    try:
        forecast = weather_service.get_forecast(days=3)
    except Exception:
        logger.exception("Falha ao obter meteorologia para recomendacao de rebocadores.")
        return {
            "guidance_question": question,
            "lines": [
                "Meteorologia: falhou a consulta de vento/rajadas. "
                "Não fechar a decisão operacional sem essa confirmação."
            ],
            "sources": [],
        }

    reference_dt = _parse_weather_reference_datetime(forecast) or datetime.now()
    observation = dict(forecast.get("current") or {})
    context_label = "atual"
    parsed_time = _parse_maneuver_time(question)
    if parsed_time:
        hour, minute, time_label = parsed_time
        target_date = _target_weather_date(weather_service, question, reference_dt)
        target_dt = datetime(target_date.year, target_date.month, target_date.day, hour, minute)
        slot = _nearest_weather_slot(forecast, target_dt)
        if slot:
            observation = dict(slot)
            context_label = f"prevista para {slot.get('date_label') or target_date.isoformat()} {slot.get('time') or time_label}"
        else:
            context_label = f"atual; sem slot horário para {target_date.strftime('%d/%m/%Y')} {time_label}"

    wind = _safe_weather_float(observation.get("wind_kts"))
    gust = _safe_weather_float(observation.get("gust_kts"))
    wind_dir = str(observation.get("wind_dir") or "").strip()
    values = [value for value in (wind, gust) if value is not None]
    strongest = max(values) if values else None
    summary = (
        f"Meteorologia considerada ({context_label}): vento {_format_weather_kts(wind)} kts "
        f"{wind_dir or '--'}; rajadas {_format_weather_kts(gust)} kts."
    )
    lines = [summary]
    if strongest is not None:
        if strongest > 25:
            lines.append("Com vento/rajadas > 25 kt, a regra operacional é suspender manobras.")
        elif strongest >= 25:
            lines.append("Vento/rajadas nos 25 kt: avaliar caso a caso com critério conservador e ponderar atrasar.")
        elif strongest >= 20:
            lines.append("Rajadas/vento no limiar de vento forte (>= 20 kt); manter recomendação conservadora e ponderar atrasar se a tendência não baixar.")
        elif strongest >= 15:
            lines.append("Vento/rajadas já exigem cautela; confirmar tendência na hora antes de fechar meios.")

    direction = _guidance_weather_direction(wind_dir)
    guidance_question = question
    if direction:
        strength = " forte" if strongest is not None and strongest >= 20 else ""
        guidance_question = f"{question} vento {direction}{strength}"

    return {
        "guidance_question": guidance_question,
        "lines": lines,
        "sources": [_build_tug_weather_source(question, weather_service, " ".join(lines))],
    }


def _append_tug_weather_lines(answer_lines: list[str], weather_context: dict) -> None:
    lines = list(weather_context.get("lines") or [])
    if not lines:
        return
    answer_lines.append("")
    answer_lines.extend(lines)


def _append_tug_local_echo(answer_lines: list[str], clean_question: str) -> None:
    if re.search(r"\bauto\s*europa\b|\bautoeuropa\b|\bcais\s*1[01]\b", clean_question):
        answer_lines.append("Local: Autoeuropa.")
    if re.search(r"\btms\s*1\b|\btms1\b", clean_question):
        answer_lines.append("Local: TMS1 / TMS 1.")
    if re.search(r"\btms\s*2\b|\btms2\b", clean_question):
        answer_lines.append("Local: TMS2 / TMS 2.")


def _tug_guidance_sources(source: dict, weather_context: dict) -> list[dict]:
    return [source] + list(weather_context.get("sources") or [])


def _answer_tanquisado_costado_wind_direct(question: str, clean_question: str) -> dict | None:
    if "tanquisado" not in clean_question:
        return None
    if not re.search(r"\b(vento|leste|este|east|\be\b|costado|largar|largada|sair|saida|saída)\b", clean_question):
        return None
    if not re.search(r"\b(costado|largar|largada|sair|saida|saída)\b", clean_question):
        return None
    answer = (
        "Para Tanquisado a sair/largar com vento Leste forte, mantém a leitura conservadora local:\n"
        "- Tanquisado com 3 rebocadores é o mínimo prático local;\n"
        "- manter 1 rebocador estabelecido à proa e 1 à popa (1 a proa e 1 a popa) para controlo longitudinal;\n"
        "- Tanquisado a sair com vento E forte: o terceiro fica como 1 rebocador a empurrar ao costado na largada dos cabos, se houver espaço e sem criar má vizinhança;\n"
        "- a equivalência E=N usada em TMS2/Autoeuropa não deve ser aplicada automaticamente ao Tanquisado.\n"
        "Confirmar força real do vento/rajadas, corrente no Canal Sul, bordo, calado, amarração e margem junto ao cais."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("tug_operational_guidance.json", "TANQUISADO_EAST_WIND_SIDE_PUSH", answer, "operational_tug_guidance")],
        "answer_origin": "operational_tug_guidance",
    }


def _extract_dwt_range_from_question(question: str) -> tuple[float, float] | None:
    text = str(question or "").lower()
    match = re.search(
        r"\bentre\s+(\d{1,3}(?:[.,]\d{3})*|\d+(?:[.,]\d+)?)\s+e\s+(\d{1,3}(?:[.,]\d{3})*|\d+(?:[.,]\d+)?)\s*dwt\b",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        try:
            first = float(match.group(1).replace(".", "").replace(",", "."))
            second = float(match.group(2).replace(".", "").replace(",", "."))
        except ValueError:
            return None
        return min(first, second), max(first, second)
    match = re.search(r"\b(\d{1,3}(?:[.,]\d{3})*|\d+(?:[.,]\d+)?)\s*dwt\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return None
    return value, value


def _answer_it016_dwt_tugs_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\b(reboque|reboques|rebocador|rebocadores)\b", clean_question):
        return None
    dwt_range = _extract_dwt_range_from_question(question)
    if not dwt_range:
        return None
    if not (15001 <= dwt_range[0] and dwt_range[1] <= 25000):
        return None
    if not re.search(r"\b(atracar|atracacao|atracação|entrada|entrar)\b", clean_question):
        return None
    if not re.search(r"\b(carregado|carga|loaded)\b", clean_question):
        return None
    if not re.search(r"\bsem\b.*\b(bow\s*thruster|bowthruster|h[eé]lice\s+de\s+proa|hpr)\b", question, re.IGNORECASE):
        return None
    answer = (
        "⚓ Pela IT-016, para atracar carregado um navio entre 15.001 e 25.000 DWT sem bow thruster, "
        "a referência é GGp: 2 rebocadores grandes + 1 rebocador pequeno, ou seja, 3 rebocadores no total.\n"
        "- Esta linha vale tanto para cargas perigosas como para outras cargas nessa faixa de DWT.\n"
        "- G = rebocador grande, com bollard pull igual ou superior a 25 t; p = rebocador pequeno, inferior a 25 t.\n"
        "- Confirmar sempre LOA, tipo de carga, estado real dos thrusters, vento/corrente, calado e indicação do Piloto Coordenador."
    )
    return {
        "answer": answer,
        "sources": [_direct_source("IT-016_Rebocadores.txt", "IT016_DWT_15001_25000_AC_NO_BOW_GGP", answer, "operational_tug_guidance")],
        "answer_origin": "operational_tug_guidance",
    }


def _answer_tug_guidance_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\b(reboque|reboques|rebocador|rebocadores)\b", clean_question):
        return None
    if not re.search(r"\b(qual|quais|quantos|qts|numero|número|regra|regras|pratica|prática|aconselha|aconselhas|recomenda|recomendas|necessarios|necessários|leva|suficiente|onde|posicion|meter|colocar|proa|popa|costado|pode|posso|devo|deve|avancar|avançar|validar|cheg\w*|basta|bastam)\b", clean_question):
        return None

    weather_context = _tug_live_weather_context(question, clean_question)
    guidance_question = str(weather_context.get("guidance_question") or question)
    source = build_tug_operational_guidance_source(guidance_question, _active_knowledge_dir() or "knowledge")
    if not source:
        return None
    snippet = str(source.get("snippet") or "")
    applicable = []
    positioning = []
    in_rules = False
    in_positioning = False
    for raw_line in snippet.splitlines():
        line = raw_line.strip()
        if line == "Regras diretamente aplicaveis:":
            in_rules = True
            in_positioning = False
            continue
        if line == "Posicionamento pratico dos rebocadores:":
            in_positioning = True
            in_rules = False
            continue
        if in_rules and not line.startswith("- "):
            break
        if in_rules and line.startswith("- "):
            applicable.append(line[2:].strip())
        if in_positioning and not line.startswith("- "):
            in_positioning = False
        if in_positioning and line.startswith("- "):
            positioning.append(line[2:].strip())

    positioning_question = bool(re.search(r"\b(onde|posicion|meter|colocar|proa|popa|costado|standby)\b", clean_question))
    requested_count_match = re.search(r"rebocadores pedidos/informados:\s*(\d+)", snippet, flags=re.IGNORECASE)
    if not applicable and positioning and not (positioning_question and requested_count_match):
        relevant_positioning = positioning
        if re.search(r"\b(roro|ro\s*ro|ro-ro|ro/ro)\b", clean_question):
            relevant_positioning = (
                [item for item in positioning if "Ro-Ro" in item]
                + [item for item in positioning if "convencionais" in item]
                + [item for item in positioning if "Normalmente" in item]
            )
        elif re.search(r"\bsem\b.*\b(bow|bowthruster|h[eé]lice de proa|hpr)\b", clean_question):
            relevant_positioning = [item for item in positioning if "Normalmente" in item or "1 a proa e 1 a popa" in item or "convencionais" in item]
        elif re.search(r"\b(com|tem)\b.*\b(bow|bowthruster|h[eé]lice de proa|hpr)\b", clean_question):
            relevant_positioning = [item for item in positioning if "Com bowthruster operacional" in item or "convencionais" in item]
        answer_lines = ["Regra prática de posicionamento dos rebocadores:"]
        _append_tug_local_echo(answer_lines, clean_question)
        for item in relevant_positioning[:3]:
            answer_lines.append(f"- {item}")
        _append_tug_weather_lines(answer_lines, weather_context)
        return {
            "answer": "\n".join(answer_lines),
            "sources": _tug_guidance_sources(source, weather_context),
            "answer_origin": "operational_tug_guidance",
        }

    if positioning and positioning_question and requested_count_match:
        requested_count = requested_count_match.group(1)
        count_specific = [
            item
            for item in positioning
            if f"Com {requested_count} rebocadores" in item
            or f"{requested_count}.º rebocador" in item
            or f"{requested_count} rebocadores" in item
        ]
        location_specific = [
            item
            for item in positioning
            if "Tanquisado" in item or "Eco-Oil" in item or "Lisnave" in item
        ]
        relevant_positioning = list(dict.fromkeys(count_specific + location_specific)) or positioning
        answer_lines = ["Posicionamento prático dos rebocadores:"]
        _append_tug_local_echo(answer_lines, clean_question)
        for item in relevant_positioning[:4]:
            answer_lines.append(f"- {item}")
        if applicable:
            answer_lines.append(f"Base/minimo a respeitar: {applicable[0]}")
        _append_tug_weather_lines(answer_lines, weather_context)
        return {
            "answer": "\n".join(answer_lines),
            "sources": _tug_guidance_sources(source, weather_context),
            "answer_origin": "operational_tug_guidance",
        }

    if not applicable:
        return None
    first_rule = _select_primary_tug_rule(applicable)
    count_match = re.search(r"\b(\d+)\s+rebocador(?:es)?\b", first_rule, flags=re.IGNORECASE)
    if not count_match:
        return None
    count = int(count_match.group(1))
    size_label = ""
    if re.search(r"\bgrandes?\b|grande\s+de\s+cerca\s+de\s+35\s*t", first_rule, flags=re.IGNORECASE):
        size_label = "grandes"
    elif re.search(r"LOA inferido: (?:1[2-9]\d|[2-9]\d\d)", snippet, flags=re.IGNORECASE):
        size_label = "grandes"
    tug_label = f"{count} rebocador" + ("" if count == 1 else "es")
    if size_label:
        tug_label += " grande" if count == 1 else f" {size_label}"

    answer_lines = [
        f"Recomendo {tug_label}.",
        f"Regra prática aplicável: {first_rule}",
        "Fonte: regra prática de rebocadores; confirmar meteorologia atual quando a decisão depender de vento/rajadas.",
    ]
    _append_tug_local_echo(answer_lines, clean_question)
    if requested_count_match:
        requested_count = int(requested_count_match.group(1))
        if requested_count < count:
            answer_lines.append(
                f"Rebocadores insuficientes: foram indicados {requested_count}, mas a regra aplicável pede {count}."
            )
    other_applicable = [rule for rule in applicable if rule != first_rule]
    if other_applicable:
        answer_lines.append("Outras regras relevantes: " + " ".join(other_applicable[:2]))
    if positioning and positioning_question:
        specific_positioning = [
            item
            for item in positioning
            if "Tanquisado" in item or "Eco-Oil" in item
        ]
        answer_lines.append("Posicionamento: " + " ".join((specific_positioning or positioning)[:2]))
    if "Prioridade:" in snippet:
        answer_lines.append(
            "Confirma DWT, carga perigosa, estado carregado/vazio e thrusters; a IT-016 pode agravar mínimos, mas não deve reduzir esta recomendação prática."
        )
    if "Meteorologia considerada" in "\n".join(weather_context.get("lines") or []):
        answer_lines.append("Referência de cautela: com rajadas 20 kt ou mais, manter recomendação conservadora e ponderar atrasar se a tendência não baixar.")
    _append_tug_weather_lines(answer_lines, weather_context)
    return {
        "answer": "\n".join(answer_lines),
        "sources": _tug_guidance_sources(source, weather_context),
        "answer_origin": "operational_tug_guidance",
    }


def _select_primary_tug_rule(applicable: list[str]) -> str:
    """Choose the most specific/conservative tug rule from extracted guidance lines."""
    ranked = []
    for index, rule in enumerate(applicable):
        count_match = re.search(r"\b(\d+)\s+rebocador(?:es)?\b", rule, flags=re.IGNORECASE)
        tug_count = int(count_match.group(1)) if count_match else 0
        clean_rule = rule.lower()
        specificity = 0
        if any(token in clean_rule for token in ("loa", "acima", "ate", "até", "mais de", "entre", "<=", ">")):
            specificity += 2
        if any(token in clean_rule for token in ("vento", "hidrolift", "eclusa", "doca")):
            specificity += 1
        if "usar sempre no minimo" in clean_rule or "usar sempre no mínimo" in clean_rule:
            specificity -= 1
        ranked.append((tug_count, specificity, index, rule))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return ranked[0][3]


def _answer_emergency_response_direct(question: str, clean_question: str) -> dict | None:
    source = build_emergency_response_source(question, _active_knowledge_dir() or "knowledge")
    if not source:
        return None

    bullets = []
    capture_bullets = False
    for raw_line in str(source.get("snippet") or "").splitlines():
        line = raw_line.strip()
        if line in {"Cenario identificado:", "Acoes comuns sempre:"}:
            capture_bullets = True
            continue
        if line == "Orientacao de resposta:":
            capture_bullets = False
            continue
        if capture_bullets and line.startswith("- "):
            bullets.append(line[2:].strip())
    answer_lines = ["Emergencia operacional:"]
    for item in bullets[:8]:
        answer_lines.append(f"- {item}")
    return {
        "answer": "\n".join(answer_lines),
        "sources": [source],
        "answer_origin": "operational_emergency_response",
    }


def _answer_fog_underway_procedure_direct(question: str, clean_question: str) -> dict | None:
    source = build_fog_underway_procedure_source(question, _active_knowledge_dir() or "knowledge")
    if not source:
        return None

    answer_lines = [
        "Nevoeiro súbito com o navio já a navegar:",
        "- Primeiro estabilizar: reduzir para velocidade de segurança, máquinas prontas, vigia visual/auditiva reforçada, radar/ECDIS/AIS bem acompanhados e avaliação contínua do risco de abalroamento.",
        "- Aplicar RIEAM/COLREG: Regra 5 (vigia), Regra 6 (velocidade segura), Regras 7/8 (risco e manobra) e Regra 19 (visibilidade reduzida). Se ouvir sinal para vante ou não conseguir evitar aproximação excessiva, reduzir ao mínimo para governar, anular seguimento se necessário e navegar com extrema precaução.",
        "- Fazer os sinais de nevoeiro da Regra 35: com seguimento, 1 som prolongado no máximo de 2 em 2 minutos; pairando/sem seguimento, 2 sons prolongados. Se houver dúvida/perigo sobre outro navio, usar pelo menos 5 sons curtos; se houver perigo ou necessidade de auxílio, usar sinais de perigo.",
        "- Avaliar posição, fundo, tráfego, corrente/vento, distância ao cais/canal/fundeadouro e altura do dia. De dia pode ser mais difícil identificar referências; de noite as luzes ajudam mas não substituem radar e vigia.",
        "- Se estiver junto do cais de destino e houver margem real, meios e referências suficientes, tentar atracar com muito cuidado. Se não, seguir para fundeadouro/posição de espera adequada e aguardar melhoria.",
        "- Se vier de entrada e ainda estiver antes do canal, tentar abortar antes de entrar, dar a volta em segurança e aguardar fora/num fundeadouro apropriado.",
        "- Reportar e coordenar sempre com Setúbal Port Control / VTS local no VHF 73 e pilotos no canal 14; usar canal 71 em manobras Lisnave.",
    ]
    return {
        "answer": "\n".join(answer_lines),
        "sources": [source],
        "answer_origin": "fog_underway_procedure",
    }


def _answer_navigation_lights_direct(question: str, clean_question: str) -> dict | None:
    source = build_navigation_lights_source(question, _active_knowledge_dir() or "knowledge")
    if not source:
        return None

    snippet = str(source.get("snippet") or "").strip()
    entries = source.get("entries") or []
    answer_lines = ["Balizagem/luzes de Setúbal:"]
    seen_lines = set(answer_lines)
    for raw_line in snippet.splitlines():
        line = raw_line.strip()
        if line in seen_lines:
            continue
        if not line or line == "Balizagem/luzes de Setúbal:" or line == "Registos relevantes:":
            continue
        if line.startswith("Fonte:"):
            continue
        if entries and line.startswith("- "):
            answer_lines.append(line)
            seen_lines.add(line)
        elif not entries and (
            "IALA A" in line
            or line.startswith("Fonte:")
            or line.startswith("- SETÚBAL")
        ):
            answer_lines.append(line)
            seen_lines.add(line)
        elif "IALA A" in line and line not in answer_lines:
            answer_lines.append(line)
            seen_lines.add(line)
    if not any(line.startswith("- ") for line in answer_lines) and snippet:
        answer_lines.extend(snippet.splitlines()[1:5])
    return {
        "answer": "\n".join(answer_lines[:10]),
        "sources": [source],
        "answer_origin": "navigation_lights",
    }


def _answer_unclear_operational_fragment(question: str, clean_question: str) -> dict | None:
    tokens = re.findall(r"[a-z0-9À-ÿ]+", clean_question or "")
    if len(tokens) > 5:
        return None
    if len(OPERATIONAL_FRAGMENT_TERMS_RE.findall(question or "")) < 2:
        return None
    if OPERATIONAL_DECISION_TERMS_RE.search(question or ""):
        return None

    answer = (
        "Não tenho informação suficiente para responder com segurança. Reformula com a decisão que queres tomar e o contexto mínimo.\n"
        "Exemplos:\n"
        "- `Navio de 100 m vai fundear no Fundeadouro Norte: precisa de rebocadores?`\n"
        "- `Navio em blackout/sem máquina, posição X, sem rebocadores perto: o que fazer de imediato?`\n"
        "- `Navio para Tanquisado, LOA/calado, com/sem bowthruster: quantos rebocadores e onde posicionar?`"
    )
    return {
        "answer": answer,
        "sources": [],
        "answer_origin": "operational_clarification",
    }


def _needs_portal_activity_context(question: str, plan: ChatExecutionPlan | None = None) -> bool:
    plan = plan or build_chat_execution_plan(question)
    clean = plan.normalized_question or _operational_lookup_key(question)
    if plan.wants_operational_lookup:
        return True
    if plan.maneuver_lookup_type and (
        PORTAL_ACTIVITY_CONTEXT_RE.search(clean)
        or PORTAL_MOVEMENT_CONTEXT_RE.search(clean)
        or PORTAL_MANEUVER_CONTEXT_RE.search(clean)
    ):
        return True
    if (
        PORTAL_ACTIVITY_CONTEXT_RE.search(clean)
        or PORTAL_MOVEMENT_CONTEXT_RE.search(clean)
        or PORTAL_MANEUVER_CONTEXT_RE.search(clean)
    ):
        return True
    return False


def build_operational_chat_sources(
    question: str,
    plan: ChatExecutionPlan | None = None,
) -> list[dict]:
    """Assemble supplemental operational context sources for the chat RAG pipeline."""
    knowledge_dir = _active_knowledge_dir() or "knowledge"
    emergency_source = build_emergency_response_source(question, knowledge_dir)
    if emergency_source:
        return [emergency_source]
    fog_underway_source = build_fog_underway_procedure_source(question, knowledge_dir)
    if fog_underway_source:
        return [fog_underway_source]

    recent_port_activity: dict | None = None
    sources: list[dict] = []
    needs_portal_activity = _needs_portal_activity_context(question, plan=plan)
    if needs_portal_activity:
        recent_port_activity = filter_port_activity_for_session(
            services.store.get_port_activity_snapshot(window_days=30),
            public_operational=True,
        )
        historical_port_activity = filter_port_activity_for_session(
            services.store.get_port_activity_snapshot(window_days=3650),
            public_operational=True,
        )
        sources.extend(
            [
                build_operational_snapshot_source(recent_port_activity),
                build_maneuver_archive_source(question, historical_port_activity),
                build_scale_registry_source(question, historical_port_activity),
            ]
        )
    berth_catalog_source = build_berth_catalog_source(question)
    if berth_catalog_source:
        sources.append(berth_catalog_source)
    vessel_catalog_source = build_vessel_catalog_source(question)
    if vessel_catalog_source:
        sources.append(vessel_catalog_source)
    lisnave_rule_source = build_lisnave_operational_rule_source(question)
    if lisnave_rule_source:
        sources.append(lisnave_rule_source)
    safety_source = build_operational_safety_source(question, knowledge_dir)
    if safety_source:
        sources.append(safety_source)
    navigation_lights_source = build_navigation_lights_source(question, knowledge_dir)
    if navigation_lights_source:
        sources.append(navigation_lights_source)
    navigation_basics_source = build_navigation_basics_source(question, knowledge_dir)
    if navigation_basics_source:
        sources.append(navigation_basics_source)
    tug_guidance_source = build_tug_operational_guidance_source(question, knowledge_dir)
    if tug_guidance_source:
        sources.append(tug_guidance_source)
    if needs_portal_activity:
        maneuver_case_source = build_maneuver_case_context_source(question, current_resolvable_port_calls())
        if maneuver_case_source:
            sources.append(maneuver_case_source)
    if recent_port_activity is None and _looks_like_cost_question(question):
        recent_port_activity = filter_port_activity_for_session(
            services.store.get_port_activity_snapshot(window_days=30),
            public_operational=True,
        )
    cost_source = build_cost_context_source(question, recent_port_activity or {})
    if cost_source:
        sources.append(cost_source)
    return sources


def answer_direct_operational_query(
    question: str,
    plan: ChatExecutionPlan | None = None,
) -> dict | None:
    """Answer deterministic operational lookup questions that should not rely on generic RAG wording."""
    plan = plan or build_chat_execution_plan(question)
    plan_question_key = _operational_lookup_key(getattr(plan, "question", "") or "")
    question_key = _operational_lookup_key(question)
    if plan.normalized_question and plan_question_key == question_key:
        clean_question = plan.normalized_question
    else:
        clean_question = question_key
    spring_tide_answer = _answer_spring_tide_definition_direct(question, clean_question)
    if spring_tide_answer:
        return _attach_operational_diagnostic(spring_tide_answer, question)
    source_coverage_answer = _answer_source_coverage_direct(question, clean_question)
    if source_coverage_answer:
        return _attach_operational_diagnostic(source_coverage_answer, question)
    checklist_answer = _answer_lisnave_checklist_direct(question, clean_question)
    if checklist_answer:
        return _attach_operational_diagnostic(checklist_answer, question)
    tup_answer = _answer_tup_formula_direct(question, clean_question)
    if tup_answer:
        return _attach_operational_diagnostic(tup_answer, question)
    tms1_defenses_answer = _answer_tms1_defenses_direct(question, clean_question)
    if tms1_defenses_answer:
        return _attach_operational_diagnostic(tms1_defenses_answer, question)
    tms1_large_capacity_answer = _answer_tms1_large_vessel_capacity_direct(question, clean_question)
    if tms1_large_capacity_answer:
        return _attach_operational_diagnostic(tms1_large_capacity_answer, question)
    tms2_capacity_answer = _answer_tms2_capacity_direct(question, clean_question)
    if tms2_capacity_answer:
        return _attach_operational_diagnostic(tms2_capacity_answer, question)
    priority_answer = _answer_operational_priority_direct(question, clean_question)
    if priority_answer:
        return _attach_operational_diagnostic(priority_answer, question)
    culture_answer = _answer_setubal_culture_direct(question, clean_question)
    if culture_answer:
        return _attach_operational_diagnostic(culture_answer, question)
    lisnave_face_answer = _answer_lisnave_face_side_direct(question, clean_question)
    if lisnave_face_answer:
        return _attach_operational_diagnostic(lisnave_face_answer, question)
    doca21_depth_answer = _answer_lisnave_doca21_depth_direct(question, clean_question)
    if doca21_depth_answer:
        return _attach_operational_diagnostic(doca21_depth_answer, question)
    hard_limit_answer = _answer_lisnave_hidrolift_hard_limit(question, clean_question)
    if hard_limit_answer:
        return _attach_operational_diagnostic(hard_limit_answer, question)
    lisnave_doca_tug_answer = _answer_lisnave_doca_tug_direct(question, clean_question)
    if lisnave_doca_tug_answer:
        return _attach_operational_diagnostic(lisnave_doca_tug_answer, question)
    tide_scheduling_answer = _answer_tide_scheduling_direct(question, clean_question)
    if tide_scheduling_answer:
        return _attach_operational_diagnostic(tide_scheduling_answer, question)
    route_answer = route_transit_answer(question, clean_question)
    if route_answer:
        return _attach_operational_diagnostic(route_answer, question)
    cross_reponto_answer = _answer_cross_reponto_scheduling_direct(question, clean_question)
    if cross_reponto_answer:
        return _attach_operational_diagnostic(cross_reponto_answer, question)
    lisnave_night_length_answer = _answer_lisnave_night_length_direct(question, clean_question)
    if lisnave_night_length_answer:
        return _attach_operational_diagnostic(lisnave_night_length_answer, question)
    lisnave_dimensions_answer = _answer_lisnave_dimensions_direct(question, clean_question)
    if lisnave_dimensions_answer:
        return _attach_operational_diagnostic(lisnave_dimensions_answer, question)
    lisnave_profile_answer = _answer_lisnave_profile_direct(question, clean_question)
    if lisnave_profile_answer:
        return _attach_operational_diagnostic(lisnave_profile_answer, question)
    tanquisado_dimensions_answer = _answer_tanquisado_dimensions_direct(question, clean_question)
    if tanquisado_dimensions_answer:
        return _attach_operational_diagnostic(tanquisado_dimensions_answer, question)
    eco_oil_limits_answer = _answer_eco_oil_limits_direct(question, clean_question)
    if eco_oil_limits_answer:
        return _attach_operational_diagnostic(eco_oil_limits_answer, question)
    barra_draft_answer = _answer_barra_draft_direct(question, clean_question)
    if barra_draft_answer:
        return _attach_operational_diagnostic(barra_draft_answer, question)
    visibility_threshold_answer = _answer_visibility_threshold_direct(question, clean_question)
    if visibility_threshold_answer:
        return _attach_operational_diagnostic(visibility_threshold_answer, question)
    emergency_answer = _answer_emergency_response_direct(question, clean_question)
    if emergency_answer:
        return _attach_operational_diagnostic(emergency_answer, question)
    fog_underway_answer = _answer_fog_underway_procedure_direct(question, clean_question)
    if fog_underway_answer:
        return _attach_operational_diagnostic(fog_underway_answer, question)
    alstom_answer = _answer_alstom_direct(question, clean_question)
    if alstom_answer:
        return _attach_operational_diagnostic(alstom_answer, question)
    safety_answer = _answer_safety_hard_limit(question, clean_question)
    if safety_answer:
        return _attach_operational_diagnostic(safety_answer, question)
    fog_port_answer = _answer_fog_port_procedure_direct(question, clean_question)
    if fog_port_answer:
        return _attach_operational_diagnostic(fog_port_answer, question)
    secil_confirmation_answer = _answer_secil_confirmation_direct(question, clean_question)
    if secil_confirmation_answer:
        return _attach_operational_diagnostic(secil_confirmation_answer, question)
    secil_entry_timing_answer = _answer_secil_entry_timing_direct(question, clean_question)
    if secil_entry_timing_answer:
        return _attach_operational_diagnostic(secil_entry_timing_answer, question)
    secil_reponto_answer = _answer_secil_reponto_direct(question, clean_question)
    if secil_reponto_answer:
        return _attach_operational_diagnostic(secil_reponto_answer, question)
    tanquisado_costado_answer = _answer_tanquisado_costado_wind_direct(question, clean_question)
    if tanquisado_costado_answer:
        return _attach_operational_diagnostic(tanquisado_costado_answer, question)
    it016_tug_answer = _answer_it016_dwt_tugs_direct(question, clean_question)
    if it016_tug_answer:
        return _attach_operational_diagnostic(it016_tug_answer, question)
    tug_guidance_answer = _answer_tug_guidance_direct(question, clean_question)
    if tug_guidance_answer:
        return _attach_operational_diagnostic(tug_guidance_answer, question)
    colreg_answer = answer_colreg_interpretation_direct(question)
    if colreg_answer:
        return _attach_operational_diagnostic(colreg_answer, question)
    navigation_lights_answer = _answer_navigation_lights_direct(question, clean_question)
    if navigation_lights_answer:
        return _attach_operational_diagnostic(navigation_lights_answer, question)
    navigation_basics_answer = answer_navigation_basics_direct(question)
    if navigation_basics_answer:
        return _attach_operational_diagnostic(navigation_basics_answer, question)
    sapec_followup_answer = _answer_sapec_non_imo_followup_direct(question, clean_question)
    if sapec_followup_answer:
        return _attach_operational_diagnostic(sapec_followup_answer, question)
    unclear_answer = _answer_unclear_operational_fragment(question, clean_question)
    if unclear_answer:
        return _attach_operational_diagnostic(unclear_answer, question)
    if plan.requires_llm_synthesis:
        return None

    live_environment_answer = _answer_live_environment_query(question, clean_question, plan=plan)
    if live_environment_answer:
        return _attach_operational_diagnostic(live_environment_answer, question)
    berthed_vessels_answer = _answer_berthed_vessels_query(question, clean_question)
    if berthed_vessels_answer:
        return _attach_operational_diagnostic(berthed_vessels_answer, question)
    vessel_detail_answer = _answer_vessel_detail_query(question, clean_question)
    if vessel_detail_answer:
        return _attach_operational_diagnostic(vessel_detail_answer, question)
    maneuver_actor_answer = _answer_maneuver_actor_query(question, clean_question, plan=plan)
    if maneuver_actor_answer:
        return _attach_operational_diagnostic(maneuver_actor_answer, question)
    agent_lookup_answer = _answer_agent_lookup_query(question, clean_question, plan=plan)
    if agent_lookup_answer:
        return _attach_operational_diagnostic(agent_lookup_answer, question)
    agent_agency_answer = _answer_agent_agency_query(question, clean_question)
    if agent_agency_answer:
        return _attach_operational_diagnostic(agent_agency_answer, question)
    maneuver_id_answer = _answer_maneuver_id_query(question, clean_question, plan=plan)
    if maneuver_id_answer:
        return _attach_operational_diagnostic(maneuver_id_answer, question)
    recent_departures_answer = _answer_recent_departures_query(question, clean_question)
    if recent_departures_answer:
        return _attach_operational_diagnostic(recent_departures_answer, question)
    expected_arrivals_answer = _answer_expected_arrivals_query(question, clean_question)
    if expected_arrivals_answer:
        return _attach_operational_diagnostic(expected_arrivals_answer, question)
    planned_maneuvers_answer = _answer_planned_maneuvers_query(question, clean_question)
    if planned_maneuvers_answer:
        return _attach_operational_diagnostic(planned_maneuvers_answer, question)

    maneuver_type = plan.maneuver_lookup_type or ""
    maneuver_label = "manobra"
    if maneuver_type == "entry":
        maneuver_type = "entry"
        maneuver_label = "manobra de entrada"
    elif maneuver_type == "departure":
        maneuver_type = "departure"
        maneuver_label = "manobra de saída"
    elif maneuver_type == "shift":
        maneuver_type = "shift"
        maneuver_label = "manobra de mudança"

    if not plan.wants_operational_lookup:
        return None
    port_calls = current_resolvable_port_calls()
    matched_port_call = _match_port_call_from_question(question, port_calls)
    if not matched_port_call:
        return None

    resolved_port_call = services.store.get_port_call(matched_port_call["id"])
    maneuvers = list(resolved_port_call.get("maneuver_history", []) or [])
    if maneuver_type:
        maneuvers = [item for item in maneuvers if (item.get("type") or "").strip().lower() == maneuver_type]
    if not maneuvers:
        answer = f"Não encontrei {maneuver_label} para {resolved_port_call.get('vessel_name', 'este navio')}."
        return _attach_operational_diagnostic(
            {"answer": answer, "sources": [], "answer_origin": "operational_lookup"},
            question,
        )

    maneuvers.sort(
        key=lambda item: (
            item.get("planned_at") or "",
            item.get("completed_at") or "",
            item.get("updated_at") or "",
            item.get("created_at") or "",
        )
    )
    maneuver = maneuvers[-1]
    maneuver_id = maneuver.get("id", "")
    short_id = maneuver_id[:8].upper() if maneuver_id else "--"
    type_label = maneuver_label if maneuver_type else f"manobra {((maneuver.get('type') or '').strip().lower() or '--')}"
    answer = (
        f"O ID da {type_label} de {resolved_port_call.get('vessel_name', 'este navio')} "
        f"é {short_id} (completo: {maneuver_id})."
    )
    return _attach_operational_diagnostic({
        "answer": answer,
        "sources": [
            {
                "document": resolved_port_call.get("vessel_name", "Manobra"),
                "source_id": resolved_port_call.get("reference_code", ""),
                "retrieval_mode": "operational_lookup",
                "snippet": answer,
            }
        ],
        "answer_origin": "operational_lookup",
    }, question)


def _source_from_answer(document: str, source_id: str, answer: str, question: str) -> list[dict]:
    return [
        {
            "document": document,
            "source_id": source_id,
            "retrieval_mode": "operational_live",
            "snippet": answer,
            "question": question,
        }
    ]


def _planned_rows_for_port_call(port_activity: dict, port_call_id: str) -> list[dict]:
    rows = [
        item
        for item in list(port_activity.get("planned_maneuvers") or []) + list(port_activity.get("archived_maneuvers") or [])
        if item.get("port_call_id") == port_call_id
    ]
    rows.sort(key=_row_timestamp)
    return rows


def _format_activity_maneuver_line(row: dict, *, include_actors: bool = True) -> str:
    maneuver_id = row.get("maneuver_id") or "--"
    time_label = (
        row.get("execution_window_label")
        or row.get("actual_label")
        or row.get("planned_label")
        or _local_iso_to_label(row.get("date_value"))
    )
    line = (
        f"- {row.get('maneuver_label') or 'Manobra'} · ID {maneuver_id} · "
        f"{row.get('situation_label') or '--'} · {time_label} · {_maneuver_route_label(row)}"
    )
    if include_actors:
        line += (
            f" · aprovada por {_pilot_display(row, 'validated_by_label', 'validated_by_profile')} "
            f"· executada por {_pilot_display(row, 'executed_by_label', 'executed_by_profile')}"
        )
    return line


def _answer_berthed_vessels_query(question: str, clean_question: str) -> dict | None:
    if not BERTHED_VESSELS_QUERY_RE.search(clean_question):
        return None
    port_activity = _visible_activity(window_days=30)
    berthed = [
        item for item in port_activity.get("in_port", []) or []
        if not is_anchorage_berth(item.get("berth_label") or item.get("berth"))
    ]
    stats = port_activity.get("stats") or {}
    if not berthed:
        answer = "Não há navios atracados em cais neste momento."
        return {
            "answer": answer,
            "sources": _source_from_answer("Navios em cais do portal", "OPS_BERTHED_VESSELS", answer, question),
            "answer_origin": "operational_live",
        }

    lines = [
        (
            f"Navios atracados em cais: {len(berthed)} "
            f"({stats.get('occupied_slot_count', len(berthed))}/{stats.get('slot_capacity_count', '--')} slots ocupados)."
        )
    ]
    for item in berthed[:8]:
        planned_rows = _planned_rows_for_port_call(port_activity, item.get("id", ""))
        next_maneuver = next((row for row in planned_rows if row.get("situation_class") in {"pending", "approved"}), None)
        suffix = ""
        if next_maneuver:
            suffix = (
                f" · próxima manobra: {next_maneuver.get('maneuver_label') or 'Manobra'} "
                f"{next_maneuver.get('planned_label') or '--'} "
                f"(ID {next_maneuver.get('maneuver_id') or '--'})"
            )
        lines.append(
            f"- {item.get('vessel_name', '--')} · {item.get('berth_label') or item.get('berth') or '--'} "
            f"· escala {item.get('reference_code') or '--'} · agente {_agent_display(item)}{suffix}"
        )
    if len(berthed) > 8:
        lines.append(f"- +{len(berthed) - 8} navio(s) adicionais em cais.")
    answer = "\n".join(lines)
    return {
        "answer": answer,
        "sources": _source_from_answer("Navios em cais do portal", "OPS_BERTHED_VESSELS", answer, question),
        "answer_origin": "operational_live",
    }


def _status_label_for_port_call(item: dict) -> str:
    status = (item.get("status") or "").strip().lower()
    berth = item.get("berth_label") or item.get("berth")
    if status == "in_port" and is_anchorage_berth(berth):
        return "Em quadro"
    if status == "in_port":
        return "Em porto"
    if status == "departed":
        return "Concluída"
    if status == "scheduled":
        return "Prevista"
    return item.get("status_label") or status or "--"


def _format_thruster_label(value: object) -> str:
    clean = " ".join(str(value or "").strip().split()).lower()
    if clean in {"yes", "sim", "true", "1"}:
        return "Sim"
    if clean in {"no", "nao", "não", "false", "0"}:
        return "Não"
    return "Desconhecido"


def _answer_vessel_detail_query(question: str, clean_question: str) -> dict | None:
    if not VESSEL_DETAIL_QUERY_RE.search(clean_question):
        return None
    port_activity = _visible_activity(window_days=3650)
    vessel = _find_visible_vessel(question, port_activity)
    catalog_only = False
    if not vessel:
        vessel = _find_catalog_vessel(question)
        catalog_only = bool(vessel)
    if not vessel:
        return None

    port_call_id = vessel.get("id") or vessel.get("port_call_id") or ""
    maneuver_rows = _planned_rows_for_port_call(port_activity, port_call_id)
    berth_label = vessel.get("berth_label") or vessel.get("berth") or "--"
    lines = [
        f"Navio {vessel.get('vessel_name') or '--'}",
        f"- Escala: {'sem escala ativa associada' if catalog_only else vessel.get('reference_code') or '--'}",
        f"- Identificação: IMO {vessel.get('vessel_imo') or vessel.get('ship_imo_label') or '--'}; indicativo {vessel.get('vessel_call_sign') or vessel.get('ship_call_sign_label') or '--'}; bandeira {vessel.get('vessel_flag') or vessel.get('ship_flag_label') or '--'}.",
        f"- Ficha: tipo {vessel.get('ship_type_label') or vessel.get('vessel_type') or '--'}; LOA {_format_measure(vessel.get('ship_loa_label') or vessel.get('vessel_loa_m'), ' m')}; boca {_format_measure(vessel.get('ship_beam_label') or vessel.get('vessel_beam_m'), ' m')}; GT {_format_measure(vessel.get('ship_gt_label') or vessel.get('vessel_gt_t') or vessel.get('vessel_gt'))}; DWT {_format_measure(vessel.get('ship_dwt_label') or vessel.get('vessel_dwt_t'))}; calado máx. {_format_measure(vessel.get('ship_max_draft_label') or vessel.get('vessel_max_draft_m'), ' m')}.",
        f"- Meios do navio: bow thruster {vessel.get('ship_bow_thruster_label') or _format_thruster_label(vessel.get('vessel_bow_thruster'))}; stern thruster {vessel.get('ship_stern_thruster_label') or _format_thruster_label(vessel.get('vessel_stern_thruster'))}.",
        f"- Estado/localização: {'Ficha de catálogo' if catalog_only else _status_label_for_port_call(vessel)} · {berth_label}.",
        f"- Tráfego: {vessel.get('last_port') or '--'} -> {vessel.get('next_port') or '--'}.",
    ]
    if catalog_only and (vessel.get("service_rate_profile") or vessel.get("regular_line_calls_365d") or vessel.get("service_notes")):
        lines.append(
            f"- Serviços/taxas: {vessel.get('service_rate_profile') or '--'}; "
            f"base linha regular {vessel.get('regular_line_calls_365d') or '0'}; "
            f"{vessel.get('service_notes') or 'sem notas'}."
        )
    if not catalog_only:
        lines.append(f"- Agente de navegação: {_agent_display(vessel)}.")
    if maneuver_rows:
        lines.extend(["", "Manobras conhecidas:"])
        for row in maneuver_rows[-6:]:
            lines.append(_format_activity_maneuver_line(row))
            constraints = _constraint_labels_from_badges(row)
            if constraints != "--" or (row.get("tug_count_label") and row.get("tug_count_label") != "--"):
                lines.append(
                    f"  Meios/restrições: rebocadores {row.get('tug_count_label') or '--'}; restrições {constraints}."
                )
    else:
        if catalog_only:
            lines.extend(["", "Manobras conhecidas:", "- Sem escala ativa/arquivada visível ligada a esta ficha de catálogo."])
        else:
            lines.extend(["", "Manobras conhecidas:", "- Sem manobras planeadas ou arquivadas visíveis para esta escala."])
    answer = "\n".join(lines)
    return {
        "answer": answer,
        "sources": _source_from_answer(vessel.get("vessel_name") or "Navio", vessel.get("reference_code") or "OPS_VESSEL_DETAIL", answer, question),
        "answer_origin": "operational_live",
    }


def _answer_maneuver_actor_query(
    question: str,
    clean_question: str,
    *,
    plan: ChatExecutionPlan | None = None,
) -> dict | None:
    if not MANEUVER_APPROVER_QUERY_RE.search(clean_question):
        return None
    port_activity = _visible_activity(window_days=3650)
    has_vessel = bool(_find_visible_vessel(question, port_activity))
    has_date = bool(_question_date_parts(question))
    if not has_vessel and not has_date:
        return None
    rows = _activity_maneuver_rows(question, clean_question, port_activity, (plan.maneuver_lookup_type if plan else "") or "")
    if not rows:
        return None
    row = rows[0]
    validated_by = _pilot_display(row, "validated_by_label", "validated_by_profile")
    executed_by = _pilot_display(row, "executed_by_label", "executed_by_profile")
    answer = (
        f"A manobra {row.get('maneuver_label', 'Manobra').lower()} do {row.get('vessel_name', '--')} "
        f"({row.get('date_label') or '--'}, {_maneuver_route_label(row)}) foi aprovada por {validated_by}."
    )
    if executed_by and executed_by != "--":
        answer += f" O registo de execução indica {executed_by} como piloto executante."
    if row.get("maneuver_id"):
        answer += f" ID da manobra: {row.get('maneuver_id')}."
    return {
        "answer": answer,
        "sources": _source_from_answer("Arquivo de manobras do portal", row.get("maneuver_id") or "OPS_MANEUVER_ACTOR", answer, question),
        "answer_origin": "operational_live",
    }


def _answer_agent_lookup_query(
    question: str,
    clean_question: str,
    *,
    plan: ChatExecutionPlan | None = None,
) -> dict | None:
    if not AGENT_LOOKUP_QUERY_RE.search(clean_question):
        return None
    port_activity = _visible_activity(window_days=3650)
    vessel = _find_visible_vessel(question, port_activity)
    rows = _activity_maneuver_rows(question, clean_question, port_activity, (plan.maneuver_lookup_type if plan else "") or "")
    if rows:
        item = rows[0]
    elif vessel:
        item = vessel
    else:
        return None
    answer = (
        f"Agente de navegação do {item.get('vessel_name', 'navio')}: "
        f"{_agent_display(item)}."
    )
    if item.get("reference_code"):
        answer += f" Escala: {item.get('reference_code')}."
    if item.get("maneuver_id"):
        answer += f" Manobra: {item.get('maneuver_id')}."
    return {
        "answer": answer,
        "sources": _source_from_answer("Agente de navegação do portal", "OPS_AGENT_LOOKUP", answer, question),
        "answer_origin": "operational_live",
    }


def _answer_agent_agency_query(question: str, clean_question: str) -> dict | None:
    if not AGENT_AGENCY_QUERY_RE.search(clean_question):
        return None
    port_activity = _visible_activity(window_days=3650)
    candidates = []
    for collection in ("arrivals", "in_port", "departed", "planned_maneuvers", "archived_maneuvers", "archived_scales"):
        for item in port_activity.get(collection, []) or []:
            label = item.get("agent_label") or ""
            label_key = _operational_lookup_key(label)
            if label_key and f" {label_key} " in f" {clean_question} ":
                candidates.append(item)
    if not candidates:
        return None
    item = candidates[0]
    agent = item.get("agent_label") or "--"
    organization = _profile_organization(item.get("agent_profile"))
    if organization:
        answer = f"{agent} está registado como agente de navegação da agência {organization}."
    else:
        answer = f"{agent} está registado como agente de navegação, mas a agência não está preenchida no perfil visível."
    return {
        "answer": answer,
        "sources": _source_from_answer("Perfis de agentes do portal", "OPS_AGENT_AGENCY", answer, question),
        "answer_origin": "operational_live",
    }


def _answer_maneuver_id_query(
    question: str,
    clean_question: str,
    *,
    plan: ChatExecutionPlan | None = None,
) -> dict | None:
    if not (re.search(r"\b(id|identificador)\b", clean_question) and "manobra" in clean_question):
        return None
    port_activity = _visible_activity(window_days=3650)
    vessel = _find_visible_vessel(question, port_activity)
    has_specific_target = bool(vessel) or bool(_question_date_parts(question)) or bool(re.search(r"\bptset[a-z0-9]+\b", clean_question))
    if not has_specific_target:
        return None
    rows = _activity_maneuver_rows(question, clean_question, port_activity, (plan.maneuver_lookup_type if plan else "") or "")
    if rows:
        scale_reference = vessel.get("reference_code") if vessel else rows[0].get("reference_code")
        lines = []
        if "escala" in clean_question:
            lines.append(f"ID da escala: {scale_reference or '--'}.")
        if len(rows) == 1:
            row = rows[0]
            lines.append(
                f"ID da manobra de {_maneuver_noun_label(row)}: {row.get('maneuver_id') or '--'} "
                f"(escala {row.get('reference_code') or scale_reference or '--'} · {row.get('vessel_name', '--')} · "
                f"{row.get('date_label') or '--'} · {_maneuver_route_label(row)})."
            )
        else:
            lines.append("Manobras encontradas:")
            for row in rows[:6]:
                lines.append(_format_activity_maneuver_line(row, include_actors=False))
            if len(rows) > 6:
                lines.append(f"- +{len(rows) - 6} manobra(s) adicionais.")
        answer = "\n".join(lines)
        return {
            "answer": answer,
            "sources": _source_from_answer("IDs de manobras do portal", "OPS_MANEUVER_IDS", answer, question),
            "answer_origin": "operational_lookup",
        }
    return None


def _looks_like_recent_departures_query(clean_question: str) -> bool:
    if not clean_question:
        return False
    departure_terms = {"saiu", "sairam", "saida", "saidas", "partiu", "partiram", "departed", "departure"}
    recency_terms = {"recente", "recentes", "ultimos", "ultimas", "agora", "hoje", "ontem"}
    tokens = set(clean_question.split())
    has_departure = bool(tokens & departure_terms)
    has_recency = bool(tokens & recency_terms) or "algum navio" in clean_question or "navios" in tokens
    return has_departure and has_recency


def _answer_recent_departures_query(question: str, clean_question: str) -> dict | None:
    if not _looks_like_recent_departures_query(clean_question):
        return None
    port_activity = filter_port_activity_for_session(
        services.store.get_port_activity_snapshot(window_days=3650),
        public_operational=True,
    )
    departed_rows = [
        item for item in port_activity.get("archived_maneuvers", []) or []
        if (item.get("maneuver_type") or "").strip().lower() == "departure"
        and item.get("situation_class") == "completed"
    ]
    departed_rows.sort(key=_row_timestamp, reverse=True)
    departed = list(port_activity.get("departed", []) or [])
    if not departed_rows and not departed:
        answer = "Não há saídas registadas no portal no histórico operacional disponível."
        return {
            "answer": answer,
            "sources": [
                {
                    "document": "Saídas recentes do portal",
                    "source_id": "OPS_RECENT_DEPARTURES",
                    "retrieval_mode": "operational_live",
                    "snippet": answer,
                }
            ],
            "answer_origin": "operational_live",
        }
    lines = ["Sim. Saídas recentes registadas no portal:"]
    if departed_rows:
        for item in departed_rows[:5]:
            atd_label = item.get("actual_label") or item.get("execution_finished_label") or _local_iso_to_label(item.get("actual_value"))
            lines.append(
                f"- {item.get('vessel_name') or '--'} · ATD {atd_label} · {_maneuver_route_label(item)} · "
                f"manobra {item.get('maneuver_id') or '--'} · agente {_agent_display(item)} · "
                f"aprovada por {_pilot_display(item, 'validated_by_label', 'validated_by_profile')} · "
                f"executada por {_pilot_display(item, 'executed_by_label', 'executed_by_profile')}."
            )
    else:
        for item in departed[:5]:
            vessel_name = item.get("vessel_name") or "--"
            atd_label = item.get("departure_label") or _local_iso_to_label(item.get("departure_at"))
            origin = item.get("berth_label") or item.get("berth") or "--"
            destination = item.get("next_port") or "--"
            lines.append(f"- {vessel_name} · ATD {atd_label} · {origin} -> {destination} · agente {_agent_display(item)}.")
    answer = "\n".join(lines)
    return {
        "answer": answer,
        "sources": [
            {
                "document": "Saídas recentes do portal",
                "source_id": "OPS_RECENT_DEPARTURES",
                "retrieval_mode": "operational_live",
                "snippet": answer,
                "question": question,
            }
        ],
        "answer_origin": "operational_live",
    }


def _looks_like_expected_arrivals_query(clean_question: str) -> bool:
    if not clean_question:
        return False
    if _looks_like_route_duration_query(clean_question):
        return False
    arrival_terms = {"chegada", "chegadas", "chegar", "chega", "entrada", "entradas", "previstos", "prevista", "previstas", "esperado", "esperados", "esperadas", "eta"}
    tokens = set(clean_question.split())
    if not (tokens & arrival_terms):
        return False
    scope_markers = {"proximo", "proximos", "hoje", "amanha", "breve", "semana", "navio", "navios", "agendados", "agendadas"}
    if tokens & scope_markers:
        return True
    return "que vao chegar" in clean_question or "a chegar" in clean_question or "vao entrar" in clean_question


def _looks_like_route_duration_query(clean_question: str) -> bool:
    route_measure = re.search(
        r"\b(quanto tempo|tempo|demora|leva|distancia|percurso|milhas|milha nautica|milhas nauticas)\b",
        clean_question,
    )
    if not route_measure:
        return False
    has_origin = re.search(
        r"\b(desde|da entrada|do pilar|pilar|barra|fundeadouro|fundeadouros|canal norte|canal sul)\b",
        clean_question,
    )
    has_destination = re.search(
        r"\b(ate|ao|a|para)\b.*\b(lisnave|mitrena|estaleiro|terminal|cais|doca|"
        r"fundeadouro|sapec|tms|secil|tanquisado|eco\s*oil|ecooil|ecoil|"
        r"teporset|tepor\s*set|termitrena|autoeuropa|auto\s*europa|praias)\b",
        clean_question,
    )
    reverse_destination = re.search(
        r"\b(lisnave|mitrena|estaleiro|terminal|cais|doca|fundeadouro|sapec|tms|"
        r"secil|tanquisado|eco\s*oil|ecooil|ecoil|teporset|tepor\s*set|"
        r"termitrena|autoeuropa|auto\s*europa|praias)\b.*\b(ate|ao|a|para)\b",
        clean_question,
    )
    return bool(has_origin and (has_destination or reverse_destination))


def _answer_expected_arrivals_query(question: str, clean_question: str) -> dict | None:
    if not _looks_like_expected_arrivals_query(clean_question):
        return None
    port_activity = filter_port_activity_for_session(
        services.store.get_port_activity_snapshot(window_days=30),
        public_operational=True,
    )
    arrivals = list(port_activity.get("arrivals", []) or [])
    if not arrivals:
        answer = "Não há chegadas previstas registadas no portal para os próximos dias."
        return {
            "answer": answer,
            "sources": [
                {
                    "document": "Chegadas previstas do portal",
                    "source_id": "OPS_EXPECTED_ARRIVALS",
                    "retrieval_mode": "operational_live",
                    "snippet": answer,
                }
            ],
            "answer_origin": "operational_live",
        }
    lines = ["Chegadas previstas registadas no portal:"]
    for item in arrivals[:5]:
        vessel_name = item.get("vessel_name") or "--"
        eta_label = (
            item.get("eta_label")
            or item.get("arrival_label")
            or item.get("planned_label")
            or _local_iso_to_label(item.get("eta") or item.get("arrival_at") or item.get("date_value"))
        )
        origin = item.get("last_port") or item.get("local_origin") or "--"
        destination = item.get("berth_label") or item.get("berth") or item.get("local_destination") or "--"
        entry_maneuver_id = _short_maneuver_id(_arrival_entry_maneuver_id(item))
        lines.append(
            f"- {vessel_name} · ETA {eta_label} · {origin} -> {destination} · "
            f"escala {item.get('reference_code') or '--'} · entrada {entry_maneuver_id} · "
            f"agente {_agent_display(item)}."
        )
    answer = "\n".join(lines)
    return {
        "answer": answer,
        "sources": [
            {
                "document": "Chegadas previstas do portal",
                "source_id": "OPS_EXPECTED_ARRIVALS",
                "retrieval_mode": "operational_live",
                "snippet": answer,
                "question": question,
            }
        ],
        "answer_origin": "operational_live",
    }


def _looks_like_planned_maneuvers_query(clean_question: str) -> bool:
    if not clean_question:
        return False
    if PLANNED_MANEUVER_SUBJECT_RE.search(clean_question) and PLANNED_MANEUVER_MARKER_RE.search(clean_question):
        return True
    maneuver_terms = {"manobra", "manobras", "planeadas", "planeado", "planeados", "agendadas", "agendados"}
    tokens = set(clean_question.split())
    if not (tokens & maneuver_terms):
        return False
    planned_markers = {
        "proxima",
        "proximas",
        "hoje",
        "amanha",
        "previstas",
        "futuras",
        "agenda",
        "programa",
        "planeamento",
    }
    if tokens & planned_markers:
        return True
    return "que estao planeadas" in clean_question or "que vao acontecer" in clean_question or "proximas manobras" in clean_question


def _answer_planned_maneuvers_query(question: str, clean_question: str) -> dict | None:
    if not _looks_like_planned_maneuvers_query(clean_question):
        return None
    port_activity = filter_port_activity_for_session(
        services.store.get_port_activity_snapshot(window_days=30),
        public_operational=True,
    )
    planned = list(port_activity.get("planned_maneuvers", []) or [])
    if not planned:
        answer = "Não há manobras planeadas registadas no portal neste momento."
        return {
            "answer": answer,
            "sources": [
                {
                    "document": "Manobras planeadas do portal",
                    "source_id": "OPS_PLANNED_MANEUVERS",
                    "retrieval_mode": "operational_live",
                    "snippet": answer,
                }
            ],
            "answer_origin": "operational_live",
        }
    lines = ["Manobras planeadas registadas no portal:"]
    for item in planned[:5]:
        vessel_name = item.get("vessel_name") or "--"
        planned_label = item.get("planned_label") or item.get("date_label") or _local_iso_to_label(item.get("date_value"))
        maneuver_label = item.get("maneuver_label") or "Manobra"
        origin = item.get("local_origin") or "--"
        destination = item.get("local_destination") or "--"
        situation = item.get("situation_label") or ""
        situation_suffix = f" [{situation}]" if situation else ""
        maneuver_id = _short_maneuver_id(item.get("maneuver_id"))
        lines.append(
            f"- {vessel_name} · {maneuver_label} {planned_label} · {origin} -> {destination}{situation_suffix} · "
            f"manobra {maneuver_id} · agente {_agent_display(item)} · "
            f"piloto {_pilot_display(item)}."
        )
    answer = "\n".join(lines)
    return {
        "answer": answer,
        "sources": [
            {
                "document": "Manobras planeadas do portal",
                "source_id": "OPS_PLANNED_MANEUVERS",
                "retrieval_mode": "operational_live",
                "snippet": answer,
                "question": question,
            }
        ],
        "answer_origin": "operational_live",
    }


def _build_tide_lookup_answer(question: str) -> tuple[str, list[dict]]:
    summaries = [
        services.tide_service.summary_for_date(target_date)
        for target_date in services.tide_service.resolve_query_dates(question)
    ]
    if not summaries:
        return "", []

    lines: list[str] = []
    for summary in summaries:
        lines.append(f"Marés para {summary.get('date_label', summary.get('date', 'a data pedida'))} em {summary.get('location', 'Setúbal / Tróia')}:")
        events = summary.get("events") or []
        if not events:
            lines.append("- Sem eventos de maré registados.")
            continue
        for item in events:
            lines.append(
                f"- {item.get('time', '--')} — {item.get('type', '--')} de {item.get('height_m', '--')} m"
            )
        luminosity = summary.get("luminosity") or {}
        if luminosity.get("summary"):
            lines.append("")
            lines.append(f"- {luminosity['summary']}")
    context = services.tide_service.context_for_question(question)
    sources = [context] if context else []
    return "\n".join(lines), sources


def _build_daylight_answer(question: str, forecast: dict, weather_service) -> tuple[str, list[dict]] | None:
    days = _select_weather_days(forecast, weather_service, question, default_count=1)
    if not days:
        return None
    lines = []
    for day in days:
        lines.extend(
            [
                f"Período luminoso em Setúbal para {day.get('date_label') or day.get('date', '--')}:",
                f"- Nascer do sol: {day.get('sunrise') or '--'}",
                f"- Pôr do sol: {day.get('sunset') or '--'}",
                f"- Duração da luz do dia: {day.get('daylight_duration_label') or '--'}",
                f"- Período noturno: {day.get('night_duration_label') or '--'}",
            ]
        )
    context = weather_service.context_for_question(question)
    return "\n".join(lines), [context] if context else []


def _build_moon_answer(question: str, forecast: dict, weather_service) -> tuple[str, list[dict]] | None:
    days = _select_weather_days(forecast, weather_service, question, default_count=1)
    if not days:
        return None
    lines = []
    for day in days:
        lines.extend(
            [
                f"Fase da lua em Setúbal para {day.get('date_label') or day.get('date', '--')}:",
                f"- Fase: {day.get('moon_phase_icon') or '🌙'} {day.get('moon_phase_label') or day.get('moon_phase') or '--'}",
                f"- Iluminação: {day.get('moon_illumination') or '--'}%",
                f"- Nascer da lua: {day.get('moonrise') or '--'}",
                f"- Ocaso da lua: {day.get('moonset') or '--'}",
            ]
        )
    context = weather_service.context_for_question(question)
    return "\n".join(lines), [context] if context else []


def _build_today_forecast_answer(question: str, forecast: dict, weather_service) -> tuple[str, list[dict]] | None:
    days = _select_weather_days(forecast, weather_service, question, default_count=1)
    if not days:
        return None
    day = days[0]
    hours = _hours_for_weather_day(forecast, day)
    wind_summary = _weather_wind_summary(hours)
    location = forecast.get("location", {})
    current = forecast.get("current", {})
    lines = [
        f"Previsão meteorológica para hoje em {location.get('name', 'Setúbal')} ({location.get('localtime', '--')}):",
        f"- Agora: {current.get('condition', '--')}; {current.get('temp_c', '--')} °C; vento {current.get('wind_kts', '--')} kts {current.get('wind_dir', '--')}; rajadas {current.get('gust_kts', '--')} kts.",
        f"- Dia: {day.get('condition') or '--'}; temperatura {day.get('min_temp_c', '--')}–{day.get('max_temp_c', '--')} °C; precipitação total {day.get('rain_mm', '--')} mm.",
        (
            f"- Vento previsto: médio {wind_summary.get('avg_wind_kts') if wind_summary.get('avg_wind_kts') is not None else '--'} kts; "
            f"máximo {wind_summary.get('max_wind_kts') if wind_summary.get('max_wind_kts') is not None else day.get('max_wind_kts', '--')} kts; "
            f"rajada máxima {wind_summary.get('max_gust_kts') if wind_summary.get('max_gust_kts') is not None else day.get('max_gust_kts', '--')} kts."
        ),
        f"- Luz do dia: {day.get('sunrise') or '--'}–{day.get('sunset') or '--'} ({day.get('daylight_duration_label') or '--'}).",
    ]
    if day.get("moon_phase"):
        lines.append(
            f"- Lua: {day.get('moon_phase_icon') or '🌙'} {day.get('moon_phase_label') or day.get('moon_phase')} ({day.get('moon_illumination') or '--'}% iluminação)."
        )
    if hours:
        lines.extend(["", "Resumo das próximas horas:"])
        for hour in hours[:8]:
            lines.append(f"- {_format_weather_slot(hour)}")
        if len(hours) > 8:
            lines.append(f"- +{len(hours) - 8} slot(s) horários até ao fim do dia.")
    context = weather_service.context_for_question(question)
    return "\n".join(lines), [context] if context else []


def _build_next_days_forecast_answer(question: str, forecast: dict, weather_service) -> tuple[str, list[dict]] | None:
    days = _select_weather_days(forecast, weather_service, question, default_count=3)
    if not days:
        return None
    location = forecast.get("location", {})
    lines = [f"Previsão geral para {location.get('name', 'Setúbal')} nos próximos dias:"]
    for day in days[:3]:
        hours = _hours_for_weather_day(forecast, day)
        wind_summary = _weather_wind_summary(hours)
        avg_wind = wind_summary.get("avg_wind_kts")
        max_wind = wind_summary.get("max_wind_kts") if wind_summary.get("max_wind_kts") is not None else day.get("max_wind_kts")
        max_gust = wind_summary.get("max_gust_kts") if wind_summary.get("max_gust_kts") is not None else day.get("max_gust_kts")
        lines.append(
            f"- {day.get('date_label') or day.get('date', '--')}: {day.get('condition') or '--'}; "
            f"{day.get('min_temp_c', '--')}–{day.get('max_temp_c', '--')} °C; "
            f"vento médio {avg_wind if avg_wind is not None else '--'} kts, máx. {max_wind or '--'} kts, "
            f"rajadas {max_gust or '--'} kts; chuva {day.get('rain_mm', '--')} mm; "
            f"luz {day.get('sunrise') or '--'}–{day.get('sunset') or '--'}."
        )
    context = weather_service.context_for_question(question)
    return "\n".join(lines), [context] if context else []


def _build_weather_lookup_answer(
    question: str,
    clean_question: str,
    *,
    plan: ChatExecutionPlan | None = None,
) -> tuple[str, list[dict]]:
    weather_service = getattr(services, "weather_service", None)
    if not weather_service or not weather_service.enabled:
        return "A meteorologia live não está configurada neste ambiente.", []

    forecast = weather_service.get_forecast(days=3)
    if not forecast:
        return "Não consegui obter as condições meteorológicas atuais.", []

    if DAYLIGHT_QUERY_RE.search(clean_question):
        daylight_answer = _build_daylight_answer(question, forecast, weather_service)
        if daylight_answer:
            return daylight_answer
    if MOON_QUERY_RE.search(clean_question):
        moon_answer = _build_moon_answer(question, forecast, weather_service)
        if moon_answer:
            return moon_answer
    if WEATHER_FORECAST_DAYS_RE.search(clean_question):
        days_answer = _build_next_days_forecast_answer(question, forecast, weather_service)
        if days_answer:
            return days_answer
    if WEATHER_FORECAST_TODAY_RE.search(clean_question):
        today_answer = _build_today_forecast_answer(question, forecast, weather_service)
        if today_answer:
            return today_answer

    location = forecast.get("location", {})
    current = forecast.get("current", {})
    knowledge_dir = _active_knowledge_dir()
    safety_source = build_operational_safety_source(
        question,
        knowledge_dir,
        forecast=forecast,
        force=True,
    )
    safety_sources = [safety_source] if safety_source else []
    weather_mode = (plan.weather_mode if plan else "").strip().lower() or "context"
    timeline_answer = _build_weather_timeline_answer(
        question,
        forecast,
        weather_service,
        include_current=(weather_mode != "timeline"),
    )
    if timeline_answer:
        text, sources = timeline_answer
        return text, sources + safety_sources
    if weather_mode == "current" or CURRENT_WEATHER_RE.search(clean_question):
        lines = [
            f"Condições meteorológicas atuais em {location.get('name', 'Setúbal')} ({location.get('localtime', '--')}):",
            f"- Estado do tempo: {current.get('condition', '--')}",
            f"- Temperatura: {current.get('temp_c', '--')} °C",
            f"- Vento: {current.get('wind_kts', '--')} kts de {current.get('wind_dir', '--')}",
            f"- Rajadas: {current.get('gust_kts', '--')} kts",
            f"- Humidade: {current.get('humidity', '--')}%",
            f"- Visibilidade: {current.get('vis_km', '--')} km",
            f"- Precipitação: {current.get('precip_mm', '--')} mm",
        ]
        safety_status_lines = build_weather_safety_status_lines(forecast, knowledge_dir)
        if safety_status_lines:
            lines.append("")
            lines.extend(safety_status_lines)
        context = weather_service.context_source()
        sources = ([context] if context else []) + safety_sources
        return "\n".join(lines), sources

    context = weather_service.context_for_question(question)
    if context:
        return context.get("text") or context.get("snippet", ""), [context] + safety_sources
    return "Não consegui obter a previsão meteorológica pedida.", []


def _parse_weather_reference_datetime(forecast: dict) -> datetime | None:
    localtime = str((forecast.get("location") or {}).get("localtime") or "").strip()
    if not localtime:
        return None
    try:
        return datetime.strptime(localtime, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _build_weather_timeline_answer(
    question: str,
    forecast: dict,
    weather_service,
    *,
    include_current: bool = True,
) -> tuple[str, list[dict]] | None:
    clean_question = _operational_lookup_key(question)
    if not WEATHER_TIMELINE_RE.search(clean_question):
        return None

    reference_dt = _parse_weather_reference_datetime(forecast)
    if not reference_dt:
        return None

    target_dates: list[str] = []
    target_times: list[str] = []
    try:
        if hasattr(weather_service, "_resolve_query_dates"):
            target_dates = list(weather_service._resolve_query_dates(question, reference_dt.date()))
        if hasattr(weather_service, "_resolve_query_times"):
            target_times = list(weather_service._resolve_query_times(question))
    except Exception:
        target_dates = []
        target_times = []

    if target_dates:
        end_date = max(target_dates)
    else:
        end_date = reference_dt.date().isoformat()

    end_time = target_times[-1] if target_times else "23:59"
    try:
        end_dt = datetime.strptime(f"{end_date} {end_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None

    if end_dt <= reference_dt:
        return None

    timeline = build_weather_timeline(forecast, max_hours=72)
    selected_slots: list[dict] = []
    for item in timeline:
        timestamp = str(item.get("timestamp") or "").strip()
        if not timestamp:
            continue
        try:
            item_dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if reference_dt <= item_dt <= end_dt:
            selected_slots.append(item)

    if not selected_slots:
        return None

    current = forecast.get("current", {})
    location = forecast.get("location", {})
    lines: list[str] = []
    if include_current:
        lines.extend(
            [
                f"Condições meteorológicas atuais em {location.get('name', 'Setúbal')} ({location.get('localtime', '--')}):",
                f"- Estado do tempo: {current.get('condition', '--')}",
                f"- Temperatura: {current.get('temp_c', '--')} °C",
                f"- Vento: {current.get('wind_kts', '--')} kts de {current.get('wind_dir', '--')}",
                f"- Rajadas: {current.get('gust_kts', '--')} kts",
                f"- Humidade: {current.get('humidity', '--')}%",
                f"- Visibilidade: {current.get('vis_km', '--')} km",
                f"- Precipitação: {current.get('precip_mm', '--')} mm",
                "",
            ]
        )
    lines.append(f"Evolução prevista até {end_dt.strftime('%d/%m/%Y %H:%M')}:")
    for slot in selected_slots[:14]:
        lines.append(
            f"- {slot.get('date_label', slot.get('date', '--'))} {slot.get('time', '--')} | "
            f"{slot.get('condition', '--')} | {slot.get('temp_c', '--')} °C | "
            f"vento {slot.get('wind_kts', '--')} kts {slot.get('wind_dir', '--')} | "
            f"chuva {slot.get('chance_of_rain', '--')}%"
        )
    remaining = len(selected_slots) - 14
    if remaining > 0:
        lines.append(f"- +{remaining} slot(s) horários adicionais até ao fim da janela pedida.")

    context = weather_service.context_for_question(question)
    sources = [context] if context else []
    return "\n".join(lines), sources


def _collect_live_environment_sections(
    question: str,
    clean_question: str,
    *,
    plan: ChatExecutionPlan | None = None,
) -> list[tuple[str, str, list[dict]]]:
    plan = plan or build_chat_execution_plan(question)
    if not plan.has_live_facets:
        return []

    sections: list[tuple[str, str, list[dict]]] = []

    if "tides" in plan.live_facets:
        try:
            tide_answer, tide_sources = _build_tide_lookup_answer(question)
        except Exception as exc:
            logger.exception("Falha ao obter marés para consulta direta.")
            tide_answer = f"Falha ao obter marés: {exc}"
            tide_sources = []
        if tide_answer:
            sections.append(("tides", tide_answer, tide_sources))

    if "weather" in plan.live_facets:
        try:
            weather_answer, weather_sources = _build_weather_lookup_answer(
                question,
                clean_question,
                plan=plan,
            )
        except Exception as exc:
            logger.exception("Falha ao obter meteorologia para consulta direta.")
            weather_answer = f"Falha ao obter meteorologia: {exc}"
            weather_sources = []
        if weather_answer:
            sections.append(("weather", weather_answer, weather_sources))

    if "waves" in plan.live_facets:
        try:
            wave_answer, wave_sources = _build_wave_lookup_answer()
        except Exception as exc:
            logger.exception("Falha ao obter ondulação para consulta direta.")
            wave_answer = f"Falha ao obter leitura costeira: {exc}"
            wave_sources = []
        if wave_answer:
            sections.append(("waves", wave_answer, wave_sources))

    if "warnings" in plan.live_facets:
        try:
            warning_answer, warning_sources = _build_local_warning_lookup_answer(question, clean_question)
        except Exception as exc:
            logger.exception("Falha ao obter avisos locais para consulta direta.")
            warning_answer = f"Falha ao obter avisos locais: {exc}"
            warning_sources = []
        if warning_answer:
            sections.append(("warnings", warning_answer, warning_sources))
    return sections


def build_live_operational_sources(
    question: str,
    plan: ChatExecutionPlan | None = None,
) -> list[dict]:
    plan = plan or build_chat_execution_plan(question)
    clean_question = plan.normalized_question or _operational_lookup_key(question)
    live_sections = _collect_live_environment_sections(question, clean_question, plan=plan)
    sources: list[dict] = []
    labels = {
        "tides": "Marés live",
        "weather": "Meteorologia live",
        "waves": "Ondulação live",
        "warnings": "Avisos locais live",
    }
    for index, (facet, answer_text, section_sources) in enumerate(live_sections, start=1):
        if not answer_text:
            continue
        sources.append(
            {
                "source_id": f"LIVE{index}",
                "document": labels.get(facet, "Contexto live"),
                "chunk_id": 0,
                "score": 1.0,
                "retrieval_mode": "live_planner",
                "snippet": answer_text,
                "text": answer_text,
            }
        )
        sources.extend(source for source in section_sources if source)
    return sources


def _answer_live_environment_query(
    question: str,
    clean_question: str,
    *,
    plan: ChatExecutionPlan | None = None,
) -> dict | None:
    plan = plan or build_chat_execution_plan(question)
    live_sections = _collect_live_environment_sections(question, clean_question, plan=plan)

    answer_parts: list[str] = []
    sources: list[dict] = []
    for _, answer_text, section_sources in live_sections:
        if answer_text:
            answer_parts.append(answer_text)
        sources.extend(source for source in section_sources if source)

    if not answer_parts:
        return None
    return {
        "answer": "\n\n".join(answer_parts),
        "sources": sources,
        "answer_origin": "operational_live",
    }


def _build_wave_lookup_answer() -> tuple[str, list[dict]]:
    wave_service = getattr(services, "wave_service", None)
    if not wave_service or not getattr(wave_service, "enabled", False):
        return "A leitura costeira/ondulação live não está configurada neste ambiente.", []

    if hasattr(wave_service, "get_current_conditions"):
        conditions = wave_service.get_current_conditions()
    else:
        conditions = wave_service.probe_current_conditions()
    if not conditions:
        return "Não consegui obter a leitura costeira atual.", []

    lines = [
        "Leitura costeira atual:",
        f"- Última leitura: {conditions.get('last_reading_label', '--')}",
        f"- Altura significativa: {conditions.get('significant_height_label', '--')}",
        f"- Altura máxima: {conditions.get('max_height_label', '--')}",
        f"- Período médio: {conditions.get('mean_period_label', '--')}",
        f"- Período máx. obs.: {conditions.get('max_observed_period_label', '--')}",
        f"- Direção da ondulação: {conditions.get('direction', '--')}",
        f"- Temperatura da água: {conditions.get('water_temp_label', '--')}",
    ]
    if conditions.get("cache_stale") and conditions.get("source_error"):
        lines.append(f"- Nota: leitura em cache; origem live com erro: {conditions.get('source_error')}")
    context = wave_service.context_source() if hasattr(wave_service, "context_source") else None
    sources = [context] if context else []
    return "\n".join(lines), sources


def _looks_like_warning_count_query(clean_question: str) -> bool:
    if not clean_question:
        return False
    count_markers = {"quantos", "quantas", "quantidade", "numero", "número", "total"}
    list_markers = {"lista", "listar", "mostra", "mostra-me", "quais"}
    tokens = set(clean_question.split())
    return bool(tokens & count_markers) and not bool(tokens & list_markers)


def _build_local_warning_lookup_answer(
    question: str = "",
    clean_question: str = "",
    limit: int = 5,
) -> tuple[str, list[dict]]:
    warning_service = getattr(services, "local_warning_service", None)
    if not warning_service or not getattr(warning_service, "enabled", False):
        return "Os avisos locais live não estão configurados neste ambiente.", []

    warnings = warning_service.list_warnings()
    status = warning_service.status() if hasattr(warning_service, "status") else {}
    if not warnings:
        if status.get("error"):
            return f"Não consegui obter avisos locais em vigor: {status.get('error')}", []
        return "Sem avisos locais em vigor.", []

    lines: list[str]
    code_match = LOCAL_WARNING_CODE_RE.search(question or "")
    if code_match and hasattr(warning_service, "detail_text"):
        answer = warning_service.detail_text(code_match.group(1))
        context = {
            "source_id": "LW_DETAIL",
            "document": "Aviso local em vigor",
            "chunk_id": 0,
            "score": 1.0,
            "retrieval_mode": "live_api",
            "snippet": answer,
            "text": answer,
        }
        return answer, [context]
    if _looks_like_warning_count_query(clean_question):
        lines = [f"Existem {len(warnings)} aviso(s) locais em vigor."]
    else:
        lines = ["Avisos locais em vigor:"]
        for item in warnings[:limit]:
            lines.append(
                f"- {item.get('display_code', '--')} · {item.get('subject', '--')} · {item.get('location', '--')}"
            )
        remaining = len(warnings) - limit
        if remaining > 0:
            lines.append(f"- +{remaining} aviso(s) adicionais em vigor.")
    if status.get("stale") and status.get("error"):
        lines.append(f"- Nota: snapshot em cache; origem live com erro: {status.get('error')}")
    context = warning_service.context_source(limit=limit) if hasattr(warning_service, "context_source") else None
    sources = [context] if context else []
    return "\n".join(lines), sources
