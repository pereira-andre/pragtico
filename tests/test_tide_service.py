import tempfile
import unittest
from datetime import date
from pathlib import Path

from integrations.tide_service import TideService


class TideServiceTests(unittest.TestCase):
    def _build_service(self, csv_text: str) -> TideService:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        csv_path = Path(temp_dir.name) / "mares_teste.csv"
        csv_path.write_text(csv_text, encoding="utf-8")
        return TideService(str(csv_path))

    def test_csv_times_are_converted_from_utc_to_lisbon_in_dst_transition(self) -> None:
        service = self._build_service(
            "\n".join(
                [
                    "Date,Hour,Minute,Height",
                    "2026-03-29,00,30,3.1",
                    "2026-03-29,01,30,0.8",
                ]
            )
        )

        summary = service.summary_for_date(date(2026, 3, 29))

        self.assertEqual(summary["events"][0]["time"], "00:30")
        self.assertEqual(summary["events"][1]["time"], "02:30")
        self.assertIn("2026-03-29 02:30", summary["summary"])

    def test_csv_times_keep_winter_hour_in_portugal(self) -> None:
        service = self._build_service(
            "\n".join(
                [
                    "Date,Hour,Minute,Height",
                    "2026-01-01,00,36,3.1",
                    "2026-01-01,06,51,0.8",
                ]
            )
        )

        summary = service.summary_for_date(date(2026, 1, 1))

        self.assertEqual(summary["events"][0]["time"], "00:36")
        self.assertEqual(summary["events"][1]["time"], "06:51")


if __name__ == "__main__":
    unittest.main()
