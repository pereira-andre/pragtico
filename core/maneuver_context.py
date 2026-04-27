"""Port-call and maneuver context builders."""

import logging
import re
from datetime import datetime

from flask import session

from core import services
from core.form_helpers import _local_iso_to_label
from core.operational_common import _operational_lookup_key, current_resolvable_port_calls
from core.rule_catalog import _active_knowledge_dir
from domain.berth_layout import canonicalize_berth_label, find_occupied_berth_conflict, is_known_berth_label
from domain.berth_profiles import find_best_berth_profile
from domain.chat_actions import (
    build_validate_maneuver_reply_template,
    candidate_maneuvers_for_action,
    resolve_maneuver,
    resolve_port_call,
)
from domain.lisnave_rules import build_lisnave_rule_items
from storage import format_constraint_labels
from storage.maneuver_case_helpers import build_case_environment_signature

logger = logging.getLogger(__name__)


def _select_validation_maneuver(scale_context: dict, port_call: dict, target: dict) -> tuple[dict | None, list[dict]]:
    """Resolve a maneuver for hard validation, returning the decorated maneuver and same-type candidates."""
    maneuvers_by_id = {
        str(item.get("id") or ""): item
        for item in list(scale_context.get("maneuvers") or [])
        if item.get("id")
    }
    maneuver_id = str((target or {}).get("maneuver_id") or "").strip()
    maneuver_type = str((target or {}).get("maneuver_type") or "").strip().lower()
    if maneuver_id:
        raw_maneuver = resolve_maneuver(port_call, "delete_maneuver", maneuver_type, maneuver_id=maneuver_id)
        if not raw_maneuver:
            return None, []
        return maneuvers_by_id.get(str(raw_maneuver.get("id") or "")), []
    if maneuver_type not in {"entry", "departure", "shift"}:
        return None, []
    candidates = []
    for item in candidate_maneuvers_for_action(port_call, "delete_maneuver", maneuver_type):
        decorated = maneuvers_by_id.get(str(item.get("id") or ""))
        if decorated:
            candidates.append(decorated)
    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates


def answer_slash_validation(target: dict, role: str) -> dict:
    """Run the deterministic checklist/casebook validation for a specific maneuver."""
    del role  # Read-only command; access is already scoped by visible port calls.

    template = build_validate_maneuver_reply_template()
    port_call_match = resolve_port_call(current_resolvable_port_calls(), target or {})
    if not port_call_match:
        return {
            "answer": "Não encontrei a escala/manobra pedida para validar.\n\n" + template,
            "sources": [],
            "answer_origin": "slash_validation",
        }

    resolved_port_call = services.store.get_port_call(port_call_match["id"])
    scale_context = build_scale_context(resolved_port_call)
    maneuver, candidates = _select_validation_maneuver(scale_context, resolved_port_call, target or {})
    if not maneuver and len(candidates) > 1:
        lines = [
            (
                f"Encontrei {len(candidates)} manobras do mesmo tipo para a escala "
                f"{resolved_port_call.get('reference_code', '--')}. Indica o ID da manobra para fazer a validação dura."
            ),
            "",
            "Candidatas:",
        ]
        for item in candidates[:5]:
            lines.append(
                f"- {item.get('id', '--')} | {item.get('title', 'Manobra')} | "
                f"{item.get('status', '--')} | {item.get('planned_label') or item.get('when_label') or '--'}"
            )
        lines.extend(["", template])
        return {
            "answer": "\n".join(lines),
            "sources": [],
            "answer_origin": "slash_validation",
        }
    if not maneuver:
        return {
            "answer": "Não encontrei a manobra pedida para validar.\n\n" + template,
            "sources": [],
            "answer_origin": "slash_validation",
        }

    recommendation = maneuver.get("casebook_recommendation") or {}
    checklist = list(maneuver.get("analysis_checklist") or [])
    similar_cases = maneuver.get("similar_cases") or []
    details = [
        (
            f"Validação da {maneuver.get('title', 'manobra').lower()} da escala "
            f"{resolved_port_call.get('reference_code', '--')} ({resolved_port_call.get('vessel_name', 'navio')})"
        ),
        f"- ID da manobra: {maneuver.get('id', '--')}",
        f"- Estado atual: {maneuver.get('status', '--')}",
        f"- Janela planeada: {maneuver.get('planned_label') or maneuver.get('when_label') or '--'}",
        f"- Trajeto: {maneuver.get('origin', '--')} -> {maneuver.get('destination', '--')}",
        "",
        _format_operational_opinion_answer(
            port_call=resolved_port_call,
            maneuver=maneuver,
            recommendation=recommendation,
            similar_cases=similar_cases,
            checklist=checklist,
        ),
    ]
    snippet = recommendation.get("summary") or details[0]
    return {
        "answer": "\n".join(details),
        "sources": [
            {
                "document": resolved_port_call.get("vessel_name", "Validação de manobra"),
                "source_id": resolved_port_call.get("reference_code", ""),
                "retrieval_mode": "maneuver_validation",
                "snippet": snippet,
            }
        ],
        "answer_origin": "slash_validation",
    }


def _match_port_call_from_question(question: str, port_calls: list[dict]) -> dict | None:
    """Resolve a single visible port call from free text using reference code or vessel name."""
    clean_question = _operational_lookup_key(question)
    if not clean_question:
        return None
    padded_question = f" {clean_question} "
    by_reference = [
        item for item in port_calls
        if item.get("reference_code") and f" {_operational_lookup_key(item.get('reference_code'))} " in padded_question
    ]
    if len(by_reference) == 1:
        return by_reference[0]

    by_name = []
    for item in port_calls:
        vessel_key = _operational_lookup_key(item.get("vessel_name"))
        if vessel_key and f" {vessel_key} " in padded_question:
            by_name.append(item)
    if len(by_name) == 1:
        return by_name[0]
    return None


def _format_maneuver_case_flags(flags: list[str] | None) -> list[str]:
    mapping = {
        "wave_related": "ondulação relevante",
        "pilotage_suspended": "pilotagem suspensa",
        "pilotage_cancelled": "pilotagem cancelada",
        "entry_aborted_by_sea_state": "entrada abortada por estado do mar",
    }
    labels = []
    for flag in flags or []:
        clean = mapping.get((flag or "").strip().lower())
        if clean:
            labels.append(clean)
    return labels


def _format_case_feedback_label(value: str | None) -> str:
    return {
        "approved": "referência positiva",
        "avoid": "evitar como padrão",
        "review": "rever caso",
    }.get((value or "").strip().lower(), "")


def _build_checklist_item(status: str, title: str, detail: str) -> dict:
    return {
        "status": status,
        "title": title,
        "detail": detail,
    }


def _format_operational_opinion_answer(
    *,
    port_call: dict,
    maneuver: dict,
    recommendation: dict,
    similar_cases: list[dict],
    checklist: list[dict],
) -> str:
    """Format a professional, structured opinion answer for a maneuver."""
    alerts = [item for item in checklist if item.get("status") == "caution"]
    infos = [item for item in checklist if item.get("status") == "info"]
    top_case = similar_cases[0] if similar_cases else {}

    lines = [
        "Leitura rápida",
        (
            f"- {maneuver.get('title', 'Manobra')} de {port_call.get('vessel_name', 'navio')}: "
            f"{recommendation.get('title', 'sem leitura histórica forte')}."
        ),
    ]
    if recommendation.get("basis_label"):
        lines.append(f"- {recommendation['basis_label']}.")

    lines.append("")
    lines.append("Alertas")
    if alerts:
        for item in alerts[:3]:
            lines.append(f"- {item.get('title', '')}: {item.get('detail', '')}")
    else:
        lines.append("- Sem alertas críticos nesta leitura determinística.")
    if not alerts and infos:
        lines.append(f"- Nota: {infos[0].get('detail', '')}")

    lines.append("")
    lines.append("Recomendação")
    lines.append(f"- {recommendation.get('summary', 'Sem recomendação automática disponível.')}")
    if recommendation.get("signals_label"):
        lines.append(f"- Sinais: {recommendation['signals_label']}.")

    lines.append("")
    lines.append("Base usada")
    lines.append("- Checklist operacional determinística do portal.")
    if any("Lisnave" in (item.get("title") or "") for item in checklist):
        lines.append("- Regra estruturada Lisnave: docas com mínimo de 4 rebocadores e orientação proa a norte; cais com proa a sul.")
    if similar_cases:
        base_line = (
            f"- Histórico semelhante: {len(similar_cases)} caso(s); mais próximo {top_case.get('reference_code', '--')} "
            f"({top_case.get('state_label', '--')} · {top_case.get('route_label', '--')})."
        )
        lines.append(base_line)
        if top_case.get("feedback_status_label"):
            lines.append(f"- Estado do caso mais próximo: {top_case['feedback_status_label']}.")
    else:
        lines.append("- Histórico semelhante: sem casos suficientes para comparação.")
    if any("Lisnave" in (item.get("title") or "") for item in checklist):
        lines.append("- Base operacional: regra estruturada Lisnave aplicada nesta leitura.")
    else:
        lines.append("- Base documental: não foi invocada regra específica nesta leitura; pede regra/norma se precisares de enquadramento normativo.")
    lines.append("")
    lines.append("Isto apoia a decisão, mas não substitui a validação operacional do momento.")
    return "\n".join(lines).strip()


def _build_maneuver_analysis_checklist(
    port_call: dict,
    maneuver: dict,
    *,
    similar_cases: list[dict],
    casebook_recommendation: dict,
) -> tuple[list[dict], dict]:
    """Build a deterministic operational checklist for a maneuver analysis."""
    items: list[dict] = []
    maneuver_type = (maneuver.get("type") or "").strip().lower()
    origin = (maneuver.get("origin") or "").strip()
    destination = (maneuver.get("destination") or "").strip()
    tug_count_raw = str(maneuver.get("tug_count") or "").strip()
    tug_count = int(tug_count_raw) if tug_count_raw.isdigit() else 0
    bow_thruster = (port_call.get("vessel_bow_thruster") or "").strip().lower()
    stern_thruster = (port_call.get("vessel_stern_thruster") or "").strip().lower()
    operational_berth = destination if maneuver_type in {"entry", "shift"} else origin

    required_profile = [
        ("tipo", port_call.get("vessel_type")),
        ("LOA", port_call.get("vessel_loa_m")),
        ("boca", port_call.get("vessel_beam_m")),
        ("GT", port_call.get("vessel_gt_t")),
        ("calado máximo", port_call.get("vessel_max_draft_m")),
    ]
    missing_profile = [label for label, value in required_profile if not str(value or "").strip()]
    if missing_profile:
        items.append(
            _build_checklist_item(
                "caution",
                "Perfil do navio",
                f"Faltam dados para análise segura: {', '.join(missing_profile)}.",
            )
        )
    else:
        items.append(
            _build_checklist_item(
                "ok",
                "Perfil do navio",
                "Tipo, dimensões, GT e calado máximo estão preenchidos.",
            )
        )

    if maneuver_type in {"entry", "shift"}:
        canonical_destination = canonicalize_berth_label(destination, berth_options=services.BERTH_OPTIONS)
        if not destination:
            items.append(_build_checklist_item("caution", "Destino operacional", "Falta indicar cais ou fundeadouro de destino."))
        elif not is_known_berth_label(canonical_destination, berth_options=services.BERTH_OPTIONS):
            items.append(
                _build_checklist_item(
                    "caution",
                    "Destino operacional",
                    f"O destino '{destination}' não está no catálogo canónico do porto.",
                )
            )
        else:
            items.append(
                _build_checklist_item(
                    "ok",
                    "Destino operacional",
                    f"Destino normalizado para {canonical_destination}.",
                )
            )
            in_port_items = [
                item
                for item in current_resolvable_port_calls()
                if (item.get("status") or "").strip().lower() == "in_port"
            ]
            conflict = find_occupied_berth_conflict(
                canonical_destination,
                in_port_items,
                current_port_call_id=port_call.get("id", ""),
                berth_options=services.BERTH_OPTIONS,
            )
            if conflict:
                items.append(
                    _build_checklist_item(
                        "caution",
                        "Disponibilidade do destino",
                        f"{canonical_destination} está ocupado por {conflict.get('vessel_name', 'outro navio')}.",
                    )
                )
            else:
                items.append(
                    _build_checklist_item(
                        "ok",
                        "Disponibilidade do destino",
                        f"{canonical_destination} está livre no snapshot operacional atual.",
                    )
                )
    else:
        if origin:
            items.append(
                _build_checklist_item(
                    "ok",
                    "Origem operacional",
                    f"A saída segue o último local conhecido do navio: {origin}.",
                )
            )
        else:
            items.append(
                _build_checklist_item(
                    "caution",
                    "Origem operacional",
                    "A origem da saída não está definida no registo atual.",
                )
            )
        if destination:
            items.append(
                _build_checklist_item(
                    "ok",
                    "Destino externo",
                    f"Próximo destino indicado: {destination}.",
                )
            )
        else:
            items.append(
                _build_checklist_item(
                    "caution",
                    "Destino externo",
                    "Falta indicar o próximo destino da saída.",
                )
            )

    if tug_count > 0:
        items.append(
            _build_checklist_item(
                "ok",
                "Meios de governo",
                f"Estão previstos {tug_count} rebocador(es) para a manobra.",
            )
        )
    elif bow_thruster == "yes" or stern_thruster == "yes":
        items.append(
            _build_checklist_item(
                "info",
                "Meios de governo",
                "Sem rebocadores previstos; o navio tem thruster(s) declarado(s).",
            )
        )
    elif bow_thruster == "unknown" or stern_thruster == "unknown":
        items.append(
            _build_checklist_item(
                "caution",
                "Meios de governo",
                "Sem rebocadores previstos e os thrusters do navio ainda não estão totalmente confirmados.",
            )
        )
    else:
        items.append(
            _build_checklist_item(
                "caution",
                "Meios de governo",
                "Sem rebocadores previstos e sem thrusters declarados.",
            )
        )

    items.extend(
        build_lisnave_rule_items(
            maneuver_type=maneuver_type,
            origin=origin,
            destination=destination,
            tug_count=tug_count_raw,
            berth_options=services.BERTH_OPTIONS,
        )
    )
    berth_profile_item = _build_berth_profile_checklist_item(operational_berth)
    if berth_profile_item:
        items.append(berth_profile_item)

    constraint_labels = format_constraint_labels(maneuver.get("constraint_codes") or [])
    if constraint_labels:
        items.append(
            _build_checklist_item(
                "caution",
                "Restrições operacionais",
                f"Há restrições ativas: {', '.join(constraint_labels)}.",
            )
        )
    else:
        items.append(
            _build_checklist_item(
                "ok",
                "Restrições operacionais",
                "Não há restrições explícitas registadas nesta manobra.",
            )
        )

    if maneuver_type in {"entry", "departure"}:
        items.append(
            _build_checklist_item(
                "info",
                "Ondulação e barra",
                "Validar leitura costeira fora da barra, Pilar 2 e zona do Outão antes de decidir.",
            )
        )
    else:
        items.append(
            _build_checklist_item(
                "info",
                "Ondulação e barra",
                "Não é fator primário para mudanças internas, salvo condicionantes excecionais.",
            )
        )

    if casebook_recommendation:
        checklist_status = (
            "ok"
            if casebook_recommendation.get("status_key") == "positive"
            else "caution"
            if casebook_recommendation.get("status_key") == "caution"
            else "info"
        )
        detail = casebook_recommendation.get("summary", "")
        if casebook_recommendation.get("basis_label"):
            detail = f"{detail} Base: {casebook_recommendation['basis_label']}"
        items.append(
            _build_checklist_item(
                checklist_status,
                "Histórico semelhante",
                detail.strip(),
            )
        )
    elif similar_cases:
        items.append(
            _build_checklist_item(
                "info",
                "Histórico semelhante",
                f"Foram encontrados {len(similar_cases)} caso(s) semelhante(s), sem padrão decisivo único.",
            )
        )
    else:
        items.append(
            _build_checklist_item(
                "info",
                "Histórico semelhante",
                "Ainda não há casos semelhantes suficientes para apoiar a decisão.",
            )
        )

    caution_count = sum(1 for item in items if item.get("status") == "caution")
    ok_count = sum(1 for item in items if item.get("status") == "ok")
    summary = {
        "caution_count": caution_count,
        "ok_count": ok_count,
        "info_count": sum(1 for item in items if item.get("status") == "info"),
        "headline": (
            "Checklist com alertas operacionais"
            if caution_count
            else "Checklist operacional coerente"
            if ok_count
            else "Checklist operacional informativa"
        ),
    }
    return items, summary


def _build_berth_profile_checklist_item(berth_label: str | None) -> dict | None:
    clean_label = " ".join(str(berth_label or "").strip().split())
    if not clean_label:
        return None
    profile_match = find_best_berth_profile(clean_label, _active_knowledge_dir())
    if not profile_match:
        return None
    profile = profile_match.get("profile") or {}
    profile_name = profile.get("name") or clean_label
    document = profile.get("document") or ""
    rules: list[str] = []
    length_markers = ("loa", "comprimento")
    meta_markers = ("nao confundir", "nao isola", "validar pelas restantes", "usar tms", "nao utilizar")
    for key in ("draft_rules", "maneuver_rules", "night_rules", "restrictions"):
        for raw_rule in profile.get(key, []) or []:
            rule = " ".join(str(raw_rule or "").strip().rstrip(".").split())
            if not rule:
                continue
            normalized = _operational_lookup_key(rule)
            if any(marker in normalized for marker in length_markers):
                continue
            if any(marker in normalized for marker in meta_markers):
                continue
            if rule not in rules:
                rules.append(rule)
            if len(rules) >= 4:
                break
        if len(rules) >= 4:
            break
    if clean_label.startswith("TMS 2") and not any("posicoes" in _operational_lookup_key(rule) for rule in rules):
        rules.append("TMS 2 tem tres posicoes operacionais: A, B e C")
    if not rules:
        return None
    normalized_rules = _operational_lookup_key(" ".join(rules))
    status = (
        "caution"
        if any(marker in normalized_rules for marker in ("reponto", "preia", "baixa", "noite", "noturna", "proibida", "mare viva", "calado"))
        else "info"
    )
    doc_label = f" ({document})" if document else ""
    detail = (
        f"{profile_name}{doc_label}: {'; '.join(rules[:4])}. "
        "Comprimento fica informativo nesta demo; calado continua a contar."
    )
    return _build_checklist_item(status, "Regras do cais", detail)


def _format_thruster_case_label(value: str | None) -> str:
    clean = (value or "").strip().lower()
    if clean in {"yes", "true", "1", "sim"}:
        return "Sim"
    if clean in {"no", "false", "0", "nao", "não"}:
        return "Não"
    return "Desconhecido"


def _extract_case_decision_excerpt(case: dict) -> str:
    for value in (
        ((case.get("execution_snapshot") or {}).get("report_note") or "").strip(),
        ((case.get("decision_snapshot") or {}).get("aborted_reason") or "").strip(),
        ((case.get("decision_snapshot") or {}).get("approval_note") or "").strip(),
        ((case.get("planning_snapshot") or {}).get("plan_observations") or "").strip(),
        ((case.get("planning_snapshot") or {}).get("plan_note") or "").strip(),
        (case.get("practice_summary") or "").strip(),
    ):
        if value:
            compact = " ".join(value.split())
            return compact[:180] + "…" if len(compact) > 180 else compact
    return ""


def _build_similar_case_cards(port_call: dict, maneuver: dict, limit: int = 3) -> list[dict]:
    environment_signature = None
    maneuver_id = (maneuver.get("id") or "").strip()
    if maneuver_id and hasattr(services.store, "get_maneuver_case"):
        try:
            current_case = services.store.get_maneuver_case(maneuver_id)
            if current_case:
                environment_signature = build_case_environment_signature(current_case)
        except Exception:
            logger.exception("Falha ao recolher assinatura ambiental da manobra %s.", maneuver_id)

    try:
        ranked_cases = services.store.find_similar_maneuver_cases(
            maneuver_type=maneuver.get("type", ""),
            origin=maneuver.get("origin", ""),
            destination=maneuver.get("destination", ""),
            vessel_type=port_call.get("vessel_type", ""),
            vessel_loa_m=port_call.get("vessel_loa_m", ""),
            bow_thruster=port_call.get("vessel_bow_thruster", ""),
            stern_thruster=port_call.get("vessel_stern_thruster", ""),
            tug_count=maneuver.get("tug_count", ""),
            environment_signature=environment_signature,
            limit=max(limit + 1, 4),
        )
    except Exception:
        logger.exception("Falha ao procurar casos semelhantes para a manobra %s.", maneuver.get("id"))
        return []

    cards = []
    for case in ranked_cases:
        if case.get("maneuver_id") == maneuver.get("id"):
            continue
        features = case.get("feature_snapshot") or {}
        decision_flags = _format_maneuver_case_flags((case.get("outcome_snapshot") or {}).get("decision_flags"))
        reasons = list(case.get("similarity_reasons") or [])
        cards.append(
            {
                "maneuver_id": case.get("maneuver_id", ""),
                "port_call_id": case.get("port_call_id", ""),
                "reference_code": case.get("reference_code", "--"),
                "source_type": case.get("source_type", "portal"),
                "source_label": case.get("source_label") or "Histórico PRAGtico",
                "vessel_name": case.get("vessel_name", "--"),
                "state_label": case.get("current_state_label", "--"),
                "status_class": (
                    "completed"
                    if case.get("current_state") == "completed"
                    else "aborted"
                    if case.get("current_state") == "aborted"
                    else "pending"
                ),
                "route_label": f"{case.get('origin_label') or '--'} → {case.get('destination_label') or '--'}",
                "latest_event_label": case.get("latest_event_label", "--"),
                "planned_label": case.get("planned_label", "--"),
                "similarity_score": case.get("similarity_score", 0),
                "reasons_label": ", ".join(reasons) if reasons else "perfil semelhante",
                "decision_flags": decision_flags,
                "decision_excerpt": _extract_case_decision_excerpt(case),
                "feedback_status": case.get("feedback_status", ""),
                "feedback_status_label": case.get("feedback_status_label", ""),
                "feedback_note": case.get("feedback_note", ""),
                "tug_count": features.get("tug_count") or "--",
                "loa_label": (
                    f"{features.get('vessel_loa_m'):.1f} m"
                    if isinstance(features.get("vessel_loa_m"), (int, float))
                    else "--"
                ),
                "bow_thruster_label": _format_thruster_case_label(features.get("bow_thruster")),
                "stern_thruster_label": _format_thruster_case_label(features.get("stern_thruster")),
            }
        )
        if len(cards) >= limit:
            break
    return cards


def _build_casebook_recommendation(maneuver: dict, similar_cases: list[dict]) -> dict:
    """Summarize similar historical cases into a short operational recommendation."""
    if not similar_cases:
        return {}

    completed = sum(1 for item in similar_cases if item.get("status_class") == "completed")
    aborted = sum(1 for item in similar_cases if item.get("status_class") == "aborted")
    approved_feedback = sum(1 for item in similar_cases if item.get("feedback_status") == "approved")
    avoid_feedback = sum(1 for item in similar_cases if item.get("feedback_status") == "avoid")
    review_feedback = sum(1 for item in similar_cases if item.get("feedback_status") == "review")
    practice_cases = sum(1 for item in similar_cases if item.get("source_type") == "practice_import")
    wave_related = sum(
        1
        for item in similar_cases
        if "ondulação relevante" in (item.get("decision_flags") or [])
    )

    tug_counter: dict[str, int] = {}
    for item in similar_cases:
        tug_value = str(item.get("tug_count") or "").strip()
        if tug_value and tug_value != "--":
            tug_counter[tug_value] = tug_counter.get(tug_value, 0) + 1
    dominant_tug_count = ""
    if tug_counter:
        dominant_tug_count = sorted(tug_counter.items(), key=lambda pair: (pair[1], pair[0]), reverse=True)[0][0]

    status_key = "neutral"
    title = "Leitura histórica mista"
    if avoid_feedback > approved_feedback:
        status_key = "caution"
        title = "Feedback validado recomenda prudência"
    elif approved_feedback and avoid_feedback == 0:
        status_key = "positive"
        title = "Feedback validado favorável"
    elif completed and aborted == 0:
        status_key = "positive"
        title = "Histórico favorável"
    elif aborted and completed == 0:
        status_key = "caution"
        title = "Histórico desfavorável"
    elif aborted > completed:
        status_key = "caution"
        title = "Histórico conservador"
    elif completed > aborted:
        status_key = "positive"
        title = "Histórico maioritariamente favorável"

    basis = f"{completed} realizada(s) e {aborted} abortada(s) em {len(similar_cases)} caso(s) semelhante(s)"
    recommendation_parts = []
    if dominant_tug_count:
        recommendation_parts.append(f"rebocadores mais usados: {dominant_tug_count}")
    if wave_related and maneuver.get("type") in {"entry", "departure"}:
        recommendation_parts.append(f"ondulação relevante em {wave_related} caso(s)")
    if approved_feedback:
        recommendation_parts.append(f"feedback positivo validado em {approved_feedback} caso(s)")
    if avoid_feedback:
        recommendation_parts.append(f"feedback a evitar em {avoid_feedback} caso(s)")
    if review_feedback:
        recommendation_parts.append(f"{review_feedback} caso(s) marcado(s) para revisão")
    if practice_cases:
        recommendation_parts.append(f"experiência prática importada em {practice_cases} padrão(ões)")

    if avoid_feedback > approved_feedback:
        summary = "Casos semelhantes foram sinalizados para evitar este padrão sem validação reforçada."
    elif approved_feedback and avoid_feedback == 0:
        summary = "Casos semelhantes com feedback validado apoiam esta abordagem, mantendo confirmação humana."
    elif status_key == "caution":
        summary = "Pede validação mais conservadora antes de confirmar esta manobra."
    elif status_key == "positive":
        summary = "O histórico semelhante é globalmente favorável, mantendo validação operacional normal."
    else:
        summary = "O histórico semelhante não aponta para um padrão único; valida pelos fatores do momento."

    return {
        "status_key": status_key,
        "title": title,
        "basis_label": basis,
        "summary": summary,
        "signals_label": " · ".join(recommendation_parts),
    }


def build_maneuver_case_context_source(question: str, port_calls: list[dict]) -> dict | None:
    """Build a compact historical casebook source for the matched scale/maneuver in chat."""
    clean_question = _operational_lookup_key(question)
    if not clean_question:
        return None
    if not re.search(r"\b(manobra|entrada|saida|departure|mudanca|shift|reboque|reboques|rebocador|rebocadores|thruster|cais|fundeadouro|aprovar|abortar|cancelar|opiniao|opiniao|achar|recomend|aconselh|suger)\b", clean_question):
        return None

    matched_port_call = _match_port_call_from_question(question, port_calls)
    if not matched_port_call:
        return None

    try:
        resolved_port_call = services.store.get_port_call(matched_port_call["id"])
        scale_context = build_scale_context(resolved_port_call)
    except Exception:
        logger.exception("Falha ao montar contexto de casos para %s.", matched_port_call.get("id"))
        return None

    maneuver_type = ""
    if re.search(r"\b(entrada|entry)\b", clean_question):
        maneuver_type = "entry"
    elif re.search(r"\b(saida|departure)\b", clean_question):
        maneuver_type = "departure"
    elif re.search(r"\b(mudanca|mudança|shift)\b", clean_question):
        maneuver_type = "shift"

    maneuvers = list(scale_context.get("maneuvers") or [])
    if maneuver_type:
        maneuvers = [item for item in maneuvers if item.get("type") == maneuver_type]
    maneuvers.sort(
        key=lambda item: (
            0 if item.get("status_key") in {"pending", "approved"} else 1,
            item.get("planned_label") or "",
        )
    )

    lines = []
    for maneuver in maneuvers[:2]:
        if not maneuver.get("similar_cases"):
            continue
        lines.append(
            f"Casos semelhantes para {maneuver.get('title', 'manobra')} "
            f"de {resolved_port_call.get('vessel_name', 'navio')} ({maneuver.get('origin', '--')} -> {maneuver.get('destination', '--')}):"
        )
        recommendation = maneuver.get("casebook_recommendation") or {}
        if recommendation:
            lines.append(
                f"- recomendação histórica: {recommendation.get('title', '')} | "
                f"{recommendation.get('basis_label', '')} | {recommendation.get('summary', '')}"
            )
            if recommendation.get("signals_label"):
                lines.append(f"  sinais: {recommendation['signals_label']}")
        checklist_summary = maneuver.get("analysis_summary") or {}
        checklist_items = list(maneuver.get("analysis_checklist") or [])
        if checklist_summary:
            lines.append(
                f"- checklist operacional: {checklist_summary.get('headline', 'sem resumo')} | "
                f"alertas {checklist_summary.get('caution_count', 0)} | "
                f"ok {checklist_summary.get('ok_count', 0)}"
            )
        prioritized_checklist = [
            *[item for item in checklist_items if item.get("status") == "caution"],
            *[item for item in checklist_items if item.get("status") != "caution"],
        ]
        for checklist_item in prioritized_checklist[:4]:
            lines.append(
                f"  checklist [{checklist_item.get('status', 'info')}]: "
                f"{checklist_item.get('title', '')} - {checklist_item.get('detail', '')}"
            )
        for case in maneuver.get("similar_cases", [])[:3]:
            lines.append(
                f"- {case.get('reference_code', '--')} | {case.get('vessel_name', '--')} | "
                f"{case.get('source_label', 'Histórico PRAGtico')} | "
                f"{case.get('state_label', '--')} | {case.get('route_label', '--')} | "
                f"{case.get('latest_event_label', '--')} | afinidade {case.get('similarity_score', 0)} | "
                f"{case.get('reasons_label', 'perfil semelhante')}"
            )
            if case.get("decision_flags"):
                lines.append(f"  flags: {', '.join(case['decision_flags'])}")
            if case.get("decision_excerpt"):
                lines.append(f"  nota: {case['decision_excerpt']}")
    if not lines:
        return None

    return {
        "source_id": f"CASEBOOK:{resolved_port_call.get('reference_code', '')}",
        "document": "casebook_manobras",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "maneuver_casebook",
        "snippet": "\n".join(lines),
    }


def build_scale_context(port_call: dict) -> dict:
    """Build the rich context dict for the port call detail page including maneuvers and actions."""
    current_role = (session.get("role") or "").strip().lower()
    casebook_enabled = hasattr(services.store, "find_similar_maneuver_cases")

    def _operator_contact_profile(username: str | None, snapshot: dict | None) -> dict:
        profile = dict(snapshot or {})
        clean_username = (username or profile.get("username") or "").strip().lower()
        if clean_username:
            try:
                live_profile = services.store.get_user_profile(clean_username) or {}
            except Exception:
                live_profile = {}
            for key in (
                "username",
                "role",
                "full_name",
                "organization",
                "email",
                "phone",
                "whatsapp_number",
                "whatsapp_opt_in",
            ):
                if live_profile.get(key):
                    profile[key] = live_profile[key]
        label = (
            profile.get("full_name")
            or profile.get("organization")
            or profile.get("email")
            or clean_username
            or "--"
        )
        contact_parts = [
            value
            for value in (profile.get("organization"), profile.get("email"), profile.get("phone"))
            if value
        ]
        return {
            **profile,
            "label": label,
            "phone_label": profile.get("phone") or "--",
            "whatsapp_label": profile.get("whatsapp_number") or "--",
            "contact_label": " · ".join(contact_parts) if contact_parts else "--",
        }

    def _hours_between(start_value: str | None, end_value: str | None) -> str:
        if not start_value or not end_value:
            return "--"
        try:
            start_dt = datetime.fromisoformat(start_value)
            end_dt = datetime.fromisoformat(end_value)
        except ValueError:
            return "--"
        hours = max((end_dt - start_dt).total_seconds() / 3600, 0)
        return f"{hours:.0f} horas"

    def _latest(history: list[dict], maneuver_type: str, states: set[str] | None = None) -> dict | None:
        items = [item for item in history if item.get("type") == maneuver_type]
        if states is not None:
            items = [item for item in items if item.get("state") in states]
        if not items:
            return None
        items.sort(key=lambda item: item.get("planned_at") or item.get("completed_at") or item.get("created_at") or "")
        return items[-1]

    def _latest_reportable(history: list[dict], maneuver_type: str) -> dict | None:
        items = [
            item for item in history
            if item.get("type") == maneuver_type and item.get("state") in {"approved", "completed", "aborted"}
            and not (item.get("report_note") or "").strip()
        ]
        if not items:
            return None
        items.sort(key=lambda item: item.get("completed_at") or item.get("planned_at") or item.get("created_at") or "")
        return items[-1]

    history = port_call.get("maneuver_history", [])
    agent_contact_profile = _operator_contact_profile(
        port_call.get("created_by"),
        port_call.get("created_by_profile") or port_call.get("agent_profile"),
    )
    entry = _latest(history, "entry")
    active_departure = _latest(history, "departure", {"pending", "approved"})
    latest_departure = _latest(history, "departure")
    completed_departure = _latest(history, "departure", {"completed"})
    active_shift = _latest(history, "shift", {"pending", "approved"})
    latest_shift = _latest(history, "shift")
    completed_shift = _latest(history, "shift", {"completed"})
    reportable_entry = _latest_reportable(history, "entry")
    reportable_departure = _latest_reportable(history, "departure")
    reportable_shift = _latest_reportable(history, "shift")
    active_entry = _latest(history, "entry", {"pending", "approved"})

    etd_value = (active_departure or completed_departure or latest_departure or {}).get("planned_at") or (completed_departure or {}).get("completed_at")
    etd_label = (
        port_call.get("planned_departure_label")
        if active_departure and active_departure.get("planned_at")
        else port_call.get("departure_label")
        if completed_departure and completed_departure.get("completed_at")
        else "Sem ETD"
    )
    ship_doc_number = f"PTSETSHP{(port_call.get('vessel_imo') or port_call['reference_code'])[-8:]}"
    maneuvers = []
    change_log_rows = []

    def _history_actor(profile: dict | None, username: str = "") -> dict:
        payload = profile or {}
        return {
            "label": payload.get("label") or payload.get("full_name") or payload.get("username") or username or "--",
            "contact": payload.get("email") or payload.get("phone") or payload.get("organization") or "--",
        }

    def _add_history_row(*, scope: str, changed_at: str | None, actor: dict | None, username: str = "", reason: str = "", summary: str = "") -> None:
        if not changed_at and not summary:
            return
        actor_meta = _history_actor(actor, username)
        change_log_rows.append({
            "maneuver_title": scope or "Escala",
            "changed_at": changed_at or "",
            "changed_at_label": _local_iso_to_label(changed_at) if changed_at else "--",
            "changed_by_label": actor_meta["label"],
            "changed_by_contact": actor_meta["contact"],
            "reason": reason or "--",
            "summary": summary or "--",
        })

    has_creation_log = any(
        "escala criada" in str(log.get("summary") or "").casefold()
        for log in port_call.get("change_log", []) or []
    )
    if not has_creation_log:
        _add_history_row(
            scope="Escala",
            changed_at=port_call.get("created_at"),
            actor=port_call.get("agent_profile") or port_call.get("created_by_profile"),
            username=port_call.get("created_by"),
            reason="Registo inicial",
            summary="Escala criada.",
        )
    for log in port_call.get("change_log", []) or []:
        _add_history_row(
            scope="Escala",
            changed_at=log.get("changed_at"),
            actor=log.get("changed_by_profile") or {},
            username=log.get("changed_by"),
            reason=log.get("reason") or "--",
            summary=log.get("summary") or "--",
        )

    for item in history:
        similar_cases = _build_similar_case_cards(port_call, item, limit=3) if casebook_enabled else []
        casebook_recommendation = _build_casebook_recommendation(item, similar_cases)
        analysis_checklist, analysis_summary = _build_maneuver_analysis_checklist(
            port_call,
            item,
            similar_cases=similar_cases,
            casebook_recommendation=casebook_recommendation,
        )
        maneuvers.append({
            "id": item.get("id"), "type": item.get("type"),
            "status_key": item.get("state", ""),
            "title": item.get("type_label", item.get("type", "")),
            "status": item.get("state_label", item.get("state", "")),
            "status_class": (
                "completed" if item.get("state") == "completed"
                else "approved" if item.get("state") == "approved"
                else "aborted" if item.get("state") == "aborted"
                else "pending"
            ),
            "when_label": item.get("effective_time_label") if item.get("state") == "completed" else item.get("planned_label"),
            "planned_label": item.get("planned_label"),
            "planned_input_value": item.get("planned_input_value", ""),
            "execution_started_label": item.get("execution_started_label"),
            "execution_started_input_value": item.get("execution_started_input_value", ""),
            "execution_finished_label": item.get("execution_finished_label"),
            "execution_finished_input_value": item.get("execution_finished_input_value", ""),
            "sort_at": (
                item.get("execution_finished_at")
                or item.get("completed_at")
                or item.get("planned_at")
                or item.get("created_at")
                or ""
            ),
            "draft": item.get("reported_draft_m") or item.get("planned_draft_m") or (port_call["ship_max_draft_label"] if port_call.get("vessel_max_draft_m") else "--"),
            "tug_count": item.get("tug_count") or "",
            "origin": item.get("origin") or "--",
            "destination": item.get("destination") or "--",
            "plan_note": item.get("plan_note") or "",
            "plan_observations": item.get("plan_observations") or item.get("plan_note") or "",
            "report_note": item.get("report_note") or "",
            "notes": item.get("report_note") or item.get("plan_note") or item.get("approval_note") or item.get("aborted_reason") or "",
            "agent_profile": item.get("agent_profile", {}),
            "validated_by_profile": item.get("pilot_profile", {}),
            "executed_by_profile": item.get("reported_by_profile", {}),
            "constraints": item.get("constraint_badges", []),
            "constraint_codes": item.get("constraints", []),
            "change_count": item.get("change_count", 0),
            "has_changes": item.get("has_changes", False),
            "report_completed": bool((item.get("report_note") or "").strip()),
            "similar_cases": similar_cases,
            "casebook_recommendation": casebook_recommendation,
            "analysis_checklist": analysis_checklist,
            "analysis_summary": analysis_summary,
            "can_edit_plan": (
                (current_role == "admin")
                or (current_role == "agente" and item.get("state") == "pending")
            ),
            "can_edit_report": current_role in {"admin", "piloto"} and item.get("state") in {"completed", "aborted"} and bool((item.get("report_note") or "").strip()),
        })
        _add_history_row(
            scope=item.get("type_label", item.get("type", "")),
            changed_at=item.get("created_at"),
            actor=item.get("agent_profile") or item.get("created_by_profile"),
            username=item.get("created_by"),
            reason="Marcação",
            summary=f"{item.get('type_label', 'Manobra')} criada para {item.get('planned_label') or '--'}.",
        )
        if item.get("decided_at"):
            decision_summary = (
                f"{item.get('type_label', 'Manobra')} abortada."
                if item.get("state") == "aborted"
                else f"{item.get('type_label', 'Manobra')} aprovada."
            )
            _add_history_row(
                scope=item.get("type_label", item.get("type", "")),
                changed_at=item.get("decided_at"),
                actor=item.get("pilot_profile") or item.get("decided_by_profile"),
                username=item.get("decided_by"),
                reason=item.get("aborted_reason") or item.get("approval_note") or "Validação operacional",
                summary=decision_summary,
            )
        if item.get("completed_at"):
            _add_history_row(
                scope=item.get("type_label", item.get("type", "")),
                changed_at=item.get("completed_at"),
                actor=item.get("reported_by_profile") or item.get("pilot_profile"),
                username=item.get("reported_by") or item.get("decided_by"),
                reason="Execução",
                summary=f"{item.get('type_label', 'Manobra')} executada.",
            )
        if item.get("reported_at"):
            _add_history_row(
                scope=item.get("type_label", item.get("type", "")),
                changed_at=item.get("reported_at"),
                actor=item.get("reported_by_profile"),
                username=item.get("reported_by"),
                reason="Registo do piloto",
                summary=f"Registo operacional guardado para {item.get('type_label', 'manobra').lower()}.",
            )
        for log in item.get("change_log", []):
            _add_history_row(
                scope=item.get("type_label", item.get("type", "")),
                changed_at=log.get("changed_at"),
                actor=log.get("changed_by_profile") or {},
                username=log.get("changed_by"),
                reason=log.get("reason") or "--",
                summary=log.get("summary") or "--",
            )
    maneuvers.sort(
        key=lambda item: (
            {"pending": 3, "approved": 3, "completed": 2, "aborted": 1}.get(item.get("status_key"), 0),
            item.get("sort_at") or "",
        ),
        reverse=True,
    )
    entry_report_exists = bool(entry and entry.get("state") in {"completed", "aborted"} and entry.get("report_note"))
    departure_report_exists = bool(latest_departure and latest_departure.get("state") in {"completed", "aborted"} and latest_departure.get("report_note"))
    shift_report_exists = bool(latest_shift and latest_shift.get("state") in {"completed", "aborted"} and latest_shift.get("report_note"))

    summary = {
        "scale_reference": port_call["reference_code"],
        "status_label": "Concluída" if port_call.get("status") == "departed" else "Em porto" if port_call.get("status") == "in_port" else "Prevista",
        "eta_label": port_call["eta_label"],
        "etd_label": etd_label or "Sem ETD",
        "eta_status_label": "Confirmado" if entry and entry.get("state") == "completed" else "Previsto",
        "etd_status_label": "Confirmado" if completed_departure else "Previsto",
        "current_location": port_call["berth_label"],
        "last_port": port_call.get("last_port") or "--",
        "next_port": port_call.get("next_port") or "--",
        "agent_label": port_call["agent_label"],
        "pilot_label": port_call["pilot_label"],
        "agent_profile": agent_contact_profile,
        "pilot_profile": port_call.get("pilot_profile", {}),
        "maneuver_count": len(maneuvers),
        "report_points_count": len(maneuvers) * 4,
        "stay_hours_label": _hours_between(
            (entry or {}).get("completed_at") or (entry or {}).get("planned_at"), etd_value,
        ),
    }
    ship_profile = {
        "doc_number": ship_doc_number,
        "scale_reference": port_call["reference_code"],
        "name": port_call["vessel_name"],
        "imo": port_call["ship_imo_label"],
        "call_sign": port_call["ship_call_sign_label"],
        "flag": port_call["ship_flag_label"],
        "type": port_call["ship_type_label"],
        "type_icon": port_call.get("ship_type_icon"),
        "loa": port_call["ship_loa_label"],
        "beam": port_call["ship_beam_label"],
        "gt": port_call["ship_gt_label"],
        "draft": port_call["ship_max_draft_label"],
        "dwt": port_call["ship_dwt_label"],
        "bow_thruster": port_call["ship_bow_thruster_label"],
        "stern_thruster": port_call["ship_stern_thruster_label"],
        "bow_thruster_value": port_call.get("vessel_bow_thruster", "unknown") or "unknown",
        "stern_thruster_value": port_call.get("vessel_stern_thruster", "unknown") or "unknown",
    }
    actions = {
        "can_approve_entry": port_call.get("status") == "scheduled" and port_call.get("approval_status") == "pending",
        "can_cancel_entry": port_call.get("status") == "scheduled" and bool(entry) and entry.get("state") == "pending",
        "can_abort_entry": port_call.get("status") == "scheduled" and bool(entry) and entry.get("state") == "approved",
        "can_complete_entry": False,
        "can_plan_entry": port_call.get("status") == "scheduled" and not active_entry,
        "can_plan_departure": port_call.get("status") == "in_port" and not active_departure and not completed_departure,
        "can_approve_departure": port_call.get("status") == "in_port" and bool(active_departure) and active_departure.get("state") == "pending",
        "can_cancel_departure": port_call.get("status") == "in_port" and bool(active_departure) and active_departure.get("state") == "pending",
        "can_abort_departure": port_call.get("status") == "in_port" and bool(active_departure) and active_departure.get("state") == "approved",
        "can_complete_departure": False,
        "can_register_entry": bool(reportable_entry),
        "can_register_departure": bool(reportable_departure),
        "can_plan_shift": port_call.get("status") == "in_port" and not active_shift,
        "can_approve_shift": port_call.get("status") == "in_port" and bool(active_shift) and active_shift.get("state") == "pending",
        "can_cancel_shift": port_call.get("status") == "in_port" and bool(active_shift) and active_shift.get("state") == "pending",
        "can_abort_shift": port_call.get("status") == "in_port" and bool(active_shift) and active_shift.get("state") == "approved",
        "can_complete_shift": False,
        "can_register_shift": bool(reportable_shift),
        "must_report_label": (
            "Registar entrada" if bool(reportable_entry)
            else "Registar saída" if bool(reportable_departure)
            else "Registar mudança" if bool(reportable_shift)
            else ""
        ),
        "entry_report_exists": entry_report_exists,
        "departure_report_exists": departure_report_exists,
        "shift_report_exists": shift_report_exists,
    }
    return {
        "ship_profile": ship_profile,
        "summary": summary,
        "maneuvers": maneuvers,
        "has_casebook_support": any(item.get("similar_cases") for item in maneuvers),
        "change_log_rows": sorted(change_log_rows, key=lambda item: item.get("changed_at") or "", reverse=True),
        "actions": actions,
    }


def build_maneuver_context(port_call: dict, maneuver_id: str) -> dict:
    """Build a dedicated maneuver detail context from a port call and maneuver id."""
    scale = build_scale_context(port_call)
    maneuver = next((item for item in scale["maneuvers"] if item.get("id") == maneuver_id), None)
    if not maneuver:
        raise ValueError("Manobra não encontrada.")
    case_record = services.store.get_maneuver_case(maneuver_id) if hasattr(services.store, "get_maneuver_case") else None

    state_key = (maneuver.get("status_key") or "").strip().lower()
    plan_status = "done" if maneuver.get("planned_label") and maneuver.get("planned_label") != "Sem hora" else "current"
    if state_key == "aborted":
        validation_status = "muted"
        report_status = "done" if maneuver.get("report_completed") else "current"
    else:
        validation_status = "done" if state_key in {"approved", "completed"} else "current" if state_key == "pending" else "muted"
        report_status = "done" if maneuver.get("report_completed") else "current" if state_key in {"approved", "completed"} else "muted"
    report_detail = (
        maneuver.get("execution_finished_label")
        if maneuver.get("report_completed")
        else "Registo do piloto em falta"
        if state_key in {"approved", "completed"}
        else "Registo do aborto em falta"
        if state_key == "aborted"
        else "Aguarda validação"
    )
    validation_detail = (
        "Manobra abortada"
        if state_key == "aborted"
        else maneuver.get("validated_by_profile", {}).get("label")
        or "--"
        if state_key in {"approved", "completed"}
        else "Aguarda confirmação"
    )
    timeline = [
        {"label": "Planeamento", "status": plan_status, "detail": maneuver.get("planned_label") or "Sem hora"},
        {"label": "Validação", "status": validation_status, "detail": validation_detail},
        {"label": "Registo do piloto", "status": report_status, "detail": report_detail},
    ]
    return {
        "scale": scale,
        "maneuver": maneuver,
        "case_record": case_record or {},
        "similar_cases": maneuver.get("similar_cases", []),
        "timeline": timeline,
    }
