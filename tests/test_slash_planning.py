from __future__ import annotations

import unittest

from flask import Flask

from core import services
from core.operational_actions import answer_slash_query
from domain.chat_action_templates import build_slash_help
from domain.chat_actions import parse_slash_command


class FakeStore:
    def __init__(self, activity: dict) -> None:
        self.activity = activity

    def get_port_activity_snapshot(self, window_days: int = 30) -> dict:
        return self.activity


class SlashPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = services.store
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.activity = {
            "stats": {},
            "arrivals": [],
            "in_port": [],
            "departed": [],
            "aborted": [],
            "planned_maneuvers": [
                {
                    "vessel_name": "WAY FORWARD",
                    "maneuver_id": "768ab23c-d9dc-4d5d-b722-49915d90a739",
                    "maneuver_label": "Entrar",
                    "situation_label": "Pendente",
                    "date_value": "2026-05-02T15:00:00+01:00",
                    "local_origin": "Southampton",
                    "local_destination": "Cais 10 / Autoeuropa",
                    "agent_label": "Administrador",
                    "agent_profile": {"organization": "APSS"},
                },
                {
                    "vessel_name": "GALBOT",
                    "maneuver_id": "391513b3-f521-4b90-9cdf-aafcc183b756",
                    "maneuver_label": "Sair",
                    "situation_label": "Aprovada",
                    "date_label": "01/05/2026",
                    "planned_label": "09:30",
                    "date_value": "2026-05-01T09:30:00+01:00",
                    "local_origin": "Tanquisado (lado jusante)",
                    "local_destination": "Lisboa",
                    "agent_label": "Duarte Gomes",
                    "agent_profile": {"organization": "Navex Setúbal"},
                },
            ],
        }
        services.store = FakeStore(self.activity)

    def tearDown(self) -> None:
        services.store = self.previous_store

    def test_parse_planeamento_command_as_query(self) -> None:
        parsed = parse_slash_command("/planeamento", "piloto")

        self.assertEqual(parsed["intent"], "query")
        self.assertEqual(parsed["command"], "planning")

    def test_planeamento_lists_all_planned_maneuvers_with_state_and_agency(self) -> None:
        with self.app.test_request_context("/"):
            payload = answer_slash_query("planning", "", "piloto")

        answer = payload["answer"]
        self.assertEqual(payload["answer_origin"], "slash_planning")
        self.assertIn("Planeamento de manobras (2):", answer)
        self.assertIn("01/05/2026 09:30 · Sair · GALBOT", answer)
        self.assertIn("Estado: Aprovada", answer)
        self.assertIn("Manobra: 391513B3", answer)
        self.assertIn("Agente/agência: Duarte Gomes (Navex Setúbal)", answer)
        self.assertIn("02/05/2026 15:00 · Entrar · WAY FORWARD", answer)
        self.assertIn("Agente/agência: Administrador (APSS)", answer)
        self.assertLess(answer.index("GALBOT"), answer.index("WAY FORWARD"))

    def test_planeamento_empty_state(self) -> None:
        self.activity["planned_maneuvers"] = []

        with self.app.test_request_context("/"):
            payload = answer_slash_query("planning", "", "piloto")

        self.assertEqual(payload["answer"], "Não há manobras no planeamento neste momento.")

    def test_help_mentions_planeamento(self) -> None:
        self.assertIn("/planeamento", build_slash_help("piloto"))


if __name__ == "__main__":
    unittest.main()
