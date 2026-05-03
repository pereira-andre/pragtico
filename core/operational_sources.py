"""Deterministic operational chat sources and live answers."""

import logging
import math
import re
from datetime import datetime

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
from core.operational_common import _operational_lookup_key, current_resolvable_port_calls
from core.rule_catalog import _active_knowledge_dir
from domain.berth_layout import is_anchorage_berth, slot_berth_options
from domain.chat_actions import visible_port_calls_from_activity
from domain.cost_engine import UP_NORMAL, UP_SHIFT_ALONG
from domain.lisnave_rules import lisnave_rule_snippet, should_include_lisnave_rule_source
from domain.navigation_lights import build_navigation_lights_source
from domain.operational_safety import (
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
    r"\b(previsao|previsoes|previsao meteorologica|previsoes meteorologicas|prognostico|"
    r"como vai estar|vai estar|meteo)\b.*\b(hoje|resto do dia|proximas horas|próximas horas)\b"
    r"|"
    r"\b(hoje|resto do dia)\b.*\b(previsao|previsoes|prognostico|meteorologia|meteo|tempo)\b"
)
WEATHER_FORECAST_DAYS_RE = re.compile(
    r"\b(proximos dias|próximos dias|dias seguintes|amanha|amanhã|depois de amanha|depois de amanhã|"
    r"previsao geral|previsões gerais|previsoes gerais)\b"
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
VESSEL_CATALOG_STATE_KEY = "port_call_vessel_catalog"
VESSEL_CATALOG_DELETED_KEYS_KEY = "deleted_keys"


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
        "- TMS 2 conta como 3 posições operacionais: A, B e C.",
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
    if "lisnave" not in clean_question:
        return None
    if not any(token in clean_question for token in ("hidrolift", "eclusa", "doca 31", "doca 32", "doca 33", "d31", "d32", "d33")):
        return None
    beam_m = _extract_hidrolift_beam_m(question)
    if beam_m is None or beam_m <= 32:
        return None

    beam_label = f"{beam_m:g}".replace(".", ",")
    answer = (
        "Não. Há um bloqueio dimensional antes de discutir a hora da manobra: "
        f"o Hidrolift/Docas 31-33 da LISNAVE admite boca máxima de 32 m e o navio indicado tem {beam_label} m de boca. "
        "Assim, a manobra não deve seguir para o Hidrolift como está marcada; será preciso escolher outro cais/doca ou obter validação operacional específica.\n\n"
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


def _answer_safety_hard_limit(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\b(manobra|manobras|sair|saida|saída|atracar|entrar|navio|reboque|reboques|rebocador|rebocadores)\b", clean_question):
        return None

    wind_kts = _extract_wind_kts_from_question(question)
    if wind_kts is not None and wind_kts > 30:
        wind_label = f"{wind_kts:g}".replace(".", ",")
        answer = (
            f"Não. Com vento sustentado ou rajada superior a 30 kt ({wind_label} kt no caso indicado), "
            "as manobras ficam suspensas por segurança. Ter mais rebocadores não anula este limite. "
            "Se a suspensão foi acionada por vento, a retoma só deve ser considerada quando o vento baixar para menos de 25 kt."
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
                    "snippet": "Com vento superior a 30 kt, todas as manobras ficam suspensas; retoma apenas abaixo de 25 kt.",
                }
            ],
            "answer_origin": "operational_safety_limit",
        }

    if re.search(r"\b(nevoeiro|nevoa|neblina|fog|mist)\b", clean_question):
        answer = (
            "Não. Com nevoeiro em porto, as manobras ficam suspensas até a visibilidade operacional ser restaurada. "
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


def _answer_tug_guidance_direct(question: str, clean_question: str) -> dict | None:
    if not re.search(r"\b(reboque|reboques|rebocador|rebocadores)\b", clean_question):
        return None
    if not re.search(r"\b(quantos|numero|número|aconselha|aconselhas|recomenda|recomendas|necessarios|necessários|leva|suficiente|onde|posicion|meter|colocar|proa|popa|costado)\b", clean_question):
        return None

    source = build_tug_operational_guidance_source(question, _active_knowledge_dir() or "knowledge")
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
        for item in relevant_positioning[:3]:
            answer_lines.append(f"- {item}")
        return {
            "answer": "\n".join(answer_lines),
            "sources": [source],
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
        for item in relevant_positioning[:4]:
            answer_lines.append(f"- {item}")
        if applicable:
            answer_lines.append(f"Base/minimo a respeitar: {applicable[0]}")
        return {
            "answer": "\n".join(answer_lines),
            "sources": [source],
            "answer_origin": "operational_tug_guidance",
        }

    if not applicable:
        return None
    first_rule = applicable[0]
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
    ]
    if len(applicable) > 1:
        answer_lines.append("Outras regras relevantes: " + " ".join(applicable[1:3]))
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
    return {
        "answer": "\n".join(answer_lines),
        "sources": [source],
        "answer_origin": "operational_tug_guidance",
    }


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
            or line.startswith("- PINHEIRO")
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
    clean_question = plan.normalized_question or _operational_lookup_key(question)
    emergency_answer = _answer_emergency_response_direct(question, clean_question)
    if emergency_answer:
        return emergency_answer
    navigation_lights_answer = _answer_navigation_lights_direct(question, clean_question)
    if navigation_lights_answer:
        return navigation_lights_answer
    unclear_answer = _answer_unclear_operational_fragment(question, clean_question)
    if unclear_answer:
        return unclear_answer
    safety_answer = _answer_safety_hard_limit(question, clean_question)
    if safety_answer:
        return safety_answer
    hard_limit_answer = _answer_lisnave_hidrolift_hard_limit(question, clean_question)
    if hard_limit_answer:
        return hard_limit_answer
    tug_guidance_answer = _answer_tug_guidance_direct(question, clean_question)
    if tug_guidance_answer:
        return tug_guidance_answer
    route_answer = route_transit_answer(question, clean_question)
    if route_answer:
        return route_answer
    if plan.requires_llm_synthesis:
        return None

    live_environment_answer = _answer_live_environment_query(question, clean_question, plan=plan)
    if live_environment_answer:
        return live_environment_answer
    berthed_vessels_answer = _answer_berthed_vessels_query(question, clean_question)
    if berthed_vessels_answer:
        return berthed_vessels_answer
    vessel_detail_answer = _answer_vessel_detail_query(question, clean_question)
    if vessel_detail_answer:
        return vessel_detail_answer
    maneuver_actor_answer = _answer_maneuver_actor_query(question, clean_question, plan=plan)
    if maneuver_actor_answer:
        return maneuver_actor_answer
    agent_lookup_answer = _answer_agent_lookup_query(question, clean_question, plan=plan)
    if agent_lookup_answer:
        return agent_lookup_answer
    agent_agency_answer = _answer_agent_agency_query(question, clean_question)
    if agent_agency_answer:
        return agent_agency_answer
    maneuver_id_answer = _answer_maneuver_id_query(question, clean_question, plan=plan)
    if maneuver_id_answer:
        return maneuver_id_answer
    recent_departures_answer = _answer_recent_departures_query(question, clean_question)
    if recent_departures_answer:
        return recent_departures_answer
    expected_arrivals_answer = _answer_expected_arrivals_query(question, clean_question)
    if expected_arrivals_answer:
        return expected_arrivals_answer
    planned_maneuvers_answer = _answer_planned_maneuvers_query(question, clean_question)
    if planned_maneuvers_answer:
        return planned_maneuvers_answer

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
        return {"answer": answer, "sources": [], "answer_origin": "operational_lookup"}

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
    return {
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
    }


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
