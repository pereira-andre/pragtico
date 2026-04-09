"""Storage utility functions — parsing, normalization, text helpers, actor/profile handling."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from domain.document_processing import iso_now

from .constants import (
    CONSTRAINT_LOOKUP,
    PORT_CALL_APPROVAL_PENDING,
    PT_MONTH_NAMES,
    USER_PROFILE_REQUIRED_FIELDS,
    USER_PROFILE_REQUIRED_ROLES,
    VESSEL_TYPE_LOOKUP,
    _lookup_key,
)


LEGACY_SYSTEM_MARKDOWN_SEEDS = {
    "manual-de-manobra": "Checklist de aproximação:",
    "norma-de-seguranca": "Em caso de dúvida operacional, prevalece o princípio de segurança.",
    "meteorologia-e-mares": "Dados de marés e meteorologia devem ser revistos antes de cada manobra.",
}


def legacy_system_markdown_seed_prefix(name: Optional[str]) -> str:
    clean_name = (name or "").strip().lower()
    if not clean_name.endswith(".md"):
        return ""
    stem = clean_name[:-3]
    for slug, prefix in LEGACY_SYSTEM_MARKDOWN_SEEDS.items():
        if stem == slug or stem.startswith(f"{slug}-"):
            return prefix
    return ""


def is_legacy_system_markdown_document(
    *,
    name: Optional[str],
    uploaded_by: Optional[str] = None,
    preview: Optional[str] = None,
    text: Optional[str] = None,
) -> bool:
    expected_prefix = legacy_system_markdown_seed_prefix(name)
    if not expected_prefix:
        return False
    uploader = _clean_text(uploaded_by).lower()
    if uploader and uploader != "system":
        return False
    normalized_expected = _clean_text(expected_prefix).lower()
    for candidate in (preview, text):
        normalized_candidate = _clean_text(candidate).lower()
        if normalized_candidate.startswith(normalized_expected):
            return True
    return False


def _utc_iso_to_label(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _local_iso_to_label(value: Optional[str]) -> str:
    dt = _parse_iso_datetime(value)
    if not dt:
        return "Sem hora"
    return dt.astimezone().strftime("%d %b %H:%M")


def _iso_to_datetime_local_value(value: Optional[str]) -> str:
    dt = _parse_iso_datetime(value)
    if not dt:
        return ""
    return dt.astimezone().strftime("%Y-%m-%dT%H:%M")


def _local_date_label(value: Optional[str]) -> str:
    dt = _parse_iso_datetime(value)
    if not dt:
        return "Sem data"
    local_dt = dt.astimezone()
    return f"{local_dt.day} {PT_MONTH_NAMES[local_dt.month]} {local_dt.year}"


def _local_time_label(value: Optional[str]) -> str:
    dt = _parse_iso_datetime(value)
    if not dt:
        return "--"
    return dt.astimezone().strftime("%H:%M")


def _normalize_actor_label(value: Optional[str], fallback: str = "--") -> str:
    clean = " ".join((value or "").strip().split())
    if not clean:
        return fallback
    return clean


def _clean_text(value: Optional[str]) -> str:
    return " ".join((value or "").strip().split())


def _question_for_assistant_message(messages: List[Dict], message_id: str) -> str:
    target_index = next(
        (index for index, item in enumerate(messages) if str(item.get("id") or "") == str(message_id)),
        -1,
    )
    if target_index < 0:
        return ""
    for index in range(target_index - 1, -1, -1):
        if _clean_text(messages[index].get("role", "")).lower() == "user":
            return _clean_text(messages[index].get("content"))
    return ""


def _declarative_subject_from_question(question: Optional[str]) -> str:
    clean_question = _clean_text(question).rstrip("?.! ")
    if not clean_question:
        return ""
    subject_match = re.match(r"^qual\s+é\s+(.+)$", clean_question, flags=re.IGNORECASE)
    if subject_match:
        subject = subject_match.group(1).strip()
        return subject[:1].upper() + subject[1:] if subject else ""
    subject_match = re.match(r"^qual\s+o\s+(.+)$", clean_question, flags=re.IGNORECASE)
    if subject_match:
        return f"O {subject_match.group(1).strip()}"
    subject_match = re.match(r"^qual\s+a\s+(.+)$", clean_question, flags=re.IGNORECASE)
    if subject_match:
        return f"A {subject_match.group(1).strip()}"
    return ""


def normalize_feedback_correction(question: Optional[str], correction_text: Optional[str]) -> str:
    raw_text = _clean_text(correction_text)
    if not raw_text:
        return ""

    text = raw_text.strip(" \"'“”‘’")
    capture_patterns = (
        r"(?:^|[.?!]\s*)(?:a\s+)?resposta\s+(?:correta|certa)\s+(?:deve\s+ser|seria|é)\s*[:\-]?\s*(.+)$",
        r"(?:^|[.?!]\s*)(?:o\s+correto\s+é|o\s+certo\s+é|o\s+correto\s+seria|o\s+certo\s+seria)\s*[:\-]?\s*(.+)$",
        r"(?:^|[.?!]\s*)(?:corrige\s+para|corrigir\s+para|deve\s+dizer|deve\s+responder)\s*[:\-]?\s*(.+)$",
        r"(?:^|[.?!]\s*)mas\s+sim\s+(.+)$",
    )
    for pattern in capture_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            text = _clean_text(match.group(1)).strip(" \"'“”‘’")
            break

    subject = _declarative_subject_from_question(question)
    concise_value_match = re.fullmatch(
        r"([0-9][0-9.,]*(?:\s*(?:m|metros?|milhas(?:\s+náuticas)?|horas?|dias?|minutos?|%))?)\.?",
        text,
        flags=re.IGNORECASE,
    )
    if concise_value_match and subject:
        return f"{subject} é {concise_value_match.group(1).strip()}."

    article_clause_match = re.fullmatch(
        r"(o|a|os|as)\s+(.+?)\s+e\s+([0-9][^.?!]*)\.?",
        text,
        flags=re.IGNORECASE,
    )
    if article_clause_match and " é " not in text.lower():
        article = article_clause_match.group(1).capitalize()
        clause = article_clause_match.group(2).strip()
        value = article_clause_match.group(3).strip()
        return f"{article} {clause} é {value}."

    if subject and text.lower().startswith(subject.lower()) and " é " not in text.lower():
        trailing_value_match = re.search(
            r"\b([0-9][0-9.,]*(?:\s*(?:m|metros?|milhas(?:\s+náuticas)?|horas?|dias?|minutos?|%)))\.?$",
            text,
            flags=re.IGNORECASE,
        )
        if trailing_value_match:
            return f"{subject} é {trailing_value_match.group(1).strip()}."

    return text


def _normalize_username(value: Optional[str]) -> str:
    return _clean_text(value).lower()


def _infer_whatsapp_number_from_username(value: Optional[str]) -> str:
    clean = _normalize_username(value)
    match = re.fullmatch(r"whatsapp-(\d+)@pragtico\.local", clean)
    if not match:
        return ""
    return match.group(1)


def _normalize_email(value: Optional[str]) -> str:
    return _clean_text(value).lower()


def _normalize_phone(value: Optional[str]) -> str:
    return _clean_text(value)


def _normalize_whatsapp_number(value: Optional[str]) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _normalize_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    clean = str(value).strip().lower()
    if not clean:
        return default
    return clean not in {"0", "false", "no", "off", "nao", "não"}


def _normalize_user_profile_payload(
    record: Optional[Dict],
    *,
    username: Optional[str] = None,
    role: Optional[str] = None,
) -> Dict:
    payload = record or {}
    normalized_role = (payload.get("role") or role or "piloto").strip().lower()
    if normalized_role not in {"admin", "agente", "piloto"}:
        normalized_role = "piloto"
    normalized_whatsapp_number = (
        _normalize_whatsapp_number(payload.get("whatsapp_number"))
        or _infer_whatsapp_number_from_username(payload.get("username") or username)
    )
    normalized_whatsapp_opt_in = bool(normalized_whatsapp_number) and _normalize_bool(
        payload.get("whatsapp_opt_in"),
        default=False,
    )
    whatsapp_opt_in_at = payload.get("whatsapp_opt_in_at")
    if normalized_whatsapp_opt_in:
        whatsapp_opt_in_at = whatsapp_opt_in_at or iso_now()
    else:
        whatsapp_opt_in_at = None
    profile = {
        "username": _normalize_username(payload.get("username") or username),
        "role": normalized_role,
        "full_name": _clean_text(payload.get("full_name")),
        "organization": _clean_text(payload.get("organization")),
        "email": _normalize_email(payload.get("email")),
        "phone": _normalize_phone(payload.get("phone")),
        "whatsapp_number": normalized_whatsapp_number,
        "whatsapp_opt_in": normalized_whatsapp_opt_in,
        "whatsapp_opt_in_at": whatsapp_opt_in_at,
        "profile_completed_at": payload.get("profile_completed_at"),
    }
    if is_user_profile_complete(profile):
        profile["profile_completed_at"] = payload.get("profile_completed_at") or iso_now()
    else:
        profile["profile_completed_at"] = payload.get("profile_completed_at")
    return profile


def is_user_profile_complete(profile: Optional[Dict]) -> bool:
    """Return True if all required profile fields are filled for the user's role."""
    payload = profile or {}
    role = (payload.get("role") or "").strip().lower()
    if role not in USER_PROFILE_REQUIRED_ROLES:
        return True
    return all(_clean_text(payload.get(field, "")) for field in USER_PROFILE_REQUIRED_FIELDS)


def _build_actor_snapshot(profile: Optional[Dict], *, username: Optional[str] = None, role: Optional[str] = None) -> Dict:
    normalized = _normalize_user_profile_payload(profile, username=username, role=role)
    return {
        "username": normalized.get("username", ""),
        "role": normalized.get("role", ""),
        "full_name": normalized.get("full_name", ""),
        "organization": normalized.get("organization", ""),
        "email": normalized.get("email", ""),
        "phone": normalized.get("phone", ""),
    }


def _actor_meta(username: Optional[str], snapshot: Optional[Dict], fallback: str = "--") -> Dict:
    normalized = _build_actor_snapshot(snapshot, username=username)
    label = normalized.get("full_name") or _normalize_actor_label(normalized.get("username"), fallback)
    details = [value for value in (normalized.get("organization"), normalized.get("email"), normalized.get("phone")) if value]
    return {
        **normalized,
        "label": label,
        "contact_label": " · ".join(details) if details else "--",
    }


def _resolve_vessel_type_meta(value: Optional[str]) -> Dict:
    meta = VESSEL_TYPE_LOOKUP.get(_lookup_key(value))
    if meta:
        return meta
    return VESSEL_TYPE_LOOKUP[_lookup_key("Restantes")]


def _split_text_values(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = re.split(r"[,;|]", str(value))
    return [_clean_text(item) for item in items if _clean_text(item)]


def normalize_constraint_codes(value) -> List[str]:
    """Normalize a list or delimited string of constraint codes to canonical codes."""
    codes: List[str] = []
    for item in _split_text_values(value):
        meta = CONSTRAINT_LOOKUP.get(_lookup_key(item))
        if not meta:
            continue
        code = meta["code"]
        if code not in codes:
            codes.append(code)
    return codes


def _constraint_badges(codes: List[str]) -> List[Dict]:
    badges = []
    for code in normalize_constraint_codes(codes):
        meta = CONSTRAINT_LOOKUP.get(_lookup_key(code))
        if not meta:
            continue
        badges.append(
            {
                "code": meta["code"],
                "label": meta["label"],
                "icon": meta["icon"],
            }
        )
    return badges


def format_constraint_labels(value) -> str:
    """Return a comma-separated string of human-readable constraint labels."""
    labels = [item["label"] for item in _constraint_badges(normalize_constraint_codes(value))]
    return ", ".join(labels)


def _validate_required_vessel_profile(record: Dict[str, str]) -> None:
    required_fields = (
        ("vessel_imo", "IMO"),
        ("vessel_call_sign", "indicativo"),
        ("vessel_flag", "bandeira"),
        ("vessel_type", "tipo de navio"),
        ("vessel_loa_m", "LOA"),
        ("vessel_beam_m", "boca"),
        ("vessel_gt_t", "GT"),
        ("vessel_max_draft_m", "calado"),
        ("vessel_dwt_t", "DWT"),
    )
    missing = [label for field, label in required_fields if not _clean_text(record.get(field, ""))]
    if missing:
        raise ValueError(f"Faltam dados obrigatórios do navio: {', '.join(missing)}.")
    numeric_fields = (
        ("vessel_loa_m", "LOA"),
        ("vessel_beam_m", "boca"),
        ("vessel_gt_t", "GT"),
        ("vessel_max_draft_m", "calado"),
        ("vessel_dwt_t", "DWT"),
    )
    for field, label in numeric_fields:
        value = _clean_text(record.get(field, "")).replace(",", ".")
        if value:
            try:
                number = float(value)
            except ValueError:
                raise ValueError(f"{label} deve ser um número válido.")
            if number <= 0:
                raise ValueError(f"{label} deve ser positivo.")


def _validate_required_operational_profile(record: Dict[str, str], fields: tuple[tuple[str, str], ...]) -> None:
    missing = [label for field, label in fields if not _clean_text(record.get(field, ""))]
    if missing:
        raise ValueError(f"Faltam dados operacionais obrigatórios: {', '.join(missing)}.")


def _normalize_maneuver_type(value: Optional[str]) -> str:
    clean = re.sub(r"[^a-z0-9_\s-]", "", (value or "").strip().lower())
    clean = re.sub(r"[\s-]+", "_", clean).strip("_")
    return clean or "entry"


def _maneuver_type_label(maneuver_type: Optional[str]) -> str:
    clean_type = _normalize_maneuver_type(maneuver_type)
    return {
        "entry": "Entrada",
        "departure": "Saída",
        "shift": "Mudança",
    }.get(clean_type, clean_type.replace("_", " ").strip().title() or "Manobra")


def _maneuver_action_label(maneuver_type: Optional[str]) -> str:
    clean_type = _normalize_maneuver_type(maneuver_type)
    return {
        "entry": "Entrar",
        "departure": "Sair",
        "shift": "Mudança",
    }.get(clean_type, _maneuver_type_label(clean_type))


def _maneuver_state_meta(maneuver_type: Optional[str], state: Optional[str]) -> tuple[str, str]:
    clean_type = _normalize_maneuver_type(maneuver_type)
    clean_state = (state or PORT_CALL_APPROVAL_PENDING).strip().lower()
    label = {
        PORT_CALL_APPROVAL_PENDING: "Pendente",
        "approved": "Aprovada",
        "aborted": "Abortada",
        "completed": "Realizada",
    }.get(clean_state, "Pendente")
    css_class = {
        PORT_CALL_APPROVAL_PENDING: "pending",
        "approved": "approved",
        "aborted": "aborted",
        "completed": "completed",
    }.get(clean_state, "pending")
    return label, css_class


def _conversation_title_from_text(text: str) -> str:
    collapsed = " ".join(text.strip().split())
    if not collapsed:
        return "Nova conversa"
    if len(collapsed) <= 52:
        return collapsed
    return collapsed[:51].rstrip() + "\u2026"


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _text_similarity(left: str, right: str) -> float:
    normalized_left = _normalize_text(left)
    normalized_right = _normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    left_tokens = set(re.findall(r"\w+", normalized_left))
    right_tokens = set(re.findall(r"\w+", normalized_right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
