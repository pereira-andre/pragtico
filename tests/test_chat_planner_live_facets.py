from __future__ import annotations

import unittest

from core.chat_planner import build_chat_execution_plan


class ChatPlannerLiveFacetTests(unittest.TestCase):
    def test_daylight_query_uses_weather_live_facet(self) -> None:
        plan = build_chat_execution_plan("Qual o período luminoso para hoje?")

        self.assertIn("weather", plan.live_facets)
        self.assertEqual(plan.primary_intent, "live_environment")

    def test_moon_phase_query_uses_weather_live_facet(self) -> None:
        plan = build_chat_execution_plan("Qual a fase da lua hoje?")

        self.assertIn("weather", plan.live_facets)
        self.assertEqual(plan.primary_intent, "live_environment")

    def test_today_forecast_query_uses_weather_live_facet(self) -> None:
        plan = build_chat_execution_plan("Quais as previsões meteorológicas para hoje?")

        self.assertIn("weather", plan.live_facets)
        self.assertEqual(plan.primary_intent, "live_environment")

    def test_weather_typo_next_hours_uses_weather_live_facet(self) -> None:
        plan = build_chat_execution_plan("previsao metrologica proximas horas")

        self.assertIn("weather", plan.live_facets)
        self.assertEqual(plan.weather_mode, "timeline")
        self.assertEqual(plan.primary_intent, "live_environment")

    def test_plain_four_digit_maneuver_hour_uses_tide_live_facet(self) -> None:
        plan = build_chat_execution_plan("Marquei manobra de entrada para a Secil E as 1925. Está correta a hora?")

        self.assertIn("tides", plan.live_facets)
        self.assertTrue(plan.requires_live_reasoning)


if __name__ == "__main__":
    unittest.main()
