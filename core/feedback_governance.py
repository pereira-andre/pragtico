"""Governance helpers for chat feedback calibration."""

from __future__ import annotations

from typing import Any


FEEDBACK_ERROR_TYPES = {
    "factual_error": "Erro factual",
    "wrong_source": "Fonte errada",
    "incomplete_answer": "Resposta incompleta",
    "ambiguous_question": "Pergunta ambigua",
    "live_data_issue": "Live feed / dados atuais",
    "operational_rule_issue": "Regra operacional",
    "command_flow_issue": "Fluxo / comando",
    "format_issue": "Formato da resposta",
    "other": "Outro",
}

FEEDBACK_SCOPES = {
    "document": "Documento",
    "tug_guidance": "Rebocadores",
    "berth_profile": "Perfil de cais",
    "live_feed": "Live feed",
    "maneuver_flow": "Escalas / manobras",
    "commands": "Comandos",
    "conversation": "So esta conversa",
    "global": "Global",
    "other": "Outro",
}

FEEDBACK_DESTINATIONS = {
    "memory": "Memoria reutilizavel",
    "eval": "Eval / regressao",
    "source_update": "Atualizar fonte",
    "rule_update": "Atualizar regra estruturada",
    "triage": "Triagem",
    "do_not_reuse": "Nao reutilizar",
}

FEEDBACK_CRITICALITIES = {
    "low": "Baixa",
    "medium": "Media",
    "high": "Alta",
    "critical": "Critica",
}

MEMORY_REUSABLE_DESTINATIONS = {"memory"}
REUSE_BLOCKING_DESTINATIONS = {"triage", "eval", "source_update", "rule_update", "do_not_reuse"}
PROMOTABLE_DESTINATIONS = {"eval"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _choice(value: Any, allowed: set[str]) -> str:
    clean = _clean(value).lower()
    return clean if clean in allowed else ""


def governance_options() -> dict[str, list[dict[str, str]]]:
    """Return option lists for templates without leaking mapping internals."""
    return {
        "error_types": [{"value": key, "label": label} for key, label in FEEDBACK_ERROR_TYPES.items()],
        "scopes": [{"value": key, "label": label} for key, label in FEEDBACK_SCOPES.items()],
        "destinations": [{"value": key, "label": label} for key, label in FEEDBACK_DESTINATIONS.items()],
        "criticalities": [{"value": key, "label": label} for key, label in FEEDBACK_CRITICALITIES.items()],
    }


def normalize_feedback_governance(
    *,
    feedback_status: str = "",
    feedback_error_type: str = "",
    feedback_scope: str = "",
    feedback_destination: str = "",
    feedback_criticality: str = "",
    feedback_correction_document: str = "",
) -> dict[str, str]:
    """Normalize governance fields and apply conservative defaults for new feedback."""
    status = _clean(feedback_status).lower()
    error_type = _choice(feedback_error_type, set(FEEDBACK_ERROR_TYPES))
    scope = _choice(feedback_scope, set(FEEDBACK_SCOPES))
    destination = _choice(feedback_destination, set(FEEDBACK_DESTINATIONS))
    criticality = _choice(feedback_criticality, set(FEEDBACK_CRITICALITIES))

    if not scope and _clean(feedback_correction_document):
        scope = "document"

    if not destination:
        if status == "approved":
            destination = "memory"
        elif status == "corrected":
            destination = "triage"
        elif status == "review":
            destination = "triage"
        elif status == "ignored":
            destination = "do_not_reuse"

    if not criticality:
        if status in {"corrected", "review"}:
            criticality = "medium"
        elif status in {"approved", "ignored"}:
            criticality = "low"

    return {
        "feedback_error_type": error_type,
        "feedback_scope": scope,
        "feedback_destination": destination,
        "feedback_criticality": criticality,
    }


def governance_labels(message: dict) -> dict[str, str]:
    error_type = _choice(message.get("feedback_error_type"), set(FEEDBACK_ERROR_TYPES))
    scope = _choice(message.get("feedback_scope"), set(FEEDBACK_SCOPES))
    destination = _choice(message.get("feedback_destination"), set(FEEDBACK_DESTINATIONS))
    criticality = _choice(message.get("feedback_criticality"), set(FEEDBACK_CRITICALITIES))
    return {
        "error_type_label": FEEDBACK_ERROR_TYPES.get(error_type, "Sem tipo"),
        "scope_label": FEEDBACK_SCOPES.get(scope, "Sem escopo"),
        "destination_label": FEEDBACK_DESTINATIONS.get(destination, "Sem destino"),
        "criticality_label": FEEDBACK_CRITICALITIES.get(criticality, "Sem criticidade"),
    }


def feedback_allows_memory_reuse(message: dict) -> bool:
    """Return whether this feedback may influence future answers as memory."""
    status = _clean(message.get("feedback_status")).lower()
    destination = _choice(message.get("feedback_destination"), set(FEEDBACK_DESTINATIONS))
    if not destination:
        return status == "approved"  # legacy positive feedback before governance fields existed
    return destination in MEMORY_REUSABLE_DESTINATIONS and status in {"approved", "corrected"}


def feedback_pipeline_stage(message: dict) -> dict[str, Any]:
    """Explain where a feedback item belongs in the learning pipeline."""
    status = _clean(message.get("feedback_status")).lower()
    destination = _choice(message.get("feedback_destination"), set(FEEDBACK_DESTINATIONS))
    governance = feedback_governance_state(message)
    if feedback_allows_memory_reuse(message):
        stage = "memory"
        action = "Pode entrar na síntese como memória operacional validada."
    elif governance["state"] == "ready_eval" or destination == "eval":
        stage = "eval"
        action = "Usar para regressão/teste; não reutilizar como memória de resposta."
    elif destination in {"source_update", "rule_update"}:
        stage = "source_action"
        action = "Atualizar knowledge/regra estruturada antes de reutilizar."
    elif status in {"corrected", "review"} or destination == "triage":
        stage = "triage"
        action = "Rever e classificar; não usar como fonte de verdade."
    elif status == "ignored" or destination == "do_not_reuse":
        stage = "blocked"
        action = "Manter fora da aprendizagem e da síntese."
    else:
        stage = "raw"
        action = "Aguardar classificação humana."

    return {
        "stage": stage,
        "action": action,
        "state": governance["state"],
        "can_reuse_memory": feedback_allows_memory_reuse(message),
        "can_promote_eval": governance["can_promote_eval"],
        "destination": destination,
    }


def feedback_governance_state(message: dict) -> dict[str, Any]:
    """Classify a feedback record for calibration queues."""
    status = _clean(message.get("feedback_status")).lower()
    error_type = _choice(message.get("feedback_error_type"), set(FEEDBACK_ERROR_TYPES))
    scope = _choice(message.get("feedback_scope"), set(FEEDBACK_SCOPES))
    destination = _choice(message.get("feedback_destination"), set(FEEDBACK_DESTINATIONS))
    criticality = _choice(message.get("feedback_criticality"), set(FEEDBACK_CRITICALITIES))
    correction = _clean(message.get("feedback_correction"))
    document = _clean(message.get("feedback_correction_document"))
    missing: list[str] = []

    if status in {"corrected", "review"}:
        if not error_type:
            missing.append("tipo")
        if not scope:
            missing.append("escopo")
        if not destination:
            missing.append("destino")
        if not criticality:
            missing.append("criticidade")
        if status == "corrected" and not correction:
            missing.append("correcao")
        if scope == "document" and not document:
            missing.append("documento")
        if destination in {"source_update", "rule_update"} and not (_clean(message.get("feedback_note")) or correction):
            missing.append("nota/correcao")

    if not status:
        state = "pending"
    elif missing:
        state = "needs_triage"
    elif destination in {"source_update", "rule_update"}:
        state = "source_action"
    elif status == "review":
        state = "blocked"
    elif status == "corrected" and destination in PROMOTABLE_DESTINATIONS:
        state = "ready_eval"
    elif status in {"approved", "corrected"} and feedback_allows_memory_reuse(message):
        state = "trusted_memory"
    elif status == "ignored" or destination == "do_not_reuse":
        state = "ignored"
    else:
        state = "triage"

    labels = governance_labels(message)
    return {
        "state": state,
        "missing": missing,
        "is_critical": criticality in {"high", "critical"},
        "can_promote_eval": state == "ready_eval",
        **labels,
    }
