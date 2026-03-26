"""Centralized input validation — numeric, text, date, email, phone, enums."""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def validate_required_text(value: Optional[str], label: str, *, max_length: int = 500) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        raise ValueError(f"{label} é obrigatório(a).")
    if len(clean) > max_length:
        raise ValueError(f"{label} não pode exceder {max_length} caracteres.")
    return clean


def validate_optional_text(value: Optional[str], *, max_length: int = 2000) -> str:
    clean = " ".join(str(value or "").strip().split())
    if len(clean) > max_length:
        raise ValueError(f"O texto não pode exceder {max_length} caracteres.")
    return clean


# ---------------------------------------------------------------------------
# Numeric
# ---------------------------------------------------------------------------

def validate_positive_number(
    value: Optional[str],
    label: str,
    *,
    required: bool = True,
    min_value: float = 0.0,
    max_value: float = 999999.0,
) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        if required:
            raise ValueError(f"{label} é obrigatório(a).")
        return ""
    clean = clean.replace(",", ".")
    try:
        number = float(clean)
    except ValueError:
        raise ValueError(f"{label} deve ser um número válido.")
    if number < min_value:
        raise ValueError(f"{label} deve ser >= {min_value}.")
    if number > max_value:
        raise ValueError(f"{label} deve ser <= {max_value}.")
    return clean


def validate_optional_positive_number(
    value: Optional[str],
    label: str,
    *,
    max_value: float = 999999.0,
) -> str:
    return validate_positive_number(value, label, required=False, max_value=max_value)


def validate_tug_count(value: Optional[str]) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        return ""
    try:
        count = int(clean)
    except ValueError:
        raise ValueError("Número de rebocadores deve ser um número inteiro.")
    if count < 0 or count > 10:
        raise ValueError("Número de rebocadores deve estar entre 0 e 10.")
    return str(count)


# ---------------------------------------------------------------------------
# Date / Time
# ---------------------------------------------------------------------------

def validate_datetime_range(
    started: Optional[str],
    finished: Optional[str],
    *,
    started_label: str = "Início",
    finished_label: str = "Fim",
) -> None:
    if not started or not finished:
        return
    try:
        dt_started = datetime.fromisoformat(started.replace("Z", "+00:00"))
        dt_finished = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return
    if dt_finished <= dt_started:
        raise ValueError(f"{finished_label} deve ser posterior a {started_label}.")


# ---------------------------------------------------------------------------
# Email / Phone
# ---------------------------------------------------------------------------

_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def validate_email(value: Optional[str], *, required: bool = True) -> str:
    clean = " ".join(str(value or "").strip().split()).lower()
    if not clean:
        if required:
            raise ValueError("Email é obrigatório.")
        return ""
    if not _EMAIL_PATTERN.match(clean):
        raise ValueError("Formato de email inválido.")
    if len(clean) > 254:
        raise ValueError("Email demasiado longo.")
    return clean


_PHONE_PATTERN = re.compile(r"^[\d\s\+\-\(\)\.]{7,20}$")


def validate_phone(value: Optional[str], *, required: bool = True) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        if required:
            raise ValueError("Telefone é obrigatório.")
        return ""
    if not _PHONE_PATTERN.match(clean):
        raise ValueError("Formato de telefone inválido.")
    return clean


# ---------------------------------------------------------------------------
# Enum / choices
# ---------------------------------------------------------------------------

def validate_choice(
    value: Optional[str],
    allowed: set,
    label: str,
    *,
    required: bool = True,
) -> str:
    clean = " ".join(str(value or "").strip().split()).lower()
    if not clean:
        if required:
            raise ValueError(f"{label} é obrigatório(a).")
        return ""
    if clean not in allowed:
        raise ValueError(f"{label} inválido(a): '{clean}'.")
    return clean


ALLOWED_ROLES = {"admin", "agente", "piloto"}


def validate_role(value: Optional[str]) -> str:
    return validate_choice(value, ALLOWED_ROLES, "Role")


ALLOWED_FEEDBACK_STATUSES = {"approved", "review"}


def validate_feedback_status(value: Optional[str]) -> str:
    return validate_choice(value, ALLOWED_FEEDBACK_STATUSES, "Estado de feedback")


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------

def validate_password(value: Optional[str], *, min_length: int = 6) -> str:
    clean = (value or "").strip()
    if len(clean) < min_length:
        raise ValueError(f"A password deve ter pelo menos {min_length} caracteres.")
    return clean


# ---------------------------------------------------------------------------
# IMO
# ---------------------------------------------------------------------------

def validate_imo(value: Optional[str], *, required: bool = True) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        if required:
            raise ValueError("IMO é obrigatório.")
        return ""
    digits = re.sub(r"\D", "", clean)
    if len(digits) != 7:
        raise ValueError("O IMO deve ter 7 dígitos.")
    return digits


# ---------------------------------------------------------------------------
# Vessel dimensions — convenience bundle
# ---------------------------------------------------------------------------

def validate_vessel_dimensions(record: dict) -> dict:
    return {
        "vessel_loa_m": validate_positive_number(
            record.get("vessel_loa_m"), "LOA (m)", max_value=500.0
        ),
        "vessel_beam_m": validate_positive_number(
            record.get("vessel_beam_m"), "Boca (m)", max_value=100.0
        ),
        "vessel_gt_t": validate_positive_number(
            record.get("vessel_gt_t"), "GT (t)", max_value=500000.0
        ),
        "vessel_max_draft_m": validate_positive_number(
            record.get("vessel_max_draft_m"), "Calado máximo (m)", max_value=30.0
        ),
        "vessel_dwt_t": validate_positive_number(
            record.get("vessel_dwt_t"), "DWT (t)", max_value=600000.0
        ),
    }
