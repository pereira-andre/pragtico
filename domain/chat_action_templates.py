from __future__ import annotations

from typing import List, Optional

from domain.chat_action_config import ACTION_SPECS, DISPLAY_FIELD_LABELS


def display_missing_field_labels(fields: List[str]) -> List[str]:
    return [DISPLAY_FIELD_LABELS.get(field, field) for field in fields]


def build_scale_edit_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para editar a escala. Usa a Ref e preenche só os campos a alterar:",
            "Ref: ",
            "Nome do navio: ",
            "ETA de chegada: ",
            "Cais previsto: ",
            "Último porto: ",
            "Próximo destino: ",
            "IMO: ",
            "Indicativo: ",
            "Bandeira: ",
            "Tipo de navio: ",
            "LOA (m): ",
            "Boca (m): ",
            "GT (t): ",
            "DWT (t): ",
            "Calado máximo (m): ",
            "Bow thruster: ",
            "Stern thruster: ",
            "Observações: ",
            "Motivo da alteração: ",
            "Formato de datas: DD/MM/AAAA, HH:MM.",
            "Opções thruster: sim, não, desconhecido.",
        ]
    )


def build_delete_scale_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para apagar a escala (basta a Ref):",
            "Ref: ",
        ]
    )


def build_create_maneuver_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para criar a manobra (o ID é gerado automaticamente):",
            "Ref: ",
            "Tipo de manobra: ",
            "Hora prevista: ",
            "Destino: ",
            "Calado: ",
            "Rebocadores: ",
            "Restrições: ",
            "Observações: ",
            "Nota: a origem segue automaticamente o último local conhecido do navio.",
            "Formato de datas: DD/MM/AAAA, HH:MM.",
            "Tipos aceites: saída, mudança.",
            "Restrições aceites: daylight, gas, estrategico.",
        ]
    )


def build_edit_maneuver_plan_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para editar o planeamento. Com ID da manobra não precisas de repetir Ref/Tipo; preenche só os campos a alterar:",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: ",
            "Hora prevista: ",
            "Origem: ",
            "Destino: ",
            "Calado: ",
            "Rebocadores: ",
            "Restrições: ",
            "Observações: ",
            "Motivo da alteração: ",
            "Formato de datas: DD/MM/AAAA, HH:MM.",
            "Tipos aceites: entrada, saída, mudança.",
            "Restrições aceites: daylight, gas, estrategico.",
        ]
    )


def build_delete_maneuver_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para cancelar a manobra pendente (basta o ID da manobra):",
            "ID da manobra: ",
        ]
    )


def build_approval_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para aprovar a manobra (se usares o ID da manobra, basta o ID; sem ID usa Ref + Tipo):",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: ",
            "Observações: ",
            "Tipos aceites: entrada, saída, mudança.",
        ]
    )


def build_command_report_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para registar a manobra (se usares o ID da manobra, basta o ID; sem ID usa Ref + Tipo):",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: ",
            "Início da manobra: ",
            "Fim da manobra: ",
            "Calado: ",
            "Observações: ",
            "Formato de datas: DD/MM/AAAA, HH:MM.",
            "Tipos aceites: entrada, saída, mudança.",
        ]
    )


def build_edit_report_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para editar o registo da manobra. Com ID da manobra não precisas de repetir Ref/Tipo; preenche só os campos a alterar:",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: ",
            "Início da manobra: ",
            "Fim da manobra: ",
            "Calado: ",
            "Observações: ",
            "Motivo da alteração: ",
            "Formato de datas: DD/MM/AAAA, HH:MM.",
            "Tipos aceites: entrada, saída, mudança.",
        ]
    )


def build_command_abort_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para abortar a manobra (se usares o ID da manobra, basta o ID; sem ID usa Ref + Tipo):",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: ",
            "Motivo: ",
            "Tipos aceites: entrada, saída, mudança.",
        ]
    )


def build_delete_report_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para apagar o registo da manobra (se usares o ID da manobra, basta o ID; sem ID usa Ref + Tipo):",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: ",
            "Tipos aceites: entrada, saída, mudança.",
        ]
    )


def build_validate_maneuver_reply_template() -> str:
    return "\n".join(
        [
            "Responde neste formato para validar a manobra (se usares o ID da manobra, basta o ID; sem ID usa Ref + Tipo):",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: ",
            "Tipos aceites: entrada, saída, mudança.",
        ]
    )


def build_action_reply_template(action: str, missing_fields: Optional[List[str]] = None) -> str:
    if action == "create_port_call":
        return build_port_call_reply_template()
    if action == "edit_port_call":
        return build_scale_edit_reply_template()
    if action == "delete_port_call":
        return build_delete_scale_reply_template()
    if action in {"schedule_departure", "schedule_shift"}:
        return build_create_maneuver_reply_template()
    if action == "edit_maneuver_plan":
        return build_edit_maneuver_plan_reply_template()
    if action in {"approve_entry", "approve_departure", "approve_shift"}:
        return build_approval_reply_template()
    if action in {"entry_report", "departure_report", "shift_report"}:
        return build_command_report_reply_template()
    if action == "edit_maneuver_report":
        return build_edit_report_reply_template()
    if action in {"delete_maneuver_report"}:
        return build_delete_report_reply_template()
    if action in {"abort_entry", "abort_departure", "abort_shift"}:
        return build_command_abort_reply_template()
    if action in {"delete_maneuver"}:
        return build_delete_maneuver_reply_template()
    return ""


def build_slash_help(role: str) -> str:
    clean_role = (role or "").strip().lower()
    lines = [
        "📋 Comandos disponíveis:",
        "/help",
        "  mostra esta ajuda",
        "",
        "Consulta rápida:",
        "/avisos-locais [código]",
        "  lista os avisos locais em vigor ou consulta um aviso específico pelo código",
        "/ondulacao",
        "  mostra a leitura costeira atual; aliases: /ondulação, /leitura-costeira",
        "/mares [hoje|amanhã|DD/MM/AAAA]",
        "  mostra marés por dia ou data pedida",
        "/meteorologia [hoje|amanhã|próximos dias]",
        "  mostra a previsão meteorológica",
        "/regras",
        "  lista os códigos de regras/instruções disponíveis",
        "/regra 015",
        "  consulta uma regra/instrução por código",
        "/consultar-navio IMO ou nome",
        "  mostra a ficha do navio conhecida no portal",
        "/reportar-evento TAG. LOCAL. DESCRIPTION",
        "  regista uma ocorrência operacional e pergunta por foto opcional",
        "  alias: /reportar_evento",
        "",
        "Planeamento:",
        "/planeamento",
        "  lista todas as manobras no planeamento com dia, hora, tipo, navio, rota, estado e agência",
        "/manobras-planeadas",
        "  lista só as manobras já aprovadas",
        "/manobras-previstas",
        "  lista só as manobras ainda pendentes",
        "",
        "Escalas:",
        "/consultar-escala REF",
        "  mostra os dados básicos da escala",
        "/consultar-escala-custo REF",
        "  mostra a escala com estimativa de custos",
    ]
    if clean_role in {"admin", "agente"}:
        lines.extend(
            [
                "/registar-escala",
                "  cria uma nova escala; a entrada inicial fica associada à escala",
                "  alias: /nova-escala",
            ]
        )
    if clean_role in {"admin", "agente"}:
        lines.extend(
            [
                "/editar-escala",
                "  atualiza os dados da escala; usa a Ref da escala",
                "/apagar-escala",
                "  remove a escala; basta a Ref da escala",
            ]
        )
    lines.extend(["", "Manobras:"])
    lines.extend(
        [
            "/validar-manobra",
            "  valida uma manobra específica com checklist e histórico; usa ID da manobra ou Ref + Tipo",
            "/verificar-manobra",
            "  alias de /validar-manobra; também: /verificar, /validar, /checklist-manobra",
            "/consultar-manobra ID ou REF + Tipo",
            "  mostra os dados básicos da manobra",
            "/consultar-manobra-custo ID ou REF + Tipo",
            "  mostra a manobra com estimativa de custo",
        ]
    )
    if clean_role in {"admin", "agente"}:
        lines.extend(
            [
                "/criar-manobra",
                "  cria uma saída ou mudança; o ID da manobra é automático e a origem segue o último local conhecido",
                "/apagar-manobra",
                "  remove a manobra planeada; usa o ID da manobra (ou Ref + Tipo se não tiveres o ID)",
                "  alias: /cancelar-manobra",
            ]
        )
    if clean_role in {"admin", "agente"}:
        lines.extend(
            [
                "/editar-manobra",
                "  altera o planeamento; usa ID da manobra ou Ref + Tipo; se houver mais do que uma, o ID é obrigatório",
            ]
        )
    if clean_role in {"admin", "piloto"}:
        lines.extend(
            [
                "/aprovar",
                "  aprova a manobra pendente; usa ID da manobra ou Ref + Tipo",
                "/registar-manobra",
                "  regista início, fim e calado; usa ID da manobra ou Ref + Tipo; se houver mais do que uma, o ID é obrigatório",
                "/editar-registo-manobra",
                "  revê um registo executado; usa ID da manobra ou Ref + Tipo; se houver mais do que uma, o ID é obrigatório",
                "/abortar",
                "  cancela/aborta a manobra; alias: /abortar-manobra; usa ID da manobra ou Ref + Tipo; se houver mais do que uma, o ID é obrigatório",
            ]
        )
    if clean_role == "admin":
        lines.extend(
            [
                "/apagar-registo-manobra",
                "  apaga o registo executado; usa ID da manobra ou Ref + Tipo; se houver mais do que uma, o ID é obrigatório",
            ]
        )
    lines.extend(
        [
            "",
            "Emergência WhatsApp:",
            "SOS",
            "  inicia pedido de ajuda via WhatsApp com partilha de localização",
            "CANCELAR SOS",
            "  cancela pedido SOS pendente ou já enviado",
            "",
            "Notas:",
            "  Sem `/` o chat responde em modo Q&A técnico e não altera o portal.",
            "  Ref identifica a escala. Se só tiveres o ID curto da escala, o bot também tenta resolvê-lo.",
            "  Ao criar manobra não precisas de indicar ID; para manobra existente podes usar ID da manobra ou Ref + Tipo.",
            "  Usa `/validar-manobra` quando quiseres a checklist determinística e a leitura histórica de uma manobra específica.",
            "  Se houver mais do que uma manobra elegível do mesmo tipo, o bot exige o ID da manobra.",
            "  Se o comando vier incompleto, o bot devolve o template certo para preencher.",
        ]
    )
    return "\n".join(lines)


def build_port_call_reply_template(missing_fields: Optional[List[str]] = None) -> str:
    lines = [
        "Se preferires, responde já neste formato e eu trato do registo:",
        "Nome: ",
        "ETA de chegada: ",
        "Cais previsto: ",
        "Último porto: ",
        "Próximo destino: ",
        "IMO: ",
        "Indicativo: ",
        "Bandeira: ",
        "Tipo de navio: ",
        "LOA (m): ",
        "Boca (m): ",
        "GT (t): ",
        "DWT (t): ",
        "Calado (m): ",
        "Bow thruster: ",
        "Stern thruster: ",
        "Calado (operacional): ",
        "Rebocadores: ",
        "Observações: ",
        "Formato de datas: DD/MM/AAAA, HH:MM.",
        "Opções thruster: sim, não, desconhecido.",
    ]
    return "\n".join(lines)


def build_maneuver_report_reply_template(missing_fields: Optional[List[str]] = None) -> str:
    return build_command_report_reply_template()


def build_abort_reply_template(missing_fields: Optional[List[str]] = None) -> str:
    return build_command_abort_reply_template()
