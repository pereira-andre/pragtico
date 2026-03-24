from datetime import datetime, timedelta, timezone
import threading
import time
import unittest
from zoneinfo import ZoneInfo

from reindex_scheduler import DeferredTaskScheduler, next_gemini_quota_reset_utc


class ReindexSchedulerTests(unittest.TestCase):
    def test_next_gemini_quota_reset_uses_pacific_midnight(self) -> None:
        pacific = ZoneInfo("America/Los_Angeles")
        now = datetime(2026, 3, 22, 18, 30, tzinfo=timezone.utc)

        reset_at = next_gemini_quota_reset_utc(now)
        reset_local = reset_at.astimezone(pacific)

        self.assertEqual(reset_local.hour, 0)
        self.assertEqual(reset_local.minute, 0)
        self.assertEqual(reset_local.second, 0)
        self.assertEqual(reset_local.date().isoformat(), "2026-03-23")

    def test_scheduler_triggers_callback_once(self) -> None:
        fired = threading.Event()
        calls: list[str] = []

        def callback() -> None:
            calls.append("called")
            fired.set()

        scheduler = DeferredTaskScheduler("test-reindex-scheduler", callback)
        scheduler.schedule(datetime.now(timezone.utc), reason="quota reset")

        self.assertTrue(fired.wait(timeout=1.0))
        time.sleep(0.05)
        self.assertEqual(calls, ["called"])
        self.assertFalse(scheduler.status()["scheduled"])

    def test_cancel_prevents_callback(self) -> None:
        fired = threading.Event()

        def callback() -> None:
            fired.set()

        scheduler = DeferredTaskScheduler("test-reindex-scheduler-cancel", callback)
        scheduler.schedule(
            datetime.now(timezone.utc) + timedelta(seconds=0.3),
            reason="quota reset",
        )
        scheduler.cancel()
        time.sleep(0.1)
        self.assertFalse(fired.is_set())


if __name__ == "__main__":
    unittest.main()
