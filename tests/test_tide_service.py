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

    def test_summary_includes_professional_luminosity_period(self) -> None:
        service = self._build_service(
            "\n".join(
                [
                    "Date,Hour,Minute,Height",
                    "2026-04-17,01,00,3.1",
                    "2026-04-17,07,00,0.8",
                ]
            )
        )

        summary = service.summary_for_date(date(2026, 4, 17))

        self.assertIn("luminosity", summary)
        self.assertRegex(summary["luminosity"]["sunrise"], r"\d{2}:\d{2}")
        self.assertRegex(summary["luminosity"]["sunset"], r"\d{2}:\d{2}")
        self.assertIn("☀️ Nascer do sol", summary["summary"])
        self.assertIn("🌙 noite", summary["summary"])

    def test_resolve_query_dates_supports_relative_days(self) -> None:
        service = self._build_service("Date,Hour,Minute,Height\n")

        resolved = service.resolve_query_dates(
            "Quais são as marés de ontem, hoje, amanhã e depois de amanhã?",
            reference_date=date(2026, 4, 24),
        )

        self.assertEqual(
            resolved,
            [
                date(2026, 4, 23),
                date(2026, 4, 24),
                date(2026, 4, 25),
                date(2026, 4, 26),
            ],
        )

    def test_resolve_query_dates_supports_natural_portuguese_dates(self) -> None:
        service = self._build_service("Date,Hour,Minute,Height\n")

        resolved = service.resolve_query_dates(
            "Preciso das marés para dia 5 maio e 7 de junho de 26.",
            reference_date=date(2026, 4, 24),
        )

        self.assertEqual(
            resolved,
            [
                date(2026, 5, 5),
                date(2026, 6, 7),
            ],
        )

    def test_resolve_query_dates_supports_numeric_formats(self) -> None:
        service = self._build_service("Date,Hour,Minute,Height\n")

        resolved = service.resolve_query_dates(
            "Marés para 25/04, 26-04-26 e 2026/04/27.",
            reference_date=date(2026, 4, 24),
        )

        self.assertEqual(
            resolved,
            [
                date(2026, 4, 25),
                date(2026, 4, 26),
                date(2026, 4, 27),
            ],
        )

    def test_resolve_query_dates_supports_next_weekday(self) -> None:
        service = self._build_service("Date,Hour,Minute,Height\n")

        resolved = service.resolve_query_dates(
            "Quero ver as marés para a próxima segunda-feira.",
            reference_date=date(2026, 4, 24),
        )

        self.assertEqual(resolved, [date(2026, 4, 27)])


if __name__ == "__main__":
    unittest.main()
