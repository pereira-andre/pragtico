from __future__ import annotations

import unittest

from core import services
from core.operational_actions import finalize_operational_proposal
from domain.chat_actions import parse_slash_command, proposal_missing_field_labels


class FakeStore:
    def __init__(self, port_call: dict) -> None:
        self.port_call = port_call

    def get_port_call(self, port_call_id: str) -> dict:
        if port_call_id != self.port_call["id"]:
            raise KeyError(port_call_id)
        return self.port_call


class SlashActionTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = services.store

    def tearDown(self) -> None:
        services.store = self.previous_store

    def _proposal(self, command: str, role: str = "admin") -> dict:
        parsed = parse_slash_command(command, role)
        self.assertEqual(parsed["intent"], "action")
        return parsed["proposal"]

    def _port_call(self) -> dict:
        return {
            "id": "pc1",
            "reference_code": "PTSET26ABCD1234",
            "vessel_name": "GALBOT",
            "maneuver_history": [
                {
                    "id": "391513b3-f521-4b90-9cdf-aafcc183b756",
                    "type": "departure",
                    "state": "pending",
                    "planned_at": "2026-05-02T10:00:00+01:00",
                    "planned_input_value": "2026-05-02T10:00",
                    "origin": "Tanquisado (lado jusante)",
                    "destination": "Lisboa",
                    "planned_draft_m": "7.0",
                    "tug_count": "1",
                    "constraints": [],
                    "plan_observations": "",
                }
            ],
        }

    def test_scale_reference_positional_is_not_treated_as_maneuver_id(self) -> None:
        proposal = self._proposal("/editar-escala PTSET26ABCD1234")

        self.assertEqual(proposal["target"]["reference_code"], "PTSET26ABCD1234")
        self.assertEqual(proposal["target"]["maneuver_id"], "")

    def test_non_ptset_scale_reference_positional_stays_scale_reference(self) -> None:
        proposal = self._proposal("/editar-escala REF123")

        self.assertEqual(proposal["target"]["reference_code"], "REF123")
        self.assertEqual(proposal["target"]["maneuver_id"], "")

    def test_maneuver_id_positional_is_not_treated_as_scale_reference(self) -> None:
        proposal = self._proposal("/editar-manobra 391513b3")

        self.assertEqual(proposal["target"]["maneuver_id"], "391513b3")
        self.assertEqual(proposal["target"]["reference_code"], "")

    def test_edit_scale_with_ref_only_asks_for_change_not_full_scale(self) -> None:
        proposal = self._proposal("/editar-escala PTSET26ABCD1234")

        self.assertEqual(proposal["missing_fields"], ["motivo da alteração", "campo a alterar"])
        self.assertNotIn("ETA", proposal["missing_fields"])
        self.assertNotIn("nome do navio", proposal["missing_fields"])

    def test_create_maneuver_with_scale_ref_does_not_set_maneuver_id(self) -> None:
        proposal = self._proposal("/criar-manobra PTSET26ABCD1234 saída")

        self.assertEqual(proposal["action"], "schedule_departure")
        self.assertEqual(proposal["target"]["reference_code"], "PTSET26ABCD1234")
        self.assertEqual(proposal["target"]["maneuver_id"], "")
        self.assertEqual(proposal["target"]["maneuver_type"], "departure")
        self.assertEqual(proposal["missing_fields"], ["próximo porto", "hora prevista de saída"])

    def test_edit_scale_with_one_change_does_not_require_other_scale_fields(self) -> None:
        missing = proposal_missing_field_labels(
            "edit_port_call",
            {"eta_local": "2026-05-02T15:00", "change_reason": "correção de ETA"},
            {"reference_code": "PTSET26ABCD1234"},
        )

        self.assertEqual(missing, [])

    def test_edit_maneuver_plan_by_id_does_not_require_ref_type_or_time(self) -> None:
        missing = proposal_missing_field_labels(
            "edit_maneuver_plan",
            {"destination": "Lisboa", "change_reason": "correção de destino"},
            {"maneuver_id": "391513b3"},
        )

        self.assertEqual(missing, [])

    def test_edit_maneuver_report_by_id_does_not_require_full_report(self) -> None:
        missing = proposal_missing_field_labels(
            "edit_maneuver_report",
            {"draft_m": "7.1", "change_reason": "correção de calado"},
            {"maneuver_id": "391513b3"},
        )

        self.assertEqual(missing, [])

    def test_edit_maneuver_with_only_reason_asks_for_field_to_change(self) -> None:
        missing = proposal_missing_field_labels(
            "edit_maneuver_plan",
            {"change_reason": "correção"},
            {"maneuver_id": "391513b3"},
        )

        self.assertEqual(missing, ["campo a alterar"])

    def test_finalize_edit_maneuver_by_id_keeps_existing_time_implicit(self) -> None:
        port_call = self._port_call()
        services.store = FakeStore(port_call)
        proposal = self._proposal(
            "/editar-manobra\nID da manobra: 391513b3\nMotivo da alteração: correção"
        )

        finalized = finalize_operational_proposal(proposal, [port_call])

        self.assertEqual(finalized["target"]["maneuver_type"], "departure")
        self.assertEqual(finalized["target"]["maneuver_id"], "391513b3-f521-4b90-9cdf-aafcc183b756")
        self.assertNotIn("planned_at_local", finalized["fields"])
        self.assertEqual(finalized["missing_fields"], ["campo a alterar"])

    def test_finalize_edit_maneuver_by_id_with_one_change_is_ready(self) -> None:
        port_call = self._port_call()
        services.store = FakeStore(port_call)
        proposal = self._proposal(
            "/editar-manobra\nID da manobra: 391513b3\nDestino: Setúbal\nMotivo da alteração: correção"
        )

        finalized = finalize_operational_proposal(proposal, [port_call])

        self.assertEqual(finalized["target"]["maneuver_type"], "departure")
        self.assertEqual(finalized["fields"]["destination"], "Setúbal")
        self.assertEqual(finalized["missing_fields"], [])


if __name__ == "__main__":
    unittest.main()
