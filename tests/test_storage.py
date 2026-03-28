"""Integration tests for the LocalStore storage backend."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ["APP_STORAGE_BACKEND"] = "local"
os.environ["RAG_INDEX_BACKEND"] = "local"

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

    def test_get_port_activity_snapshot(self) -> None:
        self._create_entry()
        snapshot = self.store.get_port_activity_snapshot(window_days=30)
        self.assertIn("planned_maneuvers", snapshot)
        self.assertIn("archived_maneuvers", snapshot)

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
