from __future__ import annotations

import time
from datetime import datetime
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
    ) -> None:
        self.endpoint = endpoint
        self.station_name = station_name
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout = timeout
        self._cache: Optional[Dict[str, Any]] = None
        self._cached_at = 0.0
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return bool((self.endpoint or "").strip())

    def _is_cache_valid(self) -> bool:
        return bool(self._cache) and (time.monotonic() - self._cached_at) < self.cache_ttl_seconds

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
            return self._cache

        with self._lock:
            if self._is_cache_valid():
                return self._cache
            try:
                response = requests.get(self.endpoint, timeout=self.timeout)
                response.raise_for_status()
                data = self._normalize_payload(response.json())
                self._cache = data
                self._cached_at = time.monotonic()
                return data
            except Exception:
                if self._cache:
                    return self._cache
                raise
