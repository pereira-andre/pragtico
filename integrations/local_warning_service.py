from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional

import requests


PT_MONTHS = {
    1: "jan",
    2: "fev",
    3: "mar",
    4: "abr",
    5: "mai",
    6: "jun",
    7: "jul",
    8: "ago",
    9: "set",
    10: "out",
    11: "nov",
    12: "dez",
}


class LocalWarningService:
    def __init__(
        self,
        endpoint: str,
        base_url: str = "https://anavnetbackend.hidrografico.pt",
        cache_ttl_seconds: int = 900,
        timeout: int = 15,
        snapshot_path: str = "",
        failure_backoff_seconds: Optional[int] = None,
    ) -> None:
        self.endpoint = endpoint
        self.base_url = base_url.rstrip("/")
        self.cache_ttl_seconds = max(int(cache_ttl_seconds or 0), 0)
        self.timeout = timeout
        self.snapshot_path = snapshot_path
        self.failure_backoff_seconds = max(
            int(failure_backoff_seconds if failure_backoff_seconds is not None else self.cache_ttl_seconds or 0),
            0,
        )
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cached_at = 0.0
        self._last_attempt_at = 0.0
        self._last_error = ""
        self._last_error_at = 0.0
        self._lock = Lock()
        self._load_snapshot()

    @property
    def enabled(self) -> bool:
        return bool((self.endpoint or "").strip())

    def _is_cache_valid(self) -> bool:
        return bool(self._cache) and (time.time() - self._cached_at) < self.cache_ttl_seconds

    def _is_backoff_active(self) -> bool:
        return bool(self._last_error and self.failure_backoff_seconds > 0) and (
            time.time() - self._last_attempt_at
        ) < self.failure_backoff_seconds

    def _format_timestamp_label(self, value: float) -> str:
        if value <= 0:
            return ""
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().strftime("%d/%m/%Y, %H:%M")

    def _snapshot_payload(self) -> Dict[str, Any]:
        return {
            "data": self._cache,
            "cached_at": self._cached_at,
            "last_attempt_at": self._last_attempt_at,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
        }

    def _load_snapshot(self) -> None:
        if not self.snapshot_path:
            return
        try:
            with open(self.snapshot_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        data = payload.get("data")
        if not isinstance(data, list):
            return
        self._cache = data
        self._cached_at = float(payload.get("cached_at") or 0.0)
        self._last_attempt_at = float(payload.get("last_attempt_at") or 0.0)
        self._last_error = str(payload.get("last_error") or "")
        self._last_error_at = float(payload.get("last_error_at") or 0.0)

    def _persist_snapshot(self) -> None:
        if not self.snapshot_path:
            return
        try:
            directory = os.path.dirname(self.snapshot_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.snapshot_path, "w", encoding="utf-8") as handle:
                json.dump(self._snapshot_payload(), handle, ensure_ascii=False, indent=2)
        except OSError:
            return

    def _humanize_error(self, exc: Exception) -> str:
        message = str(exc)
        lowered = message.lower()
        if "connection refused" in lowered or "failed to establish a new connection" in lowered:
            return "Ligação ao Instituto Hidrográfico recusada neste ambiente."
        if "max retries exceeded" in lowered or "newconnectionerror" in lowered:
            return "Ligação ao Instituto Hidrográfico indisponível neste momento."
        if "timed out" in lowered or "timeout" in lowered:
            return "O Instituto Hidrográfico não respondeu a tempo."
        return message

    def _mark_success(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = time.time()
        self._cache = data
        self._cached_at = now
        self._last_attempt_at = now
        self._last_error = ""
        self._last_error_at = 0.0
        self._persist_snapshot()
        return list(data)

    def _mark_failure(self, exc: Exception) -> None:
        now = time.time()
        self._last_attempt_at = now
        self._last_error = self._humanize_error(exc)
        self._last_error_at = now
        self._persist_snapshot()

    def status(self) -> Dict[str, Any]:
        warning_count = len(self._cache or [])
        return {
            "stale": bool(self._cache and self._last_error),
            "error": self._last_error,
            "cache_updated_at_label": self._format_timestamp_label(self._cached_at),
            "cache_updated_at_iso": (
                datetime.fromtimestamp(self._cached_at, tz=timezone.utc).isoformat() if self._cached_at else ""
            ),
            "last_attempt_at_label": self._format_timestamp_label(self._last_attempt_at),
            "count": warning_count,
        }

    def _parse_date(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        clean = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(clean)
        except ValueError:
            return None

    def _format_date_label(self, value: Optional[str], fallback: str = "n/a") -> str:
        parsed = self._parse_date(value)
        if not parsed:
            return fallback
        return f"{parsed.day:02d} {PT_MONTHS[parsed.month]} {parsed.year}"

    def _html_to_text(self, value: Optional[str]) -> str:
        text = value or ""
        replacements = {
            r"(?i)<br\s*/?>": "\n",
            r"(?i)</p>": "\n\n",
            r"(?i)</div>": "\n",
            r"(?i)</li>": "\n",
            r"(?i)<li[^>]*>": "- ",
        }
        for pattern, replacement in replacements.items():
            text = re.sub(pattern, replacement, text)
        text = re.sub(r"(?i)<[^>]+>", "", text)
        text = html.unescape(text).replace("\xa0", " ")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    def _build_excerpt(self, text: str, limit: int = 220) -> str:
        compact = re.sub(r"\s+", " ", text).strip()
        if len(compact) <= limit:
            return compact
        shortened = compact[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:")
        return f"{shortened}..."

    def _normalize_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        description_text = self._html_to_text(row.get("description"))
        attachments = row.get("attachments") or []
        entity = row.get("entity") or {}
        state = row.get("state") or {}
        start_label = self._format_date_label(row.get("startDate"))
        end_label = self._format_date_label(row.get("endDate"))
        cancel_label = self._format_date_label(row.get("cancelDate"), fallback=end_label)

        return {
            "id": row.get("id"),
            "code": row.get("code") or "--",
            "display_code": f"Anav nr {row.get('code') or '--'}",
            "entity_name": entity.get("name") or "Capitania do Porto de Setúbal",
            "status_label": "Em vigor" if (state.get("code") or "").lower() == "promulgado" else (state.get("name") or "--"),
            "subject": row.get("subject") or row.get("name") or "--",
            "location": row.get("locationDescription") or "--",
            "description_text": description_text,
            "excerpt": self._build_excerpt(description_text),
            "start_date_label": start_label,
            "end_date_label": end_label,
            "cancel_date_label": cancel_label,
            "start_date_iso": row.get("startDate"),
            "end_date_iso": row.get("endDate"),
            "has_attachments": bool(attachments),
            "attachments": [
                {
                    "id": item.get("id"),
                    "name": item.get("name") or "Anexo",
                    "url": f"{self.base_url}{item.get('uri')}",
                    "file_type": item.get("fileType") or "",
                }
                for item in attachments
                if item.get("uri")
            ],
        }

    def _fetch_page(self, page: int) -> Dict[str, Any]:
        url = self.endpoint
        if "currentPage=" in url:
            url = re.sub(r"currentPage=\d+", f"currentPage={page}", url)
        elif "?" in url:
            url = f"{url}&currentPage={page}"
        else:
            url = f"{url}?currentPage={page}"
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _fetch_all(self) -> List[Dict[str, Any]]:
        first_page = self._fetch_page(1)
        rows = list(first_page.get("rows") or [])
        total_pages = int(first_page.get("totalPages") or 1)
        for page in range(2, total_pages + 1):
            rows.extend((self._fetch_page(page).get("rows") or []))
        return [self._normalize_row(row) for row in rows]

    def probe_warnings(self) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        with self._lock:
            try:
                return self._mark_success(self._fetch_all())
            except Exception as exc:
                self._mark_failure(exc)
                if self._cache:
                    return list(self._cache)
                raise RuntimeError(self._last_error) from exc

    def list_warnings(self) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        if self._is_cache_valid():
            return list(self._cache or [])

        with self._lock:
            if self._is_cache_valid():
                return list(self._cache or [])
            if self._cache and self._is_backoff_active():
                return list(self._cache)
            try:
                return self._mark_success(self._fetch_all())
            except Exception as exc:
                self._mark_failure(exc)
                if self._cache:
                    return list(self._cache)
                raise RuntimeError(self._last_error) from exc

    def get_warning(self, warning_id: int) -> Optional[Dict[str, Any]]:
        for item in self.list_warnings():
            if item.get("id") == warning_id:
                return item
        return None

    def summary_text(self, limit: int = 12) -> str:
        warnings = self.list_warnings()
        if not warnings:
            return "Sem avisos locais em vigor."
        lines = ["Avisos locais em vigor:"]
        for item in warnings[:limit]:
            lines.append(
                f"- {item.get('display_code', '--')} · {item.get('subject', '--')} · {item.get('location', '--')}"
            )
        remaining = len(warnings) - limit
        if remaining > 0:
            lines.append(f"- +{remaining} aviso(s) adicionais em vigor.")
        return "\n".join(lines)

    def codes_summary_text(self, limit: int = 20) -> str:
        warnings = self.list_warnings()
        if not warnings:
            return "Sem avisos locais em vigor."
        lines = ["Avisos locais em vigor:"]
        for item in warnings[:limit]:
            lines.append(f"- {item.get('display_code', '--')}")
        remaining = len(warnings) - limit
        if remaining > 0:
            lines.append(f"- +{remaining} aviso(s) adicionais.")
        return "\n".join(lines)

    def context_source(self, limit: int = 12) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        summary = self.summary_text(limit=limit)
        return {
            "source_id": "LW1",
            "document": "Avisos locais em vigor",
            "chunk_id": 0,
            "score": 1.0,
            "retrieval_mode": "live_api",
            "snippet": summary,
            "text": summary,
        }
