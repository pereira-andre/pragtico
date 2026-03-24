from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict
from zoneinfo import ZoneInfo


PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def next_gemini_quota_reset_utc(now: datetime | None = None) -> datetime:
    current = now.astimezone(PACIFIC_TZ) if now else datetime.now(PACIFIC_TZ)
    next_midnight = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_midnight.astimezone(timezone.utc)


class DeferredTaskScheduler:
    def __init__(self, name: str, callback: Callable[[], None]) -> None:
        self.name = name
        self.callback = callback
        self._lock = threading.Lock()
        self._generation = 0
        self._scheduled_for: datetime | None = None
        self._reason = ""
        self._thread: threading.Thread | None = None
        self._last_triggered_at: str | None = None

    def schedule(self, run_at: datetime, reason: str = "") -> bool:
        scheduled_for = run_at.astimezone(timezone.utc)
        with self._lock:
            current = self._scheduled_for
            if (
                current is not None
                and self._thread is not None
                and self._thread.is_alive()
                and current <= scheduled_for
            ):
                return False

            self._generation += 1
            generation = self._generation
            self._scheduled_for = scheduled_for
            self._reason = reason
            self._thread = threading.Thread(
                target=self._run,
                args=(generation, scheduled_for),
                name=self.name,
                daemon=True,
            )
            self._thread.start()
            return True

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1
            self._scheduled_for = None
            self._reason = ""

    def status(self) -> Dict:
        with self._lock:
            scheduled_for = self._scheduled_for
            reason = self._reason
            running = bool(self._thread and self._thread.is_alive() and scheduled_for is not None)
            last_triggered_at = self._last_triggered_at

        eta_seconds = None
        scheduled_for_iso = None
        if running and scheduled_for is not None:
            scheduled_for_iso = scheduled_for.isoformat()
            eta_seconds = max(int((scheduled_for - datetime.now(timezone.utc)).total_seconds()), 0)

        return {
            "scheduled": running,
            "scheduled_for": scheduled_for_iso,
            "eta_seconds": eta_seconds,
            "reason": reason,
            "last_triggered_at": last_triggered_at,
        }

    def _run(self, generation: int, scheduled_for: datetime) -> None:
        while True:
            with self._lock:
                if generation != self._generation or self._scheduled_for != scheduled_for:
                    return
            remaining = (scheduled_for - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 30))

        with self._lock:
            if generation != self._generation or self._scheduled_for != scheduled_for:
                return
            self._scheduled_for = None
            self._reason = ""
            self._last_triggered_at = datetime.now(timezone.utc).isoformat()

        self.callback()
