from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Optional

import requests


class WeatherService:
    def __init__(self, api_key: str, location: str, language: str = "pt") -> None:
        self.api_key = api_key
        self.location = location
        self.language = language
        self.base_url = "https://api.weatherapi.com/v1/forecast.json"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.location)

    def _kph_to_kts(self, value) -> Optional[float]:
        if value is None:
            return None
        return round(float(value) / 1.852, 1)

    def _wind_level(self, wind_kts: Optional[float], gust_kts: Optional[float] = None) -> str:
        values = [value for value in (wind_kts, gust_kts) if value is not None]
        strongest = max(values) if values else 0.0
        if strongest >= 30:
            return "wind-extreme"
        if strongest >= 25:
            return "wind-severe"
        if strongest >= 20:
            return "wind-high"
        if strongest >= 15:
            return "wind-watch"
        if strongest >= 10:
            return "wind-ok-strong"
        return "wind-ok"

    def _optional_float(self, *values) -> Optional[float]:
        for value in values:
            if value in (None, ""):
                continue
            try:
                return round(float(value), 1)
            except (TypeError, ValueError):
                continue
        return None

    def get_forecast(self, days: int = 2) -> Optional[Dict]:
        if not self.enabled:
            return None

        response = requests.get(
            self.base_url,
            params={
                "key": self.api_key,
                "q": self.location,
                "days": days,
                "lang": self.language,
                "alerts": "yes",
                "aqi": "no",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        current = data.get("current", {})
        location = data.get("location", {})
        forecast_days = data.get("forecast", {}).get("forecastday", [])
        alerts = data.get("alerts", {}).get("alert", [])
        localtime = location.get("localtime", "")
        start_dt = datetime.strptime(localtime, "%Y-%m-%d %H:%M").replace(minute=0)

        days_summary: List[Dict] = []
        hourly_groups: List[Dict] = []
        for day in forecast_days:
            day_data = day.get("day", {})
            astro = day.get("astro", {})
            hours = []
            for hour in day.get("hour", []):
                hour_time = datetime.strptime(hour.get("time"), "%Y-%m-%d %H:%M")
                if hour_time < start_dt:
                    continue
                wind_kts = self._kph_to_kts(hour.get("wind_kph"))
                gust_kts = self._kph_to_kts(hour.get("gust_kph"))
                hours.append(
                    {
                        "time": hour_time.strftime("%H:%M"),
                        "timestamp": hour.get("time"),
                        "temp_c": hour.get("temp_c"),
                        "condition": hour.get("condition", {}).get("text", ""),
                        "wind_kts": wind_kts,
                        "gust_kts": gust_kts,
                        "wind_dir": hour.get("wind_dir"),
                        "wind_degree": hour.get("wind_degree"),
                        "precip_mm": hour.get("precip_mm"),
                        "chance_of_rain": hour.get("chance_of_rain"),
                        "wave_m": self._optional_float(
                            hour.get("sig_ht_mt"),
                            hour.get("wave_height_m"),
                            hour.get("wave_m"),
                        ),
                        "wave_dir": hour.get("swell_dir") or hour.get("wave_dir"),
                        "wave_degree": hour.get("swell_degree") or hour.get("wave_degree"),
                        "wind_level": self._wind_level(wind_kts, gust_kts),
                    }
                )
            max_wind_kts = self._kph_to_kts(day_data.get("maxwind_kph"))
            max_gust_kts = self._kph_to_kts(day_data.get("maxgust_kph"))
            days_summary.append(
                {
                    "date": day.get("date"),
                    "condition": day_data.get("condition", {}).get("text", ""),
                    "max_temp_c": day_data.get("maxtemp_c"),
                    "min_temp_c": day_data.get("mintemp_c"),
                    "max_wind_kph": day_data.get("maxwind_kph"),
                    "max_wind_kts": max_wind_kts,
                    "max_gust_kts": max_gust_kts,
                    "rain_mm": day_data.get("totalprecip_mm"),
                    "sunrise": astro.get("sunrise"),
                    "sunset": astro.get("sunset"),
                    "wind_level": self._wind_level(max_wind_kts, max_gust_kts),
                }
            )
            hourly_groups.append(
                {
                    "date": day.get("date"),
                    "hours": hours,
                }
            )

        current_wind_kts = self._kph_to_kts(current.get("wind_kph"))
        current_gust_kts = self._kph_to_kts(current.get("gust_kph"))

        summary = (
            f"Meteorologia para {location.get('name', self.location)} em {location.get('localtime', '')}: "
            f"{current.get('condition', {}).get('text', '')}, "
            f"{current.get('temp_c')} °C, vento {current_wind_kts} kts de {current.get('wind_dir')}, "
            f"humidade {current.get('humidity')}%, precipitação {current.get('precip_mm')} mm."
        )
        if days_summary:
            summary += " Próximos dias: " + "; ".join(
                f"{item['date']}: {item['condition']}, {item['min_temp_c']} a {item['max_temp_c']} °C, vento máx. {item['max_wind_kts']} kts"
                for item in days_summary
            )
        if alerts:
            summary += f" Alertas ativos: {len(alerts)}."

        return {
            "location": {
                "name": location.get("name", self.location),
                "region": location.get("region", ""),
                "country": location.get("country", ""),
                "localtime": location.get("localtime", ""),
            },
            "current": {
                "temp_c": current.get("temp_c"),
                "condition": current.get("condition", {}).get("text", ""),
                "wind_kph": current.get("wind_kph"),
                "wind_kts": current_wind_kts,
                "gust_kts": current_gust_kts,
                "wind_dir": current.get("wind_dir"),
                "wind_degree": current.get("wind_degree"),
                "humidity": current.get("humidity"),
                "precip_mm": current.get("precip_mm"),
                "vis_km": current.get("vis_km"),
                "wind_level": self._wind_level(current_wind_kts, current_gust_kts),
            },
            "forecast_days": days_summary,
            "hourly_groups": hourly_groups,
            "alerts": alerts,
            "summary": summary,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
        }

    def context_source(self) -> Optional[Dict]:
        forecast = self.get_forecast()
        if not forecast:
            return None
        return {
            "source_id": "W1",
            "document": f"WeatherAPI {forecast['location']['name']}",
            "chunk_id": 0,
            "score": 1.0,
            "retrieval_mode": "live_api",
            "snippet": forecast["summary"],
            "text": forecast["summary"],
        }
