from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import has_request_context, request, session

from core import services

AUDIT_EVENT_VERSION = 1
AUDIT_ALLOWED_RESULTS = {"success", "failed", "denied", "skipped"}
AUDIT_ALLOWED_SEVERITIES = {"info", "warning", "critical"}
AUDIT_CATEGORIES = (
    "seguranca",
    "utilizadores",
    "operacao",
    "backups",
    "base_dados",
    "configuracao",
    "documentos",
    "sistema",
)
AUDIT_REDACTED = "[redacted]"
AUDIT_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|token|secret|api[_-]?key|authorization|cookie|hash|credential)",
    flags=re.IGNORECASE,
)

_audit_lock = threading.Lock()


def audit_dir() -> Path:
    configured = os.getenv("AUDIT_LOG_DIR", "").strip()
    if configured:
        base_dir = Path(configured)
    else:
        data_dir = getattr(services, "DATA_DIR", "") or str(Path.cwd() / "data")
        base_dir = Path(data_dir) / "audit"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def audit_file_path(at: datetime | None = None) -> Path:
    dt = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return audit_dir() / f"audit-{dt.strftime('%Y-%m')}.jsonl"


def _safe_text(value: Any, *, limit: int = 500) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[max-depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value, limit=1000)
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            clean_key = str(key)
            if AUDIT_SENSITIVE_KEY_RE.search(clean_key):
                redacted[clean_key] = AUDIT_REDACTED
            else:
                redacted[clean_key] = _redact(item, depth=depth + 1)
        return redacted
    if isinstance(value, (list, tuple, set)):
        return [_redact(item, depth=depth + 1) for item in list(value)[:100]]
    return _safe_text(value)


def _request_context_payload() -> dict:
    if not has_request_context():
        return {
            "ip": "",
            "method": "",
            "path": "",
            "endpoint": "",
            "user_agent": "",
        }
    return {
        "ip": _safe_text(request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]),
        "method": request.method,
        "path": request.path,
        "endpoint": request.endpoint or "",
        "user_agent": _safe_text(request.headers.get("User-Agent", ""), limit=300),
    }


def _session_actor() -> tuple[str, str]:
    if not has_request_context():
        return "", ""
    return (
        _safe_text(session.get("username", "")),
        _safe_text(session.get("role", "")),
    )


def write_audit_event(
    action: str,
    *,
    category: str = "",
    actor: str = "",
    actor_role: str = "",
    severity: str = "info",
    result: str = "success",
    resource: str = "",
    resource_id: str = "",
    details: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    session_actor, session_role = _session_actor()
    clean_severity = severity if severity in AUDIT_ALLOWED_SEVERITIES else "info"
    clean_result = result if result in AUDIT_ALLOWED_RESULTS else "success"
    event = {
        "version": AUDIT_EVENT_VERSION,
        "id": str(uuid4()),
        "at": now.isoformat(),
        "category": category if category in AUDIT_CATEGORIES else _category_for_action(action),
        "actor": _safe_text(actor or session_actor or "system"),
        "actor_role": _safe_text(actor_role or session_role),
        "action": _safe_text(action, limit=160),
        "severity": clean_severity,
        "result": clean_result,
        "resource": _safe_text(resource, limit=160),
        "resource_id": _safe_text(resource_id, limit=220),
        "request": _request_context_payload(),
        "details": _redact(details or {}),
    }
    path = audit_file_path(now)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True)
    with _audit_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return event


def audit_files() -> list[Path]:
    base_dir = audit_dir()
    return sorted(base_dir.glob("audit-*.jsonl"), reverse=True)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_matches(event: dict, filters: dict) -> bool:
    q = str(filters.get("q") or "").strip().lower()
    actor = str(filters.get("actor") or "").strip().lower()
    action = str(filters.get("action") or "").strip().lower()
    category = str(filters.get("category") or "").strip().lower()
    severity = str(filters.get("severity") or "").strip().lower()
    result = str(filters.get("result") or "").strip().lower()
    date_from = _parse_iso(filters.get("date_from"))
    date_to = _parse_iso(filters.get("date_to"))
    event_at = _parse_iso(event.get("at"))

    if date_from and (not event_at or event_at < date_from):
        return False
    if date_to and (not event_at or event_at > date_to):
        return False
    if actor and actor not in str(event.get("actor") or "").lower():
        return False
    if action and action not in str(event.get("action") or "").lower():
        return False
    if category and category != str(event.get("category") or "").lower():
        return False
    if severity and severity != str(event.get("severity") or "").lower():
        return False
    if result and result != str(event.get("result") or "").lower():
        return False
    if q:
        haystack = json.dumps(event, ensure_ascii=False).lower()
        if q not in haystack:
            return False
    return True


def iter_audit_events(filters: dict | None = None, *, limit: int = 300) -> list[dict]:
    clean_filters = filters or {}
    events: list[dict] = []
    max_events = max(int(limit or 0), 1)
    for path in audit_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or not _event_matches(event, clean_filters):
                continue
            events.append(event)
            if len(events) >= max_events:
                return events
    return events


def audit_summary(events: list[dict]) -> dict:
    critical = sum(1 for item in events if item.get("severity") == "critical")
    failed = sum(1 for item in events if item.get("result") in {"failed", "denied"})
    actors = sorted({str(item.get("actor") or "") for item in events if item.get("actor")})
    actions = sorted({str(item.get("action") or "") for item in events if item.get("action")})
    categories = sorted({str(item.get("category") or "") for item in events if item.get("category")})
    return {
        "total": len(events),
        "critical": critical,
        "failed": failed,
        "actors": actors,
        "actions": actions,
        "categories": categories,
    }


def _category_for_action(action: str) -> str:
    clean = (action or "").lower()
    if "login" in clean or "logout" in clean or "permission" in clean:
        return "seguranca"
    if "user" in clean or "utilizador" in clean or "role" in clean:
        return "utilizadores"
    if "backup" in clean:
        return "backups"
    if "wipe" in clean or "database" in clean or "system.import" in clean or "system.export" in clean:
        return "base_dados"
    if "bot" in clean or "settings" in clean or "eval" in clean:
        return "configuracao"
    if "document" in clean or "knowledge" in clean:
        return "documentos"
    if "port_call" in clean or "maneuver" in clean or "manobra" in clean:
        return "operacao"
    if clean.startswith("request.admin"):
        return "sistema"
    return "sistema"


def should_audit_request(endpoint: str, method: str) -> bool:
    clean_endpoint = endpoint or ""
    clean_method = (method or "").upper()
    if clean_method in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    sensitive_get_endpoints = {
        "admin.download_system_backup",
        "admin.download_audit_log",
        "admin.export_audit_log_json",
        "admin.export_system_database",
        "admin.export_bot_database",
    }
    return clean_endpoint in sensitive_get_endpoints


def audit_request_response(response) -> None:
    if not has_request_context() or not session.get("username"):
        return
    if not should_audit_request(request.endpoint or "", request.method):
        return
    status_code = int(getattr(response, "status_code", 0) or 0)
    result = "success" if status_code < 400 else "denied" if status_code in {401, 403} else "failed"
    severity = "critical" if (request.endpoint or "").startswith("admin.") else "info"
    write_audit_event(
        f"request.{request.endpoint or request.path}",
        category=_category_for_action(request.endpoint or request.path),
        severity=severity,
        result=result,
        resource="http_request",
        resource_id=request.path,
        details={
            "status_code": status_code,
            "method": request.method,
            "args": dict(request.args),
            "form_keys": sorted(request.form.keys()),
        },
    )
