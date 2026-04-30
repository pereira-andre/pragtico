"""Operational rule catalog helpers."""

import os
import re

from core import services


RULE_CODE_TITLES = {
    "005": "IT-005 Multiusos Z1",
    "006": "IT-006 Multiusos Z2",
    "007": "IT-007 Autoeuropa",
    "008": "IT-008 Ecooil",
    "009": "IT-009 Secil",
    "010": "IT-010 Tanquisado",
    "011": "IT-011 Termitrena",
    "012": "IT-012 Praias do Sado",
    "013": "IT-013 Uralada",
    "014": "IT-014 Lisnave",
    "015": "IT-015 Fundeadouros",
    "016": "IT-016 Rebocadores",
    "017": "IT-017 Pilotagem Assistida",
    "018": "IT-018 Normas Especiais",
    "029": "IT-029 Cais da SAPEC",
    "036": "IT-036 Regulação de Agulhas",
    "038": "IT-038 Cais Alstom",
    "041": "IT-041 Entrada e Saída de Navios",
    "042": "IT-042 Recomendações Navios Canal Norte",
    "062": "IT-062 Cais da Teporset",
}


def _active_knowledge_dir() -> str:
    return (
        getattr(getattr(services, "store", None), "knowledge_dir", "")
        or getattr(services, "KNOWLEDGE_DIR", "")
        or ""
    )


def available_rule_code_titles() -> dict[str, str]:
    """Return the rule-code map limited to documents actually present in the knowledge base."""
    knowledge_dir = getattr(services, "KNOWLEDGE_DIR", "") or ""
    if not knowledge_dir or not os.path.isdir(knowledge_dir):
        return dict(RULE_CODE_TITLES)

    available_codes = set()
    try:
        for entry in os.listdir(knowledge_dir):
            match = re.match(r"IT-(\d{3})_", entry)
            if match:
                available_codes.add(match.group(1))
    except OSError:
        return dict(RULE_CODE_TITLES)

    filtered = {
        code: title
        for code, title in RULE_CODE_TITLES.items()
        if code in available_codes
    }
    return filtered or dict(RULE_CODE_TITLES)


def build_rule_catalog_text() -> str:
    """Return a user-facing catalog of the available operational rules by code."""
    lines = [
        "Regras/instruções disponíveis por código:",
    ]
    for code, title in sorted(available_rule_code_titles().items(), key=lambda item: item[0]):
        lines.append(f"- {code} — {title}")
    lines.extend(
        [
            "",
            "Usa `/regra 015` para resumir uma regra específica.",
        ]
    )
    return "\n".join(lines)
