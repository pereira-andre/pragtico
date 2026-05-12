"""Operational rule catalog helpers."""

import json
import os
import re
from pathlib import Path

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
    knowledge_dir = _active_knowledge_dir() or getattr(services, "KNOWLEDGE_DIR", "") or ""
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
            "Usa `/regra 015` ou `/it 015` para resumir uma regra específica.",
        ]
    )
    return "\n".join(lines)


def _knowledge_dir_candidates() -> list[Path]:
    candidates: list[Path] = []
    for raw_path in (
        _active_knowledge_dir(),
        getattr(services, "KNOWLEDGE_DIR", ""),
        "knowledge",
    ):
        path_text = str(raw_path or "").strip()
        if not path_text:
            continue
        path = Path(path_text)
        if path not in candidates:
            candidates.append(path)
    return candidates or [Path("knowledge")]


def _companion_path_for_code(code: str) -> Path | None:
    clean_code = str(code or "").strip().zfill(3)
    for knowledge_dir in _knowledge_dir_candidates():
        companion_dir = knowledge_dir / "companions"
        if not companion_dir.is_dir():
            continue
        matches = sorted(companion_dir.glob(f"IT-{clean_code}_*.json"))
        if matches:
            return matches[0]
    return None


def rule_document_for_code(code: str) -> str:
    """Return the source document filename for a rule code when available."""
    companion_path = _companion_path_for_code(code)
    if companion_path:
        try:
            payload = json.loads(companion_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        document = str(payload.get("document") or "").strip()
        if document:
            return document
    clean_code = str(code or "").strip().zfill(3)
    for knowledge_dir in _knowledge_dir_candidates():
        matches = sorted(knowledge_dir.glob(f"IT-{clean_code}_*.txt"))
        if matches:
            return matches[0].name
    return f"IT-{clean_code}"


def _load_rule_companion(code: str) -> dict:
    companion_path = _companion_path_for_code(code)
    if not companion_path:
        return {}
    try:
        payload = json.loads(companion_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _compact_sentence(value: object, *, max_chars: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    truncated = text[: max_chars - 1].rsplit(" ", 1)[0].strip()
    return f"{truncated}."


def _question_label(question: str) -> str:
    label = re.sub(r"\s+", " ", str(question or "")).strip().rstrip("?.:")
    lead_patterns = (
        r"^qual (?:é|e|o|a|os|as)\s+",
        r"^quais (?:são|sao|os|as)\s+",
        r"^quando\s+",
        r"^quem\s+",
        r"^onde\s+",
        r"^como\s+",
    )
    for pattern in lead_patterns:
        label = re.sub(pattern, "", label, flags=re.IGNORECASE)
    return label[:1].upper() + label[1:] if label else "Regra"


def _fallback_rule_points(companion: dict, *, max_points: int = 12) -> list[str]:
    faq = companion.get("faq") if isinstance(companion.get("faq"), list) else []
    points: list[str] = []
    validation_points: list[str] = []
    skipped_case_points: list[str] = []
    skipped_overview_points: list[str] = []
    for item in faq:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = _compact_sentence(item.get("answer"), max_chars=620)
        if not question or not answer:
            continue
        normalized_question = re.sub(r"\s+", " ", question.lower()).strip()
        if normalized_question.startswith("exemplo"):
            continue
        point = f"{_question_label(question)}: {answer}"
        if normalized_question.startswith(("da me mais detalhes", "dá-me mais detalhes", "dá me mais detalhes", "o que me podes dizer", "fala me", "fala-me")):
            skipped_overview_points.append(point)
            continue
        if "quem valida" in normalized_question or "valida as requisi" in normalized_question:
            validation_points.append(point)
            continue
        if normalized_question.startswith(("um navio", "posso ", "tenho ")):
            skipped_case_points.append(point)
            continue
        points.append(point)
        if len(points) >= max_points:
            break
    if len(points) < max_points:
        for point in skipped_case_points:
            points.append(point)
            if len(points) >= max_points:
                break
    if len(points) < max_points:
        for point in skipped_overview_points:
            points.append(point)
            if len(points) >= max_points:
                break
    for point in validation_points:
        if len(points) >= max_points:
            break
        points.append(point)
    if points:
        return points

    key_points = companion.get("key_points") if isinstance(companion.get("key_points"), list) else []
    return [_compact_sentence(item, max_chars=320) for item in key_points if str(item or "").strip()][:max_points]


def build_rule_summary_text(code: str) -> str:
    """Build a deterministic slash-rule summary from local IT companions."""
    clean_code = str(code or "").strip().zfill(3)
    title = RULE_CODE_TITLES.get(clean_code) or available_rule_code_titles().get(clean_code) or f"IT-{clean_code}"
    companion = _load_rule_companion(clean_code)
    document = str(companion.get("document") or rule_document_for_code(clean_code)).strip()
    heading = str(companion.get("title") or title).strip()
    summary = _compact_sentence(companion.get("summary"), max_chars=520)
    rule_summary = companion.get("rule_summary") if isinstance(companion.get("rule_summary"), dict) else {}

    lines = [heading]
    if document:
        lines.append(f"Fonte: {document}")
    if rule_summary.get("scope"):
        lines.extend(["", "Âmbito:", f"- {_compact_sentence(rule_summary.get('scope'), max_chars=520)}"])
    elif summary:
        lines.extend(["", "Âmbito:", f"- {summary}"])

    critical_points = rule_summary.get("critical_points") if isinstance(rule_summary.get("critical_points"), list) else []
    points = [_compact_sentence(item, max_chars=520) for item in critical_points if str(item or "").strip()]
    if not points:
        points = _fallback_rule_points(companion)
    if points:
        lines.extend(["", "Regras críticas:"])
        lines.extend(f"- {point}" for point in points)

    validation = _compact_sentence(rule_summary.get("validation"), max_chars=360) if rule_summary.get("validation") else ""
    if validation:
        lines.extend(["", "Validação:", f"- {validation}"])

    notes = rule_summary.get("notes") if isinstance(rule_summary.get("notes"), list) else []
    clean_notes = [_compact_sentence(item, max_chars=420) for item in notes if str(item or "").strip()]
    if clean_notes:
        lines.extend(["", "Notas operacionais:"])
        lines.extend(f"- {note}" for note in clean_notes)

    if not companion:
        lines.extend(["", "Não encontrei companion estruturado para esta IT; valida o TXT original antes de fechar a decisão operacional."])
    return "\n".join(lines).strip()
