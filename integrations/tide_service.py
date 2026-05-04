from __future__ import annotations

import csv
import os
import re
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import acos, asin, atan, cos, degrees, pi, radians, sin, tan
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo


LISBON_TZ = ZoneInfo("Europe/Lisbon")
SETUBAL_LAT = 38.5244
SETUBAL_LON = -8.8882


@dataclass
class TideEvent:
    timestamp_local: datetime
    height: float

    @property
    def timestamp_label(self) -> str:
        return self.timestamp_local.strftime("%Y-%m-%d %H:%M")

    @property
    def timestamp(self) -> datetime:
        return self.timestamp_local

    @property
    def date_value(self) -> date:
        return self.timestamp_local.date()

    @property
    def hour(self) -> int:
        return self.timestamp_local.hour

    @property
    def minute(self) -> int:
        return self.timestamp_local.minute

    @property
    def tide_type(self) -> str:
        return "preia-mar" if self.height >= 2.0 else "baixa-mar"


class TideService:
    def __init__(self, csv_path: str) -> None:
        self.csv_path = csv_path
        self.location_label = self._infer_location_label(csv_path)
        self._events_cache: Optional[List[TideEvent]] = None
        self._month_names_pt = (
            "Janeiro",
            "Fevereiro",
            "Março",
            "Abril",
            "Maio",
            "Junho",
            "Julho",
            "Agosto",
            "Setembro",
            "Outubro",
            "Novembro",
            "Dezembro",
        )

    def _infer_location_label(self, path: str) -> str:
        name = os.path.basename(path)
        if "setubal_troia" in name.lower():
            return "Setúbal / Troia"
        return os.path.splitext(name)[0]

    def _format_date_label(self, target_date: date) -> str:
        return target_date.strftime("%d/%m/%Y")

    def _format_month_name(self, target_date: date) -> str:
        return self._month_names_pt[target_date.month - 1]

    def _format_range_label(self, start_date: date, end_date: date) -> str:
        start_month = self._format_month_name(start_date)
        end_month = self._format_month_name(end_date)
        return f"{start_date.day} {start_month} a {end_date.day} de {end_month}"

    def _format_duration_label(self, duration: timedelta | None) -> str:
        if duration is None:
            return "--"
        total_minutes = max(int(duration.total_seconds() // 60), 0)
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours}h {minutes:02d}m"

    def _sun_event_local(self, target_date: date, *, sunrise: bool) -> datetime | None:
        # NOAA sunrise/sunset approximation. Good enough for operational context labels;
        # exact maneuver decisions still use official pilotage/local instructions.
        day_of_year = target_date.timetuple().tm_yday
        lng_hour = SETUBAL_LON / 15.0
        base_hour = 6 if sunrise else 18
        t = day_of_year + ((base_hour - lng_hour) / 24)
        mean_anomaly = (0.9856 * t) - 3.289
        true_longitude = (
            mean_anomaly
            + (1.916 * sin(radians(mean_anomaly)))
            + (0.020 * sin(radians(2 * mean_anomaly)))
            + 282.634
        ) % 360
        right_ascension = degrees(atan(0.91764 * tan(radians(true_longitude)))) % 360
        longitude_quadrant = (int(true_longitude / 90)) * 90
        ascension_quadrant = (int(right_ascension / 90)) * 90
        right_ascension = (right_ascension + (longitude_quadrant - ascension_quadrant)) / 15
        sin_declination = 0.39782 * sin(radians(true_longitude))
        cos_declination = cos(asin(sin_declination))
        cos_hour_angle = (
            cos(radians(90.833))
            - (sin_declination * sin(radians(SETUBAL_LAT)))
        ) / (cos_declination * cos(radians(SETUBAL_LAT)))
        if cos_hour_angle > 1 or cos_hour_angle < -1:
            return None
        hour_angle = 360 - degrees(acos(cos_hour_angle)) if sunrise else degrees(acos(cos_hour_angle))
        hour_angle /= 15
        local_mean_time = hour_angle + right_ascension - (0.06571 * t) - 6.622
        utc_hour = (local_mean_time - lng_hour) % 24
        hour = int(utc_hour)
        minute_float = (utc_hour - hour) * 60
        minute = int(minute_float)
        second = int(round((minute_float - minute) * 60))
        if second >= 60:
            minute += 1
            second = 0
        if minute >= 60:
            hour = (hour + 1) % 24
            minute = 0
        utc_dt = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            hour,
            minute,
            second,
            tzinfo=timezone.utc,
        )
        return utc_dt.astimezone(LISBON_TZ)

    def luminosity_for_date(self, target_date: date) -> Dict:
        sunrise = self._sun_event_local(target_date, sunrise=True)
        sunset = self._sun_event_local(target_date, sunrise=False)
        daylight = None
        night = None
        if sunrise and sunset:
            daylight = sunset - sunrise
            if daylight.total_seconds() < 0:
                daylight += timedelta(days=1)
            night = timedelta(days=1) - daylight
        sunrise_label = sunrise.strftime("%H:%M") if sunrise else "--"
        sunset_label = sunset.strftime("%H:%M") if sunset else "--"
        daylight_label = self._format_duration_label(daylight)
        night_label = self._format_duration_label(night)
        summary = (
            f"☀️ Nascer do sol {sunrise_label}; "
            f"🌅 pôr do sol {sunset_label}; "
            f"luz {daylight_label}; 🌙 noite {night_label}."
        )
        return {
            "sunrise": sunrise_label,
            "sunset": sunset_label,
            "daylight_duration": daylight_label,
            "night_duration": night_label,
            "summary": summary,
        }

    def _resolve_portuguese_date(
        self,
        day_str: str,
        month_str: str,
        year_str: str | None,
        reference_date: date,
    ) -> date:
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
        month = month_lookup.get((month_str or "").strip().lower(), reference_date.month)
        year = int(year_str) if year_str else reference_date.year
        return date(year, month, int(day_str))

    def _build_chart(self, events: List[TideEvent]) -> Dict:
        if not events:
            return {"points": "", "min_height_m": 0.0, "max_height_m": 0.0, "amplitude_m": 0.0}
        min_height = min(item.height for item in events)
        max_height = max(item.height for item in events)
        span = max(max_height - min_height, 0.4)
        points = []
        for item in events:
            total_minutes = item.hour * 60 + item.minute
            x = round(12 + ((total_minutes / 1440) * 276), 1)
            y = round(78 - (((item.height - min_height) / span) * 54), 1)
            points.append(f"{x},{y}")
        return {
            "points": " ".join(points),
            "min_height_m": round(min_height, 2),
            "max_height_m": round(max_height, 2),
            "amplitude_m": round(max_height - min_height, 2),
        }

    def _build_smooth_path(self, points: List[tuple[float, float]]) -> str:
        if not points:
            return ""
        if len(points) == 1:
            x, y = points[0]
            return f"M {x:.1f},{y:.1f}"
        commands = [f"M {points[0][0]:.1f},{points[0][1]:.1f}"]
        for index in range(len(points) - 1):
            p0 = points[index - 1] if index > 0 else points[index]
            p1 = points[index]
            p2 = points[index + 1]
            p3 = points[index + 2] if index + 2 < len(points) else p2
            cp1x = p1[0] + (p2[0] - p0[0]) / 6
            cp1y = p1[1] + (p2[1] - p0[1]) / 6
            cp2x = p2[0] - (p3[0] - p1[0]) / 6
            cp2y = p2[1] - (p3[1] - p1[1]) / 6
            commands.append(
                f"C {cp1x:.1f},{cp1y:.1f} {cp2x:.1f},{cp2y:.1f} {p2[0]:.1f},{p2[1]:.1f}"
            )
        return " ".join(commands)

    def _relative_day_label(self, target_date: date, reference_date: Optional[date] = None) -> str:
        ref = reference_date or datetime.now(LISBON_TZ).date()
        delta = (target_date - ref).days
        if delta == -1:
            return "Ontem"
        if delta == 0:
            return "Hoje"
        if delta == 1:
            return "Amanhã"
        return ""

    def _height_at_datetime(self, target_dt: datetime) -> tuple[float, str]:
        events = self._load_events()
        if not events:
            return 0.0, "estável"
        timestamps = [item.timestamp for item in events]
        index = bisect_left(timestamps, target_dt)
        if index <= 0:
            return events[0].height, "a descer" if len(events) > 1 and events[1].height < events[0].height else "a subir"
        if index >= len(events):
            return events[-1].height, "a subir" if len(events) > 1 and events[-1].height > events[-2].height else "a descer"

        previous_event = events[index - 1]
        next_event = events[index]
        previous_dt = previous_event.timestamp
        next_dt = next_event.timestamp
        total_seconds = max((next_dt - previous_dt).total_seconds(), 1.0)
        elapsed_seconds = min(max((target_dt - previous_dt).total_seconds(), 0.0), total_seconds)
        ratio = elapsed_seconds / total_seconds
        easing = (1 - cos(pi * ratio)) / 2
        height = previous_event.height + ((next_event.height - previous_event.height) * easing)
        trend = "a subir" if next_event.height > previous_event.height else "a descer"
        return round(height, 2), trend

    def _build_window_samples(
        self,
        start_date: date,
        days: int,
        to_x,
        to_y,
        reference_date: date,
        step_minutes: int = 15,
    ) -> list[dict]:
        start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=LISBON_TZ)
        end_dt = start_dt + timedelta(days=days)
        samples = []
        sample_count = int((days * 24 * 60) / step_minutes)
        for index in range(sample_count + 1):
            current_dt = start_dt + timedelta(minutes=index * step_minutes)
            if current_dt > end_dt:
                current_dt = end_dt
            height_value, trend = self._height_at_datetime(current_dt)
            samples.append({
                "x": round(to_x(current_dt.date(), current_dt.hour, current_dt.minute), 1),
                "y": round(to_y(height_value), 1),
                "height_m": round(height_value, 2),
                "trend": trend,
                "timestamp": current_dt.isoformat(),
                "time_label": current_dt.strftime("%d/%m/%Y %H:%M"),
                "day_label": self._relative_day_label(current_dt.date(), reference_date),
            })
        return samples

    def window_summary(self, start_date: date, days: int = 4) -> Dict:
        width = 1180
        height = 230
        left_pad = 54
        right_pad = 24
        top_pad = 24
        bottom_pad = 32
        end_date = start_date + timedelta(days=days)
        events = [
            item for item in self._load_events()
            if start_date <= item.date_value < end_date
        ]
        total_minutes = max(days * 24 * 60, 1)
        now_local = datetime.now(LISBON_TZ)
        min_height = min((item.height for item in events), default=0.0)
        max_height = max((item.height for item in events), default=0.0)
        span = max(max_height - min_height, 0.4)

        def to_x(item_date: date, hour: int, minute: int) -> float:
            offset_minutes = ((item_date - start_date).days * 24 * 60) + (hour * 60) + minute
            usable_width = width - left_pad - right_pad
            return left_pad + (offset_minutes / total_minutes) * usable_width

        def to_y(height_m: float) -> float:
            usable_height = height - top_pad - bottom_pad
            normalized = (height_m - min_height) / span
            return height - bottom_pad - (normalized * usable_height)

        sample_reference_date = now_local.date()
        chart_samples = self._build_window_samples(start_date, days, to_x, to_y, sample_reference_date)
        chart_points = [(sample["x"], sample["y"]) for sample in chart_samples]
        event_points = [(to_x(item.date_value, item.hour, item.minute), to_y(item.height)) for item in events]
        day_dividers = []
        for offset in range(days + 1):
            divider_date = start_date + timedelta(days=offset)
            x = to_x(divider_date, 0, 0)
            day_dividers.append(
                {
                    "x": round(x, 1),
                    "label": self._relative_day_label(divider_date, sample_reference_date) or divider_date.strftime("%d/%m"),
                    "is_today": divider_date == now_local.date(),
                }
            )
        now_marker_x = None
        if start_date <= now_local.date() < end_date:
            now_marker_x = round(to_x(now_local.date(), now_local.hour, now_local.minute), 1)

        return {
            "location": self.location_label,
            "days": [self.summary_for_date(start_date + timedelta(days=index)) for index in range(days)],
            "chart": {
                "width": width,
                "height": height,
                "left_pad": left_pad,
                "right_pad": right_pad,
                "top_pad": top_pad,
                "bottom_pad": bottom_pad,
                "path_d": self._build_smooth_path(chart_points),
                "samples": chart_samples,
                "points": [
                    {
                        "x": round(x, 1),
                        "y": round(y, 1),
                        "label": f"{events[index].timestamp_label} · {events[index].height:.1f} m",
                        "type": events[index].tide_type,
                    }
                    for index, (x, y) in enumerate(event_points)
                ],
                "day_dividers": day_dividers,
                "now_marker_x": now_marker_x,
                "range_label": self._format_range_label(start_date, end_date - timedelta(days=1)),
                "hours_label": f"{days * 24}h",
                "amplitude_m": round(max_height - min_height, 2),
                "min_height_m": round(min_height, 2),
                "max_height_m": round(max_height, 2),
                "y_ticks": [
                    {"value": round(min_height, 1), "y": round(to_y(min_height), 1)},
                    {"value": round((min_height + max_height) / 2, 1), "y": round(to_y((min_height + max_height) / 2), 1)},
                    {"value": round(max_height, 1), "y": round(to_y(max_height), 1)},
                ],
            },
        }

    def _load_events(self) -> List[TideEvent]:
        if self._events_cache is not None:
            return self._events_cache

        events: List[TideEvent] = []
        if not os.path.exists(self.csv_path):
            self._events_cache = events
            return events
        with open(self.csv_path, "r", encoding="utf-8", errors="ignore") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                source_dt = datetime(
                    *datetime.strptime(row["Date"], "%Y-%m-%d").date().timetuple()[:3],
                    int(row["Hour"]),
                    int(row["Minute"]),
                    tzinfo=timezone.utc,
                )
                events.append(
                    TideEvent(
                        timestamp_local=source_dt.astimezone(LISBON_TZ),
                        height=float(row["Height"]),
                    )
                )
        self._events_cache = events
        return events

    def resolve_query_dates(self, question: str, reference_date: Optional[date] = None) -> List[date]:
        ref = reference_date or datetime.now(LISBON_TZ).date()
        question_lower = question.lower()
        dates: List[date] = []

        def add_day(value: date) -> None:
            if value not in dates:
                dates.append(value)

        for match in re.finditer(r"\b(20\d{2}-\d{2}-\d{2})\b", question_lower):
            add_day(datetime.strptime(match.group(1), "%Y-%m-%d").date())

        for match in re.finditer(r"\b(\d{2}/\d{2}/20\d{2})\b", question_lower):
            add_day(datetime.strptime(match.group(1), "%d/%m/%Y").date())

        for match in re.finditer(
            r"\b(\d{1,2})\s+de\s+"
            r"(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"
            r"(?:\s+de\s+(20\d{2}))?\b",
            question_lower,
        ):
            add_day(self._resolve_portuguese_date(match.group(1), match.group(2), match.group(3), ref))

        if "hoje" in question_lower:
            add_day(ref)
        if "amanhã" in question_lower or "amanha" in question_lower:
            add_day(ref + timedelta(days=1))
        if "ontem" in question_lower:
            add_day(ref - timedelta(days=1))

        if not dates:
            add_day(ref)
        return dates

    def events_for_date(self, target_date: date) -> List[TideEvent]:
        return [item for item in self._load_events() if item.date_value == target_date]

    def summary_for_date(self, target_date: date) -> Dict:
        events = self.events_for_date(target_date)
        date_label = self._format_date_label(target_date)
        relative_label = self._relative_day_label(target_date)
        luminosity = self.luminosity_for_date(target_date)
        if not events:
            return {
                "date": target_date.isoformat(),
                "date_label": date_label,
                "relative_label": relative_label,
                "location": self.location_label,
                "events": [],
                "chart": self._build_chart(events),
                "luminosity": luminosity,
                "summary": (
                    f"Sem marés registadas para {date_label} em {self.location_label}. "
                    f"{luminosity['summary']}"
                ),
            }

        lines = [
            f"{item.timestamp_label} - {item.tide_type} de {item.height:.1f} m"
            for item in events
        ]
        chart = self._build_chart(events)
        return {
            "date": target_date.isoformat(),
            "date_label": date_label,
            "relative_label": relative_label,
            "location": self.location_label,
            "events": [
                {
                    "time": f"{item.hour:02d}:{item.minute:02d}",
                    "height_m": item.height,
                    "type": item.tide_type,
                }
                for item in events
            ],
            "chart": chart,
            "luminosity": luminosity,
            "summary": (
                f"Marés para {date_label} em {self.location_label}: "
                + "; ".join(lines)
                + f". {luminosity['summary']}"
            ),
        }

    def context_for_question(self, question: str) -> Dict:
        target_dates = self.resolve_query_dates(question)
        summaries = [self.summary_for_date(target_date) for target_date in target_dates]
        summary_lines = [item["summary"] for item in summaries]
        return {
            "source_id": "T1",
            "document": f"Marés {summaries[0]['location']}",
            "chunk_id": 0,
            "score": 1.0,
            "retrieval_mode": "structured",
            "snippet": "\n".join(summary_lines),
            "text": "\n".join(summary_lines),
        }
