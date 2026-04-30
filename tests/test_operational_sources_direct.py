from __future__ import annotations

import unittest

from flask import Flask

from core import services
from core.operational_sources import answer_direct_operational_query


class FakeStore:
    def __init__(self, activity: dict) -> None:
        self.activity = activity

    def get_port_activity_snapshot(self, window_days: int = 5) -> dict:
        return self.activity


class FakeWeatherService:
    enabled = True

    forecast = {
        "location": {"name": "Setúbal", "localtime": "2026-04-30 10:40"},
        "current": {
            "condition": "Parcialmente nublado",
            "temp_c": 18,
            "wind_kts": 7.4,
            "gust_kts": 9.3,
            "wind_dir": "S",
            "humidity": 73,
            "vis_km": 10,
            "precip_mm": 0.02,
        },
        "forecast_days": [
            {
                "date": "2026-04-30",
                "date_label": "30/04/2026",
                "condition": "Parcialmente nublado",
                "min_temp_c": 14,
                "max_temp_c": 21,
                "rain_mm": 0.4,
                "sunrise": "06:40",
                "sunset": "20:23",
                "daylight_duration_label": "13h 43m",
                "night_duration_label": "10h 17m",
                "moonrise": "18:12",
                "moonset": "05:28",
                "moon_phase": "Full Moon",
                "moon_phase_icon": "🌕",
                "moon_phase_label": "Lua cheia",
                "moon_illumination": "98",
                "max_wind_kts": 14,
                "max_gust_kts": 20,
            },
            {
                "date": "2026-05-01",
                "date_label": "01/05/2026",
                "condition": "Céu limpo",
                "min_temp_c": 13,
                "max_temp_c": 22,
                "rain_mm": 0,
                "sunrise": "06:39",
                "sunset": "20:24",
                "max_wind_kts": 12,
                "max_gust_kts": 18,
            },
        ],
        "hourly_groups": [
            {
                "date": "2026-04-30",
                "date_label": "30/04/2026",
                "hours": [
                    {"timestamp": "2026-04-30 11:00", "time": "11:00", "condition": "Nublado", "temp_c": 19, "wind_kts": 8, "gust_kts": 12, "wind_dir": "S", "chance_of_rain": 10},
                    {"timestamp": "2026-04-30 12:00", "time": "12:00", "condition": "Abertas", "temp_c": 20, "wind_kts": 10, "gust_kts": 15, "wind_dir": "SW", "chance_of_rain": 5},
                ],
            },
            {
                "date": "2026-05-01",
                "date_label": "01/05/2026",
                "hours": [
                    {"timestamp": "2026-05-01 09:00", "time": "09:00", "condition": "Céu limpo", "temp_c": 16, "wind_kts": 7, "gust_kts": 11, "wind_dir": "NW", "chance_of_rain": 0},
                ],
            },
        ],
    }

    def get_forecast(self, days: int = 3) -> dict:
        return self.forecast

    def context_for_question(self, question: str) -> dict:
        return {"document": "WeatherAPI Setúbal", "retrieval_mode": "live_api", "snippet": "forecast", "text": "forecast"}

    def context_source(self) -> dict:
        return self.context_for_question("")

    def _resolve_query_dates(self, question, reference_date):
        clean = (question or "").lower()
        if "amanh" in clean:
            return ["2026-05-01"]
        if "hoje" in clean:
            return ["2026-04-30"]
        return []

    def _resolve_query_times(self, question):
        return []


class OperationalSourcesDirectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = services.store
        self.previous_weather_service = services.weather_service
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.activity = {
            "stats": {"occupied_slot_count": 1, "slot_capacity_count": 36},
            "arrivals": [],
            "in_port": [
                {
                    "id": "pc1",
                    "reference_code": "PTSET26ELBT81C3A1",
                    "vessel_name": "ELBTOWER",
                    "vessel_imo": "9876543",
                    "vessel_call_sign": "ELBT9",
                    "vessel_flag": "PT",
                    "ship_type_label": "Carga geral",
                    "ship_loa_label": "120",
                    "ship_beam_label": "18",
                    "ship_gt_label": "8000",
                    "ship_dwt_label": "12000",
                    "ship_max_draft_label": "7.5",
                    "ship_bow_thruster_label": "Sim",
                    "ship_stern_thruster_label": "Não",
                    "status": "in_port",
                    "berth_label": "TMS 2 - Posição A",
                    "last_port": "Hamburgo",
                    "next_port": "Lisboa",
                    "agent_label": "Duarte Gomes",
                    "agent_profile": {"organization": "Navex Setúbal"},
                }
            ],
            "departed": [],
            "aborted": [],
            "departure_candidates": [],
            "planned_maneuvers": [],
            "archived_maneuvers": [
                {
                    "port_call_id": "pc1",
                    "reference_code": "PTSET26ELBT81C3A1",
                    "vessel_name": "ELBTOWER",
                    "maneuver_id": "dep-12345678",
                    "maneuver_type": "departure",
                    "maneuver_label": "Sair",
                    "situation_label": "Concluída",
                    "situation_class": "completed",
                    "date_label": "22 abril 2026",
                    "date_value": "2026-04-22T02:00:00+01:00",
                    "actual_value": "2026-04-22T02:00:00+01:00",
                    "actual_label": "02:00",
                    "local_origin": "TMS 2 - Posição A",
                    "local_destination": "Barcelona",
                    "agent_label": "Duarte Gomes",
                    "agent_profile": {"organization": "Navex Setúbal"},
                    "validated_by_label": "Piloto Validador",
                    "validated_by_profile": {"full_name": "Piloto Validador"},
                    "executed_by_label": "Piloto Executor",
                    "executed_by_profile": {"full_name": "Piloto Executor"},
                    "tug_count_label": "2",
                    "constraint_badges": [],
                }
            ],
            "archived_scales": [],
            "maneuvers": [],
            "planned_groups": [],
        }
        services.store = FakeStore(self.activity)
        services.weather_service = FakeWeatherService()

    def tearDown(self) -> None:
        services.store = self.previous_store
        services.weather_service = self.previous_weather_service

    def _answer(self, question: str) -> str:
        with self.app.test_request_context("/"):
            payload = answer_direct_operational_query(question)
        self.assertIsNotNone(payload)
        return payload["answer"]

    def test_maneuver_id_uses_maneuver_id_not_scale_reference(self) -> None:
        answer = self._answer("Qual o id da manobra de saída do ELBTOWER dia 22 de abril?")

        self.assertIn("dep-12345678", answer)
        self.assertIn("PTSET26ELBT81C3A1", answer)

    def test_maneuver_approver_answer_uses_validated_by(self) -> None:
        answer = self._answer("Quem aprovou a manobra do ELBTOWER para sair dia 22 de abril?")

        self.assertIn("Piloto Validador", answer)
        self.assertIn("dep-12345678", answer)

    def test_agent_agency_answer_uses_profile_organization(self) -> None:
        answer = self._answer("O Duarte Gomes trabalha para que agência?")

        self.assertIn("Navex Setúbal", answer)

    def test_agent_lookup_mentions_agency(self) -> None:
        answer = self._answer("Qual era o agente do ELBTOWER na saída dia 22 de abril?")

        self.assertIn("Duarte Gomes", answer)
        self.assertIn("Navex Setúbal", answer)

    def test_vessel_detail_answer_includes_profile_location_and_maneuver(self) -> None:
        answer = self._answer("Podes dar dados do navio ELBTOWER?")

        self.assertIn("GT 8000", answer)
        self.assertIn("DWT 12000", answer)
        self.assertIn("TMS 2 - Posição A", answer)
        self.assertIn("dep-12345678", answer)
        self.assertIn("Navex Setúbal", answer)

    def test_daylight_answer_uses_weather_astro_data(self) -> None:
        answer = self._answer("Qual o período luminoso para hoje?")

        self.assertIn("06:40", answer)
        self.assertIn("20:23", answer)
        self.assertIn("13h 43m", answer)

    def test_moon_answer_uses_weather_astro_data(self) -> None:
        answer = self._answer("Qual a fase da lua hoje?")

        self.assertIn("Lua cheia", answer)
        self.assertIn("98%", answer)

    def test_today_forecast_includes_next_hours_summary(self) -> None:
        answer = self._answer("Quais as previsões meteorológicas para hoje?")

        self.assertIn("Resumo das próximas horas", answer)
        self.assertIn("vento", answer)
        self.assertIn("rajadas", answer)

    def test_next_days_forecast_includes_wind_and_gusts(self) -> None:
        answer = self._answer("Meteo próximos dias")

        self.assertIn("Previsão geral", answer)
        self.assertIn("vento médio", answer)
        self.assertIn("rajadas", answer)


if __name__ == "__main__":
    unittest.main()
