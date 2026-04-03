import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["APP_STORAGE_BACKEND"] = "local"
os.environ["RAG_INDEX_BACKEND"] = "local"
os.environ["MANEUVER_CASE_CAPTURE_ENVIRONMENT"] = "0"

import app
from core import services
from domain.chat_actions import normalize_action_candidate
from flask import session
from core.helpers import answer_direct_operational_query, build_operational_chat_sources, build_scale_context
from storage import LocalStore


class OperationalFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))
        self.original_store = services.store
        services.store = self.store

    def tearDown(self) -> None:
        services.store = self.original_store
        self.temp_dir.cleanup()

    def _create_entry(self, *, notes: str, eta: str = "2026-03-24T05:30:00+00:00") -> dict:
        return self.store.create_port_call(
            vessel_name="BELITAKI",
            eta=eta,
            created_by="admin",
            berth="TMS 2",
            last_port="Leixoes",
            next_port="Barcelona",
            notes=notes,
            vessel_imo="9152923",
            vessel_call_sign="D5OC2",
            vessel_flag="Liberia",
            vessel_type="Porta-contentores",
            vessel_loa_m="179.23",
            vessel_beam_m="25.3",
            vessel_gt_t="16281",
            vessel_max_draft_m="9.94",
            vessel_dwt_t="22330",
        )

    def _move_port_call_in_port(self, port_call_id: str) -> dict:
        self.store.approve_port_call(port_call_id, decided_by="admin")
        return self.store.mark_port_call_arrived(
            port_call_id,
            arrived_at="2026-03-24T06:00:00+00:00",
            updated_by="admin",
        )

    def _archive_entry(
        self,
        *,
        vessel_name: str,
        eta: str,
        arrived_at: str,
        started_at: str,
        finished_at: str,
    ) -> dict:
        port_call = self.store.create_port_call(
            vessel_name=vessel_name,
            eta=eta,
            created_by="admin",
            berth="TMS 2",
            last_port="Leixoes",
            next_port="Barcelona",
            notes="Arquivo de teste.",
            vessel_imo="9152923",
            vessel_call_sign="D5OC2",
            vessel_flag="Liberia",
            vessel_type="Porta-contentores",
            vessel_loa_m="179.23",
            vessel_beam_m="25.3",
            vessel_gt_t="16281",
            vessel_max_draft_m="9.94",
            vessel_dwt_t="22330",
        )
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            port_call["id"],
            arrived_at=arrived_at,
            updated_by="admin",
        )
        self.store.attach_entry_report(
            port_call["id"],
            updated_by="admin",
            maneuver_started_at=started_at,
            maneuver_finished_at=finished_at,
            draft_m="9.94",
            notes="Entrada concluída.",
        )
        return self.store.get_port_call(port_call["id"])

    def _login_client_as_admin(self, client) -> None:
        with client.session_transaction() as flask_session:
            flask_session["username"] = "admin"
            flask_session["role"] = "admin"

    def test_complete_entry_with_real_times_only_confirms_maneuver(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            result, message = app.execute_pending_operational_action(
                {
                    "action": "complete_entry",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "entry"},
                    "fields": {
                        "maneuver_started_local": "2026-03-24T10:00",
                        "maneuver_finished_local": "2026-03-24T12:00",
                    },
                },
                username="admin",
                role="admin",
            )

        self.assertIn("entrada confirmada", message.lower())
        self.assertEqual(result["status"], "in_port")
        updated = self.store.get_port_call(port_call["id"])
        entry = next(item for item in updated["maneuver_history"] if item["type"] == "entry")
        self.assertEqual(entry["state"], "completed")
        self.assertFalse(entry.get("report_note"))
        self.assertFalse(entry.get("reported_draft_m"))
        self.assertFalse(entry.get("execution_started_at"))
        self.assertFalse(entry.get("execution_finished_at"))

    def test_finalize_completion_with_real_times_does_not_require_report_fields(self) -> None:
        port_call = self._create_entry(notes="Sem calado planeado")
        self.store.approve_port_call(port_call["id"], decided_by="admin")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "complete_entry",
                "target": {
                    "reference_code": port_call["reference_code"],
                    "vessel_name": "BELITAKI",
                    "maneuver_type": "entry",
                },
                "fields": {
                    "maneuver_started_local": "2026-03-24T10:00",
                    "maneuver_finished_local": "2026-03-24T12:00",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertEqual(finalized["missing_fields"], [])


    def test_create_port_call_with_past_eta_is_rejected(self) -> None:
        with app.app.test_request_context("/"):
            session["role"] = "agente"
            with self.assertRaisesRegex(ValueError, "ETA não pode ser anterior à data/hora presente"):
                app.execute_pending_operational_action(
                    {
                        "action": "create_port_call",
                        "fields": {
                            "vessel_name": "MV SETUBAL PIONEER",
                            "eta_local": "02/04/2025, 06:30",
                            "berth": "Cais 3 – Terminal Multipurpose",
                            "last_port": "Sines (PT)",
                            "next_port": "Leixões (PT)",
                            "vessel_imo": "9876543",
                            "vessel_call_sign": "CQAB7",
                            "vessel_flag": "Portugal",
                            "vessel_type": "General Cargo",
                            "vessel_loa_m": "142.50",
                            "vessel_beam_m": "21.80",
                            "vessel_gt_t": "8950",
                            "vessel_dwt_t": "12400",
                            "vessel_max_draft_m": "7.20",
                            "notes": "Carga geral paletizada.",
                        },
                    },
                    username="admin",
                    role="agente",
                )

    def test_edit_port_call_with_explicit_past_eta_is_rejected(self) -> None:
        port_call = self._create_entry(eta="2026-04-02T05:30:00+00:00", notes="Teste")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            with self.assertRaisesRegex(ValueError, "ETA não pode ser anterior à data/hora presente"):
                app.execute_pending_operational_action(
                    {
                        "action": "edit_port_call",
                        "port_call_id": port_call["id"],
                        "target": {"reference_code": port_call["reference_code"]},
                        "fields": {"eta_local": "02/04/2025, 06:30"},
                    },
                    username="admin",
                    role="admin",
                )

    def test_create_port_call_canonicalizes_known_berth_alias(self) -> None:
        with app.app.test_request_context("/"):
            session["role"] = "agente"
            result, _message = app.execute_pending_operational_action(
                {
                    "action": "create_port_call",
                    "fields": {
                        "vessel_name": "AUTO TESTE",
                        "eta_local": "2026-04-02T06:30",
                        "berth": "Cais 10 Autoeuropa",
                        "last_port": "Sines",
                        "next_port": "Vigo",
                        "vessel_imo": "9876543",
                        "vessel_call_sign": "CQAB7",
                        "vessel_flag": "Portugal",
                        "vessel_type": "General Cargo",
                        "vessel_loa_m": "142.50",
                        "vessel_beam_m": "21.80",
                        "vessel_gt_t": "8950",
                        "vessel_dwt_t": "12400",
                        "vessel_max_draft_m": "7.20",
                        "notes": "Carga geral paletizada.",
                    },
                },
                username="admin",
                role="agente",
            )

        self.assertEqual(result["berth"], "Cais 10 / Autoeuropa")

    def test_schedule_shift_uses_current_berth_as_origin_when_omitted(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            result, _message = app.execute_pending_operational_action(
                {
                    "action": "schedule_shift",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "shift"},
                    "fields": {
                        "planned_shift_at_local": "2026-04-02T08:30",
                        "destination_berth": "Cais 3 Terminal Multipurpose",
                        "draft_m": "9.94",
                        "tug_count": "2",
                        "notes": "Mudança operacional.",
                    },
                },
                username="admin",
                role="admin",
            )

        shift = next(item for item in result["maneuver_history"] if item["type"] == "shift")
        self.assertEqual(shift["origin"], "TMS 2")
        self.assertEqual(shift["destination"], "TMS 1 - Cais 3")
        self.assertIn("Origem: TMS 2", shift["plan_note"])

    def test_schedule_departure_uses_current_berth_as_origin_when_omitted(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            result, _message = app.execute_pending_operational_action(
                {
                    "action": "schedule_departure",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "departure"},
                    "fields": {
                        "planned_departure_at_local": "2026-04-02T09:10",
                        "next_port": "Barcelona",
                        "draft_m": "9.94",
                        "tug_count": "2",
                        "notes": "Saída operacional.",
                    },
                },
                username="admin",
                role="admin",
            )

        departure = next(item for item in result["maneuver_history"] if item["type"] == "departure")
        self.assertEqual(departure["origin"], "TMS 2")
        self.assertEqual(departure["destination"], "Barcelona")
        self.assertIn("Origem: TMS 2", departure["plan_note"])

    def test_scale_context_exposes_similar_cases_for_matching_maneuver_profile(self) -> None:
        historical = self._create_entry(notes="Entrada histórica", eta="2026-03-20T05:30:00+00:00")
        self.store.approve_port_call(historical["id"], decided_by="admin", approval_note="Aprovada sem incidentes.")
        historical_done = self.store.mark_port_call_arrived(
            historical["id"],
            arrived_at="2026-03-20T06:00:00+00:00",
            updated_by="admin",
        )
        historical_entry = next(item for item in historical_done["maneuver_history"] if item["type"] == "entry")
        self.store.update_maneuver_case_feedback(
            maneuver_id=historical_entry["id"],
            feedback_status="approved",
            feedback_note="Boa referência para este perfil.",
            feedback_by="admin",
        )

        current = self._create_entry(notes="Nova entrada", eta="2026-04-05T05:30:00+00:00")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            scale = build_scale_context(self.store.get_port_call(current["id"]))

        entry = next(item for item in scale["maneuvers"] if item["type"] == "entry")
        self.assertTrue(entry["similar_cases"])
        self.assertEqual(entry["similar_cases"][0]["reference_code"], historical["reference_code"])
        self.assertNotEqual(entry["similar_cases"][0]["maneuver_id"], entry["id"])
        self.assertEqual(entry["casebook_recommendation"]["status_key"], "positive")
        self.assertIn("Feedback", entry["casebook_recommendation"]["title"])
        self.assertEqual(entry["similar_cases"][0]["feedback_status"], "approved")
        self.assertTrue(entry["analysis_checklist"])
        self.assertTrue(any(item["title"] == "Disponibilidade do destino" for item in entry["analysis_checklist"]))

    def test_operational_chat_sources_include_casebook_when_scale_is_identified(self) -> None:
        historical = self._create_entry(notes="Entrada histórica", eta="2026-03-19T05:30:00+00:00")
        self.store.approve_port_call(historical["id"], decided_by="admin", approval_note="Aprovada com 2 rebocadores.")
        self.store.mark_port_call_arrived(
            historical["id"],
            arrived_at="2026-03-19T06:00:00+00:00",
            updated_by="admin",
        )

        current = self._create_entry(notes="Nova entrada", eta="2026-04-06T05:30:00+00:00")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            sources = build_operational_chat_sources(
                f"O que achas da entrada da escala {current['reference_code']}?"
            )

        casebook_sources = [item for item in sources if item.get("retrieval_mode") == "maneuver_casebook"]
        self.assertTrue(casebook_sources)
        self.assertIn(historical["reference_code"], casebook_sources[0]["snippet"])
        self.assertIn("recomendação histórica", casebook_sources[0]["snippet"])

    def test_direct_operational_query_returns_none_for_opinion_question(self) -> None:
        historical = self._create_entry(notes="Entrada histórica", eta="2026-03-18T05:30:00+00:00")
        self.store.approve_port_call(historical["id"], decided_by="admin", approval_note="Aprovada sem incidentes.")
        historical_done = self.store.mark_port_call_arrived(
            historical["id"],
            arrived_at="2026-03-18T06:00:00+00:00",
            updated_by="admin",
        )
        historical_entry = next(item for item in historical_done["maneuver_history"] if item["type"] == "entry")
        self.store.update_maneuver_case_feedback(
            maneuver_id=historical_entry["id"],
            feedback_status="approved",
            feedback_note="Boa referência.",
            feedback_by="admin",
        )

        current = self._create_entry(notes="Nova entrada", eta="2026-04-07T05:30:00+00:00")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            answer = answer_direct_operational_query(f"O que achas da entrada da escala {current['reference_code']}?")

        self.assertIsNone(answer)

    def test_complete_entry_rejects_occupied_quay(self) -> None:
        occupied = self._create_entry(notes="Primeira escala")
        self._move_port_call_in_port(occupied["id"])
        waiting = self._create_entry(notes="Segunda escala", eta="2026-04-02T05:30:00+00:00")
        self.store.approve_port_call(waiting["id"], decided_by="admin")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            with self.assertRaisesRegex(ValueError, "já está ocupado"):
                app.execute_pending_operational_action(
                    {
                        "action": "complete_entry",
                        "port_call_id": waiting["id"],
                        "target": {"maneuver_type": "entry"},
                        "fields": {},
                    },
                    username="admin",
                    role="admin",
                )

    def test_admin_can_edit_completed_maneuver_plan_for_archive_corrections(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            port_call["id"],
            arrived_at="2026-03-24T06:00:00+00:00",
            updated_by="admin",
        )
        entry = next(item for item in self.store.get_port_call(port_call["id"])["maneuver_history"] if item["type"] == "entry")

        updated = self.store.edit_maneuver_plan(
            port_call["id"],
            entry["id"],
            updated_by="admin",
            actor_role="admin",
            planned_at="2026-03-24T05:45:00+00:00",
            origin="Sines",
            destination="TMS 1 - Cais 3",
            draft_m="9.94",
            tug_count="2",
            constraints=["daylight"],
            plan_note="Correção histórica do planeamento.",
            change_reason="Correção de arquivo.",
        )

        updated_entry = next(item for item in updated["maneuver_history"] if item["id"] == entry["id"])
        self.assertEqual(updated_entry["state"], "completed")
        self.assertEqual(updated_entry["destination"], "TMS 1 - Cais 3")
        self.assertTrue(updated_entry["change_log"])

    def test_entry_report_accepts_scale_reference_passed_in_id_field(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "entry_report",
                "target": {
                    "maneuver_id": port_call["reference_code"],
                    "maneuver_type": "entry",
                },
                "fields": {
                    "maneuver_started_local": "2026-03-24T10:00",
                    "maneuver_finished_local": "2026-03-24T12:00",
                    "draft_m": "9.94",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertEqual(finalized["port_call_id"], port_call["id"])
        self.assertEqual(finalized["target"]["reference_code"], port_call["reference_code"])
        self.assertEqual(finalized["target"]["maneuver_id"], "")
        self.assertEqual(finalized["missing_fields"], [])

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            result, message = app.execute_pending_operational_action(
                finalized,
                username="admin",
                role="admin",
            )

        self.assertIn("registo de entrada guardado", message.lower())
        self.assertEqual(result["status"], "in_port")

    def test_entry_report_accepts_maneuver_id_passed_in_reference_field(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        entry = next(item for item in self.store.get_port_call(port_call["id"])["maneuver_history"] if item["type"] == "entry")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "entry_report",
                "target": {
                    "reference_code": entry["id"][:8].upper(),
                    "maneuver_type": "entry",
                },
                "fields": {
                    "maneuver_started_local": "2026-03-24T10:00",
                    "maneuver_finished_local": "2026-03-24T12:00",
                    "draft_m": "9.94",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertEqual(finalized["port_call_id"], port_call["id"])
        self.assertEqual(finalized["target"]["reference_code"], port_call["reference_code"])
        self.assertEqual(finalized["target"]["maneuver_id"], entry["id"])
        self.assertEqual(finalized["missing_fields"], [])

    def test_direct_operational_query_returns_real_maneuver_id_not_scale_reference(self) -> None:
        port_call = self.store.create_port_call(
            vessel_name="OCEAN BULKER",
            eta="2026-03-29T15:10:00+00:00",
            created_by="admin",
            berth="Teporset",
            last_port="Casablanca",
            next_port="Setubal",
            notes="Registo do agente · Entrada\nCalado: 10.8",
            vessel_imo="9999999",
            vessel_call_sign="CQ1234",
            vessel_flag="Malta",
            vessel_type="Graneleiro",
            vessel_loa_m="179.23",
            vessel_beam_m="25.3",
            vessel_gt_t="16281",
            vessel_max_draft_m="10.8",
            vessel_dwt_t="22330",
        )
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        refreshed = self.store.get_port_call(port_call["id"])
        entry = next(item for item in refreshed["maneuver_history"] if item["type"] == "entry")

        with app.app.test_request_context("/"):
            session["role"] = "piloto"
            answer = answer_direct_operational_query("qual o id da manobra de entrada do OCEAN BULKER?")

        self.assertIsNotNone(answer)
        self.assertIn(entry["id"][:8].upper(), answer["answer"])
        self.assertNotIn(port_call["reference_code"], answer["answer"])

    def test_completed_maneuver_only_moves_to_archive_after_report(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            app.execute_pending_operational_action(
                {
                    "action": "complete_entry",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "entry"},
                    "fields": {},
                },
                username="admin",
                role="admin",
            )

            snapshot_without_report = self.store.get_port_activity_snapshot(window_days=3650)
            archived_ids = {item["port_call_id"] for item in snapshot_without_report["archived_maneuvers"]}
            planned_ids = {item["port_call_id"] for item in snapshot_without_report["planned_maneuvers"]}

            self.assertNotIn(port_call["id"], archived_ids)
            self.assertIn(port_call["id"], planned_ids)

            app.execute_pending_operational_action(
                {
                    "action": "entry_report",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "entry"},
                    "fields": {
                        "maneuver_started_local": "2026-03-24T10:00",
                        "maneuver_finished_local": "2026-03-24T12:00",
                        "draft_m": "9.94",
                        "notes": "Sem incidentes.",
                    },
                },
                username="admin",
                role="admin",
            )

        snapshot_with_report = self.store.get_port_activity_snapshot(window_days=3650)
        archived_ids = {item["port_call_id"] for item in snapshot_with_report["archived_maneuvers"]}
        planned_ids = {item["port_call_id"] for item in snapshot_with_report["planned_maneuvers"]}
        self.assertIn(port_call["id"], archived_ids)
        self.assertNotIn(port_call["id"], planned_ids)

    def test_aborted_maneuver_moves_to_archive_and_leaves_planning(self) -> None:
        port_call = self._create_entry(
            notes="Registo do agente · Entrada\nCalado: 9.94",
            eta="2026-04-02T05:30:00+00:00",
        )

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            app.execute_pending_operational_action(
                {
                    "action": "abort_entry",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "entry"},
                    "fields": {
                        "aborted_reason": "Cancelada pelo agente.",
                    },
                },
                username="admin",
                role="admin",
            )

        snapshot = self.store.get_port_activity_snapshot(window_days=3650)
        archived_ids = {item["port_call_id"] for item in snapshot["archived_maneuvers"]}
        planned_ids = {item["port_call_id"] for item in snapshot["planned_maneuvers"]}

        self.assertIn(port_call["id"], archived_ids)
        self.assertNotIn(port_call["id"], planned_ids)

    def test_chat_ok_confirms_pending_action(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            proposal = {
                "intent": "action",
                "action": "complete_entry",
                "port_call_id": port_call["id"],
                "target": {
                    "reference_code": port_call["reference_code"],
                    "vessel_name": port_call["vessel_name"],
                    "maneuver_type": "entry",
                },
                "fields": {},
                "missing_fields": [],
            }
            app.save_pending_chat_action("admin", conversation["id"], proposal, "Concluir entrada")

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "ok",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "pending_action_confirmed")
        self.assertIsNone(payload["pending_action"])
        self.assertIn("entrada confirmada", payload["answer"].lower())
        updated = self.store.get_port_call(port_call["id"])
        entry = next(item for item in updated["maneuver_history"] if item["type"] == "entry")
        self.assertEqual(entry["state"], "completed")

    def test_maneuver_archive_defaults_to_latest_available_month(self) -> None:
        older = self._archive_entry(
            vessel_name="BELITAKI FEB",
            eta="2026-02-10T05:30:00+00:00",
            arrived_at="2026-02-10T06:00:00+00:00",
            started_at="2026-02-10T05:40:00+00:00",
            finished_at="2026-02-10T06:00:00+00:00",
        )
        latest = self._archive_entry(
            vessel_name="BELITAKI MAR",
            eta="2026-03-12T05:30:00+00:00",
            arrived_at="2026-03-12T06:00:00+00:00",
            started_at="2026-03-12T05:40:00+00:00",
            finished_at="2026-03-12T06:00:00+00:00",
        )

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get("/maneuvers/archive")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Março 2026", html)
        self.assertIn(latest["reference_code"], html)
        self.assertNotIn(older["reference_code"], html)

    def test_archive_billing_report_uses_filtered_scale_selection(self) -> None:
        archived = self._archive_entry(
            vessel_name="BELITAKI REPORT",
            eta="2026-03-18T05:30:00+00:00",
            arrived_at="2026-03-18T06:00:00+00:00",
            started_at="2026-03-18T05:40:00+00:00",
            finished_at="2026-03-18T06:00:00+00:00",
        )

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get(
                f"/maneuvers/archive/report?year=2026&month=3&selection=scales&scale_ids={archived['id']}"
            )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Relatório de Faturação por Escala", html)
        self.assertIn(archived["reference_code"], html)
        self.assertIn("Imprimir / PDF", html)

    def test_maneuver_estimate_report_renders_formal_document(self) -> None:
        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get(
                "/maneuvers/archive/estimate-report"
                "?vessel_name=MSC%20Lyria"
                "&vessel_type=contentores"
                "&gt=32540"
                "&stay_days=2"
                "&manoeuvres=entry,departure"
                "&surcharges=no_propulsion"
                "&reductions=regular_line"
                "&include_tup=1"
            )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Estimativa Formal de Pilotagem", html)
        self.assertIn("MSC Lyria", html)
        self.assertIn("Imprimir / PDF", html)

    def test_chat_message_feedback_api_returns_updated_label(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="Resposta operacional.",
            )
            response = client.post(
                f"/api/messages/{message['id']}/feedback",
                json={
                    "conversation_id": conversation["id"],
                    "feedback_status": "approved",
                    "feedback_note": "Boa resposta.",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["feedback_status"], "approved")
        self.assertEqual(payload["feedback_note"], "Boa resposta.")
        self.assertTrue(payload["feedback_updated_at"])
        self.assertTrue(payload["feedback_updated_at_label"])

    def test_chat_message_feedback_review_requires_note(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="Resposta operacional.",
            )
            response = client.post(
                f"/api/messages/{message['id']}/feedback",
                json={
                    "conversation_id": conversation["id"],
                    "feedback_status": "review",
                    "feedback_note": "",
                },
            )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("indica a correção", payload["error"].lower())

    def test_repeat_question_with_reviewed_feedback_is_blocked_before_llm(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="user",
                content="qual é a distancia da barra ao outão?",
            )
            reviewed_message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="A distância da saída da Barra até ao Outão é de 3,00 milhas náuticas.",
            )
            self.store.update_message_feedback(
                "admin",
                conversation["id"],
                reviewed_message["id"],
                "review",
                "3,23 milhas náuticas = 5982 m",
            )

            with patch.object(services.rag, "answer") as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "qual é a distancia da barra ao outão?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "review_guard")
        self.assertIn("marcada para revisão", payload["answer"].lower())
        self.assertIn("3,23 milhas náuticas", payload["answer"])
        answer_mock.assert_not_called()

    def test_slash_rules_lists_available_rule_codes(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "/regras",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_rule")
        self.assertIn("015", payload["answer"])
        self.assertIn("IT-015 Fundeadouros", payload["answer"])
        self.assertIn("062", payload["answer"])
        self.assertNotIn("013", payload["answer"])

    def test_slash_rule_without_code_lists_available_rule_codes(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "/regra",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_rule")
        self.assertIn("Regras/instruções disponíveis", payload["answer"])
        self.assertIn("/regra 015", payload["answer"])

    def test_slash_rule_for_missing_code_returns_catalog(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "/regra 013",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_rule")
        self.assertIn("não encontrei a regra 013", payload["answer"].lower())
        self.assertIn("IT-015 Fundeadouros", payload["answer"])

    def test_chat_ok_does_not_confirm_pending_report_with_missing_target(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "/registar-manobra Tipo de manobra: Entrada Início da manobra: 29/03/2026, 07:45 Fim da manobra: 29/03/2026, 08:30 Calado: 9,8 m Observações: 2 rebocadores",
                },
            )

            first_payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(first_payload["answer_origin"], "slash_template")
            self.assertIn("ref ou nome do navio", first_payload["pending_action"]["proposal"]["missing_fields"])

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "ok",
                },
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["answer_origin"], "pending_action_block")
        self.assertIn("Ainda faltam dados obrigatórios", payload["answer"])
        self.assertIsNotNone(payload["pending_action"])

    def test_finalize_edit_plan_reuses_existing_planned_time(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "edit_maneuver_plan",
                "target": {
                    "reference_code": port_call["reference_code"],
                    "vessel_name": port_call["vessel_name"],
                    "maneuver_type": "entry",
                },
                "fields": {
                    "change_reason": "piloto disponivel",
                    "pier": "Tanquisado (lado jusante)",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertEqual(finalized["missing_fields"], [])
        self.assertEqual(finalized["fields"]["planned_at_local"], "2026-03-24T05:30")
        self.assertEqual(finalized["fields"]["destination"], "Tanquisado (lado jusante)")

    def test_finalize_edit_plan_accepts_scale_id_prefix_in_reference_field(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "edit_maneuver_plan",
                "target": {
                    "reference_code": port_call["id"][:8],
                    "maneuver_type": "entry",
                },
                "fields": {
                    "planned_at_local": "2026-03-24T06:00",
                    "change_reason": "ajuste de janela",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertEqual(finalized["intent"], "action")
        self.assertEqual(finalized["port_call_id"], port_call["id"])
        self.assertEqual(finalized["target"]["reference_code"], port_call["reference_code"])

    def test_pending_approve_replaces_previous_edit_plan(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            proposal = app.finalize_operational_proposal(
                normalize_action_candidate(
                    {
                        "intent": "action",
                        "action": "edit_maneuver_plan",
                        "port_call_id": port_call["id"],
                        "target": {
                            "reference_code": port_call["reference_code"],
                            "vessel_name": port_call["vessel_name"],
                            "maneuver_type": "entry",
                        },
                        "fields": {
                            "change_reason": "piloto disponivel",
                            "pier": "Tanquisado (lado jusante)",
                        },
                        "missing_fields": [],
                    },
                    "admin",
                ),
                [self.store.get_port_call(port_call["id"])],
            )
            app.save_pending_chat_action("admin", conversation["id"], proposal, "Mudar cais")

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "aprova",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_replace")
        self.assertEqual(payload["pending_action"]["proposal"]["action"], "approve_entry")
        self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

    def test_piloto_edit_plan_via_chat_is_rejected(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])
        self.store.create_user(
            "piloto-teste",
            "secret1",
            "piloto",
            full_name="Piloto Teste",
            organization="APSS",
            email="piloto@example.com",
            phone="+351912345678",
        )
        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2026-03-24T08:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 1",
        )
        current = self.store.get_port_call(port_call["id"])
        shift = next(item for item in current["maneuver_history"] if item["type"] == "shift")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "piloto-teste"
                flask_session["role"] = "piloto"

            conversation = self.store.ensure_conversation(username="piloto-teste")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        f"/editar-manobra\n"
                        f"ID da manobra: {shift['id'][:8].upper()}\n"
                        f"Ref: {port_call['reference_code']}\n"
                        f"Tipo de manobra: mudança\n"
                        f"Hora prevista: 24/03/2026, 08:30\n"
                        f"Origem: TMS 2\n"
                        f"Destino: TMS 1\n"
                        f"Calado: 9,94\n"
                        f"Rebocadores: 2\n"
                        f"Restrições: daylight\n"
                        f"Observações: Aguarda rebocador"
                    ),
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_rejected")
        self.assertIn("não está autorizada", payload["answer"].lower())

    def test_abort_shift_via_chat_allows_approved_maneuver_after_planned_time_and_keeps_berth(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])
        self.store.create_user(
            "piloto-aborto",
            "secret1",
            "piloto",
            full_name="Piloto Aborto",
            organization="APSS",
            email="piloto-aborto@example.com",
            phone="+351912345679",
        )
        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2000-01-01T08:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 1",
        )
        self.store.approve_shift_plan(port_call["id"], decided_by="admin")
        current = self.store.get_port_call(port_call["id"])
        shift = next(item for item in current["maneuver_history"] if item["type"] == "shift")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "piloto-aborto"
                flask_session["role"] = "piloto"

            conversation = self.store.ensure_conversation(username="piloto-aborto")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        f"/abortar\n"
                        f"ID da manobra: {shift['id'][:8].upper()}\n"
                        f"Ref: {port_call['reference_code']}\n"
                        f"Tipo de manobra: mudança\n"
                        f"Motivo: nevoeiro"
                    ),
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["answer_origin"], "slash_proposal")
            self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

        self.assertEqual(response.status_code, 200)
        updated = self.store.get_port_call(port_call["id"])
        snapshot = self.store.get_port_activity_snapshot(window_days=3650)
        archived_ids = {item["port_call_id"] for item in snapshot["archived_maneuvers"]}
        shift = next(item for item in updated["maneuver_history"] if item["type"] == "shift")
        self.assertEqual(shift["state"], "aborted")
        self.assertEqual(shift["aborted_reason"], "nevoeiro")
        self.assertEqual(updated["berth"], "TMS 2")
        self.assertIn(port_call["id"], archived_ids)

    def test_edit_plan_confirmation_rejects_stale_maneuver_id(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        current = self.store.get_port_call(port_call["id"])
        entry = next(item for item in current["maneuver_history"] if item["type"] == "entry")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            with self.assertRaisesRegex(ValueError, "não identifica a manobra a editar"):
                app.execute_pending_operational_action(
                    {
                        "action": "edit_maneuver_plan",
                        "port_call_id": port_call["id"],
                        "maneuver_id": "stale-id",
                        "target": {"maneuver_type": "entry"},
                        "fields": {
                            "planned_at_local": entry["planned_at"][:16],
                            "change_reason": "piloto disponivel",
                            "berth": "Tanquisado (lado jusante)",
                        },
                    },
                    username="admin",
                    role="admin",
                )

    def test_finalize_shift_report_requires_maneuver_id_when_multiple_shifts_match(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])

        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2026-03-24T08:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 1",
        )
        self.store.approve_shift_plan(port_call["id"], decided_by="admin")
        self.store.mark_shift_completed(port_call["id"], shifted_at="2026-03-24T08:20:00+00:00", updated_by="admin")

        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2026-03-24T12:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 2",
        )
        self.store.approve_shift_plan(port_call["id"], decided_by="admin")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "shift_report",
                "target": {
                    "reference_code": port_call["reference_code"],
                    "maneuver_type": "shift",
                },
                "fields": {
                    "maneuver_started_local": "2026-03-24T08:05",
                    "maneuver_finished_local": "2026-03-24T08:20",
                    "draft_m": "9.94",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertIn("ID da manobra", finalized["missing_fields"])

    def test_delete_shift_without_id_is_blocked_when_multiple_shifts_match(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])

        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2026-03-24T08:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 1",
        )
        self.store.approve_shift_plan(port_call["id"], decided_by="admin")
        self.store.mark_shift_completed(port_call["id"], shifted_at="2026-03-24T08:20:00+00:00", updated_by="admin")

        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2026-03-24T12:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 2",
        )
        self.store.approve_shift_plan(port_call["id"], decided_by="admin")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            with self.assertRaisesRegex(ValueError, "Indica o ID da manobra"):
                app.execute_pending_operational_action(
                    {
                        "action": "delete_maneuver",
                        "port_call_id": port_call["id"],
                        "target": {"maneuver_type": "shift"},
                        "fields": {},
                    },
                    username="admin",
                    role="admin",
                )

    def test_passa_a_previsto_reports_already_pending_when_it_is_already_planned(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            proposal = app.propose_operational_action(
                f"passa a manobra do {port_call['vessel_name']} a previsto",
                "admin",
            )

        self.assertEqual(proposal["intent"], "unsupported")
        self.assertIn("já está prevista", proposal["reason"])

    def test_operational_consultation_question_uses_llm_instead_of_action_template(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=True), patch.object(
                services.rag,
                "answer",
                return_value={"answer": "Deve embarcar com antecedência suficiente para não perder a maré.", "sources": []},
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": (
                            "Sabendo que o preia-mar hoje é às 15:13, a que horas deve embarcar piloto "
                            "para trazer um navio para a Lisnave?"
                        ),
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "llm")
        self.assertNotEqual(payload["answer_origin"], "operational_clarification")
        self.assertIn("antecedência suficiente", payload["answer"])
        answer_mock.assert_called_once()

    def test_operational_consultation_question_for_lisnave_dry_dock_uses_llm(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=True), patch.object(
                services.rag,
                "answer",
                return_value={"answer": "Para a Doca 21, a resposta deve seguir a janela documental aplicável.", "sources": []},
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Sabendo isso, tenho um navio para entrar para a doca 21. A que horas podemos marcar piloto para hoje?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "llm")
        self.assertNotEqual(payload["answer_origin"], "operational_clarification")
        self.assertIn("Doca 21", payload["answer"])
        answer_mock.assert_called_once()

    def test_plain_text_operational_command_redirects_to_slash_commands(self) -> None:
        port_call = self._create_entry(notes="Entrada pendente")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": f"Aprova a entrada da escala {port_call['reference_code']}.",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_redirect")
        self.assertIsNone(payload["pending_action"])
        self.assertIn("/aprovar", payload["answer"])
        self.assertIn("/validar-manobra", payload["answer"])

    def test_slash_validate_maneuver_returns_structured_validation(self) -> None:
        port_call = self._create_entry(notes="Entrada pendente")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": f"/validar-manobra {port_call['reference_code']} entrada",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_validation")
        self.assertIn("Validação da", payload["answer"])
        self.assertIn("Checklist operacional determinística do portal", payload["answer"])
        self.assertIn(port_call["reference_code"], payload["answer"])

    def test_widget_create_conversation_api_returns_widget_payload(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            response = client.post("/api/conversations")

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertIn("conversation", payload)
        self.assertIn("conversations", payload)
        self.assertEqual(payload["conversation"]["id"], payload["conversations"][0]["id"])
        self.assertEqual(payload["messages"], [])
        self.assertIsNone(payload["pending_action"])

    def test_widget_get_conversation_api_returns_selected_messages(self) -> None:
        first = self.store.create_conversation("admin", title="Primeira conversa")
        second = self.store.create_conversation("admin", title="Segunda conversa")
        self.store.append_chat_message("admin", second["id"], "user", "Estado da maré?")
        self.store.append_chat_message("admin", second["id"], "assistant", "Preia-mar às 15:13.")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            response = client.get(f"/api/conversations/{second['id']}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["conversation"]["id"], second["id"])
        self.assertEqual(payload["conversation"]["title"], "Estado da maré?")
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][0]["content"], "Estado da maré?")
        self.assertEqual(payload["messages"][1]["content"], "Preia-mar às 15:13.")
        self.assertEqual(payload["conversations"][0]["id"], second["id"])
        self.assertEqual(payload["conversations"][1]["id"], first["id"])

    def test_widget_rename_conversation_api_updates_title(self) -> None:
        conversation = self.store.create_conversation("admin")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            response = client.post(
                f"/api/conversations/{conversation['id']}/rename",
                json={"title": "Janela de marés Lisnave"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["conversation"]["id"], conversation["id"])
        self.assertEqual(payload["conversation"]["title"], "Janela de marés Lisnave")
        self.assertEqual(payload["conversations"][0]["title"], "Janela de marés Lisnave")
        self.assertIsNone(payload["pending_action"])

    def test_chat_archive_page_renders_compact_export_actions(self) -> None:
        conversation = self.store.create_conversation("admin", title="Arquivo operacional")

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get(f"/conversations?conversation_id={conversation['id']}")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Exportar", html)
        self.assertIn("export.txt", html)
        self.assertIn("export.pdf", html)
        self.assertIn("Nova", html)

    def test_conversation_export_txt_returns_plain_text_transcript(self) -> None:
        conversation = self.store.create_conversation("admin", title="Janela de marés")
        self.store.append_chat_message("admin", conversation["id"], "user", "Qual é a maré?")
        self.store.append_chat_message("admin", conversation["id"], "assistant", "Preia-mar às 15:13.")

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get(f"/conversations/{conversation['id']}/export.txt")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.mimetype)
        body = response.get_data(as_text=True)
        self.assertIn("Conversa:", body)
        self.assertIn("Qual é a maré?", body)
        self.assertIn("Preia-mar às 15:13.", body)

    def test_conversation_export_pdf_returns_pdf_bytes(self) -> None:
        conversation = self.store.create_conversation("admin", title="Estado operacional")
        self.store.append_chat_message("admin", conversation["id"], "user", "Resumo do porto?")
        self.store.append_chat_message("admin", conversation["id"], "assistant", "Duas entradas e uma saída.")

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get(f"/conversations/{conversation['id']}/export.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF-1.4"))


class AdminDocumentPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))
        self.original_store = services.store
        self.original_manual_authoring_enabled = app.app.config["MANUAL_KNOWLEDGE_AUTHORING_ENABLED"]
        services.store = self.store
        app.app.config["MANUAL_KNOWLEDGE_AUTHORING_ENABLED"] = False

    def tearDown(self) -> None:
        services.store = self.original_store
        app.app.config["MANUAL_KNOWLEDGE_AUTHORING_ENABLED"] = self.original_manual_authoring_enabled
        self.temp_dir.cleanup()

    def _set_admin_session(self, client) -> str:
        with client.session_transaction() as flask_session:
            flask_session["username"] = "admin"
            flask_session["role"] = "admin"
            flask_session["_csrf_token"] = "test-token"
        return "test-token"

    def test_manual_document_creation_route_is_disabled(self) -> None:
        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)

            response = client.post(
                "/documents",
                data={
                    "csrf_token": csrf_token,
                    "title": "Meteorologia e Mares",
                    "content": "Conteúdo não oficial.",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.store.list_documents(), [])

    def test_manual_document_edit_route_is_disabled(self) -> None:
        filename = self.store.save_document("Norma de Seguranca", "Versão oficial.")

        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)

            response = client.post(
                f"/documents/{filename}/edit",
                data={
                    "csrf_token": csrf_token,
                    "content": "Versão alterada no browser.",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("Versão oficial.", self.store.get_document_text(filename))


if __name__ == "__main__":
    unittest.main()
