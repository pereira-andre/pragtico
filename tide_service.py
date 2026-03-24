from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional


@dataclass
class TideEvent:
    date_value: date
    hour: int
    minute: int
    height: float

    @property
    def timestamp_label(self) -> str:
        return f"{self.date_value.isoformat()} {self.hour:02d}:{self.minute:02d}"

    @property
    def tide_type(self) -> str:
        return "preia-mar" if self.height >= 2.0 else "baixa-mar"


class TideService:
    def __init__(self, csv_path: str) -> None:
        self.csv_path = csv_path
        self.location_label = self._infer_location_label(csv_path)
        self._events_cache: Optional[List[TideEvent]] = None

    def _infer_location_label(self, path: str) -> str:
        name = os.path.basename(path)
        if "setubal_troia" in name.lower():
            return "Setúbal / Troia"
        return os.path.splitext(name)[0]

    def _load_events(self) -> List[TideEvent]:
        if self._events_cache is not None:
            return self._events_cache

        events: List[TideEvent] = []
        with open(self.csv_path, "r", encoding="utf-8", errors="ignore") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                events.append(
                    TideEvent(
                        date_value=datetime.strptime(row["Date"], "%Y-%m-%d").date(),
                        hour=int(row["Hour"]),
                        minute=int(row["Minute"]),
                        height=float(row["Height"]),
                    )
                )
        self._events_cache = events
        return events

    def resolve_query_dates(self, question: str, reference_date: Optional[date] = None) -> List[date]:
        ref = reference_date or datetime.now().date()
        question_lower = question.lower()
        dates: List[date] = []

        def add_day(value: date) -> None:
            if value not in dates:
                dates.append(value)

        for match in re.finditer(r"\b(20\d{2}-\d{2}-\d{2})\b", question_lower):
            add_day(datetime.strptime(match.group(1), "%Y-%m-%d").date())

        for match in re.finditer(r"\b(\d{2}/\d{2}/20\d{2})\b", question_lower):
            add_day(datetime.strptime(match.group(1), "%d/%m/%Y").date())

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
        if not events:
            return {
                "date": target_date.isoformat(),
                "location": self.location_label,
                "events": [],
                "summary": f"Sem marés registadas para {target_date.isoformat()} em {self.location_label}.",
            }

        lines = [
            f"{item.timestamp_label} - {item.tide_type} de {item.height:.1f} m"
            for item in events
        ]
        return {
            "date": target_date.isoformat(),
            "location": self.location_label,
            "events": [
                {
                    "time": f"{item.hour:02d}:{item.minute:02d}",
                    "height_m": item.height,
                    "type": item.tide_type,
                }
                for item in events
            ],
            "summary": f"Marés para {target_date.isoformat()} em {self.location_label}: " + "; ".join(lines),
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
