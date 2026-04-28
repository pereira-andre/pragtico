"""Storage constants, metadata tuples, and lookup tables."""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

PASSWORD_HASH_METHOD = "scrypt"

DEFAULT_CONVERSATION_TITLE = "Nova conversa"
FEEDBACK_APPROVED = "approved"
FEEDBACK_REVIEW = "review"
FEEDBACK_IGNORED = "ignored"
ALLOWED_FEEDBACK_STATUSES = {FEEDBACK_APPROVED, FEEDBACK_REVIEW, FEEDBACK_IGNORED}
PORT_CALL_STATUS_SCHEDULED = "scheduled"
PORT_CALL_STATUS_IN_PORT = "in_port"
PORT_CALL_STATUS_DEPARTED = "departed"
ALLOWED_PORT_CALL_STATUSES = {
    PORT_CALL_STATUS_SCHEDULED,
    PORT_CALL_STATUS_IN_PORT,
    PORT_CALL_STATUS_DEPARTED,
}
PORT_CALL_APPROVAL_PENDING = "pending"
PORT_CALL_APPROVAL_APPROVED = "approved"
PORT_CALL_APPROVAL_ABORTED = "aborted"
ALLOWED_PORT_CALL_APPROVAL_STATUSES = {
    PORT_CALL_APPROVAL_PENDING,
    PORT_CALL_APPROVAL_APPROVED,
    PORT_CALL_APPROVAL_ABORTED,
}
PT_MONTH_NAMES = (
    "",
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)
USER_PROFILE_REQUIRED_ROLES = {"agente", "piloto"}
USER_PROFILE_REQUIRED_FIELDS = ("full_name", "organization", "email", "phone")
WHATSAPP_STATUS_UNKNOWN = "unknown"
WHATSAPP_STATUS_NOT_OPTED_IN = "not_opted_in"
WHATSAPP_STATUS_DISABLED = "disabled"
WHATSAPP_STATUS_LOCAL_DENIED = "local_denied"
WHATSAPP_STATUS_PENDING = "pending"
WHATSAPP_STATUS_SENT = "sent"
WHATSAPP_STATUS_RECIPIENT_NOT_ALLOWED = "recipient_not_allowed"
WHATSAPP_STATUS_TOKEN_INVALID = "token_invalid"
WHATSAPP_STATUS_TEMPLATE_MISSING = "template_missing"
WHATSAPP_STATUS_INVALID_NUMBER = "invalid_number"
WHATSAPP_STATUS_NETWORK_ERROR = "network_error"
WHATSAPP_STATUS_API_ERROR = "api_error"
WHATSAPP_STATUS_INTERNAL_ERROR = "internal_error"
VESSEL_TYPE_META = (
    {
        "label": "Atividades ao largo",
        "icon": "atividades-ao-largo.png",
        "aliases": ("atividades ao largo", "offshore", "offshore apoio", "apoio offshore"),
    },
    {
        "label": "Batelão s/ propulsão",
        "icon": "batelao-s-propulsao.png",
        "aliases": ("batelao", "batelao s propulsao", "batelao sem propulsao"),
    },
    {
        "label": "Carga geral",
        "icon": "carga-geral.png",
        "aliases": ("carga geral", "general cargo"),
    },
    {
        "label": "Contentores",
        "icon": "contentores.png",
        "aliases": ("contentores", "container", "containers"),
    },
    {
        "label": "Cruzeiros",
        "icon": "cruzeiros.png",
        "aliases": ("cruzeiros", "cruzeiro", "cruise"),
    },
    {
        "label": "Diversos",
        "icon": "diversos.png",
        "aliases": ("diversos", "misc", "multiusos"),
    },
    {
        "label": "Estruturas diversas",
        "icon": "estruturas-diversas.png",
        "aliases": ("estruturas diversas", "estrutura"),
    },
    {
        "label": "Frigorífico",
        "icon": "frigorifico.png",
        "aliases": ("frigorifico", "reefer"),
    },
    {
        "label": "Graneis líquidos",
        "icon": "graneis-liquidos.png",
        "aliases": ("graneis liquidos", "petroleiro", "tanque", "tanker", "gas"),
    },
    {
        "label": "Graneis sólidos",
        "icon": "graneis-solidos.png",
        "aliases": ("graneis solidos", "graneleiro", "bulk carrier", "bulk"),
    },
    {
        "label": "Navios de guerra",
        "icon": "navios-de-guerra.png",
        "aliases": ("navios de guerra", "guerra", "militar", "naval"),
    },
    {
        "label": "Passageiros",
        "icon": "passageiros.png",
        "aliases": ("passageiros", "passenger"),
    },
    {
        "label": "Pesca",
        "icon": "pesca.png",
        "aliases": ("pesca", "fishing"),
    },
    {
        "label": "Porta-contentores",
        "icon": "porta-contentores.png",
        "aliases": ("porta contentores", "porta-contentores"),
    },
    {
        "label": "Propulsão",
        "icon": "propulsao.png",
        "aliases": ("propulsao", "propulsão"),
    },
    {
        "label": "Rebocadores",
        "icon": "rebocadores.png",
        "aliases": ("rebocadores", "rebocador", "tug", "tugs"),
    },
    {
        "label": "Restantes",
        "icon": "restantes.png",
        "aliases": ("restantes", "restante", "navio", "outros", "outro"),
    },
    {
        "label": "Roll-on/Roll-off",
        "icon": "roll-on-roll-off.png",
        "aliases": ("roll on roll off", "ro ro", "ro-ro", "ro ro pcc", "ro-ro / pcc", "pcc"),
    },
    {
        "label": "Transporte especializado carga seca",
        "icon": "transporte-especializado-carga-seca.png",
        "aliases": ("transporte especializado carga seca", "carga seca especializada"),
    },
)
CONSTRAINT_META = (
    {
        "code": "daylight",
        "label": "Day-light",
        "icon": "constraint-daylight-21-alt.svg",
        "aliases": ("daylight", "day light", "day-light", "dia"),
    },
    {
        "code": "gas",
        "label": "Gás / carga perigosa",
        "icon": "constraint-gas-21-alt.svg",
        "aliases": ("gas", "gás", "carga perigosa", "perigosa", "dangerous cargo"),
    },
    {
        "code": "estrategico",
        "label": "Estratégico",
        "icon": "constraint-estrategico-21-alt.svg",
        "aliases": ("estrategico", "estratégico", "strategic"),
    },
)


def _lookup_key(value: Optional[str]) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


VESSEL_TYPE_LOOKUP = {
    _lookup_key(alias): item
    for item in VESSEL_TYPE_META
    for alias in (item["label"], *item.get("aliases", ()))
}
CONSTRAINT_LOOKUP = {
    _lookup_key(alias): item
    for item in CONSTRAINT_META
    for alias in (item["code"], item["label"], *item.get("aliases", ()))
}


def get_vessel_type_options() -> List[Dict]:
    return [{"label": item["label"], "icon": item["icon"]} for item in VESSEL_TYPE_META]


def get_constraint_options() -> List[Dict]:
    return [{"code": item["code"], "label": item["label"], "icon": item["icon"]} for item in CONSTRAINT_META]
