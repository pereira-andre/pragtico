"""Operational rule catalog helpers."""

import os
import re
import textwrap

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


def _fallback_knowledge_dir() -> str:
    cwd_candidate = os.path.join(os.getcwd(), "knowledge")
    if os.path.isdir(cwd_candidate):
        return cwd_candidate
    package_candidate = os.path.join(os.path.dirname(os.path.dirname(__file__)), "knowledge")
    return package_candidate if os.path.isdir(package_candidate) else ""


def available_rule_code_titles() -> dict[str, str]:
    """Return the rule-code map limited to documents actually present in the knowledge base."""
    knowledge_dir = _active_knowledge_dir() or _fallback_knowledge_dir()
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


def _rule_document_path(code: str) -> str:
    knowledge_dir = _active_knowledge_dir() or _fallback_knowledge_dir()
    try:
        for entry in os.listdir(knowledge_dir):
            if re.match(rf"IT-{re.escape(code)}_", entry):
                return os.path.join(knowledge_dir, entry)
    except OSError:
        return ""
    return ""


def _clean_rule_lines(section: str, *, max_items: int = 18) -> list[str]:
    items: list[str] = []
    current = ""
    for raw_line in section.splitlines():
        line = " ".join(str(raw_line or "").strip().split())
        if not line or set(line) <= {"=", "-"}:
            continue
        if re.match(r"^(Pergunta|Resposta):", line, flags=re.IGNORECASE):
            if current:
                items.append(current)
            current = line
            continue
        if current:
            current = f"{current} {line}".strip()
        else:
            current = line
    if current:
        items.append(current)

    compact: list[str] = []
    pending_question = ""
    for item in items:
        if item.lower().startswith("pergunta:"):
            pending_question = re.sub(r"^Pergunta:\s*", "", item, flags=re.IGNORECASE).strip()
            continue
        if item.lower().startswith("resposta:"):
            answer = re.sub(r"^Resposta:\s*", "", item, flags=re.IGNORECASE).strip()
            if pending_question:
                compact.append(f"- {pending_question} {answer}")
                pending_question = ""
            else:
                compact.append(f"- {answer}")
            continue
        compact.append(f"- {item}")
        pending_question = ""
    return compact[:max_items]


def _extract_summary_items(text: str) -> list[str]:
    match = re.search(
        r"RESUMO DAS REGRAS EM LINGUAGEM OPERACIONAL DIRETA\s*=+\s*(.*?)(?:=+\s*FIM DO DOCUMENTO|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return _clean_rule_lines(match.group(1), max_items=18)
    return []


def _extract_fallback_items(text: str) -> list[str]:
    wanted = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line or set(line) <= {"=", "-"}:
            continue
        if re.search(
            r"\b(calado|comprimento|loa|reponto|preia|baixa-mar|mar[eé]|rebocador|"
            r"vento|visibilidade|proib|obrigat|f[oó]rmula|canal|fundeadouro|vts|pilotagem)\b",
            line,
            flags=re.IGNORECASE,
        ):
            wanted.append(f"- {line}")
        if len(wanted) >= 14:
            break
    return wanted


def _verification_items(summary_text: str) -> list[str]:
    clean = summary_text.lower()
    items: list[str] = []
    if any(token in clean for token in ("calado", "sonda", "profundidade")):
        items.append(
            "- Calado: confirmar calado real do navio, terminal/cais exato, altura de água ou preia/baixa-mar aplicável, fórmula documental e teto absoluto."
        )
    if any(token in clean for token in ("comprimento", "loa", "duque", "cabeço", "cabeco")):
        items.append(
            "- Comprimento/LOA: comparar LOA com o limite do cais e com eventuais duques d'alba, cabeços, rampa ou margem de separação."
        )
    if any(token in clean for token in ("reponto", "preia", "baixa-mar", "maré", "mare")):
        items.append(
            "- Maré/reponto: confirmar se a regra usa altura instantânea, preia-mar do dia ou baixa-mar de referência; não trocar estes conceitos entre terminais."
        )
    if any(token in clean for token in ("rebocador", "vento", "visibilidade", "canal", "vts", "pilotagem")):
        items.append(
            "- Segurança operacional: confirmar vento, visibilidade, rebocadores, estado de máquina/governo e comunicações VTS quando forem condicionantes."
        )
    items.append(
        "- Se faltarem dados críticos ou a situação estiver fora da regra documentada, pedir a informação em falta e escalar ao Piloto Coordenador."
    )
    return items


def build_rule_summary_text(code: str) -> str | None:
    """Return a deterministic operational summary for an IT code."""
    clean_code = re.sub(r"\D", "", str(code or ""))[-3:]
    title = available_rule_code_titles().get(clean_code) or RULE_CODE_TITLES.get(clean_code)
    if not title:
        return None
    path = _rule_document_path(clean_code)
    if not path:
        return None
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None

    header = next((line.strip() for line in text.splitlines() if line.strip().startswith("DOCUMENTO:")), title)
    revision = next((line.strip() for line in text.splitlines() if line.strip().startswith(("REVISÃO", "REVISAO"))), "")
    summary_items = _extract_summary_items(text) or _extract_fallback_items(text)
    summary_text = "\n".join(summary_items)
    verification = _verification_items(summary_text)

    lines = [header]
    if revision:
        lines.append(revision)
    lines.extend(["", "Resumo operacional:"])
    lines.extend(summary_items or ["- Documento disponível, mas sem resumo estruturado extraído automaticamente. Consultar a IT completa antes de validar."])
    lines.extend(["", "Como verificar antes de validar:"])
    lines.extend(verification)
    wrapped_lines = []
    for line in lines:
        if line.startswith("- "):
            wrapped_lines.append(textwrap.fill(line, width=110, subsequent_indent="  "))
        else:
            wrapped_lines.append(line)
    return "\n".join(wrapped_lines)


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
