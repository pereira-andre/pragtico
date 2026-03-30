"""Integration tests for the LocalStore storage backend."""

import math
import os
import tempfile
import unittest
from pathlib import Path

os.environ["APP_STORAGE_BACKEND"] = "local"
os.environ["RAG_INDEX_BACKEND"] = "local"

from domain.cost_engine import UP_NORMAL, calculate_tup
from storage import LocalStore


class LocalStoreUserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_default_users_seeded(self) -> None:
        users = self.store.list_users()
        usernames = {u["username"] for u in users}
        self.assertIn("admin", usernames)
        self.assertIn("agente", usernames)
        self.assertIn("piloto", usernames)

    def test_authenticate_default_admin(self) -> None:
        user = self.store.authenticate("admin", "admin123")
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "admin")

    def test_authenticate_wrong_password(self) -> None:
        user = self.store.authenticate("admin", "wrong")
        self.assertIsNone(user)

    def test_create_user(self) -> None:
        user = self.store.create_user("test@example.com", "secret123", "agente")
        self.assertEqual(user["username"], "test@example.com")
        self.assertEqual(user["role"], "agente")

    def test_create_user_short_username_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_user("ab", "secret123", "agente")

    def test_create_user_short_password_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_user("test@example.com", "abc", "agente")

    def test_create_user_invalid_role_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_user("test@example.com", "secret123", "superuser")

    def test_create_duplicate_user_raises(self) -> None:
        self.store.create_user("test@example.com", "secret123", "agente")
        with self.assertRaises(ValueError):
            self.store.create_user("test@example.com", "secret456", "piloto")

    def test_update_user_profile(self) -> None:
        updated = self.store.update_user_profile(
            "admin", full_name="Admin User", organization="Porto de Setúbal",
            email="admin@porto.pt", phone="+351 912 345 678",
        )
        self.assertEqual(updated["full_name"], "Admin User")
        self.assertEqual(updated["organization"], "Porto de Setúbal")

    def test_set_user_role(self) -> None:
        self.store.create_user("test@example.com", "secret123", "agente")
        updated = self.store.set_user_role("test@example.com", "piloto")
        self.assertEqual(updated["role"], "piloto")

    def test_reset_password(self) -> None:
        result = self.store.reset_user_password("admin", "newpass123")
        self.assertTrue(result)
        user = self.store.authenticate("admin", "newpass123")
        self.assertIsNotNone(user)

    def test_delete_user(self) -> None:
        self.store.create_user("test@example.com", "secret123", "agente")
        self.store.delete_user("test@example.com")
        self.assertIsNone(self.store.get_user_profile("test@example.com"))

    def test_delete_last_admin_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.delete_user("admin")


class LocalStoreConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_conversation(self) -> None:
        conv = self.store.create_conversation("admin")
        self.assertEqual(conv["username"], "admin")
        self.assertEqual(conv["title"], "Nova conversa")

    def test_rename_conversation(self) -> None:
        conv = self.store.create_conversation("admin")
        renamed = self.store.rename_conversation("admin", conv["id"], "Teste")
        self.assertEqual(renamed["title"], "Teste")

    def test_rename_empty_title_raises(self) -> None:
        conv = self.store.create_conversation("admin")
        with self.assertRaises(ValueError):
            self.store.rename_conversation("admin", conv["id"], "  ")

    def test_ensure_conversation_creates_if_empty(self) -> None:
        conv = self.store.ensure_conversation("admin")
        self.assertIsNotNone(conv["id"])

    def test_ensure_conversation_returns_existing(self) -> None:
        first = self.store.create_conversation("admin")
        second = self.store.ensure_conversation("admin", first["id"])
        self.assertEqual(first["id"], second["id"])

    def test_append_and_list_messages(self) -> None:
        conv = self.store.create_conversation("admin")
        self.store.append_chat_message("admin", conv["id"], "user", "Olá")
        self.store.append_chat_message("admin", conv["id"], "assistant", "Boa tarde!")
        messages = self.store.list_messages("admin", conv["id"])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[1]["role"], "assistant")

    def test_clear_conversation(self) -> None:
        conv = self.store.create_conversation("admin")
        self.store.append_chat_message("admin", conv["id"], "user", "teste")
        self.store.clear_conversation("admin", conv["id"])
        messages = self.store.list_messages("admin", conv["id"])
        self.assertEqual(len(messages), 0)

    def test_delete_conversation(self) -> None:
        conv = self.store.create_conversation("admin")
        self.store.delete_conversation("admin", conv["id"])
        conversations = self.store.list_conversations("admin")
        self.assertEqual(len(conversations), 0)


class LocalStoreDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_save_and_get_document(self) -> None:
        filename = self.store.save_document("Teste", "Conteúdo de teste.")
        doc = self.store.get_document(filename)
        self.assertIsNotNone(doc)
        self.assertEqual(doc["name"], filename)

    def test_get_document_text(self) -> None:
        filename = self.store.save_document("Teste", "Conteúdo de teste.")
        text = self.store.get_document_text(filename)
        self.assertIn("Conteúdo de teste", text)

    def test_update_document_text(self) -> None:
        filename = self.store.save_document("Teste", "Original.")
        updated = self.store.update_document_text(filename, "Atualizado.", "admin")
        text = self.store.get_document_text(filename)
        self.assertIn("Atualizado", text)

    def test_update_empty_content_raises(self) -> None:
        filename = self.store.save_document("Teste", "Original.")
        with self.assertRaises(ValueError):
            self.store.update_document_text(filename, "  ", "admin")

    def test_delete_document(self) -> None:
        filename = self.store.save_document("Teste", "Conteúdo.")
        self.store.delete_document(filename)
        self.assertIsNone(self.store.get_document(filename))

    def test_list_documents(self) -> None:
        self.store.save_document("Doc1", "Primeiro.")
        self.store.save_document("Doc2", "Segundo.")
        docs = self.store.list_documents()
        self.assertGreaterEqual(len(docs), 2)


class LocalStorePortCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_entry(self) -> dict:
        return self.store.create_port_call(
            vessel_name="BELITAKI", eta="2026-03-24T05:30:00+00:00",
            created_by="admin", berth="TMS 2", last_port="Leixoes",
            next_port="Barcelona", notes="Teste",
            vessel_imo="9152923", vessel_call_sign="D5OC2",
            vessel_flag="Liberia", vessel_type="Porta-contentores",
            vessel_loa_m="179.23", vessel_beam_m="25.3",
            vessel_gt_t="16281", vessel_max_draft_m="9.94",
            vessel_dwt_t="22330",
        )

    def test_create_port_call(self) -> None:
        pc = self._create_entry()
        self.assertEqual(pc["vessel_name"], "BELITAKI")
        self.assertEqual(pc["status"], "scheduled")

    def test_create_port_call_missing_vessel_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_port_call(
                vessel_name="", eta="2026-03-24T05:30:00+00:00",
                created_by="admin", berth="TMS 2", last_port="Leixoes",
                next_port="Barcelona", vessel_imo="9152923",
                vessel_call_sign="D5OC2", vessel_flag="Liberia",
                vessel_type="Porta-contentores", vessel_loa_m="179.23",
                vessel_beam_m="25.3", vessel_gt_t="16281",
                vessel_max_draft_m="9.94", vessel_dwt_t="22330",
            )

    def test_create_port_call_invalid_numeric_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_port_call(
                vessel_name="TEST", eta="2026-03-24T05:30:00+00:00",
                created_by="admin", berth="TMS 2", last_port="Leixoes",
                next_port="Barcelona", vessel_imo="9152923",
                vessel_call_sign="D5OC2", vessel_flag="Liberia",
                vessel_type="Porta-contentores", vessel_loa_m="abc",
                vessel_beam_m="25.3", vessel_gt_t="16281",
                vessel_max_draft_m="9.94", vessel_dwt_t="22330",
            )

    def test_approve_port_call(self) -> None:
        pc = self._create_entry()
        approved = self.store.approve_port_call(pc["id"], decided_by="admin")
        entry = next(m for m in approved["maneuver_history"] if m["type"] == "entry")
        self.assertEqual(entry["state"], "approved")

    def test_full_lifecycle_entry_to_departure(self) -> None:
        pc = self._create_entry()
        self.store.approve_port_call(pc["id"], decided_by="admin")
        arrived = self.store.mark_port_call_arrived(
            pc["id"], arrived_at="2026-03-24T06:00:00+00:00", updated_by="admin",
        )
        self.assertEqual(arrived["status"], "in_port")

        self.store.schedule_departure_plan(
            pc["id"], planned_departure_at="2026-03-25T10:00:00+00:00",
            updated_by="admin", next_port="Barcelona",
        )
        self.store.approve_port_call(pc["id"], decided_by="admin")
        departed = self.store.mark_port_call_departed(
            pc["id"], departed_at="2026-03-25T10:30:00+00:00", updated_by="admin",
        )
        self.assertEqual(departed["status"], "departed")

    def test_abort_shift_plan_allows_approved_maneuver_after_planned_time_and_keeps_berth(self) -> None:
        pc = self._create_entry()
        self.store.approve_port_call(pc["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            pc["id"], arrived_at="2026-03-24T06:00:00+00:00", updated_by="admin",
        )
        self.store.schedule_shift_plan(
            pc["id"],
            planned_shift_at="2000-01-01T08:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 1",
        )
        self.store.approve_shift_plan(pc["id"], decided_by="admin")

        aborted = self.store.abort_shift_plan(pc["id"], updated_by="piloto", aborted_reason="nevoeiro")
        shift = next(m for m in aborted["maneuver_history"] if m["type"] == "shift")
        snapshot = self.store.get_port_activity_snapshot(window_days=3650)
        archived_ids = {item["port_call_id"] for item in snapshot["archived_maneuvers"]}

        self.assertEqual(shift["state"], "aborted")
        self.assertEqual(shift["aborted_reason"], "nevoeiro")
        self.assertEqual(aborted["berth"], "TMS 2")
        self.assertIn(pc["id"], archived_ids)

    def test_attach_entry_report_rejects_finished_before_started(self) -> None:
        pc = self._create_entry()
        self.store.approve_port_call(pc["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            pc["id"], arrived_at="2026-03-24T06:00:00+00:00", updated_by="admin",
        )

        with self.assertRaisesRegex(ValueError, "Fim da manobra deve ser posterior a Início da manobra."):
            self.store.attach_entry_report(
                pc["id"],
                updated_by="admin",
                maneuver_started_at="2026-03-24T06:10:00+00:00",
                maneuver_finished_at="2026-03-24T06:00:00+00:00",
                draft_m="9.94",
                notes="Sem incidentes.",
            )

    def test_edit_maneuver_report_rejects_finished_before_started(self) -> None:
        pc = self._create_entry()
        self.store.approve_port_call(pc["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            pc["id"], arrived_at="2026-03-24T06:00:00+00:00", updated_by="admin",
        )
        reported = self.store.attach_entry_report(
            pc["id"],
            updated_by="admin",
            maneuver_started_at="2026-03-24T05:35:00+00:00",
            maneuver_finished_at="2026-03-24T06:00:00+00:00",
            draft_m="9.94",
            notes="Sem incidentes.",
        )
        entry = next(m for m in reported["maneuver_history"] if m["type"] == "entry")

        with self.assertRaisesRegex(ValueError, "Fim da manobra deve ser posterior a Início da manobra."):
            self.store.edit_maneuver_report(
                pc["id"],
                entry["id"],
                updated_by="admin",
                maneuver_started_at="2026-03-24T06:10:00+00:00",
                maneuver_finished_at="2026-03-24T06:00:00+00:00",
                draft_m="9.94",
                notes="Ajuste",
                change_reason="correção",
            )

    def test_get_port_activity_snapshot(self) -> None:
        self._create_entry()
        snapshot = self.store.get_port_activity_snapshot(window_days=30)
        self.assertIn("planned_maneuvers", snapshot)
        self.assertIn("archived_maneuvers", snapshot)
        self.assertIn("archived_scales", snapshot)

    def test_archived_scale_estimate_uses_full_gt_and_aggregates_scale_costs(self) -> None:
        port_call = self.store.create_port_call(
            vessel_name="OCEAN BULKER",
            eta="2026-03-24T05:30:00+00:00",
            created_by="admin",
            berth="Teporset",
            last_port="Casablanca",
            next_port="Rotterdam",
            notes="Escala de teste.",
            vessel_imo="9152923",
            vessel_call_sign="D5OC2",
            vessel_flag="Liberia",
            vessel_type="Graneis sólidos",
            vessel_loa_m="179.23",
            vessel_beam_m="25.3",
            vessel_gt_t="34.860",
            vessel_max_draft_m="10.60",
            vessel_dwt_t="22330",
        )
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            port_call["id"],
            arrived_at="2026-03-24T06:00:00+00:00",
            updated_by="admin",
        )
        self.store.attach_entry_report(
            port_call["id"],
            updated_by="admin",
            maneuver_started_at="2026-03-24T05:35:00+00:00",
            maneuver_finished_at="2026-03-24T06:00:00+00:00",
            draft_m="10.60",
            notes="Entrada concluída.",
        )
        self.store.schedule_departure_plan(
            port_call["id"],
            planned_departure_at="2026-03-25T10:00:00+00:00",
            updated_by="admin",
            next_port="Rotterdam",
        )
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_departed(
            port_call["id"],
            departed_at="2026-03-25T10:30:00+00:00",
            updated_by="admin",
        )
        self.store.attach_departure_report(
            port_call["id"],
            updated_by="admin",
            maneuver_started_at="2026-03-25T09:50:00+00:00",
            maneuver_finished_at="2026-03-25T10:30:00+00:00",
            draft_m="10.40",
            notes="Saída concluída.",
        )

        snapshot = self.store.get_port_activity_snapshot(window_days=3650)
        scale = next(item for item in snapshot["archived_scales"] if item["port_call_id"] == port_call["id"])
        expected_pilotage = round(2 * UP_NORMAL * math.sqrt(34860), 2)
        expected_tup = calculate_tup(34860, "restantes", scale["stay_days"])

        self.assertEqual(scale["maneuver_count"], 2)
        self.assertAlmostEqual(scale["estimated_pilotage_total"], expected_pilotage, places=2)
        self.assertAlmostEqual(scale["estimated_tup"], expected_tup, places=2)
        self.assertGreater(scale["estimated_grand_total"], scale["estimated_pilotage_total"])
        self.assertEqual([item["maneuver_type"] for item in scale["maneuvers"]], ["entry", "departure"])
        self.assertTrue(all(item["estimated_cost"] and item["estimated_cost"] > 1000 for item in scale["maneuvers"]))

    def test_archived_scale_estimate_adds_standby_after_three_hours(self) -> None:
        port_call = self._create_entry()
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            port_call["id"],
            arrived_at="2026-03-24T06:00:00+00:00",
            updated_by="admin",
        )
        self.store.attach_entry_report(
            port_call["id"],
            updated_by="admin",
            maneuver_started_at="2026-03-24T05:00:00+00:00",
            maneuver_finished_at="2026-03-24T09:30:00+00:00",
            draft_m="9.94",
            notes="Entrada longa.",
        )

        snapshot = self.store.get_port_activity_snapshot(window_days=3650)
        scale = next(item for item in snapshot["archived_scales"] if item["port_call_id"] == port_call["id"])
        entry = next(item for item in scale["maneuvers"] if item["maneuver_type"] == "entry")
        base_cost = round(UP_NORMAL * math.sqrt(16281), 2)

        self.assertAlmostEqual(entry["derived_standby_hours"], 1.5, places=2)
        self.assertEqual(entry["derived_standby_hours_label"], "1,5 h")
        self.assertGreater(entry["estimated_cost"], base_cost)

    def test_edit_port_call_updates_vessel_and_operational_fields(self) -> None:
        pc = self._create_entry()
        updated = self.store.edit_port_call(
            pc["id"],
            updated_by="admin",
            vessel_name="BELITAKI II",
            berth="TMS 1 - Cais 3",
            last_port="Sines",
            next_port="Valencia",
        )

        self.assertEqual(updated["vessel_name"], "BELITAKI II")
        self.assertEqual(updated["berth"], "TMS 1 - Cais 3")
        entry = next(m for m in updated["maneuver_history"] if m["type"] == "entry")
        self.assertEqual(entry["origin"], "Sines")
        self.assertEqual(entry["destination"], "TMS 1 - Cais 3")

    def test_delete_port_call_removes_scale(self) -> None:
        pc = self._create_entry()
        removed = self.store.delete_port_call(pc["id"])

        self.assertEqual(removed["id"], pc["id"])
        with self.assertRaises(ValueError):
            self.store.get_port_call(pc["id"])

    def test_delete_maneuver_removes_departure_plan(self) -> None:
        pc = self._create_entry()
        self.store.approve_port_call(pc["id"], decided_by="admin")
        self.store.mark_port_call_arrived(pc["id"], arrived_at="2026-03-24T06:00:00+00:00", updated_by="admin")
        updated = self.store.schedule_departure_plan(
            pc["id"],
            planned_departure_at="2026-03-25T10:00:00+00:00",
            updated_by="admin",
            next_port="Barcelona",
        )
        departure = next(m for m in updated["maneuver_history"] if m["type"] == "departure")

        result = self.store.delete_maneuver(pc["id"], departure["id"], updated_by="admin")

        self.assertFalse(any(m["type"] == "departure" for m in result["maneuver_history"]))

    def test_delete_maneuver_report_clears_report_fields(self) -> None:
        pc = self._create_entry()
        self.store.approve_port_call(pc["id"], decided_by="admin")
        self.store.mark_port_call_arrived(pc["id"], arrived_at="2026-03-24T06:00:00+00:00", updated_by="admin")
        reported = self.store.attach_entry_report(
            pc["id"],
            updated_by="admin",
            maneuver_started_at="2026-03-24T05:35:00+00:00",
            maneuver_finished_at="2026-03-24T06:00:00+00:00",
            draft_m="9.94",
            notes="Sem incidentes.",
        )
        entry = next(m for m in reported["maneuver_history"] if m["type"] == "entry")

        cleared = self.store.delete_maneuver_report(pc["id"], entry["id"], updated_by="admin")
        updated_entry = next(m for m in cleared["maneuver_history"] if m["type"] == "entry")

        self.assertEqual(updated_entry["report_note"], "")
        self.assertEqual(updated_entry["reported_draft_m"], "")
        self.assertFalse(updated_entry["execution_started_at"])
        self.assertFalse(updated_entry["execution_finished_at"])

    def test_attach_shift_report_can_target_specific_maneuver_id(self) -> None:
        pc = self._create_entry()
        self.store.approve_port_call(pc["id"], decided_by="admin")
        self.store.mark_port_call_arrived(pc["id"], arrived_at="2026-03-24T06:00:00+00:00", updated_by="admin")

        first_shift = self.store.schedule_shift_plan(
            pc["id"],
            planned_shift_at="2026-03-24T08:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 1",
        )
        first_shift_id = next(m for m in first_shift["maneuver_history"] if m["type"] == "shift")["id"]
        self.store.approve_shift_plan(pc["id"], decided_by="admin")
        self.store.mark_shift_completed(pc["id"], shifted_at="2026-03-24T08:20:00+00:00", updated_by="admin")

        second_shift = self.store.schedule_shift_plan(
            pc["id"],
            planned_shift_at="2026-03-24T12:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 2",
        )
        second_shift_id = [
            m["id"]
            for m in second_shift["maneuver_history"]
            if m["type"] == "shift"
        ][-1]
        self.store.approve_shift_plan(pc["id"], decided_by="admin")

        reported = self.store.attach_shift_report(
            pc["id"],
            updated_by="admin",
            maneuver_started_at="2026-03-24T08:05:00+00:00",
            maneuver_finished_at="2026-03-24T08:20:00+00:00",
            draft_m="9.94",
            notes="Registo da primeira mudança.",
            maneuver_id=first_shift_id,
        )

        first = next(m for m in reported["maneuver_history"] if m["id"] == first_shift_id)
        second = next(m for m in reported["maneuver_history"] if m["id"] == second_shift_id)
        self.assertEqual(first["report_note"], "Registo da primeira mudança.")
        self.assertEqual(second["report_note"], "")

    def test_clear_port_calls(self) -> None:
        self._create_entry()
        removed = self.store.clear_port_calls()
        snapshot = self.store.get_port_activity_snapshot(window_days=30)
        self.assertEqual(removed, 1)
        self.assertEqual(snapshot["planned_maneuvers"], [])
        self.assertEqual(snapshot["archived_maneuvers"], [])

    def test_runtime_state_crud(self) -> None:
        self.store.set_runtime_state("test_key", {"value": 42})
        state = self.store.get_runtime_state("test_key")
        self.assertEqual(state["value"], 42)
        self.store.delete_runtime_state("test_key")
        self.assertIsNone(self.store.get_runtime_state("test_key"))

    def test_message_feedback(self) -> None:
        conv = self.store.create_conversation("admin")
        self.store.append_chat_message("admin", conv["id"], "user", "Pergunta")
        msg = self.store.append_chat_message("admin", conv["id"], "assistant", "Resposta")
        updated = self.store.update_message_feedback(
            "admin", conv["id"], msg["id"], "approved", "Boa resposta",
        )
        self.assertEqual(updated["feedback_status"], "approved")

    def test_message_feedback_invalid_status_raises(self) -> None:
        conv = self.store.create_conversation("admin")
        msg = self.store.append_chat_message("admin", conv["id"], "assistant", "Resposta")
        with self.assertRaises(ValueError):
            self.store.update_message_feedback("admin", conv["id"], msg["id"], "invalid")


if __name__ == "__main__":
    unittest.main()
