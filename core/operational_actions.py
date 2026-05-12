"""Operational chat action proposal and execution helpers."""

import re
from datetime import datetime, timezone

from flask import session

from core import services
from core.access_control import ensure_port_call_scope_access, filter_port_activity_for_session
from core.form_helpers import (
    _build_created_port_call_message,
    build_departure_plan_note,
    build_entry_request_note,
    build_pilot_report_note,
    build_shift_plan_note,
    ensure_maneuver_hour_capacity_for_approval,
    ensure_portal_berth_is_available,
    ensure_portal_berth_is_physically_available,
    normalize_portal_berth,
    parse_local_datetime_input,
    parse_optional_local_datetime_input,
    require_form_text,
)
from core.maneuver_context import build_scale_context
from core.operational_common import (
    _operational_lookup_key,
    current_resolvable_port_calls,
    current_visible_port_calls,
)
from core.operational_sources import _build_tide_lookup_answer
from core.rule_catalog import available_rule_code_titles, build_rule_catalog_text
from core.validators import normalize_thruster_state, validate_not_past_datetime
from domain.chat_actions import (
    ACTION_SPECS,
    action_for_maneuver_type,
    action_prefers_explicit_maneuver_id,
    build_operational_action_prompt,
    build_pending_action_update_prompt,
    build_slash_help,
    candidate_maneuvers_for_action,
    extract_json_object,
    extract_pending_field_updates,
    extract_pending_target_updates,
    format_action_summary,
    infer_maneuver_type,
    looks_like_abort_payload,
    looks_like_maneuver_report_payload,
    looks_like_port_call_payload,
    merge_action_candidate,
    normalize_action_candidate,
    proposal_missing_field_labels,
    resolve_maneuver,
    resolve_port_call,
)
from domain.colreg_rules import COLREG_SOURCE_DOCUMENT, format_colreg_catalog, format_colreg_rule, parse_colreg_rule_number
from domain.cost_engine import (
    ManoeuvreInput,
    ManoeuvreType,
    calculate_scale_cost,
    format_cost_summary,
)
from storage import normalize_constraint_codes


def pending_action_state_key(username: str, conversation_id: str) -> str:
    """Return the runtime state key for a user's pending chat action in a given conversation."""
    return f"chat_pending_action:{username}:{conversation_id}"


def looks_like_pending_confirmation(question: str) -> bool:
    """Return True if the question is a simple confirmation phrase like 'sim' or 'ok'."""
    clean = _operational_lookup_key(question)
    return clean in {
        "ok", "okay", "okey", "sim", "confirma", "confirmar",
        "confirmado", "podes confirmar", "avanca", "avancar", "segue",
    }


def refresh_proposal_missing_fields(proposal: dict) -> dict:
    """Recompute and update the missing_fields list on a proposal dict in-place."""
    proposal["missing_fields"] = proposal_missing_field_labels(
        proposal.get("action", ""),
        proposal.get("fields") or {},
        proposal.get("target") or {},
    )
    return proposal


def _float_from_port_value(value: str | float | int | None) -> float:
    text = str(value or "").strip().replace(" ", "").replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else 0.0


def _maneuver_type_label(maneuver_type: str) -> str:
    return {
        "entry": "entrada",
        "departure": "saída",
        "shift": "mudança",
        "anchoring": "fundear/suspender",
    }.get((maneuver_type or "").strip().lower(), "manobra")


def _maneuver_type_from_argument(argument: str) -> str:
    clean = _operational_lookup_key(argument)
    if re.search(r"\b(saida|departure|sair)\b", clean):
        return "departure"
    if re.search(r"\b(mudanca|shift|mudar)\b", clean):
        return "shift"
    if re.search(r"\b(entrada|entry|entrar)\b", clean):
        return "entry"
    return ""


def _argument_without_maneuver_type(argument: str) -> str:
    clean = re.sub(
        r"\b(entrada|sa[ií]da|mudan[cç]a|entry|departure|shift|tipo de manobra|tipo|id|ref)\b",
        " ",
        str(argument or ""),
        flags=re.IGNORECASE,
    )
    clean = clean.replace(":", " ")
    return " ".join(clean.split())


def _resolve_port_call_for_slash_argument(argument: str) -> dict | None:
    clean_argument = " ".join((argument or "").strip().split())
    if not clean_argument:
        return None
    port_calls = current_resolvable_port_calls()
    lookup_argument = _argument_without_maneuver_type(clean_argument) or clean_argument
    for target in (
        {"maneuver_id": lookup_argument},
        {"reference_code": lookup_argument},
        {"vessel_name": lookup_argument},
        {"reference_code": clean_argument},
        {"vessel_name": clean_argument},
    ):
        match = resolve_port_call(port_calls, target)
        if match:
            return services.store.get_port_call(match["id"])
    lookup = _operational_lookup_key(clean_argument)
    by_imo = [
        item for item in port_calls
        if lookup and _operational_lookup_key(item.get("vessel_imo")) == lookup
    ]
    if len(by_imo) == 1:
        return services.store.get_port_call(by_imo[0]["id"])
    full_imo_matches = []
    for item in port_calls:
        item_id = item.get("id")
        if not item_id:
            continue
        try:
            full_port_call = services.store.get_port_call(item_id)
        except Exception:
            continue
        if lookup and _operational_lookup_key(full_port_call.get("vessel_imo")) == lookup:
            full_imo_matches.append(full_port_call)
    if len(full_imo_matches) == 1:
        return full_imo_matches[0]
    return None


def _resolve_maneuver_for_slash_argument(port_call: dict, argument: str) -> tuple[dict | None, str]:
    maneuver_type = _maneuver_type_from_argument(argument)
    clean_argument = " ".join((argument or "").strip().split())
    lookup_argument = _argument_without_maneuver_type(clean_argument)
    explicit_id = lookup_argument or clean_argument
    if explicit_id:
        for maneuver in port_call.get("maneuver_history", []) or []:
            maneuver_id = _operational_lookup_key(maneuver.get("id"))
            target_id = _operational_lookup_key(explicit_id)
            if target_id and (maneuver_id == target_id or maneuver_id.startswith(target_id)):
                return maneuver, maneuver.get("type", "")
    if maneuver_type:
        return resolve_maneuver(port_call, "edit_maneuver_plan", maneuver_type), maneuver_type
    maneuvers = list(port_call.get("maneuver_history", []) or [])
    if len(maneuvers) == 1:
        return maneuvers[0], maneuvers[0].get("type", "")
    return None, maneuver_type


def _scale_cost_summary(port_call: dict, *, maneuver: dict | None = None) -> str:
    gt = _float_from_port_value(port_call.get("vessel_gt_t") or port_call.get("vessel_gt"))
    if gt <= 0:
        return "Não consigo estimar custo: falta GT válido na ficha do navio."
    vessel_type = port_call.get("vessel_type") or "restantes"
    if maneuver:
        maneuver_types = [(maneuver.get("type") or "entry").strip().lower()]
        include_tup = False
    else:
        maneuver_types = [
            (item.get("type") or "").strip().lower()
            for item in port_call.get("maneuver_history", []) or []
            if (item.get("type") or "").strip().lower()
        ] or ["entry"]
        include_tup = True
    inputs = []
    for maneuver_type in maneuver_types:
        try:
            enum_value = ManoeuvreType(maneuver_type)
        except ValueError:
            enum_value = ManoeuvreType.ENTRY
        inputs.append(ManoeuvreInput(manoeuvre_type=enum_value, gt=gt))
    estimate = calculate_scale_cost(
        vessel_name=port_call.get("vessel_name") or "Navio",
        gt=gt,
        vessel_type=vessel_type,
        manoeuvres=inputs,
        include_tup=include_tup,
    )
    return format_cost_summary(estimate)


def _profile_organization(profile: dict | None) -> str:
    return " ".join(str((profile or {}).get("organization") or "").split())


def _agent_label_with_agency(port_call: dict) -> str:
    label = " ".join(str(port_call.get("agent_label") or "--").split()) or "--"
    organization = _profile_organization(port_call.get("agent_profile") or port_call.get("created_by_profile"))
    if organization and label not in {"--", organization}:
        return f"{label} ({organization})"
    if organization:
        return organization
    return f"{label} (agência não registada)" if label != "--" else "--"


def _pilot_label(profile_label: str, profile: dict | None) -> str:
    label = " ".join(str(profile_label or "--").split()) or "--"
    organization = _profile_organization(profile)
    if organization and label not in {"--", organization}:
        return f"{label} ({organization})"
    return organization or label


def _planned_maneuver_datetime(item: dict) -> datetime | None:
    for key in ("date_value", "planned_value", "planned_at"):
        value = item.get(key)
        if not value:
            continue
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _planned_maneuver_sort_key(item: dict) -> tuple[float, str]:
    planned_dt = _planned_maneuver_datetime(item)
    return (
        planned_dt.timestamp() if planned_dt else float("inf"),
        " ".join(str(item.get("vessel_name") or "").split()),
    )


def _planned_maneuver_status_key(item: dict) -> str:
    clean_status = (item.get("situation_class") or "").strip().lower()
    if clean_status:
        return clean_status
    label_key = _operational_lookup_key(item.get("situation_label") or "")
    if "aprovad" in label_key:
        return "approved"
    if "pendent" in label_key or "previst" in label_key:
        return "pending"
    return label_key


def _planned_maneuver_day_time(item: dict) -> tuple[str, str]:
    planned_dt = _planned_maneuver_datetime(item)
    day_label = " ".join(str(item.get("date_label") or "").split())
    time_label = " ".join(str(item.get("planned_label") or "").split())
    if planned_dt:
        if not day_label:
            day_label = planned_dt.strftime("%d/%m/%Y")
        if not time_label:
            time_label = planned_dt.strftime("%H:%M")
    return day_label or "--", time_label or "--"


def _format_planning_query_answer(status_filter: str = "") -> str:
    port_activity = filter_port_activity_for_session(
        services.store.get_port_activity_snapshot(window_days=3650),
        public_operational=True,
    )
    planned = list(port_activity.get("planned_maneuvers", []) or [])
    clean_filter = (status_filter or "").strip().lower()
    if clean_filter:
        planned = [
            item
            for item in planned
            if _planned_maneuver_status_key(item) == clean_filter
        ]
    emoji = "🗓️"
    if clean_filter == "approved":
        emoji = "✅"
    elif clean_filter == "pending":
        emoji = "⏳"
    if not planned:
        if clean_filter == "approved":
            return f"{emoji} Não há manobras planeadas/aprovadas no planeamento neste momento."
        if clean_filter == "pending":
            return f"{emoji} Não há manobras previstas/pendentes no planeamento neste momento."
        return f"{emoji} Não há manobras no planeamento neste momento."

    planned.sort(key=_planned_maneuver_sort_key)
    title = "Planeamento de manobras"
    if clean_filter == "approved":
        title = "Manobras planeadas/aprovadas"
    elif clean_filter == "pending":
        title = "Manobras previstas/pendentes"
    lines = [f"{emoji} {title} ({len(planned)}):"]
    for item in planned:
        day_label, time_label = _planned_maneuver_day_time(item)
        maneuver_id = str(item.get("maneuver_id") or "").strip()
        short_id = maneuver_id[:8].upper() if maneuver_id else "--"
        origin = " ".join(str(item.get("local_origin") or "--").split()) or "--"
        destination = " ".join(str(item.get("local_destination") or "--").split()) or "--"
        lines.append(
            f"- {day_label} {time_label} · {item.get('maneuver_label') or 'Manobra'} · "
            f"{item.get('vessel_name') or '--'} · {origin} -> {destination} · "
            f"Estado: {item.get('situation_label') or '--'} · "
            f"Manobra: {short_id} · Agente/agência: {_agent_label_with_agency(item)}"
        )
    return "\n".join(lines)


def _format_scale_query_answer(port_call: dict, *, include_cost: bool = False) -> str:
    lines = [
        f"Escala {port_call.get('reference_code') or port_call.get('id', '--')}",
        f"Navio: {port_call.get('vessel_name', '--')}",
        f"Estado: {port_call.get('status_label') or port_call.get('status') or '--'}",
        f"Cais/fundeadouro: {port_call.get('berth_label') or port_call.get('berth') or '--'}",
        f"ETA: {port_call.get('eta_label') or '--'}",
        f"ATA: {port_call.get('ata_label') or '--'}",
        f"ATD: {port_call.get('departure_label') or '--'}",
        f"Agente de navegação: {_agent_label_with_agency(port_call)}",
    ]
    if include_cost:
        lines.extend(["", _scale_cost_summary(port_call)])
    return "\n".join(lines)


def _format_maneuver_query_answer(port_call: dict, maneuver: dict, *, include_cost: bool = False) -> str:
    maneuver_type = maneuver.get("type", "")
    short_id = (maneuver.get("id") or "")[:8].upper() or "--"
    lines = [
        f"Manobra {short_id}",
        f"Navio: {port_call.get('vessel_name', '--')}",
        f"Escala: {port_call.get('reference_code') or '--'}",
        f"Tipo: {_maneuver_type_label(maneuver_type)}",
        f"Estado: {maneuver.get('state_label') or maneuver.get('state') or '--'}",
        f"Origem: {maneuver.get('origin') or '--'}",
        f"Destino: {maneuver.get('destination') or '--'}",
        f"Prevista: {maneuver.get('planned_label') or '--'}",
        f"Executada: {maneuver.get('completed_label') or maneuver.get('finished_label') or '--'}",
        f"Aprovada por: {_pilot_label(maneuver.get('pilot_label'), maneuver.get('pilot_profile'))}",
        f"Executada/registada por: {_pilot_label(maneuver.get('reported_by_label'), maneuver.get('reported_by_profile'))}",
    ]
    if include_cost:
        lines.extend(["", _scale_cost_summary(port_call, maneuver=maneuver)])
    return "\n".join(lines)


def _format_vessel_query_answer(port_call: dict) -> str:
    return "\n".join(
        [
            f"Navio: {port_call.get('vessel_name', '--')}",
            f"IMO: {port_call.get('vessel_imo') or '--'}",
            f"Indicativo: {port_call.get('vessel_call_sign') or '--'}",
            f"Bandeira: {port_call.get('vessel_flag') or '--'}",
            f"Tipo: {port_call.get('vessel_type') or '--'}",
            f"LOA: {port_call.get('vessel_loa_m') or '--'} m",
            f"Boca: {port_call.get('vessel_beam_m') or '--'} m",
            f"GT: {port_call.get('vessel_gt_t') or '--'}",
            f"DWT: {port_call.get('vessel_dwt_t') or '--'}",
            f"Calado max.: {port_call.get('vessel_max_draft_m') or '--'} m",
            f"Bow thruster: {port_call.get('ship_bow_thruster_label') or port_call.get('vessel_bow_thruster') or '--'}",
            f"Stern thruster: {port_call.get('ship_stern_thruster_label') or port_call.get('vessel_stern_thruster') or '--'}",
            f"Estado/localização: {port_call.get('status_label') or port_call.get('status') or '--'} · {port_call.get('berth_label') or port_call.get('berth') or '--'}",
            f"Agente de navegação: {_agent_label_with_agency(port_call)}",
            f"Ultima escala visivel: {port_call.get('reference_code') or '--'}",
        ]
    )


def answer_slash_query(command: str, argument: str, role: str) -> dict:
    """Answer direct slash-query commands without entering the operational proposal flow."""
    clean_argument = " ".join((argument or "").strip().split())
    if command == "help":
        return {"answer": build_slash_help(role), "sources": [], "answer_origin": "slash_help"}
    if command == "local_warnings":
        if not getattr(services, "local_warning_service", None) or not services.local_warning_service.enabled:
            return {"answer": "Os avisos locais não estão configurados neste ambiente.", "sources": [], "answer_origin": "slash_local_warnings"}
        try:
            clean_argument = " ".join((clean_argument or "").strip().split())
            return {
                "answer": (
                    services.local_warning_service.detail_text(clean_argument)
                    if clean_argument
                    else services.local_warning_service.browse_text()
                ),
                "sources": [],
                "answer_origin": "slash_local_warnings",
            }
        except Exception as exc:
            return {"answer": f"Falha ao obter avisos locais: {exc}", "sources": [], "answer_origin": "slash_local_warnings"}
    if command == "wave":
        if not getattr(services, "wave_service", None) or not services.wave_service.enabled:
            return {"answer": "A leitura costeira não está configurada neste ambiente.", "sources": [], "answer_origin": "slash_wave"}
        try:
            return {
                "answer": services.wave_service.summary_text(),
                "sources": [],
                "answer_origin": "slash_wave",
            }
        except Exception as exc:
            return {"answer": f"Falha ao obter leitura costeira: {exc}", "sources": [], "answer_origin": "slash_wave"}
    if command == "tides":
        tide_answer, _sources = _build_tide_lookup_answer(clean_argument or "hoje")
        return {"answer": tide_answer or "Sem dados de maré.", "sources": [], "answer_origin": "slash_tides"}
    if command == "weather":
        if not services.weather_service.enabled:
            return {"answer": "A meteorologia não está configurada neste ambiente.", "sources": [], "answer_origin": "slash_weather"}
        try:
            forecast = services.weather_service.get_forecast(days=3)
        except Exception as exc:
            return {"answer": f"Falha ao obter meteorologia: {exc}", "sources": [], "answer_origin": "slash_weather"}
        question = clean_argument or "hoje"
        context = services.weather_service.context_for_question(question)
        if context:
            return {"answer": context.get("text") or context.get("snippet", "Sem previsão disponível."), "sources": [], "answer_origin": "slash_weather"}
        return {"answer": f"Sem previsão disponível para {question}.", "sources": [], "answer_origin": "slash_weather"}
    if command in {"planning", "planning_approved", "planning_pending"}:
        status_filter = ""
        if command == "planning_approved":
            status_filter = "approved"
        elif command == "planning_pending":
            status_filter = "pending"
        return {
            "answer": _format_planning_query_answer(status_filter),
            "sources": [],
            "answer_origin": "slash_planning",
        }
    if command == "colreg_list":
        return {
            "answer": format_colreg_catalog(),
            "sources": [{"document": COLREG_SOURCE_DOCUMENT, "retrieval_mode": "colreg_catalog"}],
            "answer_origin": "slash_colreg",
        }
    if command == "colreg_rule":
        return {
            "answer": format_colreg_rule(parse_colreg_rule_number(clean_argument)),
            "sources": [{"document": COLREG_SOURCE_DOCUMENT, "retrieval_mode": "colreg_rule"}],
            "answer_origin": "slash_colreg",
        }
    if command == "rule":
        code_match = re.search(r"\b(\d{3})\b", clean_argument)
        if not code_match:
            return {
                "answer": build_rule_catalog_text(),
                "sources": [],
                "answer_origin": "slash_rule",
            }
        code = code_match.group(1)
        available_titles = available_rule_code_titles()
        title = available_titles.get(code)
        if not title:
            return {
                "answer": f"Não encontrei a regra {code} neste ambiente.\n\n{build_rule_catalog_text()}",
                "sources": [],
                "answer_origin": "slash_rule",
            }
        if not services.rag.can_generate():
            return {
                "answer": f"Pedido da regra {title} recebido, mas o provider está indisponível neste ambiente.",
                "sources": [],
                "answer_origin": "slash_rule",
            }
        answer = services.rag.answer(
            question=f"Resume a regra {title} e destaca os pontos operacionais mais importantes.",
            role=role,
            history=[],
            supplemental_sources=[],
            trusted_answers=[],
        )
        answer["answer_origin"] = "slash_rule"
        return answer
    if command in {"consult_scale", "consult_scale_cost"}:
        port_call = _resolve_port_call_for_slash_argument(clean_argument)
        if not port_call:
            return {
                "answer": "Não encontrei uma escala visível para esse identificador. Usa a Ref, ID curto ou nome do navio.",
                "sources": [],
                "answer_origin": "slash_consult_scale",
            }
        return {
            "answer": _format_scale_query_answer(port_call, include_cost=command == "consult_scale_cost"),
            "sources": [],
            "answer_origin": "slash_consult_scale",
        }
    if command in {"consult_maneuver", "consult_maneuver_cost"}:
        port_call = _resolve_port_call_for_slash_argument(clean_argument)
        if not port_call:
            return {
                "answer": "Não encontrei uma manobra visível para esse identificador. Usa ID da manobra ou Ref + tipo.",
                "sources": [],
                "answer_origin": "slash_consult_maneuver",
            }
        maneuver, maneuver_type = _resolve_maneuver_for_slash_argument(port_call, clean_argument)
        if not maneuver:
            hint = " Indica também o tipo: entrada, saída ou mudança." if not maneuver_type else ""
            return {
                "answer": f"Encontrei a escala, mas não consegui escolher uma manobra única.{hint}",
                "sources": [],
                "answer_origin": "slash_consult_maneuver",
            }
        return {
            "answer": _format_maneuver_query_answer(
                port_call,
                maneuver,
                include_cost=command == "consult_maneuver_cost",
            ),
            "sources": [],
            "answer_origin": "slash_consult_maneuver",
        }
    if command == "consult_vessel":
        port_call = _resolve_port_call_for_slash_argument(clean_argument)
        if not port_call:
            return {
                "answer": "Não encontrei esse navio nas escalas visíveis. Usa nome parcial ou IMO.",
                "sources": [],
                "answer_origin": "slash_consult_vessel",
            }
        return {
            "answer": _format_vessel_query_answer(port_call),
            "sources": [],
            "answer_origin": "slash_consult_vessel",
        }
    return {"answer": "Comando não suportado.", "sources": [], "answer_origin": "slash_unknown"}


def action_target_port_call(port_call_id: str) -> dict:
    """Fetch a port call and enforce agent scope access, returning the decorated record."""
    port_call = services.store.get_port_call(port_call_id)
    if (session.get("role") or "").strip().lower() == "agente":
        ensure_port_call_scope_access(port_call_id)
    return port_call


def heuristic_operational_proposal(question: str, role: str, port_calls: list[dict]) -> dict | None:
    """Apply deterministic pattern matching to derive an operational action proposal from the question."""
    from domain.chat_actions import _extract_labelled_values

    clean = _operational_lookup_key(question)
    if not clean:
        return None

    extracted = _extract_labelled_values(question)

    explicit_scale_request = bool(
        re.search(r"\b(regist\w*\s+(esta\s+)?escala|nova escala|cria\w*\s+escala|register scale)\b", clean)
    )
    if (
        (explicit_scale_request or looks_like_port_call_payload(question))
        and "manobra" not in clean
        and not looks_like_maneuver_report_payload(question)
        and not looks_like_abort_payload(question)
    ):
        vessel_name = extracted.pop("vessel_name", "")
        maneuver_type = "entry"
        if re.search(r"\b(saida|saída|departure)\b", clean):
            maneuver_type = "departure"
        elif re.search(r"\b(mudanca|mudança|shift)\b", clean):
            maneuver_type = "shift"
        proposal = normalize_action_candidate(
            {
                "intent": "action", "action": "create_port_call", "confidence": 0.99,
                "reason": "Heurística: registo de escala com campos extraídos da mensagem.",
                "target": {"vessel_name": vessel_name, "maneuver_type": maneuver_type},
                "fields": extracted, "missing_fields": [],
            },
            role,
        )
        if proposal and proposal.get("intent") == "action":
            return proposal
        return None

    wants_previsto = bool(re.search(r"\b(previsto|prevista|planeado|planeada)\b", clean))

    action_verb = ""
    if re.search(r"\b(aprova|approve|aprovar|valida|validar|confirma|confirmar)\b", clean):
        action_verb = "approve"
    elif re.search(r"\b(aborta|abortar|cancela|cancelar|anula|anular)\b", clean):
        action_verb = "abort"
    elif re.search(r"\b(regist|registar|registra|relatorio|relatório|realizada|concluida|concluído|fechar|fecha|concluir)\b", clean):
        action_verb = "report"
    elif wants_previsto:
        action_verb = "approve"
    if not action_verb:
        return None

    maneuver_type = ""
    if re.search(r"\b(saida|saída|departure|sair)\b", clean):
        maneuver_type = "departure"
    elif re.search(r"\b(mudanca|mudança|shift|mudar)\b", clean):
        maneuver_type = "shift"
    elif re.search(r"\b(entrada|entry|entrar)\b", clean):
        maneuver_type = "entry"

    action_suffix = maneuver_type or "entry"
    if action_verb == "report":
        action = f"{action_suffix}_report"
    else:
        action = f"{action_verb}_{action_suffix}"

    if action not in ACTION_SPECS:
        if action_verb == "report":
            action = "entry_report"
        else:
            action = f"{action_verb}_entry"
        if action not in ACTION_SPECS:
            return None

    matched_port_call = None
    clean_question = f" {clean} "
    by_reference = [
        item for item in port_calls
        if item.get("reference_code") and f" {_operational_lookup_key(item.get('reference_code'))} " in clean_question
    ]
    if len(by_reference) == 1:
        matched_port_call = by_reference[0]
    else:
        by_name = []
        for item in port_calls:
            vessel_key = _operational_lookup_key(item.get("vessel_name"))
            if vessel_key and f" {vessel_key} " in clean_question:
                by_name.append(item)
        if len(by_name) == 1:
            matched_port_call = by_name[0]

    if not matched_port_call:
        return None

    resolved_port_call = services.store.get_port_call(matched_port_call["id"])
    if wants_previsto:
        inferred_type = maneuver_type or infer_maneuver_type(resolved_port_call, "edit_maneuver_plan") or "entry"
        target_maneuver = resolve_maneuver(resolved_port_call, "edit_maneuver_plan", inferred_type)
        if target_maneuver and target_maneuver.get("state") == "pending":
            type_label = {"entry": "entrada", "departure": "saída", "shift": "mudança"}.get(inferred_type, "manobra")
            return {
                "intent": "unsupported", "action": "", "confidence": 0.99,
                "reason": f"A {type_label} de {matched_port_call.get('vessel_name', 'este navio')} já está prevista.",
                "target": {}, "fields": {}, "missing_fields": [],
            }

    extracted_fields = _extract_labelled_values(question)
    proposal = normalize_action_candidate(
        {
            "intent": "action", "action": action, "confidence": 0.99,
            "reason": (
                "Heurística determinística para comando equivalente a confirmar a manobra."
                if action == "complete_entry" and re.search(r"\b(previsto|prevista|planeado|planeada)\b", clean)
                else "Heurística determinística para ação operacional direta."
            ),
            "target": {
                "reference_code": matched_port_call.get("reference_code", ""),
                "vessel_name": matched_port_call.get("vessel_name", ""),
                "maneuver_type": maneuver_type,
            },
            "fields": extracted_fields, "missing_fields": [],
        },
        role,
    )
    if proposal and proposal.get("intent") == "action":
        return proposal
    return None


def build_tracked_scales(port_activity: dict) -> list[dict]:
    """Build a flat list of tracked scale summaries for vessels currently in port or with planned maneuvers."""
    tracked = []
    seen_ids: set[str] = set()
    for item in port_activity.get("in_port", []) or []:
        item_id = (item.get("id") or "").strip()
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        tracked.append({
            "id": item_id, "reference_code": item.get("reference_code", ""),
            "vessel_name": item.get("vessel_name", ""),
            "location_label": item.get("berth_label", ""),
            "status_label": "Em porto", "status_class": "approved",
            "meta": f"ETA {item.get('eta_label', '--')} · ATA {item.get('ata_label', '--')} · agente {item.get('agent_label', '--')}",
        })
    for item in port_activity.get("planned_maneuvers", []) or []:
        item_id = (item.get("port_call_id") or "").strip()
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        tracked.append({
            "id": item_id, "reference_code": item.get("reference_code", ""),
            "vessel_name": item.get("vessel_name", ""),
            "location_label": item.get("local_destination", "") or item.get("berth_label", ""),
            "status_label": item.get("situation_label", "Prevista"),
            "status_class": item.get("situation_class", "pending"),
            "meta": (
                f"{item.get('maneuver_label', 'Manobra')} · {item.get('date_label', '--')} "
                f"às {item.get('planned_label', '--')} · agente {item.get('agent_label', '--')}"
            ),
        })
    for item in port_activity.get("arrivals", []) or []:
        item_id = (item.get("id") or "").strip()
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        tracked.append({
            "id": item_id, "reference_code": item.get("reference_code", ""),
            "vessel_name": item.get("vessel_name", ""),
            "location_label": item.get("berth_label", ""),
            "status_label": "Pendente", "status_class": "pending",
            "meta": f"ETA {item.get('eta_label', '--')} · agente {item.get('agent_label', '--')}",
        })
    return tracked


def load_pending_chat_action(username: str, conversation_id: str) -> dict | None:
    """Load and normalize the pending chat action for a conversation, or return None."""
    payload = services.store.get_runtime_state(pending_action_state_key(username, conversation_id))
    if not payload:
        return None
    if payload.get("username") != username or payload.get("conversation_id") != conversation_id:
        return None
    proposal = payload.get("proposal") or {}
    normalized = normalize_action_candidate(
        {
            "intent": proposal.get("intent", "action"),
            "action": proposal.get("action", ""),
            "confidence": proposal.get("confidence", 0.0),
            "reason": proposal.get("reason", ""),
            "target": proposal.get("target", {}),
            "fields": proposal.get("fields", {}),
            "missing_fields": proposal.get("missing_fields", []),
        },
        session.get("role", "piloto"),
    )
    if normalized and normalized.get("intent") == "action":
        proposal = {**proposal, **normalized, "port_call_id": proposal.get("port_call_id", ""), "maneuver_id": proposal.get("maneuver_id", "")}
        proposal = refresh_proposal_missing_fields(proposal)
        payload = {**payload, "proposal": proposal}
        services.store.set_runtime_state(pending_action_state_key(username, conversation_id), payload)
    target_port_call = None
    port_call_id = (proposal.get("port_call_id") or "").strip()
    if port_call_id:
        try:
            target_port_call = action_target_port_call(port_call_id)
        except Exception:
            target_port_call = None
    return {
        **payload,
        "proposal": proposal,
        "summary": payload.get("summary") or format_action_summary(proposal, target_port_call),
        "target_reference": target_port_call.get("reference_code") if target_port_call else proposal.get("target", {}).get("reference_code", ""),
        "target_vessel_name": target_port_call.get("vessel_name") if target_port_call else proposal.get("target", {}).get("vessel_name", ""),
        "can_confirm": bool(proposal.get("action")) and not proposal.get("missing_fields"),
    }


def save_pending_chat_action(username: str, conversation_id: str, proposal: dict, question: str) -> dict:
    """Persist a pending action proposal to runtime state and return the stored payload."""
    port_call = None
    if proposal.get("port_call_id"):
        try:
            port_call = action_target_port_call(proposal["port_call_id"])
        except Exception:
            port_call = None
    payload = {
        "username": username, "conversation_id": conversation_id,
        "question": question, "proposal": proposal,
        "summary": format_action_summary(proposal, port_call),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    services.store.set_runtime_state(pending_action_state_key(username, conversation_id), payload)
    return payload


def clear_pending_chat_action(username: str, conversation_id: str) -> None:
    """Delete the pending chat action for the given user and conversation from runtime state."""
    services.store.delete_runtime_state(pending_action_state_key(username, conversation_id))


def propose_operational_action(question: str, role: str) -> dict | None:
    """Attempt to derive an operational action proposal from the question."""
    if looks_like_operational_query(question) and not looks_like_operational_command(question):
        return {
            "intent": "question",
            "action": "",
            "confidence": 0.99,
            "reason": "Pergunta consultiva sem pedido explícito de execução operacional.",
            "target": {},
            "fields": {},
            "missing_fields": [],
        }
    if not looks_like_operational_command(question):
        return None
    resolvable_port_calls = current_resolvable_port_calls()
    heuristic_proposal = heuristic_operational_proposal(question, role, resolvable_port_calls)
    if heuristic_proposal:
        return finalize_operational_proposal(heuristic_proposal, resolvable_port_calls)
    if not services.rag.can_generate():
        unavailable_reason = services.rag.generation_unavailable_reason()
        return {
            "intent": "unsupported", "action": "", "confidence": 0.0,
            "reason": f"O bot operador está indisponível: {unavailable_reason}",
            "target": {}, "fields": {}, "missing_fields": [],
        }
    port_calls = current_visible_port_calls()
    prompt = build_operational_action_prompt(
        question=question, role=role, now_local=datetime.now().astimezone(),
        port_calls=port_calls, berth_options=services.BERTH_OPTIONS,
        constraint_options=services.CONSTRAINT_OPTIONS,
    )
    try:
        gen_result = services.rag.generate_text(prompt)
    except Exception as exc:
        return {
            "intent": "unsupported", "action": "", "confidence": 0.0,
            "reason": f"Falha a interpretar a ação operacional: {exc}",
            "target": {}, "fields": {}, "missing_fields": [],
        }
    candidate = extract_json_object(gen_result.text or "")
    proposal = normalize_action_candidate(candidate or {}, role)
    if proposal and proposal.get("intent") == "unsupported":
        heuristic_proposal = heuristic_operational_proposal(question, role, resolvable_port_calls)
        if heuristic_proposal:
            return finalize_operational_proposal(heuristic_proposal, resolvable_port_calls)
    return finalize_operational_proposal(proposal, current_visible_port_calls())


def finalize_operational_proposal(proposal: dict | None, port_calls: list[dict] | None = None) -> dict | None:
    """Resolve the target port call and maneuver for an action proposal and refresh missing fields."""
    if not proposal or proposal.get("intent") != "action":
        return proposal
    proposal_target = proposal.setdefault("target", {})
    target = None
    existing_port_call_id = (proposal.get("port_call_id") or "").strip()
    if existing_port_call_id:
        try:
            target = services.store.get_port_call(existing_port_call_id)
        except Exception:
            target = None
    visible_port_calls = port_calls if port_calls is not None else current_visible_port_calls()
    if not target:
        target = resolve_port_call(visible_port_calls, proposal_target)
    if (
        not target
        and proposal.get("action") != "create_port_call"
        and proposal_target.get("reference_code")
        and not proposal_target.get("maneuver_id")
    ):
        maneuver_reference = " ".join(str(proposal_target.get("reference_code") or "").split())
        if maneuver_reference:
            maneuver_id_target = {
                **proposal_target,
                "maneuver_id": maneuver_reference,
                "reference_code": "",
            }
            target = resolve_port_call(visible_port_calls, maneuver_id_target)
            if target:
                proposal_target["maneuver_id"] = maneuver_id_target["maneuver_id"]
                proposal_target["reference_code"] = target.get("reference_code", "")
    if (
        not target
        and proposal.get("action") != "create_port_call"
        and proposal_target.get("maneuver_id")
        and not proposal_target.get("reference_code")
        and not proposal_target.get("vessel_name")
    ):
        legacy_reference_target = {
            **proposal_target,
            "reference_code": proposal_target.get("maneuver_id", ""),
            "maneuver_id": "",
        }
        target = resolve_port_call(visible_port_calls, legacy_reference_target)
        if target:
            proposal_target["reference_code"] = target.get("reference_code", "") or legacy_reference_target["reference_code"]
            proposal_target["maneuver_id"] = ""
            proposal["maneuver_id"] = ""
    if not target and proposal.get("action") != "create_port_call":
        resolvable_port_calls = current_resolvable_port_calls()
        target = resolve_port_call(resolvable_port_calls, proposal_target)
        if (
            not target
            and proposal_target.get("reference_code")
            and not proposal_target.get("maneuver_id")
        ):
            maneuver_reference = " ".join(str(proposal_target.get("reference_code") or "").split())
            if maneuver_reference:
                maneuver_id_target = {
                    **proposal_target,
                    "maneuver_id": maneuver_reference,
                    "reference_code": "",
                }
                target = resolve_port_call(resolvable_port_calls, maneuver_id_target)
                if target:
                    proposal_target["maneuver_id"] = maneuver_id_target["maneuver_id"]
                    proposal_target["reference_code"] = target.get("reference_code", "")
        if (
            not target
            and proposal_target.get("maneuver_id")
            and not proposal_target.get("reference_code")
            and not proposal_target.get("vessel_name")
        ):
            legacy_reference_target = {
                **proposal_target,
                "reference_code": proposal_target.get("maneuver_id", ""),
                "maneuver_id": "",
            }
            target = resolve_port_call(resolvable_port_calls, legacy_reference_target)
            if target:
                proposal_target["reference_code"] = target.get("reference_code", "") or legacy_reference_target["reference_code"]
                proposal_target["maneuver_id"] = ""
                proposal["maneuver_id"] = ""

    fields = proposal.setdefault("fields", {})
    if fields.get("docking_depth") and not fields.get("draft_m"):
        fields["draft_m"] = fields.pop("docking_depth")

    if proposal.get("action") == "create_port_call":
        has_scale_context = any(
            " ".join(str(fields.get(key) or "").split())
            for key in (
                "eta_local",
                "berth",
                "last_port",
                "next_port",
                "vessel_imo",
                "vessel_call_sign",
                "vessel_flag",
                "vessel_type",
                "vessel_loa_m",
                "vessel_beam_m",
                "vessel_gt_t",
                "vessel_dwt_t",
                "vessel_max_draft_m",
                "vessel_bow_thruster",
                "vessel_stern_thruster",
            )
        )
        has_report_fields = any(
            " ".join(str(fields.get(key) or "").split())
            for key in ("maneuver_started_local", "maneuver_finished_local", "draft_m")
        )
        if has_scale_context or not has_report_fields:
            proposal["port_call_id"] = ""
            proposal["target"]["reference_code"] = ""
            proposal["target"]["maneuver_type"] = ""
            return refresh_proposal_missing_fields(proposal)

    if proposal.get("action") != "create_port_call" and not target:
        proposal["intent"] = "unsupported"
        proposal["action"] = ""
        proposal["reason"] = "Não consegui identificar uma escala correspondente para executar a ação. Usa a Ref da escala, o nome do navio ou o ID da manobra."
        return proposal

    if target:
        proposal["port_call_id"] = target.get("id", "")
        proposal["target"]["reference_code"] = target.get("reference_code", "")
        proposal["target"]["vessel_name"] = target.get("vessel_name", "")

    if target and proposal.get("action") == "create_port_call":
        report_like_fields = any(
            " ".join(str(fields.get(key) or "").split())
            for key in ("maneuver_started_local", "maneuver_finished_local", "draft_m")
        )
        if report_like_fields:
            resolved_target = services.store.get_port_call(target["id"])
            inferred_existing_type = (
                (proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
                or infer_maneuver_type(resolved_target, "entry_report")
                or "entry"
            )
            if inferred_existing_type in {"entry", "departure", "shift"}:
                proposal["action"] = f"{inferred_existing_type}_report"
                proposal["target"]["maneuver_type"] = inferred_existing_type

    target_maneuver_id = " ".join(str((proposal.get("target", {}) or {}).get("maneuver_id") or proposal.get("maneuver_id") or "").split())
    maneuver_type = (proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
    resolved_port_call = services.store.get_port_call(target["id"]) if target else None
    resolved_maneuver = (
        resolve_maneuver(
            resolved_port_call or {},
            proposal.get("action", ""),
            maneuver_type,
            target_maneuver_id,
        )
        if resolved_port_call and target_maneuver_id
        else None
    )
    if resolved_maneuver:
        proposal["maneuver_id"] = resolved_maneuver.get("id", "")
        proposal["target"]["maneuver_id"] = resolved_maneuver.get("id", "")
        if resolved_maneuver.get("type") in {"entry", "departure", "shift"}:
            proposal["target"]["maneuver_type"] = resolved_maneuver.get("type", "")
    maneuver_type = (proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
    inferred_type = (
        resolved_maneuver.get("type", "")
        if resolved_maneuver
        else infer_maneuver_type(resolved_port_call or {}, proposal.get("action", "")) if resolved_port_call else ""
    )
    if inferred_type and proposal.get("action") in {
        "approve_entry", "approve_departure", "approve_shift",
        "abort_entry", "abort_departure", "abort_shift",
        "entry_report", "departure_report", "shift_report",
    }:
        proposal["action"] = action_for_maneuver_type(proposal["action"], inferred_type)

    if proposal.get("action") in {"approve_entry", "abort_entry", "entry_report"}:
        proposal["target"]["maneuver_type"] = "entry"
    elif proposal.get("action") in {"approve_departure", "abort_departure", "departure_report", "schedule_departure"}:
        proposal["target"]["maneuver_type"] = "departure"
    elif proposal.get("action") in {"approve_shift", "abort_shift", "shift_report", "schedule_shift"}:
        proposal["target"]["maneuver_type"] = "shift"
    elif maneuver_type not in {"entry", "departure", "shift"} and proposal.get("action") == "edit_maneuver_plan":
        if inferred_type:
            proposal["target"]["maneuver_type"] = inferred_type
        else:
            proposal["intent"] = "unsupported"
            proposal["action"] = ""
            proposal["reason"] = "Indica se queres alterar a entrada, a saída ou a mudança."
            return proposal
    elif maneuver_type not in {"entry", "departure", "shift"} and inferred_type and proposal.get("action") in {
        "approve_entry", "approve_departure", "approve_shift",
        "abort_entry", "abort_departure", "abort_shift",
        "entry_report", "departure_report", "shift_report",
    }:
        proposal["target"]["maneuver_type"] = inferred_type

    maneuver_type = (proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
    if (
        target
        and action_prefers_explicit_maneuver_id(proposal.get("action", ""))
        and not ((proposal.get("maneuver_id") or proposal.get("target", {}).get("maneuver_id", "")).strip())
        and maneuver_type in {"entry", "departure", "shift"}
    ):
        matching_maneuvers = candidate_maneuvers_for_action(
            services.store.get_port_call(target["id"]),
            proposal["action"],
            maneuver_type,
        )
        if len(matching_maneuvers) > 1:
            proposal["missing_fields"] = proposal_missing_field_labels(
                proposal.get("action", ""),
                proposal.get("fields") or {},
                {**(proposal.get("target") or {}), "maneuver_id": ""},
            )
            if "ID da manobra" not in proposal["missing_fields"]:
                proposal["missing_fields"].append("ID da manobra")
            return proposal

    if target and proposal.get("action") in {"edit_maneuver_plan", "edit_maneuver_report", "delete_maneuver", "delete_maneuver_report"}:
        maneuver = resolve_maneuver(
            services.store.get_port_call(target["id"]),
            proposal["action"],
            proposal["target"].get("maneuver_type", ""),
            proposal.get("maneuver_id") or proposal.get("target", {}).get("maneuver_id", ""),
        )
        if not maneuver:
            proposal["intent"] = "unsupported"
            proposal["action"] = ""
            proposal["reason"] = "Não encontrei a manobra certa para editar nesta escala."
            return proposal
        proposal["maneuver_id"] = maneuver.get("id", "")
        proposal["target"]["maneuver_id"] = maneuver.get("id", "")
        if proposal.get("action") == "edit_maneuver_plan":
            fields = proposal.setdefault("fields", {})
            mt = proposal["target"].get("maneuver_type", "")
            if mt == "entry" and fields.get("berth") and not fields.get("destination"):
                fields["destination"] = fields["berth"]
            elif mt == "departure" and fields.get("next_port") and not fields.get("destination"):
                fields["destination"] = fields["next_port"]
            elif mt == "shift" and fields.get("destination_berth") and not fields.get("destination"):
                fields["destination"] = fields["destination_berth"]

    proposal["fields"]["constraints"] = normalize_constraint_codes(proposal.get("fields", {}).get("constraints", []))
    return refresh_proposal_missing_fields(proposal)


def pending_action_override(question: str, pending_proposal: dict, role: str) -> dict | None:
    """Check if the question replaces the pending action with a different verb, returning the replacement or None."""
    clean = _operational_lookup_key(question)
    if not clean:
        return None
    maneuver_type = (pending_proposal.get("target", {}).get("maneuver_type") or "").strip().lower()
    if maneuver_type not in {"entry", "departure", "shift"}:
        return None

    if re.search(r"\b(aprova|approve|aprovar|confirma|confirmar)\b", clean):
        action = action_for_maneuver_type("approve_entry", maneuver_type)
    elif re.search(r"\b(aborta|cancela|anula)\b", clean):
        action = action_for_maneuver_type("abort_entry", maneuver_type)
    elif re.search(r"\b(previsto|prevista|planeado|planeada)\b", clean):
        action = action_for_maneuver_type("approve_entry", maneuver_type)
    elif re.search(r"\b(regist|registar|relatorio|relatório|realizada|concluida|fechar|fecha|concluir)\b", clean):
        maneuver_suffix = maneuver_type or "entry"
        action = f"{maneuver_suffix}_report"
    else:
        return None

    if action == pending_proposal.get("action"):
        return None

    replacement = normalize_action_candidate(
        {
            "intent": "action", "action": action, "confidence": 0.99,
            "reason": "Troca direta da ação pendente pelo pedido do utilizador.",
            "target": pending_proposal.get("target", {}), "fields": {}, "missing_fields": [],
        },
        role,
    )
    if not replacement or replacement.get("intent") != "action":
        return None
    if pending_proposal.get("port_call_id"):
        replacement["port_call_id"] = pending_proposal["port_call_id"]
    if pending_proposal.get("maneuver_id"):
        replacement["maneuver_id"] = pending_proposal["maneuver_id"]
    return finalize_operational_proposal(replacement)


def refine_pending_operational_action(question: str, pending_proposal: dict, role: str) -> dict | None:
    """Update, replace, cancel, or reject a pending proposal based on the user's follow-up question."""
    replacement = pending_action_override(question, pending_proposal, role)
    if replacement and replacement.get("intent") == "action":
        return {"intent": "replace", "proposal": replacement}

    direct_updates = extract_pending_field_updates(question, pending_proposal)
    target_updates = extract_pending_target_updates(question)
    if direct_updates or target_updates:
        updates = normalize_action_candidate(
            {
                "intent": "action",
                "action": pending_proposal.get("action", ""),
                "confidence": pending_proposal.get("confidence", 0.0),
                "reason": pending_proposal.get("reason", ""),
                "target": {
                    **(pending_proposal.get("target", {}) or {}),
                    **target_updates,
                },
                "fields": direct_updates, "missing_fields": [],
            },
            role,
        )
        merged = merge_action_candidate(pending_proposal, updates or {}, role)
        return {"intent": "update", "proposal": finalize_operational_proposal(merged)}

    if not services.rag.can_generate():
        unavailable_reason = services.rag.generation_unavailable_reason()
        return {
            "intent": "unsupported",
            "reason": f"O bot operador está indisponível: {unavailable_reason}",
        }

    prompt = build_pending_action_update_prompt(
        question=question, role=role, proposal=pending_proposal,
        berth_options=services.BERTH_OPTIONS, constraint_options=services.CONSTRAINT_OPTIONS,
    )
    try:
        gen_result = services.rag.generate_text(prompt)
    except Exception as exc:
        return {"intent": "unsupported", "reason": f"Falha a atualizar a proposta pendente: {exc}"}

    candidate = extract_json_object(gen_result.text or "") or {}
    intent = (candidate.get("intent") or "").strip().lower()
    if intent in {"cancel", "question", "unsupported"}:
        return {"intent": intent or "unsupported", "reason": " ".join(str(candidate.get("reason") or "").strip().split())}
    if intent == "replace":
        proposal = normalize_action_candidate(candidate, role)
        return {"intent": "replace", "proposal": finalize_operational_proposal(proposal)}

    updates = normalize_action_candidate(
        {
            "intent": "action",
            "action": candidate.get("action") or pending_proposal.get("action", ""),
            "confidence": candidate.get("confidence", pending_proposal.get("confidence", 0.0)),
            "reason": candidate.get("reason", pending_proposal.get("reason", "")),
            "target": candidate.get("target") if isinstance(candidate.get("target"), dict) else {},
            "fields": candidate.get("fields") if isinstance(candidate.get("fields"), dict) else {},
            "missing_fields": [],
        },
        role,
    )
    merged = merge_action_candidate(pending_proposal, updates or {}, role)
    return {"intent": "update", "proposal": finalize_operational_proposal(merged)}


def execute_pending_operational_action(proposal: dict, username: str, role: str) -> tuple[dict, str]:
    """Execute an approved operational action proposal against the store and return the result and message."""
    action = proposal.get("action") or ""
    target = proposal.get("target") or {}
    fields = proposal.get("fields") or {}
    port_call_id = (proposal.get("port_call_id") or "").strip()
    role = (role or "").strip().lower()

    _action_redirects = {
        "edit_maneuver_report": "entry_report",
    }
    _conditional_approve_redirects = {
        "complete_entry": ("approve_entry", "entry"),
        "complete_departure": ("approve_departure", "departure"),
        "complete_shift": ("approve_shift", "shift"),
    }
    if action in _action_redirects:
        action = _action_redirects[action]

    def apply_scope(port_call_id_value: str) -> dict:
        return action_target_port_call(port_call_id_value)

    def field_text(name: str, fallback="") -> str:
        raw = fields.get(name)
        if raw in (None, ""):
            raw = fallback
        return " ".join(str(raw or "").strip().split())

    def resolve_target_maneuver(current_port_call: dict, current_action: str, current_maneuver_type: str) -> dict | None:
        explicit_maneuver_id = (proposal.get("maneuver_id") or target.get("maneuver_id", "")).strip()
        if explicit_maneuver_id:
            return resolve_maneuver(current_port_call, current_action, current_maneuver_type, explicit_maneuver_id)
        candidates = candidate_maneuvers_for_action(current_port_call, current_action, current_maneuver_type)
        if action_prefers_explicit_maneuver_id(current_action) and len(candidates) > 1:
            raise ValueError("Há várias manobras deste tipo nesta escala. Indica o ID da manobra para evitar alterar a errada.")
        return candidates[-1] if candidates else None

    def apply_plan_updates_before_approval(current_port_call: dict, current_maneuver_type: str) -> None:
        current_maneuver = resolve_maneuver(current_port_call, "edit_maneuver_plan", current_maneuver_type)
        if not current_maneuver:
            raise ValueError("Não encontrei a manobra certa para atualizar antes da aprovação.")
        if current_maneuver_type == "entry":
            destination = field_text("destination", field_text("berth", current_maneuver.get("destination") or current_port_call.get("berth", "")))
        elif current_maneuver_type == "departure":
            destination = field_text("destination", field_text("next_port", current_maneuver.get("destination") or current_port_call.get("next_port", "")))
        else:
            destination = field_text("destination", field_text("destination_berth", current_maneuver.get("destination") or current_port_call.get("shift_destination_berth", "") or current_port_call.get("berth", "")))
        planned_at_value = field_text("planned_at_local", field_text("eta_local"))
        if current_maneuver_type == "departure":
            planned_at_value = field_text("planned_at_local", field_text("planned_departure_at_local", planned_at_value))
        elif current_maneuver_type == "shift":
            planned_at_value = field_text("planned_at_local", field_text("planned_shift_at_local", planned_at_value))
        if not any([
            planned_at_value,
            field_text("draft_m"),
            field_text("tug_count"),
            field_text("notes"),
            field_text("plan_observations"),
            field_text("change_reason"),
            fields.get("constraints"),
        ]):
            return
        origin_value = field_text("origin", current_maneuver.get("origin") or current_port_call.get("last_port", ""))
        destination_value = destination
        if current_maneuver_type == "entry":
            destination_value = normalize_portal_berth(destination_value, "Destino")
        elif current_maneuver_type in {"departure", "shift"}:
            origin_value = normalize_portal_berth(origin_value, "Origem")
            if current_maneuver_type == "shift":
                destination_value = normalize_portal_berth(destination_value, "Destino")
                if destination_value == origin_value:
                    raise ValueError("O cais destino tem de ser diferente do local atual do navio.")
        services.store.edit_maneuver_plan(
            port_call_id=port_call_id, maneuver_id=current_maneuver.get("id", ""),
            updated_by=username, actor_role=role,
            planned_at=parse_local_datetime_input(planned_at_value or current_maneuver.get("planned_input_value") or current_maneuver.get("planned_at") or ""),
            origin=require_form_text(origin_value, "Origem"),
            destination=require_form_text(destination_value, "Destino"),
            draft_m=field_text("draft_m", current_maneuver.get("planned_draft_m", "")),
            tug_count=field_text("tug_count", current_maneuver.get("tug_count", "")),
            constraints=normalize_constraint_codes(fields.get("constraints") or current_maneuver.get("constraints", [])),
            plan_note=field_text("plan_observations", field_text("notes", current_maneuver.get("plan_observations", ""))),
            change_reason=require_form_text(field_text("change_reason", field_text("reason")), "Motivo da alteração"),
        )

    def ensure_maneuver_can_be_approved(current_maneuver_type: str, label: str = "Cais destino") -> None:
        refreshed_port_call = services.store.get_port_call(port_call_id)
        maneuver = ensure_maneuver_hour_capacity_for_approval(refreshed_port_call, current_maneuver_type)
        if not maneuver or current_maneuver_type not in {"entry", "shift"}:
            return
        ensure_portal_berth_is_available(
            maneuver.get("destination", ""),
            current_port_call_id=port_call_id,
            label=label,
            target_planned_at=maneuver.get("planned_at"),
            target_vessel_loa_m=refreshed_port_call.get("vessel_loa_m"),
        )

    if action == "create_port_call":
        eta = parse_local_datetime_input(field_text("eta_local"), "ETA")
        validate_not_past_datetime(eta, "ETA")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        draft_m = field_text("draft_m")
        tug_count = field_text("tug_count")
        port_call = services.store.create_port_call(
            vessel_name=field_text("vessel_name"), eta=eta, created_by=username,
            constraints=constraints,
            berth=normalize_portal_berth(field_text("berth"), "Cais previsto"),
            last_port=require_form_text(field_text("last_port"), "Porto anterior"),
            next_port=require_form_text(field_text("next_port"), "Próximo destino"),
            vessel_short_name=field_text("vessel_short_name"),
            vessel_imo=require_form_text(field_text("vessel_imo"), "IMO"),
            vessel_call_sign=require_form_text(field_text("vessel_call_sign"), "Indicativo"),
            vessel_flag=require_form_text(field_text("vessel_flag"), "Bandeira"),
            vessel_type=require_form_text(field_text("vessel_type"), "Tipo de navio"),
            vessel_loa_m=require_form_text(field_text("vessel_loa_m"), "LOA"),
            vessel_beam_m=require_form_text(field_text("vessel_beam_m"), "Boca"),
            vessel_gt_t=require_form_text(field_text("vessel_gt_t"), "GT"),
            vessel_max_draft_m=require_form_text(field_text("vessel_max_draft_m"), "Calado máximo"),
            vessel_dwt_t=require_form_text(field_text("vessel_dwt_t"), "DWT"),
            vessel_bow_thruster=normalize_thruster_state(fields.get("vessel_bow_thruster"), "Bow thruster"),
            vessel_stern_thruster=normalize_thruster_state(fields.get("vessel_stern_thruster"), "Stern thruster"),
            notes=build_entry_request_note({"draft_m": draft_m, "tug_count": tug_count, "constraints": constraints, "notes": fields.get("notes", "")}),
        )
        return port_call, _build_created_port_call_message(port_call)
    if action == "edit_port_call":
        current_port_call = apply_scope(port_call_id) if port_call_id else None
        parsed_eta = None
        if field_text("eta_local"):
            parsed_eta = parse_optional_local_datetime_input(field_text("eta_local"), "ETA")
            if (current_port_call or {}).get("status") == "scheduled":
                validate_not_past_datetime(parsed_eta, "ETA")
        berth_value = fields.get("berth")
        normalized_berth = None
        if berth_value not in {None, ""}:
            if (current_port_call or {}).get("status") == "in_port":
                normalized_berth = ensure_portal_berth_is_available(
                    berth_value,
                    current_port_call_id=port_call_id,
                    label="Cais",
                    target_vessel_loa_m=fields.get("vessel_loa_m") or (current_port_call or {}).get("vessel_loa_m"),
                )
            else:
                normalized_berth = normalize_portal_berth(berth_value, "Cais")
        result = services.store.edit_port_call(
            port_call_id=port_call_id,
            updated_by=username,
            vessel_name=fields.get("vessel_name"),
            eta=parsed_eta,
            berth=normalized_berth,
            last_port=fields.get("last_port"),
            next_port=fields.get("next_port"),
            notes=fields.get("notes"),
            constraints=normalize_constraint_codes(fields.get("constraints")) if "constraints" in fields else None,
            vessel_short_name=fields.get("vessel_short_name"),
            vessel_imo=fields.get("vessel_imo"),
            vessel_call_sign=fields.get("vessel_call_sign"),
            vessel_flag=fields.get("vessel_flag"),
            vessel_type=fields.get("vessel_type"),
            vessel_loa_m=fields.get("vessel_loa_m"),
            vessel_beam_m=fields.get("vessel_beam_m"),
            vessel_gt_t=fields.get("vessel_gt_t"),
            vessel_max_draft_m=fields.get("vessel_max_draft_m"),
            vessel_dwt_t=fields.get("vessel_dwt_t"),
            vessel_bow_thruster=(
                normalize_thruster_state(fields.get("vessel_bow_thruster"), "Bow thruster")
                if "vessel_bow_thruster" in fields else None
            ),
            vessel_stern_thruster=(
                normalize_thruster_state(fields.get("vessel_stern_thruster"), "Stern thruster")
                if "vessel_stern_thruster" in fields else None
            ),
            change_reason=require_form_text(field_text("change_reason", field_text("reason")), "Motivo da alteração"),
        )
        return result, f"Escala atualizada para {result['vessel_name']}."
    if action == "delete_port_call":
        removed = services.store.delete_port_call(port_call_id)
        return removed, f"Escala apagada: {removed['reference_code']} · {removed['vessel_name']}."

    if not port_call_id:
        raise ValueError("A proposta não tem escala associada.")

    port_call = apply_scope(port_call_id)
    maneuver_type = target.get("maneuver_type", "")

    if action in _conditional_approve_redirects:
        approve_action, m_type = _conditional_approve_redirects[action]
        target_maneuver = resolve_maneuver(port_call, action, m_type)
        if target_maneuver and target_maneuver.get("state") == "pending":
            action = approve_action

    if action == "approve_entry":
        apply_plan_updates_before_approval(port_call, "entry")
        ensure_maneuver_can_be_approved("entry", label="Cais")
        result = services.store.approve_port_call(port_call_id=port_call_id, decided_by=username, approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))))
        return result, f"Entrada aprovada para {result['vessel_name']}."
    if action == "abort_entry":
        target_maneuver = resolve_target_maneuver(port_call, action, "entry")
        maneuver_state = (target_maneuver or {}).get("state", "pending")
        if maneuver_state == "pending":
            raise ValueError("Entrada ainda pendente. Cancela a marcação antes da aprovação; aborto só depois de aprovada.")
        result = services.store.abort_port_call(port_call_id=port_call_id, decided_by=username, aborted_reason=require_form_text(field_text("aborted_reason", field_text("reason")), "Motivo"), approval_note=field_text("approval_note"))
        return result, f"Entrada abortada para {result['vessel_name']}."
    if action == "complete_entry":
        arrived_at_value = field_text("arrived_at_local", field_text("maneuver_finished_local"))
        target_berth = field_text("berth", port_call.get("berth"))
        berth_for_arrival = ensure_portal_berth_is_physically_available(target_berth, current_port_call_id=port_call_id, label="Cais")
        result = services.store.mark_port_call_arrived(port_call_id=port_call_id, arrived_at=parse_optional_local_datetime_input(arrived_at_value, "ATA") or datetime.now().astimezone().isoformat(), updated_by=username, berth=berth_for_arrival)
        return result, f"Entrada confirmada para {result['vessel_name']} às {result['ata_label']}. Já podes preencher o registo operacional."
    if action == "entry_report":
        target_maneuver = resolve_target_maneuver(port_call, action, "entry")
        if not target_maneuver:
            raise ValueError("A proposta não identifica a manobra a registar.")
        if target_maneuver.get("state") == "approved":
            ensure_portal_berth_is_physically_available(
                target_maneuver.get("destination") or port_call.get("berth", ""),
                current_port_call_id=port_call_id,
                label="Cais",
            )
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": fields.get("notes", "")}, "Entrada")
        result = services.store.attach_entry_report(
            port_call_id=port_call_id,
            updated_by=username,
            maneuver_started_at=started_at,
            maneuver_finished_at=finished_at,
            draft_m=draft_m,
            notes=note,
            maneuver_id=target_maneuver.get("id"),
        )
        return result, f"Registo de entrada guardado para {result['vessel_name']}."
    if action == "schedule_departure":
        planned_departure_at = parse_local_datetime_input(field_text("planned_departure_at_local"), "Hora prevista de saída")
        validate_not_past_datetime(planned_departure_at, "Hora prevista de saída")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        result = services.store.schedule_departure_plan(
            port_call_id=port_call_id,
            planned_departure_at=planned_departure_at,
            updated_by=username,
            next_port=require_form_text(field_text("next_port", port_call.get("next_port", "")), "Próximo destino"),
            constraints=constraints,
            departure_plan_note=build_departure_plan_note(
                {
                    "origin_berth": port_call.get("berth", ""),
                    "draft_m": field_text("draft_m"),
                    "tug_count": field_text("tug_count"),
                    "constraints": constraints,
                    "notes": field_text("notes"),
                }
            ),
            draft_m=field_text("draft_m"),
            tug_count=field_text("tug_count"),
        )
        return result, f"Saída planeada para {result['vessel_name']} às {result['planned_departure_label']}."
    if action == "approve_departure":
        if port_call.get("status") != "in_port":
            raise ValueError("A saída só pode ser aprovada depois da entrada estar concluída.")
        apply_plan_updates_before_approval(port_call, "departure")
        ensure_maneuver_can_be_approved("departure")
        result = services.store.approve_port_call(port_call_id=port_call_id, decided_by=username, approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))))
        return result, f"Saída aprovada para {result['vessel_name']}."
    if action == "abort_departure":
        target_m = resolve_target_maneuver(port_call, action, "departure")
        m_state = (target_m or {}).get("state", "pending")
        if m_state == "pending":
            raise ValueError("Saída ainda pendente. Cancela a marcação antes da aprovação; aborto só depois de aprovada.")
        result = services.store.abort_departure_plan(port_call_id=port_call_id, updated_by=username, aborted_reason=require_form_text(field_text("aborted_reason", field_text("reason")), "Motivo"))
        return result, f"Saída abortada para {result['vessel_name']}."
    if action == "complete_departure":
        departed_at_value = field_text("departed_at_local", field_text("maneuver_finished_local"))
        result = services.store.mark_port_call_departed(port_call_id=port_call_id, departed_at=parse_optional_local_datetime_input(departed_at_value, "ATD") or datetime.now().astimezone().isoformat(), updated_by=username, next_port=field_text("next_port", port_call.get("next_port")))
        return result, f"Saída confirmada para {result['vessel_name']} às {result['departure_label']}. Já podes preencher o registo operacional."
    if action == "departure_report":
        target_maneuver = resolve_target_maneuver(port_call, action, "departure")
        if not target_maneuver:
            raise ValueError("A proposta não identifica a manobra a registar.")
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": fields.get("notes", "")}, "Saída")
        result = services.store.attach_departure_report(
            port_call_id=port_call_id,
            updated_by=username,
            maneuver_started_at=started_at,
            maneuver_finished_at=finished_at,
            draft_m=draft_m,
            notes=note,
            maneuver_id=target_maneuver.get("id"),
        )
        return result, f"Registo de saída guardado para {result['vessel_name']}."
    if action == "schedule_shift":
        planned_shift_at = parse_local_datetime_input(field_text("planned_shift_at_local"), "Hora prevista da mudança")
        validate_not_past_datetime(planned_shift_at, "Hora prevista da mudança")
        constraints = normalize_constraint_codes(fields.get("constraints", []))
        origin_berth = normalize_portal_berth(field_text("origin_berth", port_call.get("berth", "")), "Cais origem")
        destination_berth = normalize_portal_berth(field_text("destination_berth"), "Cais destino")
        if destination_berth == origin_berth:
            raise ValueError("O cais destino tem de ser diferente do local atual do navio.")
        result = services.store.schedule_shift_plan(
            port_call_id=port_call_id,
            planned_shift_at=planned_shift_at,
            updated_by=username,
            destination_berth=destination_berth,
            constraints=constraints,
            shift_plan_note=build_shift_plan_note({
                "origin_berth": origin_berth,
                "destination_berth": destination_berth,
                "draft_m": field_text("draft_m"),
                "tug_count": field_text("tug_count"),
                "constraints": constraints,
                "notes": field_text("notes"),
            }),
            draft_m=field_text("draft_m"),
            tug_count=field_text("tug_count"),
        )
        return result, f"Mudança planeada para {result['vessel_name']} às {result['planned_shift_label']}."
    if action == "approve_shift":
        if port_call.get("status") != "in_port":
            raise ValueError("A mudança só pode ser aprovada depois da entrada estar concluída.")
        apply_plan_updates_before_approval(port_call, "shift")
        ensure_maneuver_can_be_approved("shift", label="Cais destino")
        result = services.store.approve_shift_plan(port_call_id=port_call_id, decided_by=username, approval_note=field_text("approval_note", field_text("change_reason", field_text("reason", field_text("notes")))))
        return result, f"Mudança aprovada para {result['vessel_name']}."
    if action == "abort_shift":
        target_ms = resolve_target_maneuver(port_call, action, "shift")
        ms_state = (target_ms or {}).get("state", "pending")
        if ms_state == "pending":
            raise ValueError("Mudança ainda pendente. Cancela a marcação antes da aprovação; aborto só depois de aprovada.")
        result = services.store.abort_shift_plan(port_call_id=port_call_id, updated_by=username, aborted_reason=require_form_text(field_text("aborted_reason", field_text("reason")), "Motivo"))
        return result, f"Mudança abortada para {result['vessel_name']}."
    if action == "complete_shift":
        shifted_at_value = field_text("shifted_at_local", field_text("maneuver_finished_local"))
        shift_destination = normalize_portal_berth(
            field_text("destination_berth", port_call.get("shift_destination_berth", "") or port_call.get("berth", "")),
            "Cais destino",
        )
        ensure_portal_berth_is_physically_available(shift_destination, current_port_call_id=port_call_id, label="Cais destino")
        result = services.store.mark_shift_completed(port_call_id=port_call_id, shifted_at=parse_optional_local_datetime_input(shifted_at_value, "Hora da mudança") or datetime.now().astimezone().isoformat(), updated_by=username)
        return result, f"Mudança concluída para {result['vessel_name']} às {result['shift_label']}. Já podes preencher o registo operacional."
    if action == "shift_report":
        target_maneuver = resolve_target_maneuver(port_call, action, "shift")
        if not target_maneuver:
            raise ValueError("A proposta não identifica a manobra a registar.")
        if target_maneuver.get("state") == "approved":
            ensure_portal_berth_is_physically_available(
                target_maneuver.get("destination") or port_call.get("shift_destination_berth", "") or port_call.get("berth", ""),
                current_port_call_id=port_call_id,
                label="Cais destino",
            )
        started_at = parse_local_datetime_input(field_text("maneuver_started_local"), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local"), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m"), "Calado")
        note = build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": fields.get("notes", "")}, "Mudança")
        result = services.store.attach_shift_report(
            port_call_id=port_call_id,
            updated_by=username,
            maneuver_started_at=started_at,
            maneuver_finished_at=finished_at,
            draft_m=draft_m,
            notes=note,
            maneuver_id=target_maneuver.get("id"),
        )
        return result, f"Registo de mudança guardado para {result['vessel_name']}."
    if action == "edit_maneuver_plan":
        current_port_call = services.store.get_port_call(port_call_id)
        maneuver = resolve_target_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "")
        if not maneuver_id:
            raise ValueError("A proposta não identifica a manobra a editar.")
        base_origin = field_text("origin", (maneuver or {}).get("origin") or current_port_call.get("last_port", ""))
        if maneuver_type == "entry":
            base_destination = field_text("destination", field_text("berth", (maneuver or {}).get("destination") or current_port_call.get("berth", "")))
        elif maneuver_type == "departure":
            base_destination = field_text("destination", field_text("next_port", (maneuver or {}).get("destination") or current_port_call.get("next_port", "")))
        else:
            base_destination = field_text("destination", field_text("destination_berth", (maneuver or {}).get("destination") or current_port_call.get("shift_destination_berth", "") or current_port_call.get("berth", "")))
        result = services.store.edit_maneuver_plan(
            port_call_id=port_call_id, maneuver_id=maneuver_id, updated_by=username, actor_role=role,
            planned_at=parse_local_datetime_input(field_text("planned_at_local", (maneuver or {}).get("planned_input_value", ""))),
            origin=require_form_text(base_origin, "Origem"),
            destination=require_form_text(base_destination, "Destino"),
            draft_m=field_text("draft_m", (maneuver or {}).get("planned_draft_m", "")),
            tug_count=field_text("tug_count", (maneuver or {}).get("tug_count", "")),
            constraints=normalize_constraint_codes(fields.get("constraints") or (maneuver or {}).get("constraints", [])),
            plan_note=field_text("plan_observations", field_text("notes", (maneuver or {}).get("plan_observations", ""))),
            change_reason=require_form_text(field_text("change_reason"), "Motivo da alteração"),
        )
        return result, f"Planeamento atualizado para {result['vessel_name']}."
    if action == "edit_maneuver_report":
        current_port_call = services.store.get_port_call(port_call_id)
        maneuver = resolve_target_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "")
        if not maneuver_id:
            raise ValueError("A proposta não identifica a manobra a editar.")
        started_at = parse_local_datetime_input(field_text("maneuver_started_local", (maneuver or {}).get("execution_started_input_value", "")), "Início da manobra")
        finished_at = parse_local_datetime_input(field_text("maneuver_finished_local", (maneuver or {}).get("execution_finished_input_value", "")), "Fim da manobra")
        draft_m = require_form_text(field_text("draft_m", (maneuver or {}).get("reported_draft_m", "")), "Calado")
        result = services.store.edit_maneuver_report(
            port_call_id=port_call_id, maneuver_id=maneuver_id, updated_by=username,
            maneuver_started_at=started_at, maneuver_finished_at=finished_at, draft_m=draft_m,
            notes=build_pilot_report_note({"maneuver_started_at": started_at, "maneuver_finished_at": finished_at, "draft_m": draft_m, "notes": field_text("notes", (maneuver or {}).get("report_note", ""))}, "Entrada" if maneuver_type == "entry" else "Saída" if maneuver_type == "departure" else "Mudança", existing_note=""),
            change_reason=require_form_text(field_text("change_reason"), "Motivo da alteração"),
        )
        return result, f"Registo operacional revisto para {result['vessel_name']}."
    if action == "delete_maneuver":
        current_port_call = services.store.get_port_call(port_call_id)
        maneuver = resolve_target_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "")
        if not maneuver_id:
            raise ValueError("A proposta não identifica a manobra a apagar.")
        removed_or_updated = services.store.delete_maneuver(
            port_call_id=port_call_id,
            maneuver_id=maneuver_id,
            updated_by=username,
        )
        return removed_or_updated, f"Manobra cancelada para {removed_or_updated['vessel_name']}."
    if action == "delete_maneuver_report":
        current_port_call = services.store.get_port_call(port_call_id)
        maneuver = resolve_target_maneuver(current_port_call, action, maneuver_type)
        maneuver_id = (maneuver or {}).get("id", "")
        if not maneuver_id:
            raise ValueError("A proposta não identifica o registo a apagar.")
        result = services.store.delete_maneuver_report(
            port_call_id=port_call_id,
            maneuver_id=maneuver_id,
            updated_by=username,
        )
        return result, f"Registo da manobra removido para {result['vessel_name']}."

    raise ValueError("Ação operacional não suportada.")


# ---------------------------------------------------------------------------
# Scale context builder (for port_call_detail page)
# ---------------------------------------------------------------------------
