import threading
import time
import unittest

from core.live_data_refresh import PeriodicTaskScheduler


class PeriodicTaskSchedulerTests(unittest.TestCase):
    def test_scheduler_runs_immediately_and_repeats(self) -> None:
        fired = threading.Event()
        calls: list[float] = []
        lock = threading.Lock()

        def callback() -> None:
            with lock:
                calls.append(time.time())
                if len(calls) >= 2:
                    fired.set()

        scheduler = PeriodicTaskScheduler(
            "test-live-data-refresh",
            callback,
            interval_seconds=0.05,
        )

        self.assertTrue(scheduler.start())
        self.assertTrue(fired.wait(timeout=0.5))
        scheduler.stop()

        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(scheduler.status()["last_run_at"])

    def test_scheduler_recovers_after_callback_error(self) -> None:
        fired = threading.Event()
        attempts = 0

        def callback() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary failure")
            fired.set()

        scheduler = PeriodicTaskScheduler(
            "test-live-data-refresh-error",
            callback,
            interval_seconds=0.05,
        )

        self.assertTrue(scheduler.start())
        self.assertTrue(fired.wait(timeout=0.5))
        time.sleep(0.05)
        status = scheduler.status()
        scheduler.stop()

        self.assertGreaterEqual(attempts, 2)
        self.assertEqual(status["last_error"], "")
        self.assertTrue(status["last_run_at"])

    def test_scheduler_does_not_start_with_zero_interval(self) -> None:
        scheduler = PeriodicTaskScheduler(
            "test-live-data-refresh-disabled",
            lambda: None,
            interval_seconds=0,
        )

        self.assertFalse(scheduler.start())
        self.assertFalse(scheduler.status()["running"])


if __name__ == "__main__":
    unittest.main()
