from __future__ import annotations

import html
import re
import time
from datetime import datetime
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
    ) -> None:
        self.endpoint = endpoint
        self.base_url = base_url.rstrip("/")
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout = timeout
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cached_at = 0.0
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return bool((self.endpoint or "").strip())

    def _is_cache_valid(self) -> bool:
        return bool(self._cache) and (time.monotonic() - self._cached_at) < self.cache_ttl_seconds

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

    def list_warnings(self) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        if self._is_cache_valid():
            return list(self._cache or [])

        with self._lock:
            if self._is_cache_valid():
                return list(self._cache or [])
            try:
                data = self._fetch_all()
                self._cache = data
                self._cached_at = time.monotonic()
                return list(data)
            except Exception:
                if self._cache:
                    return list(self._cache)
                raise

    def get_warning(self, warning_id: int) -> Optional[Dict[str, Any]]:
        for item in self.list_warnings():
            if item.get("id") == warning_id:
                return item
        return None
