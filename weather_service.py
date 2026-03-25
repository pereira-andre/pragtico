"""Weather service with day/night detection, sunrise/sunset sectors, and moon phase.

Integrates WeatherAPI.com forecast data with operational context:
- Hourly forecasts with is_day flag for correct icon selection (sun vs moon)
- Sunrise/sunset times per day (critical for pilotage sector planning)
- Moon phase for tidal context
- Wind levels classified for maritime operations
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Optional

import requests


# Setúbal approximate coordinates for fallback sunrise/sunset
SETUBAL_LAT = 38.5244
SETUBAL_LON = -8.8882


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
        values = [v for v in (wind_kts, gust_kts) if v is not None]
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
        for v in values:
            if v in (None, ""):
                continue
            try:
                return round(float(v), 1)
            except (TypeError, ValueError):
                continue
        return None

    def _parse_astro_time(self, time_str: str, ref_date: str) -> str:
        """Parse WeatherAPI astro time like '06:42 AM' to HH:MM format."""
        if not time_str:
            return ""
        clean = time_str.strip()
        for fmt in ("%I:%M %p", "%H:%M"):
            try:
                parsed = datetime.strptime(clean, fmt)
                return parsed.strftime("%H:%M")
            except ValueError:
                continue
        return clean

    def _is_night_hour(self, hour_str: str, sunrise: str, sunset: str) -> bool:
        """Determine if an hour falls in the night sector."""
        if not sunrise or not sunset or not hour_str:
            return False
        try:
            h = int(hour_str.split(":")[0])
            sr = int(sunrise.split(":")[0])
            ss = int(sunset.split(":")[0])
            return h < sr or h >= ss
        except (ValueError, IndexError):
            return False

    def get_forecast(self, days: int = 3) -> Optional[Dict]:
        """Fetch weather forecast with day/night, sunrise/sunset, and moon data.

        Parameters:
            days: Number of forecast days (max 3 for free tier).

        Returns:
            Structured forecast dict with all operational context.
        """
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
            day_date = day.get("date", "")

            # Parse sunrise/sunset to HH:MM
            sunrise = self._parse_astro_time(astro.get("sunrise", ""), day_date)
            sunset = self._parse_astro_time(astro.get("sunset", ""), day_date)
            moonrise = self._parse_astro_time(astro.get("moonrise", ""), day_date)
            moonset = self._parse_astro_time(astro.get("moonset", ""), day_date)
            moon_phase = astro.get("moon_phase", "")
            moon_illumination = astro.get("moon_illumination", "")

            hours = []
            for hour in day.get("hour", []):
                hour_time = datetime.strptime(hour.get("time"), "%Y-%m-%d %H:%M")
                if hour_time < start_dt:
                    continue
                wind_kts = self._kph_to_kts(hour.get("wind_kph"))
                gust_kts = self._kph_to_kts(hour.get("gust_kph"))
                time_label = hour_time.strftime("%H:%M")
                is_day = bool(hour.get("is_day", 1))
                is_night = not is_day

                hours.append(
                    {
                        "time": time_label,
                        "timestamp": hour.get("time"),
                        "temp_c": hour.get("temp_c"),
                        "condition": hour.get("condition", {}).get("text", ""),
                        "condition_icon": hour.get("condition", {}).get("icon", ""),
                        "wind_kts": wind_kts,
                        "gust_kts": gust_kts,
                        "wind_dir": hour.get("wind_dir"),
                        "wind_degree": hour.get("wind_degree"),
                        "precip_mm": hour.get("precip_mm"),
                        "chance_of_rain": hour.get("chance_of_rain"),
                        "humidity": hour.get("humidity"),
                        "vis_km": hour.get("vis_km"),
                        "wave_m": self._optional_float(
                            hour.get("sig_ht_mt"),
                            hour.get("wave_height_m"),
                            hour.get("wave_m"),
                        ),
                        "wave_dir": hour.get("swell_dir") or hour.get("wave_dir"),
                        "wave_degree": hour.get("swell_degree") or hour.get("wave_degree"),
                        "wind_level": self._wind_level(wind_kts, gust_kts),
                        "is_day": is_day,
                        "is_night": is_night,
                        "sector": "dia" if is_day else "noite",
                    }
                )

            max_wind_kts = self._kph_to_kts(day_data.get("maxwind_kph"))
            max_gust_kts = self._kph_to_kts(day_data.get("maxgust_kph"))

            day_summary = {
                "date": day_date,
                "condition": day_data.get("condition", {}).get("text", ""),
                "max_temp_c": day_data.get("maxtemp_c"),
                "min_temp_c": day_data.get("mintemp_c"),
                "max_wind_kph": day_data.get("maxwind_kph"),
                "max_wind_kts": max_wind_kts,
                "max_gust_kts": max_gust_kts,
                "rain_mm": day_data.get("totalprecip_mm"),
                "sunrise": sunrise,
                "sunset": sunset,
                "moonrise": moonrise,
                "moonset": moonset,
                "moon_phase": moon_phase,
                "moon_illumination": moon_illumination,
                "wind_level": self._wind_level(max_wind_kts, max_gust_kts),
            }
            days_summary.append(day_summary)
            hourly_groups.append({"date": day_date, "hours": hours, **day_summary})

        current_wind_kts = self._kph_to_kts(current.get("wind_kph"))
        current_gust_kts = self._kph_to_kts(current.get("gust_kph"))
        current_is_day = bool(current.get("is_day", 1))

        summary = (
            f"Meteorologia para {location.get('name', self.location)} em {location.get('localtime', '')}: "
            f"{current.get('condition', {}).get('text', '')}, "
            f"{current.get('temp_c')} °C, vento {current_wind_kts} kts de {current.get('wind_dir')}, "
            f"humidade {current.get('humidity')}%, precipitação {current.get('precip_mm')} mm."
        )
        if days_summary:
            summary += " Próximos dias: " + "; ".join(
                f"{d['date']}: {d['condition']}, {d['min_temp_c']}–{d['max_temp_c']} °C, "
                f"vento máx. {d['max_wind_kts']} kts, "
                f"nascer {d['sunrise']} pôr {d['sunset']}"
                + (f", lua {d['moon_phase']}" if d.get("moon_phase") else "")
                for d in days_summary
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
                "is_day": current_is_day,
            },
            "forecast_days": days_summary,
            "hourly_groups": hourly_groups,
            "alerts": alerts,
            "summary": summary,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
        }

    def context_source(self) -> Optional[Dict]:
        """Build RAG context source with weather + sector data."""
        forecast = self.get_forecast()
        if not forecast:
            return None

        # Enrich summary with sector info for chatbot
        sector_lines = []
        for d in forecast.get("forecast_days", []):
            if d.get("sunrise") and d.get("sunset"):
                sector_lines.append(
                    f"Sectores {d['date']}: Dia {d['sunrise']}–{d['sunset']}, "
                    f"Noite antes das {d['sunrise']} e após as {d['sunset']}."
                )
            if d.get("moon_phase"):
                sector_lines.append(
                    f"Lua {d['date']}: {d['moon_phase']} ({d.get('moon_illumination', '--')}% iluminação)."
                )

        full_snippet = forecast["summary"]
        if sector_lines:
            full_snippet += "\n" + "\n".join(sector_lines)

        return {
            "source_id": "W1",
            "document": f"WeatherAPI {forecast['location']['name']}",
            "chunk_id": 0,
            "score": 1.0,
            "retrieval_mode": "live_api",
            "snippet": full_snippet,
            "text": full_snippet,
        }
