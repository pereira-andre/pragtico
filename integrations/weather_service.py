"""Weather service with day/night detection, sunrise/sunset sectors, and moon phase.

Integrates WeatherAPI.com forecast data with operational context:
- Hourly forecasts with is_day flag for correct icon selection (sun vs moon)
- Sunrise/sunset times per day (critical for pilotage sector planning)
- Moon phase for tidal context
- Wind levels classified for maritime operations
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
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

    def _format_date_label(self, date_str: str) -> str:
        if not date_str:
            return ""
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return date_str

    def _duration_between(self, start_time: str, end_time: str) -> Optional[timedelta]:
        if not start_time or not end_time:
            return None
        try:
            start_dt = datetime.strptime(start_time, "%H:%M")
            end_dt = datetime.strptime(end_time, "%H:%M")
        except ValueError:
            return None
        if end_dt < start_dt:
            end_dt += timedelta(days=1)
        return end_dt - start_dt

    def _format_duration_label(self, duration: Optional[timedelta]) -> str:
        if duration is None:
            return ""
        total_minutes = int(duration.total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours}h {minutes:02d}m"

    def _moon_phase_display(self, phase: str) -> tuple[str, str]:
        clean = (phase or "").strip()
        lookup = clean.casefold()
        phase_map = {
            "new moon": ("🌑", "Lua nova"),
            "waxing crescent": ("🌒", "Lua crescente"),
            "first quarter": ("🌓", "Quarto crescente"),
            "waxing gibbous": ("🌔", "Lua gibosa crescente"),
            "full moon": ("🌕", "Lua cheia"),
            "waning gibbous": ("🌖", "Lua gibosa minguante"),
            "last quarter": ("🌗", "Quarto minguante"),
            "waning crescent": ("🌘", "Lua minguante"),
            "lua nova": ("🌑", "Lua nova"),
            "lua crescente": ("🌒", "Lua crescente"),
            "quarto crescente": ("🌓", "Quarto crescente"),
            "lua cheia": ("🌕", "Lua cheia"),
            "quarto minguante": ("🌗", "Quarto minguante"),
            "lua minguante": ("🌘", "Lua minguante"),
        }
        return phase_map.get(lookup, ("🌙", clean or "Lua"))

    def _moon_line(self, payload: Dict) -> str:
        icon = payload.get("moon_phase_icon") or "🌙"
        label = payload.get("moon_phase_label") or payload.get("moon_phase") or "Lua"
        illumination = payload.get("moon_illumination", "--")
        return f"{icon} {label} ({illumination}% iluminação)"

    def _resolve_query_dates(self, question: str, reference_date: date) -> List[str]:
        question_lower = (question or "").lower()
        resolved: List[str] = []

        def add_day(value: date) -> None:
            iso = value.isoformat()
            if iso not in resolved:
                resolved.append(iso)

        for match in re.finditer(r"\b(20\d{2}-\d{2}-\d{2})\b", question_lower):
            add_day(datetime.strptime(match.group(1), "%Y-%m-%d").date())

        for match in re.finditer(r"\b(\d{2})/(\d{2})/(20\d{2})\b", question_lower):
            add_day(date(int(match.group(3)), int(match.group(2)), int(match.group(1))))

        month_lookup = {
            "janeiro": 1,
            "fevereiro": 2,
            "marco": 3,
            "março": 3,
            "abril": 4,
            "maio": 5,
            "junho": 6,
            "julho": 7,
            "agosto": 8,
            "setembro": 9,
            "outubro": 10,
            "novembro": 11,
            "dezembro": 12,
        }
        for match in re.finditer(
            r"\b(\d{1,2})\s+de\s+"
            r"(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"
            r"(?:\s+de\s+(20\d{2}))?\b",
            question_lower,
        ):
            month = month_lookup.get(match.group(2), reference_date.month)
            year = int(match.group(3)) if match.group(3) else reference_date.year
            add_day(date(year, month, int(match.group(1))))

        if "hoje" in question_lower:
            add_day(reference_date)
        if "amanhã" in question_lower or "amanha" in question_lower:
            add_day(reference_date + timedelta(days=1))
        if "ontem" in question_lower:
            add_day(reference_date - timedelta(days=1))

        return resolved

    def _resolve_query_times(self, question: str) -> List[str]:
        times: List[str] = []
        for match in re.finditer(r"\b(\d{1,2}:\d{2})\b", question or ""):
            try:
                normalized = datetime.strptime(match.group(1), "%H:%M").strftime("%H:%M")
            except ValueError:
                continue
            if normalized not in times:
                times.append(normalized)
        return times

    def _closest_hour(self, hours: List[Dict], target_time: str) -> Optional[Dict]:
        if not hours:
            return None
        try:
            target_dt = datetime.strptime(target_time, "%H:%M")
        except ValueError:
            return None
        best_hour = None
        best_delta = None
        for hour in hours:
            try:
                current_dt = datetime.strptime(hour.get("time", ""), "%H:%M")
            except ValueError:
                continue
            delta = abs((current_dt - target_dt).total_seconds())
            if best_delta is None or delta < best_delta:
                best_hour = hour
                best_delta = delta
        return best_hour

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
            moon_phase_icon, moon_phase_label = self._moon_phase_display(moon_phase)
            moon_illumination = astro.get("moon_illumination", "")
            daylight_duration = self._duration_between(sunrise, sunset)
            night_duration = timedelta(days=1) - daylight_duration if daylight_duration is not None else None

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
                "date_label": self._format_date_label(day_date),
                "condition": day_data.get("condition", {}).get("text", ""),
                "max_temp_c": day_data.get("maxtemp_c"),
                "min_temp_c": day_data.get("mintemp_c"),
                "max_wind_kph": day_data.get("maxwind_kph"),
                "max_wind_kts": max_wind_kts,
                "max_gust_kts": max_gust_kts,
                "rain_mm": day_data.get("totalprecip_mm"),
                "sunrise": sunrise,
                "sunset": sunset,
                "daylight_duration_label": self._format_duration_label(daylight_duration),
                "night_duration_label": self._format_duration_label(night_duration),
                "moonrise": moonrise,
                "moonset": moonset,
                "moon_phase": moon_phase,
                "moon_phase_icon": moon_phase_icon,
                "moon_phase_label": moon_phase_label,
                "moon_illumination": moon_illumination,
                "wind_level": self._wind_level(max_wind_kts, max_gust_kts),
            }
            days_summary.append(day_summary)
            hourly_groups.append({"date": day_date, "date_label": self._format_date_label(day_date), "hours": hours, **day_summary})

        current_wind_kts = self._kph_to_kts(current.get("wind_kph"))
        current_gust_kts = self._kph_to_kts(current.get("gust_kph"))
        current_is_day = bool(current.get("is_day", 1))

        summary_lines = [
            f"Meteorologia para {location.get('name', self.location)} ({location.get('localtime', '')}):",
            f"- Estado: {current.get('condition', {}).get('text', '--')}",
            f"- Temperatura: {current.get('temp_c', '--')} °C",
            f"- Vento: {current_wind_kts} kts de {current.get('wind_dir', '--')}",
            f"- Humidade: {current.get('humidity', '--')}%",
            f"- Precipitação: {current.get('precip_mm', '--')} mm",
        ]
        if days_summary:
            summary_lines.extend(["", "Próximos dias:"])
            for d in days_summary:
                summary_lines.append(
                    f"- {d['date_label']}: {d['condition']}, {d['min_temp_c']}–{d['max_temp_c']} °C, "
                    f"vento máx. {d['max_wind_kts']} kts."
                )
                if d.get("sunrise") and d.get("sunset"):
                    summary_lines.append(f"  - ☀️ Nascer {d['sunrise']}; 🌅 pôr {d['sunset']}.")
                if d.get("moon_phase"):
                    summary_lines.append(f"  - {self._moon_line(d)}.")
        if alerts:
            summary_lines.extend(["", f"Alertas ativos: {len(alerts)}."])
        summary = "\n".join(summary_lines)

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
                    f"Sectores {d['date_label']}: Dia {d['sunrise']}–{d['sunset']}, "
                    f"Noite antes das {d['sunrise']} e após as {d['sunset']}."
                )
            if d.get("moon_phase"):
                sector_lines.append(
                    f"Lua {d['date_label']}: {self._moon_line(d)}."
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

    def context_for_question(self, question: str) -> Optional[Dict]:
        """Return weather context focused on the dates and hours requested by the user."""
        forecast = self.get_forecast()
        if not forecast:
            return None

        try:
            reference_date = datetime.strptime(
                forecast.get("location", {}).get("localtime", ""),
                "%Y-%m-%d %H:%M",
            ).date()
        except ValueError:
            reference_date = datetime.now().date()

        target_dates = self._resolve_query_dates(question, reference_date)
        target_times = self._resolve_query_times(question)
        groups = forecast.get("hourly_groups", [])

        if not target_dates and not target_times:
            return self.context_source()

        selected_groups = [group for group in groups if group.get("date") in target_dates] if target_dates else groups[:1]
        if not selected_groups:
            summary = (
                f"A previsão integrada disponível só cobre {len(groups)} dia(s). "
                "A data pedida não está no horizonte atual."
            )
            return {
                "source_id": "W1",
                "document": f"WeatherAPI {forecast['location']['name']}",
                "chunk_id": 0,
                "score": 1.0,
                "retrieval_mode": "live_api",
                "snippet": summary,
                "text": summary,
            }

        lines = [f"Previsão meteorológica detalhada para {forecast['location']['name']}:"]
        for group in selected_groups:
            lines.extend(
                [
                    "",
                    f"{group.get('date_label') or group.get('date')}:",
                    f"- Estado: {group.get('condition')}",
                    f"- Temperatura: {group.get('min_temp_c')}–{group.get('max_temp_c')} °C",
                    f"- Vento máximo: {group.get('max_wind_kts')} kts",
                ]
            )
            if group.get("sunrise") and group.get("sunset"):
                lines.append(f"- ☀️ Nascer {group.get('sunrise')}; 🌅 pôr {group.get('sunset')}.")
            if group.get("moon_phase"):
                lines.append(f"- {self._moon_line(group)}.")
            if target_times:
                lines.append("- Horas pedidas:")
                for target_time in target_times:
                    closest = self._closest_hour(group.get("hours", []), target_time)
                    if not closest:
                        continue
                    lines.append(
                        f"  - {target_time} -> {closest.get('time')}: {closest.get('condition')}; "
                        f"{closest.get('temp_c')} °C; vento {closest.get('wind_kts')} kts {closest.get('wind_dir')}; "
                        f"rajadas {closest.get('gust_kts')} kts; chuva {closest.get('chance_of_rain')}%."
                    )
            else:
                lines.append("- Próximas horas:")
                for hour in group.get("hours", [])[:8]:
                    lines.append(
                        f"  - {hour.get('time')}: {hour.get('condition')}; {hour.get('temp_c')} °C; "
                        f"vento {hour.get('wind_kts')} kts {hour.get('wind_dir')}; chuva {hour.get('chance_of_rain')}%."
                    )

        text = "\n".join(lines)
        return {
            "source_id": "W1",
            "document": f"WeatherAPI {forecast['location']['name']}",
            "chunk_id": 0,
            "score": 1.0,
            "retrieval_mode": "live_api",
            "snippet": text,
            "text": text,
        }
