"""Admin-governed runtime settings for the bot (learning thresholds, auto-promote, windows)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from core import services

logger = logging.getLogger(__name__)

_BOT_SETTINGS_FILENAME = "bot_settings.json"
_LOCK = Lock()

DEFAULTS: dict[str, Any] = {
    "auto_promote_corrections": True,
    "auto_trust_positive_feedback": True,
    "require_admin_validation": False,
    "signals_window_hours": 168,
    "review_guard_similarity": 0.90,
    "review_block_similarity": 0.97,
    "review_correction_similarity": 0.94,
    "trusted_document_hint_similarity": 0.82,
    "outlier_review_threshold": 0.85,
}


def _settings_path() -> Path:
    data_dir = getattr(services, "DATA_DIR", "") or ""
    return Path(data_dir) / _BOT_SETTINGS_FILENAME


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce(key: str, value: Any) -> Any:
    default = DEFAULTS.get(key)
    if default is None:
        return value
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "sim", "on"}
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return value


def load_bot_settings() -> dict[str, Any]:
    path = _settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        logger.exception("Falha ao ler bot_settings.json; a usar defaults.")
        raw = {}
    merged = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key, default in DEFAULTS.items():
            if key in raw:
                merged[key] = _coerce(key, raw[key])
        merged["updated_at"] = str(raw.get("updated_at") or "")
        merged["updated_by"] = str(raw.get("updated_by") or "")
    else:
        merged["updated_at"] = ""
        merged["updated_by"] = ""
    return merged


def save_bot_settings(updates: dict[str, Any], *, updated_by: str = "") -> dict[str, Any]:
    with _LOCK:
        current = load_bot_settings()
        for key, value in (updates or {}).items():
            if key in DEFAULTS:
                current[key] = _coerce(key, value)
        current["updated_at"] = _iso_now()
        current["updated_by"] = str(updated_by or "").strip()
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: current.get(key) for key in (*DEFAULTS.keys(), "updated_at", "updated_by")}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return current


def reset_bot_settings(*, updated_by: str = "") -> dict[str, Any]:
    return save_bot_settings(DEFAULTS, updated_by=updated_by)
