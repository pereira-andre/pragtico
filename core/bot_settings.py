"""Admin-governed runtime settings for the bot (learning thresholds, auto-promote, windows)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from core import services

logger = logging.getLogger(__name__)

_BOT_SETTINGS_FILENAME = "bot_settings.json"
_BOT_SETTINGS_HISTORY_FILENAME = "bot_settings_history.json"
_BOT_SETTINGS_HISTORY_LIMIT = 48
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


def _settings_history_path() -> Path:
    data_dir = getattr(services, "DATA_DIR", "") or ""
    return Path(data_dir) / _BOT_SETTINGS_HISTORY_FILENAME


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


def _normalize_settings_snapshot(raw: Any) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    source = raw
    if isinstance(raw, dict) and isinstance(raw.get("settings"), dict):
        source = raw.get("settings")
    if isinstance(source, dict):
        for key in DEFAULTS.keys():
            if key in source:
                merged[key] = _coerce(key, source[key])
        merged["updated_at"] = str(source.get("updated_at") or "")
        merged["updated_by"] = str(source.get("updated_by") or "")
    else:
        merged["updated_at"] = ""
        merged["updated_by"] = ""
    return merged


def _read_json_file(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback
    except Exception:
        logger.exception("Falha ao ler %s.", path.name)
        return fallback


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_history_changes(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    changes: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if key not in DEFAULTS or not isinstance(value, dict):
            continue
        changes[key] = {
            "before": _coerce(key, value.get("before")),
            "after": _coerce(key, value.get("after")),
        }
    return changes


def _normalize_history_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    settings = _normalize_settings_snapshot(raw.get("settings") or {})
    changes = _normalize_history_changes(raw.get("changes"))
    changed_keys = [str(key) for key in raw.get("changed_keys") or [] if str(key) in DEFAULTS]
    if not changed_keys and changes:
        changed_keys = list(changes.keys())
    changed_at = str(raw.get("changed_at") or settings.get("updated_at") or "")
    return {
        "revision_id": str(raw.get("revision_id") or uuid4().hex[:12]),
        "action": str(raw.get("action") or "update").strip() or "update",
        "summary": str(raw.get("summary") or "").strip(),
        "changed_at": changed_at,
        "changed_by": str(raw.get("changed_by") or settings.get("updated_by") or "").strip(),
        "changed_keys": changed_keys,
        "changes": changes,
        "settings": settings,
    }


def _read_history() -> list[dict[str, Any]]:
    raw = _read_json_file(_settings_history_path(), [])
    if not isinstance(raw, list):
        return []
    entries = []
    for item in raw:
        normalized = _normalize_history_entry(item)
        if normalized:
            entries.append(normalized)
    entries.sort(key=lambda item: (item.get("changed_at") or "", item.get("revision_id") or ""))
    return entries[-_BOT_SETTINGS_HISTORY_LIMIT:]


def _write_history(entries: list[dict[str, Any]]) -> None:
    normalized_entries = []
    for item in entries[-_BOT_SETTINGS_HISTORY_LIMIT:]:
        normalized = _normalize_history_entry(item)
        if normalized:
            normalized_entries.append(normalized)
    _write_json_file(_settings_history_path(), normalized_entries)


def _diff_settings(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for key in DEFAULTS.keys():
        if before.get(key) == after.get(key):
            continue
        changes[key] = {
            "before": before.get(key),
            "after": after.get(key),
        }
    return changes


def _build_history_summary(action: str, changed_keys: list[str], summary: str = "") -> str:
    if summary:
        return summary
    if action == "reset":
        return "Repostos os valores por defeito."
    if action == "rollback":
        return "Rollback para uma revisão anterior."
    if action == "import":
        return "Definições importadas para este ambiente."
    if not changed_keys:
        return "Metadados atualizados."
    if len(changed_keys) == 1:
        return f"{changed_keys[0]} atualizado."
    if len(changed_keys) <= 3:
        return f"{', '.join(changed_keys)} atualizados."
    return f"{len(changed_keys)} definições atualizadas."


def _append_history_entry(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    updated_by: str,
    action: str,
    summary: str = "",
) -> dict[str, Any]:
    changes = _diff_settings(before, after)
    entry = {
        "revision_id": uuid4().hex[:12],
        "action": action,
        "summary": _build_history_summary(action, list(changes.keys()), summary),
        "changed_at": str(after.get("updated_at") or _iso_now()),
        "changed_by": str(updated_by or "").strip(),
        "changed_keys": list(changes.keys()),
        "changes": changes,
        "settings": dict(after),
    }
    history = _read_history()
    history.append(entry)
    _write_history(history)
    return entry


def list_bot_settings_history(*, limit: int | None = 12) -> list[dict[str, Any]]:
    history = list(reversed(_read_history()))
    if limit is None or limit <= 0:
        return history
    return history[:limit]


def merge_bot_settings_history(entries: list[dict[str, Any]] | None) -> int:
    if not isinstance(entries, list):
        return 0
    with _LOCK:
        current = _read_history()
        seen_ids = {str(item.get("revision_id") or "") for item in current}
        added = 0
        for item in entries:
            normalized = _normalize_history_entry(item)
            if not normalized:
                continue
            revision_id = str(normalized.get("revision_id") or "")
            if revision_id and revision_id in seen_ids:
                continue
            current.append(normalized)
            if revision_id:
                seen_ids.add(revision_id)
            added += 1
        if added:
            current.sort(key=lambda item: (item.get("changed_at") or "", item.get("revision_id") or ""))
            _write_history(current)
        return added


def load_bot_settings() -> dict[str, Any]:
    path = _settings_path()
    raw = _read_json_file(path, {})
    return _normalize_settings_snapshot(raw)


def save_bot_settings(
    updates: dict[str, Any],
    *,
    updated_by: str = "",
    action: str = "update",
    summary: str = "",
) -> dict[str, Any]:
    with _LOCK:
        current = load_bot_settings()
        previous = dict(current)
        for key, value in (updates or {}).items():
            if key in DEFAULTS:
                current[key] = _coerce(key, value)
        current["updated_at"] = _iso_now()
        current["updated_by"] = str(updated_by or "").strip()
        path = _settings_path()
        payload = {key: current.get(key) for key in (*DEFAULTS.keys(), "updated_at", "updated_by")}
        _write_json_file(path, payload)
        _append_history_entry(previous, payload, updated_by=current["updated_by"], action=action, summary=summary)
        return current


def reset_bot_settings(*, updated_by: str = "") -> dict[str, Any]:
    return save_bot_settings(DEFAULTS, updated_by=updated_by, action="reset")


def restore_bot_settings_revision(revision_id: str, *, updated_by: str = "") -> dict[str, Any]:
    target_revision = str(revision_id or "").strip()
    if not target_revision:
        raise ValueError("Revisão inválida.")
    for item in list_bot_settings_history(limit=None):
        if str(item.get("revision_id") or "") != target_revision:
            continue
        snapshot = item.get("settings") or {}
        summary = f"Rollback para revisão {target_revision[:8]}."
        return save_bot_settings(snapshot, updated_by=updated_by, action="rollback", summary=summary)
    raise ValueError("Revisão não encontrada.")
