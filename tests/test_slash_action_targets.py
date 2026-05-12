from __future__ import annotations

import unittest

from core import services
from core.operational_actions import finalize_operational_proposal
from core.operational_test_suite import OperationalFlowSuite
from domain.chat_action_config import SLASH_COMMAND_ALIASES
from domain.chat_actions import build_slash_help, format_action_summary, parse_slash_command, proposal_missing_field_labels


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

    def test_maneuver_commands_with_id_do_not_require_ref_or_type(self) -> None:
        cases = [
            "/aprovar\nID da manobra: 391513b3",
            "/apagar-manobra\nID da manobra: 391513b3",
            (
                "/registar-manobra\n"
                "ID da manobra: 391513b3\n"
                "Início da manobra: 01/05/2026, 18:00\n"
                "Fim da manobra: 01/05/2026, 19:00\n"
                "Calado: 7.2"
            ),
            (
                "/editar-registo-manobra\n"
                "ID da manobra: 391513b3\n"
                "Calado: 7.3\n"
                "Motivo da alteração: correção"
            ),
            "/apagar-registo-manobra\nID da manobra: 391513b3",
            "/abortar\nID da manobra: 391513b3\nMotivo: indisponibilidade",
        ]

        for command in cases:
            with self.subTest(command=command.splitlines()[0]):
                proposal = self._proposal(command)
                self.assertNotIn("ref ou nome do navio", proposal["missing_fields"])
                self.assertNotIn("tipo de manobra", proposal["missing_fields"])

    def test_scale_commands_by_ref_do_not_require_full_scale_identity(self) -> None:
        edit_proposal = self._proposal(
            "/editar-escala\n"
            "Ref: PTSET26ABCD1234\n"
            "ETA de chegada: 02/05/2026, 15:00\n"
            "Motivo da alteração: correção de ETA"
        )
        delete_proposal = self._proposal("/apagar-escala\nRef: PTSET26ABCD1234")

        self.assertEqual(edit_proposal["missing_fields"], [])
        self.assertEqual(delete_proposal["missing_fields"], [])

    def test_agent_cannot_delete_scale_by_command(self) -> None:
        parsed = parse_slash_command("/apagar-escala\nRef: PTSET26ABCD1234", "agente")

        self.assertEqual(parsed["intent"], "unsupported")
        self.assertIn("não está autorizada", parsed["answer"])
        self.assertNotIn("Ref:", parsed["answer"])
        self.assertNotIn("/apagar-escala", build_slash_help("agente"))
        self.assertIn("/apagar-escala", build_slash_help("admin"))

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

    def test_multiline_edit_maneuver_keeps_blank_fields_empty(self) -> None:
        proposal = self._proposal(
            "/editar-manobra\n"
            "ID da manobra: 86683899\n"
            "Ref: \n"
            "Tipo de manobra: saída\n"
            "Hora prevista: 01/05/2026, 19:00\n"
            "Origem: \n"
            "Destino: \n"
            "Calado: \n"
            "Rebocadores: 0\n"
            "Restrições: daylight, gas, estrategico\n"
            "Observações: \n"
            "Motivo da alteração: Carga atrasada\n"
        )

        self.assertEqual(proposal["target"]["maneuver_id"], "86683899")
        self.assertEqual(proposal["target"]["reference_code"], "")
        self.assertEqual(proposal["target"]["maneuver_type"], "departure")
        self.assertEqual(proposal["fields"]["planned_at_local"], "01/05/2026, 19:00")
        self.assertNotIn("origin", proposal["fields"])
        self.assertNotIn("destination", proposal["fields"])
        self.assertNotIn("draft_m", proposal["fields"])
        self.assertEqual(proposal["fields"]["tug_count"], "0")
        self.assertEqual(proposal["fields"]["change_reason"], "Carga atrasada")
        self.assertEqual(proposal["missing_fields"], [])

    def test_multiline_edit_maneuver_missing_reason_is_explicit(self) -> None:
        proposal = self._proposal(
            "/editar-manobra\n"
            "ID da manobra: 86683899\n"
            "Tipo de manobra: saída\n"
            "Hora prevista: 01/05/2026, 19:00\n"
            "Motivo da alteração:\n"
        )

        self.assertEqual(proposal["missing_fields"], ["motivo da alteração"])

    def test_copied_edit_maneuver_template_does_not_count_placeholders_as_values(self) -> None:
        parsed = parse_slash_command(
            "/editar-manobra\n"
            "ID da manobra: \n"
            "Ref: \n"
            "Tipo de manobra: entrada | saída | mudança\n"
            "Hora prevista: DD/MM/AAAA, HH:MM\n"
            "Origem: \n"
            "Destino: \n"
            "Calado: \n"
            "Rebocadores: \n"
            "Restrições: daylight, gas, estrategico\n"
            "Observações: \n"
            "Motivo da alteração:\n",
            "admin",
        )

        self.assertEqual(parsed["intent"], "template")
        proposal = parsed["proposal"]
        self.assertEqual(proposal["target"]["maneuver_type"], "")
        self.assertNotIn("planned_at_local", proposal["fields"])
        self.assertEqual(proposal["fields"]["constraints"], [])
        self.assertEqual(
            proposal["missing_fields"],
            ["motivo da alteração", "ref ou nome do navio", "tipo de manobra", "campo a alterar"],
        )

    def test_action_summary_lists_recognized_and_missing_fields(self) -> None:
        proposal = self._proposal(
            "/editar-manobra\n"
            "ID da manobra: 86683899\n"
            "Tipo de manobra: saída\n"
            "Hora prevista: 01/05/2026, 19:00\n"
            "Motivo da alteração: Carga atrasada\n"
        )

        summary = format_action_summary(proposal)

        self.assertIn("Elementos reconhecidos:", summary)
        self.assertIn("ID da manobra: 86683899", summary)
        self.assertIn("Tipo de manobra: saída", summary)
        self.assertIn("hora prevista: 01/05/2026, 19:00", summary)

    def test_inline_labelled_maneuver_id_prefers_full_label_over_id_alias(self) -> None:
        proposal = self._proposal(
            "/editar-manobra ID da manobra: 86683899 "
            "Tipo de manobra: saída "
            "Hora prevista: 01/05/2026, 19:00 "
            "Motivo da alteração: Carga atrasada"
        )

        self.assertEqual(proposal["target"]["maneuver_id"], "86683899")
        self.assertEqual(proposal["target"]["maneuver_type"], "departure")
        self.assertEqual(proposal["fields"]["change_reason"], "Carga atrasada")
        self.assertEqual(proposal["missing_fields"], [])

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

    def test_every_registered_slash_alias_is_parseable(self) -> None:
        suite = OperationalFlowSuite(actor_username="admin@porto.pt", cleanup_after=False)
        ctx = suite._slash_context()
        failures = []

        for index, (alias, command) in enumerate(sorted(SLASH_COMMAND_ALIASES.items()), start=1):
            sample = suite._slash_sample_for_alias(alias, command, ctx, index)
            parsed = parse_slash_command(sample, "admin")
            expected_intents = suite._slash_expected_intents(command)
            if not isinstance(parsed, dict) or parsed.get("intent") not in expected_intents:
                failures.append(f"/{alias}: {parsed}")

        self.assertEqual(failures, [])

    def test_slash_help_and_unknown_command_are_actionable(self) -> None:
        help_text = build_slash_help("admin")
        for alias in (
            "help",
            "consultar-escala",
            "consultar-manobra",
            "consultar-navio",
            "validar-manobra",
            "registar-escala",
            "editar-escala",
            "criar-manobra",
            "aprovar",
            "registar-manobra",
            "reportar-evento",
            "it",
            "porque",
            "diagnostico",
            "debug",
        ):
            self.assertIn(f"/{alias}", help_text)
        self.assertNotIn("/debug", build_slash_help("piloto"))

        parsed = parse_slash_command("/comando-que-nao-existe", "admin")

        self.assertEqual(parsed["intent"], "help")
        self.assertIn("Comando não reconhecido", parsed["answer"])
        self.assertIn("/help", parsed["answer"])


if __name__ == "__main__":
    unittest.main()
