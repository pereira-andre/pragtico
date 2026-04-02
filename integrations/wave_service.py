from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional

import requests


class WaveService:
    def __init__(
        self,
        endpoint: str,
        station_name: str = "Sines",
        cache_ttl_seconds: int = 900,
        timeout: int = 10,
        snapshot_path: str = "",
        failure_backoff_seconds: Optional[int] = None,
    ) -> None:
        self.endpoint = endpoint
        self.station_name = station_name
        self.cache_ttl_seconds = max(int(cache_ttl_seconds or 0), 0)
        self.timeout = timeout
        self.snapshot_path = snapshot_path
        self.failure_backoff_seconds = max(
            int(failure_backoff_seconds if failure_backoff_seconds is not None else self.cache_ttl_seconds or 0),
            0,
        )
        self._cache: Optional[Dict[str, Any]] = None
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
        if not isinstance(data, dict):
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

    def _mark_success(self, data: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        self._cache = data
        self._cached_at = now
        self._last_attempt_at = now
        self._last_error = ""
        self._last_error_at = 0.0
        self._persist_snapshot()
        return self._decorate_payload(data, stale=False)

    def _mark_failure(self, exc: Exception) -> None:
        now = time.time()
        self._last_attempt_at = now
        self._last_error = self._humanize_error(exc)
        self._last_error_at = now
        self._persist_snapshot()

    def _decorate_payload(self, payload: Dict[str, Any], *, stale: bool) -> Dict[str, Any]:
        decorated = dict(payload)
        decorated["cache_stale"] = stale
        decorated["cache_updated_at_iso"] = (
            datetime.fromtimestamp(self._cached_at, tz=timezone.utc).isoformat() if self._cached_at else ""
        )
        decorated["cache_updated_at_label"] = self._format_timestamp_label(self._cached_at)
        decorated["source_error"] = self._last_error if stale else ""
        return decorated

    def status(self) -> Dict[str, Any]:
        return {
            "stale": bool(self._cache and self._last_error),
            "error": self._last_error,
            "cache_updated_at_label": self._format_timestamp_label(self._cached_at),
            "cache_updated_at_iso": (
                datetime.fromtimestamp(self._cached_at, tz=timezone.utc).isoformat() if self._cached_at else ""
            ),
            "last_attempt_at_label": self._format_timestamp_label(self._last_attempt_at),
        }

    def _format_decimal(self, value: Any, unit: str, decimals: int = 1) -> str:
        if value in (None, ""):
            return "--"
        number = round(float(value), decimals)
        if decimals > 0 and float(number).is_integer():
            return f"{int(number)}{unit}"
        return f"{number:.{decimals}f}{unit}"

    def _normalize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        reading_at = datetime.strptime(payload["date"], "%Y-%m-%d %H:%M:%S")
        significant_height_m = round(float(payload.get("hm0") or 0.0), 2)
        max_height_m = round(float(payload.get("hmax") or 0.0), 2)
        mean_period_s = round(float(payload.get("t02") or 0.0), 1)
        max_observed_period_s = round(float(payload.get("tmax") or 0.0), 1)
        water_temp_c = round(float(payload.get("temp") or 0.0), 1)

        return {
            "station_name": self.station_name,
            "last_reading_label": reading_at.strftime("%d/%m/%Y, %H:%M"),
            "last_reading_iso": reading_at.isoformat(),
            "significant_height_m": significant_height_m,
            "significant_height_label": self._format_decimal(significant_height_m, "m", decimals=2),
            "max_height_m": max_height_m,
            "max_height_label": self._format_decimal(max_height_m, "m", decimals=2),
            "mean_period_s": mean_period_s,
            "mean_period_label": self._format_decimal(mean_period_s, "s", decimals=1),
            "max_observed_period_s": max_observed_period_s,
            "max_observed_period_label": self._format_decimal(max_observed_period_s, "s", decimals=1),
            "direction": (payload.get("thtp") or "--").strip() or "--",
            "water_temp_c": water_temp_c,
            "water_temp_label": self._format_decimal(water_temp_c, "°C", decimals=1),
            "metrics": [
                {"label": "Última leitura", "value": reading_at.strftime("%d/%m/%Y, %H:%M")},
                {"label": "Altura significativa", "value": self._format_decimal(significant_height_m, "m", decimals=2)},
                {"label": "Altura máxima", "value": self._format_decimal(max_height_m, "m", decimals=2)},
                {"label": "Período médio", "value": self._format_decimal(mean_period_s, "s", decimals=1)},
                {"label": "Período máx. obs.", "value": self._format_decimal(max_observed_period_s, "s", decimals=1)},
                {"label": "Dir. ondulação", "value": (payload.get("thtp") or "--").strip() or "--"},
                {"label": "Temp. água", "value": self._format_decimal(water_temp_c, "°C", decimals=1)},
            ],
        }

    def get_current_conditions(self) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        if self._is_cache_valid():
            return self._decorate_payload(self._cache or {}, stale=False)

        with self._lock:
            if self._is_cache_valid():
                return self._decorate_payload(self._cache or {}, stale=False)
            if self._cache and self._is_backoff_active():
                return self._decorate_payload(self._cache, stale=True)
            try:
                response = requests.get(self.endpoint, timeout=self.timeout)
                response.raise_for_status()
                data = self._normalize_payload(response.json())
                return self._mark_success(data)
            except Exception as exc:
                self._mark_failure(exc)
                if self._cache:
                    return self._decorate_payload(self._cache, stale=True)
                raise RuntimeError(self._last_error) from exc

    def summary_text(self) -> str:
        conditions = self.get_current_conditions()
        if not conditions:
            return "Sem leitura costeira atual disponível."
        return "\n".join(
            [
                "Leitura costeira atual:",
                f"- Última leitura: {conditions.get('last_reading_label', '--')}",
                f"- Altura significativa: {conditions.get('significant_height_label', '--')}",
                f"- Altura máxima: {conditions.get('max_height_label', '--')}",
                f"- Período médio: {conditions.get('mean_period_label', '--')}",
                f"- Período máx. obs.: {conditions.get('max_observed_period_label', '--')}",
                f"- Direção da ondulação: {conditions.get('direction', '--')}",
                f"- Temperatura da água: {conditions.get('water_temp_label', '--')}",
            ]
        )

    def context_source(self) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        summary = self.summary_text()
        return {
            "source_id": "WV1",
            "document": "Leitura costeira atual",
            "chunk_id": 0,
            "score": 1.0,
            "retrieval_mode": "live_api",
            "snippet": summary,
            "text": summary,
        }
