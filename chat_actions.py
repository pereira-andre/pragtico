from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from typing import Dict, List, Optional


ACTION_SPECS = {
    # --- Escala (port_call) — Agente/Admin gerem ---
    "create_port_call": {
        "label": "Registar escala",
        "roles": {"admin", "agente"},
        "requires_target": False,
    },
    # --- Entrada — Piloto/Admin operam ---
    "approve_entry": {
        "label": "Aprovar entrada",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    "abort_entry": {
        "label": "Cancelar/Abortar entrada",
        "roles": {"admin", "agente", "piloto"},
        "requires_target": True,
    },
    "entry_report": {
        "label": "Registar manobra de entrada",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    # --- Saída — Agente planeia, Piloto/Admin operam ---
    "schedule_departure": {
        "label": "Planear saída",
        "roles": {"admin", "agente"},
        "requires_target": True,
    },
    "approve_departure": {
        "label": "Aprovar saída",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    "abort_departure": {
        "label": "Cancelar/Abortar saída",
        "roles": {"admin", "agente", "piloto"},
        "requires_target": True,
    },
    "departure_report": {
        "label": "Registar manobra de saída",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    # --- Mudança de cais — Agente planeia, Piloto/Admin operam ---
    "schedule_shift": {
        "label": "Planear mudança",
        "roles": {"admin", "agente"},
        "requires_target": True,
    },
    "approve_shift": {
        "label": "Aprovar mudança",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    "abort_shift": {
        "label": "Cancelar/Abortar mudança",
        "roles": {"admin", "agente", "piloto"},
        "requires_target": True,
    },
    "shift_report": {
        "label": "Registar manobra de mudança",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    # --- Edição de planeamento — Agente/Admin editam ---
    "edit_maneuver_plan": {
        "label": "Editar planeamento",
        "roles": {"admin", "agente"},
        "requires_target": True,
    },
}

GENERIC_ACTION_FAMILIES = (
    ("approve", "approve"),
    ("abort", "abort"),
    ("cancel", "abort"),
    ("complete", "approve"),
    ("confirm", "approve"),
    ("report", "report"),
    ("edit_plan", "edit_plan"),
    ("edit_report", "edit_report"),
)

ACTION_KEYWORDS = (
    "aprova",
    "aprove",
    "aborta",
    "cancela",
    "anula",
    "regista",
    "registar",
    "cria",
    "criar",
    "marca",
    "marcar",
    "agenda",
    "agendar",
    "planeia",
    "planear",
    "altera",
    "altera",
    "editar",
    "edita",
    "atualiza",
    "actualiza",
    "muda",
    "mete",
    "poe",
    "põe",
    "prevista",
    "previsto",
    "planeada",
    "planeado",
    "pendente",
    "ajusta",
    "fechar",
    "fecha",
    "confirma",
    "confirma",
)

QUERY_HINTS = (
    "qual",
    "quais",
    "como",
    "quando",
    "onde",
    "porque",
    "porquê",
    "podes explicar",
    "explica",
    "consulta",
    "mostra",
    "ver",
)

MANEUVER_TYPES = {"entry", "departure", "shift"}
JSON_BLOCK_RE = re.compile(r"\{.*\}", flags=re.DOTALL)
TIME_OR_STATUS_HINT_RE = re.compile(
    r"\b(\d{1,2}:\d{2}|hoje|amanha|amanhã|mesmo dia|previst\w*|pendente|aprovad\w*|abortad\w*)\b"
)
OPERATIONAL_OBJECT_HINTS = ("navio", "escala", "manobra", "entrada", "saida", "saída", "mudanca", "mudança")
TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}$")
PENDING_UPDATE_FIELD_ALIASES = {
    "change_reason": [
        "motivo da alteracao",
        "motivo da alteração",
        "change_reason",
        "motivo",
    ],
    "planned_at_local": [
        "planned_at_local",
        "hora prevista",
        "nova hora prevista",
        "hora de marcacao",
        "hora de marcação",
    ],
    "notes": ["notes", "nota", "observacoes", "observações"],
    "berth": ["cais previsto", "cais", "planned_quay", "planned_berth", "quay", "berth", "pier"],
    "destination_berth": ["cais destino", "destination_quay", "destination_berth", "destination_pier"],
    "origin_berth": ["cais origem", "origin_quay", "origin_berth", "origin_pier"],
    "next_port": ["proximo porto", "próximo porto", "next_port", "port_of_destination"],
    "last_port": ["ultimo porto", "último porto", "last_port", "port_of_origin"],
    "eta_local": ["eta", "eta_local"],
    "planned_departure_at_local": ["etd", "planned_departure_at_local"],
    "draft_m": ["calado", "draft_m", "draught", "draft"],
    "tug_count": ["rebocadores", "reboques", "tug_count", "tugs", "numero de rebocadores"],
    # Vessel data fields (for create_port_call)
    "vessel_name": ["nome do navio", "nome navio", "vessel_name", "navio"],
    "vessel_imo": ["imo", "vessel_imo"],
    "vessel_call_sign": ["indicativo", "call_sign", "vessel_call_sign", "callsign"],
    "vessel_flag": ["bandeira", "flag", "vessel_flag"],
    "vessel_type": ["tipo de navio", "tipo navio", "vessel_type", "tipo"],
    "vessel_loa_m": ["loa", "loa (m)", "vessel_loa_m", "comprimento"],
    "vessel_beam_m": ["boca", "boca (m)", "beam", "vessel_beam_m", "largura"],
    "vessel_gt_t": ["gt", "gt (t)", "vessel_gt_t", "arqueacao bruta", "arqueação bruta"],
    "vessel_dwt_t": ["dwt", "dwt (t)", "vessel_dwt_t", "deadweight"],
    "vessel_max_draft_m": ["calado maximo", "calado máximo", "calado maximo (m)", "calado máximo (m)", "max_draft", "vessel_max_draft_m"],
}
FIELD_ALIASES = {
    "name": "vessel_name",
    "vessel": "vessel_name",
    "ship": "vessel_name",
    "ship_name": "vessel_name",
    "nome_navio": "vessel_name",
    "imo": "vessel_imo",
    "call_sign": "vessel_call_sign",
    "callsign": "vessel_call_sign",
    "indicativo": "vessel_call_sign",
    "flag": "vessel_flag",
    "bandeira": "vessel_flag",
    "loa": "vessel_loa_m",
    "vessel_loa": "vessel_loa_m",
    "length_overall": "vessel_loa_m",
    "length": "vessel_loa_m",
    "breadth": "vessel_beam_m",
    "beam": "vessel_beam_m",
    "vessel_beam": "vessel_beam_m",
    "boca": "vessel_beam_m",
    "gt": "vessel_gt_t",
    "vessel_gt": "vessel_gt_t",
    "gross_tonnage": "vessel_gt_t",
    "dwt": "vessel_dwt_t",
    "vessel_dwt": "vessel_dwt_t",
    "deadweight": "vessel_dwt_t",
    "draft": "draft_m",
    "draught": "draft_m",
    "vessel_draft": "draft_m",
    "max_draft": "vessel_max_draft_m",
    "eta": "eta_local",
    "arrival_eta": "eta_local",
    "arrival_time": "eta_local",
    "estimated_arrival": "eta_local",
    "estimated_time_of_arrival": "eta_local",
    "eta_setubal": "eta_local",
    "berth": "berth",
    "pier": "berth",
    "quay": "berth",
    "planned_quay": "berth",
    "planned_berth": "berth",
    "quay_planned": "berth",
    "berthing_quay": "berth",
    "port_of_call": "berth",
    "ata": "arrived_at_local",
    "atd": "departed_at_local",
    "etd": "planned_departure_at_local",
    "planned_departure_at": "planned_departure_at_local",
    "planned_shift_at": "planned_shift_at_local",
    "planned_at": "planned_at_local",
    "planned_datetime": "planned_at_local",
    "start_time": "maneuver_started_local",
    "end_time": "maneuver_finished_local",
    "maneuver_started_at": "maneuver_started_local",
    "maneuver_finished_at": "maneuver_finished_local",
    "last_port_of_call": "last_port",
    "port_of_origin": "last_port",
    "previous_port": "last_port",
    "next_port_of_call": "next_port",
    "port_of_destination": "next_port",
    "destination_port": "next_port",
    "next_destination": "next_port",
    "origin_berth": "origin_berth",
    "origin_pier": "origin_berth",
    "origin_quay": "origin_berth",
    "destination_berth": "destination_berth",
    "destination_pier": "destination_berth",
    "destination_quay": "destination_berth",
    "number_of_tugs": "tug_count",
    "tugs": "tug_count",
    "rebocadores": "tug_count",
    "operational_note": "notes",
    "operational_notes": "notes",
    "observation": "notes",
    "observations": "notes",
    "note": "notes",
}
NESTED_FIELD_GROUPS = {
    "port_call_data",
    "entry_data",
    "departure_data",
    "shift_data",
    "report_data",
    "plan_data",
}
REQUIRED_FIELDS_BY_ACTION = {
    "create_port_call": {
        "vessel_name",
        "vessel_imo",
        "vessel_call_sign",
        "vessel_flag",
        "vessel_type",
        "vessel_loa_m",
        "vessel_beam_m",
        "vessel_gt_t",
        "vessel_max_draft_m",
        "vessel_dwt_t",
        "eta_local",
        "berth",
        "last_port",
        "next_port",
    },
    "abort_entry": {"aborted_reason"},
    "abort_departure": {"aborted_reason"},
    "abort_shift": {"aborted_reason"},
    "entry_report": {"maneuver_started_local", "maneuver_finished_local", "draft_m"},
    "departure_report": {"maneuver_started_local", "maneuver_finished_local", "draft_m"},
    "shift_report": {"maneuver_started_local", "maneuver_finished_local", "draft_m"},
    "schedule_departure": {"planned_departure_at_local", "next_port"},
    "schedule_shift": {"planned_shift_at_local", "destination_berth"},
    "edit_maneuver_plan": {"planned_at_local", "change_reason"},
}
DISPLAY_FIELD_LABELS = {
    "vessel_name": "nome do navio",
    "vessel_imo": "IMO",
    "vessel_call_sign": "indicativo",
    "vessel_flag": "bandeira",
    "vessel_type": "tipo de navio",
    "vessel_loa_m": "LOA",
    "vessel_beam_m": "boca",
    "vessel_gt_t": "GT",
    "vessel_max_draft_m": "calado máximo",
    "vessel_dwt_t": "DWT",
    "eta_local": "ETA",
    "berth": "cais previsto",
    "last_port": "último porto",
    "next_port": "próximo porto",
    "planned_departure_at_local": "hora prevista de saída",
    "planned_shift_at_local": "hora prevista da mudança",
    "destination_berth": "cais destino",
    "aborted_reason": "motivo",
    "maneuver_started_local": "início da manobra",
    "maneuver_finished_local": "fim da manobra",
    "draft_m": "calado",
    "change_reason": "motivo da alteração",
}


def _lookup_key(value: Optional[str]) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


def _normalized_ascii_text(value: Optional[str]) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    return normalized.encode("ascii", "ignore").decode("ascii")


def looks_like_operational_command(question: str) -> bool:
    clean = _lookup_key(question)
    if not clean:
        return False
    if "arquivo" in clean or "archived" in clean:
        return False
    if any(token in clean for token in ACTION_KEYWORDS):
        return True
    if any(token in clean for token in OPERATIONAL_OBJECT_HINTS) and TIME_OR_STATUS_HINT_RE.search(clean):
        return True
    if any(clean.startswith(token) for token in QUERY_HINTS):
        return False
    return bool(re.search(r"\b(ata|atd|eta|etd)\b", clean))


def extract_json_object(text: str) -> Optional[Dict]:
    if not text:
        return None
    clean = text.strip()
    try:
        payload = json.loads(clean)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass

    match = JSON_BLOCK_RE.search(clean)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def allowed_actions_for_role(role: str) -> List[str]:
    clean_role = (role or "").strip().lower()
    return [
        action
        for action, spec in ACTION_SPECS.items()
        if clean_role in spec["roles"]
    ]


def canonicalize_action_name(raw_action: str, maneuver_type: str = "") -> str:
    clean_action = _lookup_key(raw_action).replace(" ", "_")
    clean_type = (maneuver_type or "").strip().lower()
    if clean_action in ACTION_SPECS:
        return clean_action
    if clean_type not in MANEUVER_TYPES:
        clean_type = "entry"

    generic_aliases = {
        "approve_maneuver": f"approve_{clean_type}",
        "approve_manobra": f"approve_{clean_type}",
        "approve_port_call": f"approve_{clean_type}",
        "approve_scale": f"approve_{clean_type}",
        "approve": f"approve_{clean_type}",
        "abort_maneuver": f"abort_{clean_type}",
        "cancel_maneuver": f"abort_{clean_type}",
        "abort_port_call": f"abort_{clean_type}",
        "cancel_port_call": f"abort_{clean_type}",
        "complete_maneuver": f"complete_{clean_type}",
        "confirm_maneuver": f"complete_{clean_type}",
        "complete_port_call": f"complete_{clean_type}",
        "report_maneuver": f"{clean_type}_report",
        "edit_plan": "edit_maneuver_plan",
        "update_plan": "edit_maneuver_plan",
        "edit_maneuver": "edit_maneuver_plan",
        "edit_report": "edit_maneuver_plan",
        "update_report": "edit_maneuver_plan",
    }
    if clean_action in generic_aliases:
        return generic_aliases[clean_action]

    for family_prefix, family_name in GENERIC_ACTION_FAMILIES:
        if clean_action.startswith(family_prefix):
            if family_name == "approve":
                return f"approve_{clean_type}"
            if family_name == "abort":
                return f"abort_{clean_type}"
            if family_name == "complete":
                return f"complete_{clean_type}"
            if family_name == "report":
                return f"{clean_type}_report"
            if family_name == "edit_plan":
                return "edit_maneuver_plan"
            if family_name == "edit_report":
                return "edit_maneuver_plan"
    return clean_action


def _canonical_field_name(name: str) -> str:
    clean = _lookup_key(name).replace(" ", "_")
    return FIELD_ALIASES.get(clean, name.strip())


def normalize_action_fields(action: str, fields: Dict) -> Dict:
    normalized: Dict = {}

    def _consume(payload: Dict) -> None:
        for key, value in (payload or {}).items():
            canonical = _canonical_field_name(str(key))
            if canonical in NESTED_FIELD_GROUPS and isinstance(value, dict):
                _consume(value)
                continue
            if isinstance(value, dict):
                normalized[canonical] = {
                    sub_key: sub_value
                    for sub_key, sub_value in value.items()
                }
                continue
            if isinstance(value, list):
                normalized[canonical] = value
                continue
            if value is None:
                normalized[canonical] = ""
            else:
                normalized[canonical] = str(value).strip()

    _consume(fields or {})

    if "reason" in normalized:
        if action == "edit_maneuver_plan" and not normalized.get("change_reason"):
            normalized["change_reason"] = normalized["reason"]
        elif action in {"abort_entry", "abort_departure", "abort_shift"} and not normalized.get("aborted_reason"):
            normalized["aborted_reason"] = normalized["reason"]
        normalized.pop("reason", None)

    if action == "edit_maneuver_plan" and normalized.get("eta_local") and not normalized.get("planned_at_local"):
        normalized["planned_at_local"] = normalized["eta_local"]

    if action == "create_port_call":
        if normalized.get("draft_m") and not normalized.get("vessel_max_draft_m"):
            normalized["vessel_max_draft_m"] = normalized["draft_m"]
        if normalized.get("vessel_max_draft_m") and not normalized.get("draft_m"):
            normalized["draft_m"] = normalized["vessel_max_draft_m"]
    if normalized.get("notes") is None:
        normalized["notes"] = ""
    return normalized


def required_missing_fields(action: str, fields: Dict) -> List[str]:
    required = REQUIRED_FIELDS_BY_ACTION.get(action, set())
    missing = []
    for field in required:
        value = fields.get(field)
        if isinstance(value, list):
            if not value:
                missing.append(field)
            continue
        if not " ".join(str(value or "").split()):
            missing.append(field)
    return sorted(missing)


def display_missing_field_labels(fields: List[str]) -> List[str]:
    return [DISPLAY_FIELD_LABELS.get(field, field) for field in fields]


def _extract_labelled_values(question: str) -> Dict[str, str]:
    text = " ".join((question or "").strip().split())
    if not text:
        return {}
    normalized_text = _normalized_ascii_text(text)
    hits = []
    for canonical, aliases in PENDING_UPDATE_FIELD_ALIASES.items():
        for alias in aliases:
            needle = _normalized_ascii_text(alias).strip()
            pattern = re.compile(
                rf"(^|[\s,;]){re.escape(needle)}\s*(?:=|:|\beh\b|\be\b)\s*",
                flags=re.IGNORECASE,
            )
            match = pattern.search(normalized_text)
            if not match:
                continue
            hits.append((match.start(), match.end(), canonical, needle))
            break
    if not hits:
        return {}
    hits.sort(key=lambda item: item[0])
    extracted = {}
    for index, (_start, value_start, canonical, needle) in enumerate(hits):
        end = hits[index + 1][0] if index + 1 < len(hits) else len(normalized_text)
        raw_value = normalized_text[value_start:end]
        raw_value = raw_value.strip(" ,;.:-")
        if raw_value:
            extracted[canonical] = raw_value
    return extracted


def _normalize_time_only(value: str, fallback: str) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not TIME_ONLY_RE.match(clean):
        return clean
    fallback_clean = " ".join(str(fallback or "").strip().split())
    if "T" not in fallback_clean:
        return clean
    date_part = fallback_clean.split("T", 1)[0]
    return f"{date_part}T{clean}"


def extract_pending_field_updates(question: str, proposal: Dict) -> Dict[str, str]:
    extracted = _extract_labelled_values(question)
    if not extracted:
        return {}
    fields = proposal.get("fields") or {}
    action = proposal.get("action") or ""

    if action == "edit_maneuver_plan" and extracted.get("planned_at_local"):
        fallback = fields.get("planned_at_local") or fields.get("eta_local") or ""
        extracted["planned_at_local"] = _normalize_time_only(extracted["planned_at_local"], fallback)
    if action == "schedule_departure" and extracted.get("planned_departure_at_local"):
        fallback = fields.get("planned_departure_at_local") or ""
        extracted["planned_departure_at_local"] = _normalize_time_only(extracted["planned_departure_at_local"], fallback)
    if action == "schedule_shift" and extracted.get("planned_shift_at_local"):
        fallback = fields.get("planned_shift_at_local") or ""
        extracted["planned_shift_at_local"] = _normalize_time_only(extracted["planned_shift_at_local"], fallback)
    return extracted


def merge_action_candidate(existing: Dict, updates: Dict, role: str) -> Optional[Dict]:
    if not isinstance(existing, dict):
        return None
    if not isinstance(updates, dict):
        return existing

    action = (updates.get("action") or existing.get("action") or "").strip()
    target = {
        **(existing.get("target") or {}),
        **(updates.get("target") or {}),
    }
    existing_fields = existing.get("fields") or {}
    update_fields = dict(updates.get("fields") or {})
    update_reason = " ".join(str(updates.get("reason") or "").strip().split())
    if action == "edit_maneuver_plan" and update_reason:
        if not " ".join(str(update_fields.get("change_reason") or existing_fields.get("change_reason") or "").split()):
            update_fields["change_reason"] = update_reason
    elif action in {"abort_entry", "abort_departure", "abort_shift"} and update_reason:
        if not " ".join(str(update_fields.get("aborted_reason") or existing_fields.get("aborted_reason") or "").split()):
            update_fields["aborted_reason"] = update_reason
    fields = normalize_action_fields(
        action,
        {
            **existing_fields,
            **update_fields,
        },
    )
    candidate = normalize_action_candidate(
        {
            "intent": "action",
            "action": action,
            "confidence": updates.get("confidence", existing.get("confidence", 0.0)),
            "reason": updates.get("reason") or existing.get("reason", ""),
            "target": target,
            "fields": fields,
            "missing_fields": [],
        },
        role,
    )
    if not candidate:
        return None
    for key in ("port_call_id", "maneuver_id"):
        if existing.get(key) and not candidate.get(key):
            candidate[key] = existing.get(key)
    return candidate


def visible_port_calls_from_activity(port_activity: Dict) -> List[Dict]:
    visible = {}
    for key in ("arrivals", "in_port", "departed", "aborted", "departure_candidates"):
        for item in port_activity.get(key, []) or []:
            item_id = item.get("id")
            if item_id:
                visible[item_id] = item
    for key in ("planned_maneuvers", "archived_maneuvers"):
        for item in port_activity.get(key, []) or []:
            item_id = item.get("port_call_id") or item.get("id")
            if not item_id:
                continue
            existing = visible.get(item_id, {})
            visible[item_id] = {
                **item,
                **existing,
                "id": item_id,
                "reference_code": existing.get("reference_code") or item.get("reference_code"),
                "vessel_name": existing.get("vessel_name") or item.get("vessel_name"),
            }
    return sorted(
        visible.values(),
        key=lambda item: (
            item.get("eta")
            or item.get("ata")
            or item.get("departure_at")
            or item.get("planned_value")
            or item.get("actual_value")
            or item.get("date_value")
            or "",
            item.get("vessel_name") or "",
        ),
    )


def summarize_port_calls(port_calls: List[Dict], limit: int = 18) -> str:
    if not port_calls:
        return "Sem escalas visíveis."
    rows = []
    for item in port_calls[:limit]:
        rows.append(
            " | ".join(
                [
                    item.get("reference_code") or "--",
                    item.get("vessel_name") or "--",
                    item.get("status_label") or item.get("status") or "--",
                    item.get("berth_label") or item.get("berth") or "--",
                    f"ETA {item.get('eta_label') or '--'}",
                    f"ATA {item.get('ata_label') or '--'}",
                    f"ATD {item.get('departure_label') or '--'}",
                ]
            )
        )
    extra = len(port_calls) - limit
    if extra > 0:
        rows.append(f"+{extra} escala(s) adicionais")
    return "\n".join(f"- {row}" for row in rows)


def build_operational_action_prompt(
    *,
    question: str,
    role: str,
    now_local: datetime,
    port_calls: List[Dict],
    berth_options: List[str],
    constraint_options: List[Dict],
) -> str:
    allowed_actions = allowed_actions_for_role(role)
    action_lines = []
    for action in allowed_actions:
        spec = ACTION_SPECS[action]
        action_lines.append(f"- {action}: {spec['label']}")
    constraints = ", ".join(item.get("code", "") for item in constraint_options if item.get("code"))
    berths = ", ".join(berth_options[:24])

    return f"""
És um classificador de ações operacionais para um portal marítimo.
Responde apenas com JSON puro, sem markdown.

Data/hora local atual: {now_local.strftime("%Y-%m-%d %H:%M")} Europe/Lisbon
Papel autenticado: {role}

Objetivo:
- Dizer se a mensagem é uma ação operacional executável no portal.
- Nunca propor edição do arquivo histórico. O arquivo é só consulta.
- Se faltar informação obrigatória, preencher `missing_fields`.
- Se a mensagem for só consulta, usa `intent: "question"`.
- Se a mensagem pedir algo proibido ou fora de papel/arquivo, usa `intent: "unsupported"`.

Ações permitidas para este papel:
{chr(10).join(action_lines) or "- nenhuma"}

Tipos de manobra possíveis:
- entry
- departure
- shift

Restrições válidas:
- {constraints or "nenhuma"}

Cais conhecidos:
- {berths}

Escalas visíveis neste utilizador:
{summarize_port_calls(port_calls)}

Regras de saída:
- Campos do schema: intent, action, confidence, reason, target, fields, missing_fields.
- `intent` deve ser um de: action, question, unsupported.
- `action` deve ser uma ação permitida ou string vazia.
- `confidence` entre 0 e 1.
- `target.reference_code` e `target.vessel_name` devem vir limpos, sem inventar.
- `target.maneuver_type` deve ser entry, departure, shift ou vazio.
- Em datas/horas operacionais, usa formato `YYYY-MM-DDTHH:MM` no fuso local.
- Se a mensagem pedir alterar um planeamento existente, prefere `edit_maneuver_plan`.
- Se a mensagem pedir rever um registo já concluído, prefere `edit_maneuver_plan`.
- Em `fields.constraints`, devolve códigos válidos.

Mensagem do utilizador:
{question}
""".strip()


def build_pending_action_update_prompt(
    *,
    question: str,
    role: str,
    proposal: Dict,
    berth_options: List[str],
    constraint_options: List[Dict],
) -> str:
    constraints = ", ".join(item.get("code", "") for item in constraint_options if item.get("code"))
    berths = ", ".join(berth_options[:24])
    current_fields = json.dumps(proposal.get("fields") or {}, ensure_ascii=False, indent=2)
    current_target = json.dumps(proposal.get("target") or {}, ensure_ascii=False, indent=2)
    missing = ", ".join(proposal.get("missing_fields") or []) or "nenhum"

    return f"""
Estás a completar ou corrigir uma ação operacional já pendente num portal marítimo.
Responde apenas com JSON puro, sem markdown.

Papel autenticado: {role}
Ação pendente atual: {proposal.get("action", "")}
Target atual:
{current_target}

Campos atuais:
{current_fields}

Campos ainda em falta:
{missing}

Restrições válidas:
- {constraints or "nenhuma"}

Cais conhecidos:
- {berths}

Objetivo:
- Se o utilizador estiver a responder ao que falta ou a corrigir dados, devolve `intent: "update"`.
- Se o utilizador quiser trocar para outra ação operacional, devolve `intent: "replace"` e inclui a nova ação completa.
- Se o utilizador quiser desistir, devolve `intent: "cancel"`.
- Se não houver alteração operacional concreta, devolve `intent: "question"`.

Schema:
- `intent`: update | replace | cancel | question | unsupported
- `action`: ação operacional ou string vazia
- `confidence`: 0..1
- `reason`: texto curto
- `target`: objeto com correções opcionais de target
- `fields`: apenas os campos novos/corrigidos

Regras:
- Em `update`, mantém a mesma ação salvo correção explícita do utilizador.
- Usa formato `YYYY-MM-DDTHH:MM` para datas/horas locais.
- Não inventes valores em falta.

Mensagem do utilizador:
{question}
""".strip()


def normalize_action_candidate(candidate: Dict, role: str) -> Optional[Dict]:
    if not isinstance(candidate, dict):
        return None
    intent = (candidate.get("intent") or "").strip().lower()
    raw_action = (candidate.get("action") or "").strip()
    if intent not in {"action", "question", "unsupported"}:
        return None

    if intent != "action":
        return {
            "intent": intent,
            "action": "",
            "confidence": float(candidate.get("confidence") or 0.0),
            "reason": " ".join((candidate.get("reason") or "").strip().split()),
            "target": {},
            "fields": {},
            "missing_fields": [],
        }

    target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
    fields = candidate.get("fields") if isinstance(candidate.get("fields"), dict) else {}
    maneuver_type = (target.get("maneuver_type") or fields.get("maneuver_type") or "").strip().lower()
    if maneuver_type not in MANEUVER_TYPES:
        maneuver_type = ""
    action = canonicalize_action_name(raw_action, maneuver_type)
    spec = ACTION_SPECS.get(action)
    if not spec or (role or "").strip().lower() not in spec["roles"]:
        return {
            "intent": "unsupported",
            "action": "",
            "confidence": float(candidate.get("confidence") or 0.0),
            "reason": "A ação pedida não está autorizada para este perfil.",
            "target": {},
            "fields": {},
            "missing_fields": [],
        }

    normalized_fields = normalize_action_fields(action, fields)
    if action == "create_port_call":
        if not normalized_fields.get("vessel_name"):
            normalized_fields["vessel_name"] = " ".join((target.get("vessel_name") or "").strip().split())
        target["maneuver_type"] = ""
    constraints = normalized_fields.get("constraints")
    if not isinstance(constraints, list):
        constraints = []
    missing_fields = required_missing_fields(action, normalized_fields)

    return {
        "intent": "action",
        "action": action,
        "confidence": max(0.0, min(float(candidate.get("confidence") or 0.0), 1.0)),
        "reason": " ".join((candidate.get("reason") or "").strip().split()),
        "target": {
            "reference_code": " ".join((target.get("reference_code") or "").strip().split()),
            "vessel_name": " ".join((target.get("vessel_name") or "").strip().split()),
            "maneuver_type": maneuver_type,
        },
        "fields": {
            key: value
            for key, value in normalized_fields.items()
            if key != "constraints"
        }
        | {"constraints": [str(item).strip() for item in constraints if str(item).strip()]},
        "missing_fields": display_missing_field_labels(missing_fields),
    }


def resolve_port_call(port_calls: List[Dict], target: Dict) -> Optional[Dict]:
    if not port_calls:
        return None
    target_reference = _lookup_key(target.get("reference_code"))
    target_vessel = _lookup_key(target.get("vessel_name"))

    if target_reference:
        for item in port_calls:
            if _lookup_key(item.get("reference_code")) == target_reference:
                return item

    if target_vessel:
        exact = [
            item
            for item in port_calls
            if _lookup_key(item.get("vessel_name")) == target_vessel
        ]
        if len(exact) == 1:
            return exact[0]

        partial = [
            item
            for item in port_calls
            if target_vessel in _lookup_key(item.get("vessel_name"))
        ]
        if len(partial) == 1:
            return partial[0]
    return None


def _maneuver_sort_key(item: Dict) -> tuple:
    return (
        item.get("planned_at") or "",
        item.get("completed_at") or "",
        item.get("updated_at") or "",
        item.get("created_at") or "",
    )


def resolve_maneuver(port_call: Dict, action: str, maneuver_type: str) -> Optional[Dict]:
    clean_type = (maneuver_type or "").strip().lower()
    if clean_type not in MANEUVER_TYPES:
        return None
    history = [
        item
        for item in port_call.get("maneuver_history", []) or []
        if (item.get("type") or "").strip().lower() == clean_type
    ]
    if not history:
        return None

    if action.endswith("_report"):
        valid_states = {"approved", "completed"}
    elif action.startswith("approve_"):
        valid_states = {"pending"}
    elif action.startswith("abort_"):
        valid_states = {"pending", "approved"}
    elif action == "edit_maneuver_plan":
        valid_states = {"pending", "approved"}
    else:
        valid_states = {"pending", "approved", "completed"}

    candidates = [item for item in history if (item.get("state") or "").strip().lower() in valid_states]
    if not candidates:
        candidates = history
    candidates.sort(key=_maneuver_sort_key)
    return candidates[-1]


def infer_maneuver_type(port_call: Dict, action: str) -> str:
    matching_types = [
        maneuver_type
        for maneuver_type in sorted(MANEUVER_TYPES)
        if resolve_maneuver(port_call, action, maneuver_type)
    ]
    if len(matching_types) == 1:
        return matching_types[0]
    return ""


def action_for_maneuver_type(action: str, maneuver_type: str) -> str:
    clean_type = (maneuver_type or "").strip().lower()
    if clean_type not in MANEUVER_TYPES:
        return action
    if action.startswith("approve_"):
        return f"approve_{clean_type}"
    if action.startswith("abort_"):
        return f"abort_{clean_type}"
    if action.startswith("complete_"):
        return f"complete_{clean_type}"
    if action.endswith("_report"):
        return f"{clean_type}_report"
    return action


def format_action_summary(proposal: Dict, port_call: Optional[Dict] = None) -> str:
    action = proposal.get("action") or ""
    spec = ACTION_SPECS.get(action, {})
    label = spec.get("label") or action or "Ação"
    target = port_call or {}
    fields = proposal.get("fields") or {}
    lines = [f"Proposta pronta para confirmar: {label}."]
    if target:
        lines.append(
            f"Escala: {target.get('reference_code') or '--'} · {target.get('vessel_name') or '--'}."
        )
    if proposal.get("target", {}).get("maneuver_type"):
        lines.append(f"Manobra: {proposal['target']['maneuver_type']}.")
    for key, value in fields.items():
        if value in ("", None, []):
            continue
        if key == "constraints" and isinstance(value, list):
            clean_value = ", ".join(value)
        else:
            clean_value = str(value)
        lines.append(f"{DISPLAY_FIELD_LABELS.get(key, key)}: {clean_value}.")
    if proposal.get("reason"):
        lines.append(f"Nota: {proposal['reason']}.")
    missing_fields = proposal.get("missing_fields") or []
    if missing_fields:
        lines.append("Dados ainda em falta: " + ", ".join(missing_fields) + ".")
    lines.append("Confirma para aplicar a alteração no portal.")
    return "\n".join(lines)
