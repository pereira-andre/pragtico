from __future__ import annotations

import re


ACTION_SPECS = {
    # --- Escala (port_call) — Agente/Admin gerem ---
    "create_port_call": {
        "label": "Registar escala",
        "roles": {"admin", "agente"},
        "requires_target": False,
    },
    "edit_port_call": {
        "label": "Editar escala",
        "roles": {"admin", "agente"},
        "requires_target": True,
    },
    "delete_port_call": {
        "label": "Apagar escala",
        "roles": {"admin", "agente"},
        "requires_target": True,
    },
    # --- Entrada — Piloto/Admin operam ---
    "approve_entry": {
        "label": "Aprovar entrada",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    "abort_entry": {
        "label": "Abortar entrada",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    "complete_entry": {
        "label": "Confirmar entrada",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    "entry_report": {
        "label": "Registar manobra de entrada",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    "edit_maneuver_report": {
        "label": "Editar registo de manobra",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    "delete_maneuver_report": {
        "label": "Apagar registo de manobra",
        "roles": {"admin"},
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
        "label": "Abortar saída",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    "complete_departure": {
        "label": "Confirmar saída",
        "roles": {"admin", "piloto"},
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
        "label": "Abortar mudança",
        "roles": {"admin", "piloto"},
        "requires_target": True,
    },
    "complete_shift": {
        "label": "Confirmar mudança",
        "roles": {"admin", "piloto"},
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
    "delete_maneuver": {
        "label": "Cancelar manobra",
        "roles": {"admin", "agente"},
        "requires_target": True,
    },
}

GENERIC_ACTION_FAMILIES = (
    ("approve", "approve"),
    ("abort", "abort"),
    ("cancel", "cancel"),
    ("complete", "approve"),
    ("confirm", "approve"),
    ("report", "report"),
    ("edit_plan", "edit_plan"),
    ("edit_report", "edit_report"),
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
OPERATIONAL_QUERY_HINT_RE = re.compile(
    r"\b("
    r"qual|quais|como|quando|onde|porque|"
    r"a que horas|que horas|"
    r"devo|devemos|posso|podemos|"
    r"explica|consulta|sabendo que"
    r")\b"
)
OPERATIONAL_STRONG_ACTION_HINT_RE = re.compile(
    r"\b("
    r"aprova|aprove|aprovar|"
    r"aborta|abortar|cancela|cancelar|anula|anular|"
    r"regista|registar|registra|"
    r"cria|criar|"
    r"apaga|apagar|remove|remover|"
    r"altera|alterar|edita|editar|"
    r"atualiza|actualiza|"
    r"fecha|fechar|"
    r"confirma|confirmar"
    r")\b"
)
OPERATIONAL_WEAK_ACTION_HINT_RE = re.compile(
    r"\b("
    r"marca|marcar|agenda|agendar|"
    r"planeia|planear|"
    r"muda|mudar|mete|poe|põe|ajusta|"
    r"prevista|previsto|planeada|planeado|pendente"
    r")\b"
)
OPERATIONAL_TIMING_QUERY_HINT_RE = re.compile(
    r"\b("
    r"a que horas|que horas|quando|"
    r"qual a antecedencia|qual a antecedencia de marcacao|"
    r"pode atracar|podemos atracar|"
    r"podemos marcar|marcar piloto|embarcar piloto|"
    r"deve embarcar|deve marcar|"
    r"reponto|preia mar|janela"
    r")\b"
)

MANEUVER_TYPES = {"entry", "departure", "shift"}
MANEUVER_ID_SENSITIVE_ACTIONS = {
    "edit_maneuver_plan",
    "delete_maneuver",
    "entry_report",
    "departure_report",
    "shift_report",
    "edit_maneuver_report",
    "delete_maneuver_report",
    "abort_entry",
    "abort_departure",
    "abort_shift",
}
JSON_BLOCK_RE = re.compile(r"\{.*\}", flags=re.DOTALL)
TIME_OR_STATUS_HINT_RE = re.compile(
    r"\b(\d{1,2}(?::|\s)\d{2}|hoje|amanha|amanhã|mesmo dia|previst\w*|pendente|aprovad\w*|abortad\w*)\b"
)
OPERATIONAL_OBJECT_HINTS = ("navio", "escala", "manobra", "entrada", "saida", "saída", "mudanca", "mudança")
TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}$")
PENDING_UPDATE_FIELD_ALIASES = {
    "maneuver_id": [
        "id",
        "id da manobra",
        "id manobra",
        "manobra id",
        "maneuver id",
        "maneuver_id",
    ],
    "reference_code": [
        "ref",
        "referencia",
        "referência",
        "numero de escala",
        "número de escala",
        "codigo de escala",
        "código de escala",
        "reference_code",
    ],
    "vessel_name": ["nome do navio", "nome navio", "navio", "vessel_name"],
    "maneuver_type": [
        "tipo de manobra",
        "tipo manobra",
        "manobra",
    ],
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
    ],
    "notes": ["notes", "nota", "observacoes", "observações", "obs"],
    "constraints": ["restricoes", "restrições", "constraints"],
    "berth": ["cais previsto", "cais", "planned_quay", "planned_berth", "quay", "berth", "pier"],
    "destination_berth": ["cais destino", "destination_quay", "destination_berth", "destination_pier"],
    "origin_berth": ["cais origem", "origin_quay", "origin_berth", "origin_pier"],
    "origin": ["origem"],
    "destination": ["destino"],
    "next_port": ["proximo porto", "próximo porto", "próximo destino", "proximo destino", "next_port", "port_of_destination"],
    "last_port": ["ultimo porto", "último porto", "last_port", "port_of_origin"],
    "eta_local": ["eta de chegada", "eta chegada", "eta", "eta_local"],
    "planned_departure_at_local": ["etd", "planned_departure_at_local"],
    "maneuver_started_local": [
        "inicio da manobra",
        "início da manobra",
        "inicio",
        "início",
        "hora de inicio",
        "hora de início",
        "start_time",
        "maneuver_started_local",
        "maneuver_started_at",
    ],
    "maneuver_finished_local": [
        "fim da manobra",
        "fim",
        "hora de fim",
        "end_time",
        "maneuver_finished_local",
        "maneuver_finished_at",
    ],
    "draft_m": [
        "calado operacional",
        "calado (operacional)",
        "draft_operational",
        "draft_operacional",
        "calado_operacional",
        "draft_m",
        "draught",
        "draft",
        "calado",
    ],
    "tug_count": ["rebocadores", "reboques", "tug_count", "tugs", "numero de rebocadores"],
    # Vessel data fields (for create_port_call)
    "vessel_name": ["nome do navio", "nome navio", "nome", "vessel_name"],
    "vessel_imo": ["imo", "vessel_imo"],
    "vessel_call_sign": ["indicativo", "call_sign", "vessel_call_sign", "callsign", "indicative"],
    "vessel_flag": ["bandeira", "flag", "vessel_flag"],
    "vessel_type": ["tipo de navio", "tipo navio", "vessel_type", "tipo"],
    "vessel_loa_m": ["loa", "loa (m)", "vessel_loa_m", "comprimento"],
    "vessel_beam_m": ["boca", "boca (m)", "beam", "vessel_beam_m", "largura"],
    "vessel_gt_t": ["gt", "gt (t)", "vessel_gt_t", "arqueacao bruta", "arqueação bruta"],
    "vessel_dwt_t": ["dwt", "dwt (t)", "vessel_dwt_t", "deadweight"],
    "vessel_bow_thruster": ["bow thruster", "thruster de proa", "propulsor de proa", "vessel_bow_thruster"],
    "vessel_stern_thruster": ["stern thruster", "thruster de popa", "propulsor de popa", "vessel_stern_thruster"],
    "vessel_max_draft_m": ["calado maximo", "calado máximo", "calado maximo (m)", "calado máximo (m)", "calado (m)", "max_draft", "vessel_max_draft_m"],
}
SLASH_COMMAND_ALIASES = {
    "help": "help",
    "avisos-locais": "local_warnings",
    "ondulacao": "wave",
    "ondulação": "wave",
    "leitura-costeira": "wave",
    "validar-manobra": "validate_maneuver",
    "verificar-manobra": "validate_maneuver",
    "verificar": "validate_maneuver",
    "validar": "validate_maneuver",
    "checklist-manobra": "validate_maneuver",
    "planeamento": "planning",
    "manobras-planeadas": "planning_approved",
    "manobras-previstas": "planning_pending",
    "consultar-escala": "consult_scale",
    "consultar-manobra": "consult_maneuver",
    "consultar-escala-custo": "consult_scale_cost",
    "consultar-manobra-custo": "consult_maneuver_cost",
    "consultar-navio": "consult_vessel",
    "reportar_evento": "event_report",
    "reportar-evento": "event_report",
    "registar-escala": "register_scale",
    "nova-escala": "register_scale",
    "editar-escala": "edit_scale",
    "apagar-escala": "delete_scale",
    "criar-manobra": "create_maneuver",
    "editar-manobra": "edit_maneuver",
    "cancelar-manobra": "delete_maneuver",
    "apagar-manobra": "delete_maneuver",
    "aprovar": "approve_maneuver",
    "registar-manobra": "create_report",
    "editar-registo-manobra": "edit_report",
    "apagar-registo-manobra": "delete_report",
    "abortar": "abort_maneuver",
    "abortar-manobra": "abort_maneuver",
    "mares": "tides",
    "meteorologia": "weather",
    "regra": "rule",
    "regras": "rule",
}
SLASH_COMMAND_FIELD_ALIASES = {
    "maneuver_id": [
        "id",
        "id da manobra",
        "id manobra",
        "manobra id",
        "maneuver id",
        "maneuver_id",
    ],
    "reference_code": [
        "ref",
        "id da escala",
        "id escala",
        "scale id",
        "port_call_id",
        "referencia",
        "referência",
        "numero de escala",
        "número de escala",
        "codigo de escala",
        "código de escala",
        "reference_code",
    ],
    "maneuver_type": [
        "tipo de manobra",
        "tipo manobra",
    ],
}
FIELD_ALIASES = {
    "name": "vessel_name",
    "nome": "vessel_name",
    "nome_do_navio": "vessel_name",
    "vessel": "vessel_name",
    "ship": "vessel_name",
    "ship_name": "vessel_name",
    "nome_navio": "vessel_name",
    "imo": "vessel_imo",
    "call_sign": "vessel_call_sign",
    "callsign": "vessel_call_sign",
    "indicativo": "vessel_call_sign",
    "indicative": "vessel_call_sign",
    "flag": "vessel_flag",
    "bandeira": "vessel_flag",
    "tipo_de_navio": "vessel_type",
    "loa": "vessel_loa_m",
    "loa_m": "vessel_loa_m",
    "vessel_loa": "vessel_loa_m",
    "length_overall": "vessel_loa_m",
    "length": "vessel_loa_m",
    "breadth": "vessel_beam_m",
    "beam": "vessel_beam_m",
    "vessel_beam": "vessel_beam_m",
    "boca": "vessel_beam_m",
    "boca_m": "vessel_beam_m",
    "gt": "vessel_gt_t",
    "gt_t": "vessel_gt_t",
    "vessel_gt": "vessel_gt_t",
    "gross_tonnage": "vessel_gt_t",
    "dwt": "vessel_dwt_t",
    "dwt_t": "vessel_dwt_t",
    "vessel_dwt": "vessel_dwt_t",
    "deadweight": "vessel_dwt_t",
    "bow_thruster": "vessel_bow_thruster",
    "thruster_de_proa": "vessel_bow_thruster",
    "propulsor_de_proa": "vessel_bow_thruster",
    "stern_thruster": "vessel_stern_thruster",
    "thruster_de_popa": "vessel_stern_thruster",
    "propulsor_de_popa": "vessel_stern_thruster",
    "draft": "draft_m",
    "draught": "draft_m",
    "vessel_draft": "draft_m",
    "max_draft": "vessel_max_draft_m",
    "eta": "eta_local",
    "port": "berth",
    "ship_type": "vessel_type",
    "operational_draft": "draft_m",
    "arrival_eta": "eta_local",
    "arrival_time": "eta_local",
    "estimated_arrival": "eta_local",
    "estimated_time_of_arrival": "eta_local",
    "eta_setubal": "eta_local",
    "eta_de_chegada": "eta_local",
    "berth": "berth",
    "pier": "berth",
    "quay": "berth",
    "cais_previsto": "berth",
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
    "ultimo_porto": "last_port",
    "port_of_origin": "last_port",
    "previous_port": "last_port",
    "next_port_of_call": "next_port",
    "proximo_porto": "next_port",
    "proximo_destino": "next_port",
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
    "docking_depth": "draft_m",
    "calado_operacional": "draft_m",
    "draft_operational": "draft_m",
    "draft_operacional": "draft_m",
    "expected_berth": "berth",
    "calado_maximo": "vessel_max_draft_m",
    "calado_maximo_m": "vessel_max_draft_m",
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
    "edit_maneuver_report": {"change_reason"},
    "schedule_departure": {"planned_departure_at_local", "next_port"},
    "schedule_shift": {"planned_shift_at_local", "destination_berth"},
    "edit_maneuver_plan": {"change_reason"},
    "edit_port_call": {"change_reason"},
}
DISPLAY_FIELD_LABELS = {
    "maneuver_id": "ID da manobra",
    "maneuver_type": "tipo de manobra",
    "target_port_call": "ref ou nome do navio",
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
    "vessel_bow_thruster": "bow thruster",
    "vessel_stern_thruster": "stern thruster",
    "eta_local": "ETA",
    "berth": "cais previsto",
    "last_port": "último porto",
    "next_port": "próximo porto",
    "planned_at_local": "hora prevista",
    "planned_departure_at_local": "hora prevista de saída",
    "planned_shift_at_local": "hora prevista da mudança",
    "destination_berth": "cais destino",
    "origin": "origem",
    "destination": "destino",
    "origin_berth": "cais origem",
    "constraints": "restrições",
    "aborted_reason": "motivo",
    "maneuver_started_local": "início da manobra",
    "maneuver_finished_local": "fim da manobra",
    "draft_m": "calado",
    "tug_count": "rebocadores",
    "notes": "observações",
    "change_reason": "motivo da alteração",
    "update_field": "campo a alterar",
}


PORT_CALL_FIELD_HINTS = {
    "vessel_name",
    "vessel_imo",
    "vessel_call_sign",
    "vessel_flag",
    "vessel_type",
    "vessel_loa_m",
    "vessel_beam_m",
    "vessel_gt_t",
    "vessel_dwt_t",
    "vessel_bow_thruster",
    "vessel_stern_thruster",
    "vessel_max_draft_m",
    "eta_local",
    "berth",
    "last_port",
    "next_port",
    "draft_m",
    "tug_count",
    "notes",
}

MANEUVER_REPORT_FIELD_HINTS = {
    "maneuver_started_local",
    "maneuver_finished_local",
    "draft_m",
    "notes",
}

ABORT_FIELD_HINTS = {
    "aborted_reason",
}
