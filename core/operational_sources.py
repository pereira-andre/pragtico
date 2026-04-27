"""Deterministic operational chat sources and live answers."""

import logging
import math
import re
from datetime import datetime

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
from domain.cost_engine import UP_NORMAL, UP_SHIFT_ALONG
from domain.lisnave_rules import lisnave_rule_snippet, should_include_lisnave_rule_source
from domain.operational_safety import build_operational_safety_source, build_weather_safety_status_lines
from domain.tug_guidance import build_tug_operational_guidance_source

logger = logging.getLogger(__name__)


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
        lines.append(
            f"- {item['date_label']} | {item['reference_code']} | {item['vessel_name']} | "
            f"{item['maneuver_label']} | situação {item['situation_label']} | "
            f"Hora {item['planned_label']} | "
            f"{item['local_origin']} -> {item['local_destination']} | "
            f"agente {item['agent_label']} | piloto {item['pilot_label']}"
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
        lines.append(
            f"- {item.get('date_label', '--')} | {item.get('reference_code', '--')} | {item.get('vessel_name', '--')} | "
            f"{item.get('maneuver_label', '--')} | Hora {item.get('execution_window_label') or item.get('actual_label') or item.get('planned_label') or '--'} | "
            f"{item.get('local_origin', '--')} -> {item.get('local_destination', '--')} | "
            f"agente {item.get('agent_label', '--')} | validado por {item.get('validated_by_label', '--')} | "
            f"executado por {item.get('executed_by_label', '--')} | rebocadores {item.get('tug_count_label', '--')} | "
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
            f"agente {item.get('agent_label', '--')} | piloto {item.get('pilot_label', '--')}"
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
        "- Aliases Lisnave reconhecidos pelo sistema: 'Doca 21' e 'Doca seca 21' -> 'Lisnave - Doca 21'; 'Cais 2 A' -> 'Lisnave - Cais 2 A'.",
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


def build_operational_chat_sources(question: str) -> list[dict]:
    """Assemble supplemental operational context sources for the chat RAG pipeline."""
    recent_port_activity = services.store.get_port_activity_snapshot(window_days=30)
    historical_port_activity = services.store.get_port_activity_snapshot(window_days=3650)
    sources = [
        build_operational_snapshot_source(recent_port_activity),
        build_maneuver_archive_source(question, historical_port_activity),
        build_scale_registry_source(question, historical_port_activity),
    ]
    berth_catalog_source = build_berth_catalog_source(question)
    if berth_catalog_source:
        sources.append(berth_catalog_source)
    lisnave_rule_source = build_lisnave_operational_rule_source(question)
    if lisnave_rule_source:
        sources.append(lisnave_rule_source)
    knowledge_dir = _active_knowledge_dir()
    safety_source = build_operational_safety_source(question, knowledge_dir)
    if safety_source:
        sources.append(safety_source)
    tug_guidance_source = build_tug_operational_guidance_source(question, knowledge_dir)
    if tug_guidance_source:
        sources.append(tug_guidance_source)
    maneuver_case_source = build_maneuver_case_context_source(question, current_resolvable_port_calls())
    if maneuver_case_source:
        sources.append(maneuver_case_source)
    cost_source = build_cost_context_source(question, recent_port_activity)
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
    if plan.requires_llm_synthesis:
        return None

    live_environment_answer = _answer_live_environment_query(question, clean_question, plan=plan)
    if live_environment_answer:
        return live_environment_answer
    recent_departures_answer = _answer_recent_departures_query(question, clean_question)
    if recent_departures_answer:
        return recent_departures_answer
    expected_arrivals_answer = _answer_expected_arrivals_query(question, clean_question)
    if expected_arrivals_answer:
        return expected_arrivals_answer
    planned_maneuvers_answer = _answer_planned_maneuvers_query(question, clean_question)
    if planned_maneuvers_answer:
        return planned_maneuvers_answer
    port_calls = current_resolvable_port_calls()
    matched_port_call = _match_port_call_from_question(question, port_calls)

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
    port_activity = filter_port_activity_for_session(services.store.get_port_activity_snapshot(window_days=3650))
    departed = list(port_activity.get("departed", []) or [])
    if not departed:
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
    for item in departed[:5]:
        vessel_name = item.get("vessel_name") or "--"
        atd_label = item.get("departure_label") or _local_iso_to_label(item.get("departure_at"))
        origin = item.get("berth_label") or item.get("berth") or "--"
        destination = item.get("next_port") or "--"
        lines.append(f"- {vessel_name} - ATD {atd_label} - {origin} -> {destination}.")
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
    arrival_terms = {"chegada", "chegadas", "chegar", "chega", "entrada", "entradas", "previstos", "prevista", "previstas", "esperado", "esperados", "esperadas", "eta"}
    tokens = set(clean_question.split())
    if not (tokens & arrival_terms):
        return False
    scope_markers = {"proximo", "proximos", "hoje", "amanha", "breve", "semana", "navio", "navios", "agendados", "agendadas"}
    if tokens & scope_markers:
        return True
    return "que vao chegar" in clean_question or "a chegar" in clean_question or "vao entrar" in clean_question


def _answer_expected_arrivals_query(question: str, clean_question: str) -> dict | None:
    if not _looks_like_expected_arrivals_query(clean_question):
        return None
    port_activity = filter_port_activity_for_session(services.store.get_port_activity_snapshot(window_days=30))
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
        eta_label = item.get("arrival_label") or item.get("planned_label") or _local_iso_to_label(item.get("arrival_at") or item.get("date_value"))
        origin = item.get("last_port") or item.get("local_origin") or "--"
        destination = item.get("berth_label") or item.get("berth") or item.get("local_destination") or "--"
        lines.append(f"- {vessel_name} - ETA {eta_label} - {origin} -> {destination}.")
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
    maneuver_terms = {"manobra", "manobras", "planeadas", "planeado", "planeados", "agendadas", "agendados"}
    tokens = set(clean_question.split())
    if not (tokens & maneuver_terms):
        return False
    planned_markers = {"proxima", "proximas", "hoje", "amanha", "hoje", "previstas", "futuras", "agenda", "programa"}
    if tokens & planned_markers:
        return True
    return "que estao planeadas" in clean_question or "que vao acontecer" in clean_question or "proximas manobras" in clean_question


def _answer_planned_maneuvers_query(question: str, clean_question: str) -> dict | None:
    if not _looks_like_planned_maneuvers_query(clean_question):
        return None
    port_activity = filter_port_activity_for_session(services.store.get_port_activity_snapshot(window_days=30))
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
        lines.append(f"- {vessel_name} - {maneuver_label} {planned_label} - {origin} -> {destination}{situation_suffix}.")
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
