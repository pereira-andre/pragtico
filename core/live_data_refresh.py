from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict


class PeriodicTaskScheduler:
    def __init__(
        self,
        name: str,
        callback: Callable[[], None],
        *,
        interval_seconds: float,
        run_immediately: bool = True,
    ) -> None:
        self.name = name
        self.callback = callback
        self.interval_seconds = max(float(interval_seconds or 0), 0.0)
        self.run_immediately = run_immediately
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run_at = ""
        self._last_error = ""
        self._last_error_at = ""

    def start(self) -> bool:
        if self.interval_seconds <= 0:
            return False

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                name=self.name,
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            thread = self._thread
            running = bool(thread and thread.is_alive() and not self._stop_event.is_set())
            return {
                "running": running,
                "interval_seconds": self.interval_seconds,
                "last_run_at": self._last_run_at,
                "last_error": self._last_error,
                "last_error_at": self._last_error_at,
            }

    def _record_success(self) -> None:
        with self._lock:
            self._last_run_at = datetime.now(timezone.utc).isoformat()
            self._last_error = ""
            self._last_error_at = ""

    def _record_failure(self, exc: Exception) -> None:
        with self._lock:
            self._last_error = str(exc)
            self._last_error_at = datetime.now(timezone.utc).isoformat()

    def _run(self) -> None:
        should_run_now = self.run_immediately
        while not self._stop_event.is_set():
            if not should_run_now and self._stop_event.wait(self.interval_seconds):
                break
            should_run_now = False
            try:
                self.callback()
                self._record_success()
            except Exception as exc:
                self._record_failure(exc)
