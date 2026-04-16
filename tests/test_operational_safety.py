import unittest
from pathlib import Path

from domain.operational_safety import (
    build_operational_safety_source,
    build_weather_safety_status_lines,
    evaluate_weather_safety,
    load_operational_safety_limits,
)


KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"


class OperationalSafetyTests(unittest.TestCase):
    def test_fog_question_includes_all_maneuvers_suspended_rule(self) -> None:
        source = build_operational_safety_source(
            "Com nevoeiro no porto posso fazer a manobra?",
            str(KNOWLEDGE_DIR),
        )

        self.assertIsNotNone(source)
        self.assertEqual(source["retrieval_mode"], "operational_safety_limits")
        self.assertIn("todas as manobras ficam suspensas", source["snippet"])
        self.assertIn("visibilidade seja restaurada", source["snippet"])

    def test_fog_current_weather_suspends_maneuvers(self) -> None:
        forecast = {"current": {"condition": "Fog", "wind_kts": 8.0, "gust_kts": 10.0, "vis_km": 0.8}}

        lines = build_weather_safety_status_lines(forecast, str(KNOWLEDGE_DIR))

        self.assertTrue(lines)
        self.assertIn("Manobras suspensas neste momento", "\n".join(lines))
        self.assertIn("nevoeiro", "\n".join(lines))

    def test_wind_above_30_kts_suspends_maneuvers(self) -> None:
        forecast = {"current": {"condition": "Clear", "wind_kts": 31.0, "gust_kts": 35.0, "vis_km": 10}}

        status = evaluate_weather_safety(
            forecast,
            load_operational_safety_limits(str(KNOWLEDGE_DIR)),
        )

        self.assertTrue(status["suspended"])
        self.assertIn("superior a 30", " ".join(status["reasons"]))

    def test_wind_between_25_and_30_holds_after_prior_suspension(self) -> None:
        forecast = {"current": {"condition": "Clear", "wind_kts": 27.0, "gust_kts": 29.0, "vis_km": 10}}

        lines = build_weather_safety_status_lines(forecast, str(KNOWLEDGE_DIR))

        self.assertTrue(lines)
        self.assertIn("manter suspenso", "\n".join(lines))
        self.assertIn("abaixo de 25", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
