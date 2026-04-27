"""Integration tests for the admin bot redesign: health page, settings and playground."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("APP_STORAGE_BACKEND", "local")
os.environ.setdefault("RAG_INDEX_BACKEND", "local")
os.environ.pop("DATABASE_URL", None)

import app  # noqa: E402
from core import services  # noqa: E402
from core.bot_settings import DEFAULTS, load_bot_settings, save_bot_settings  # noqa: E402
from core.chat_feedback import sync_feedback_correction_eval_case  # noqa: E402
from storage.local import LocalStore  # noqa: E402


class AdminBotDashboardTests(unittest.TestCase):
    """Render the new /admin/bot page and exercise settings + playground endpoints."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))
        self.original_store = services.store
        self.original_data_dir = getattr(services, "DATA_DIR", "")
        services.store = self.store
        services.DATA_DIR = str(base / "data")

    def tearDown(self) -> None:
        services.store = self.original_store
        services.DATA_DIR = self.original_data_dir
        self.temp_dir.cleanup()

    def _set_admin_session(self, client) -> str:
        with client.session_transaction() as flask_session:
            flask_session["username"] = "admin"
            flask_session["role"] = "admin"
            flask_session["_csrf_token"] = "test-token"
        return "test-token"

    def test_admin_bot_page_renders_with_all_zones(self) -> None:
        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get("/admin/bot")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for marker in (
            "Painel operacional",
            "Playground",
            "Como o bot decide",
            "Inputs que o bot pode reutilizar",
            "De onde o bot aprende",
            "O que o bot está a aprender",
            "Só o que precisa",
            "Evals de conhecimento",
            "Definições e aprendizagem",
            "berth_profiles.json",
            "review_correction_memory",
        ):
            self.assertIn(marker, html, msg=f"missing marker: {marker}")
        self.assertIn("X-CSRF-Token", html)
        self.assertNotIn("X-CSRFToken", html)

    def test_settings_form_persists_values(self) -> None:
        with app.app.test_client() as client:
            csrf = self._set_admin_session(client)
            response = client.post(
                "/admin/bot/settings",
                data={
                    "csrf_token": csrf,
                    "auto_promote_corrections": "on",
                    "signals_window_hours": "48",
                    "review_guard_similarity": "0.85",
                    "outlier_review_threshold": "0.75",
                },
            )
        self.assertIn(response.status_code, (200, 302))
        stored = load_bot_settings()
        self.assertTrue(stored["auto_promote_corrections"])
        self.assertEqual(stored["signals_window_hours"], 48)
        self.assertAlmostEqual(stored["review_guard_similarity"], 0.85, places=2)
        self.assertAlmostEqual(stored["outlier_review_threshold"], 0.75, places=2)

    def test_settings_reset_restores_defaults(self) -> None:
        with app.app.test_client() as client:
            csrf = self._set_admin_session(client)
            client.post(
                "/admin/bot/settings",
                data={"csrf_token": csrf, "signals_window_hours": "24"},
            )
            self.assertEqual(load_bot_settings()["signals_window_hours"], 24)
            response = client.post(
                "/admin/bot/settings/reset",
                data={"csrf_token": csrf},
            )
        self.assertIn(response.status_code, (200, 302))
        self.assertEqual(load_bot_settings()["signals_window_hours"], DEFAULTS["signals_window_hours"])

    def test_settings_form_rejects_unsafe_threshold_order(self) -> None:
        with app.app.test_client() as client:
            csrf = self._set_admin_session(client)
            response = client.post(
                "/admin/bot/settings",
                data={
                    "csrf_token": csrf,
                    "signals_window_hours": "48",
                    "review_guard_similarity": "0.96",
                    "review_correction_similarity": "0.94",
                    "review_block_similarity": "0.98",
                    "trusted_document_hint_similarity": "0.82",
                    "outlier_review_threshold": "0.85",
                },
            )
        self.assertIn(response.status_code, (200, 302))
        stored = load_bot_settings()
        self.assertEqual(stored["signals_window_hours"], DEFAULTS["signals_window_hours"])
        self.assertAlmostEqual(stored["review_guard_similarity"], DEFAULTS["review_guard_similarity"], places=2)

    def test_admin_validation_setting_blocks_non_admin_feedback_promotion(self) -> None:
        self.store.create_user(
            "pilot@example.com",
            "password",
            "piloto",
            full_name="Pilot",
            email="pilot@example.com",
        )
        self.store.create_user(
            "admin@example.com",
            "password",
            "admin",
            full_name="Admin",
            email="admin@example.com",
        )
        conversation = self.store.ensure_conversation("pilot@example.com")
        self.store.append_chat_message(
            "pilot@example.com",
            conversation["id"],
            "user",
            "Qual é o fundeadouro principal?",
        )
        assistant_message = self.store.append_chat_message(
            "pilot@example.com",
            conversation["id"],
            "assistant",
            "Resposta antiga.",
        )
        save_bot_settings(
            {"auto_promote_corrections": True, "require_admin_validation": True},
            updated_by="admin@example.com",
        )

        self.store.update_message_feedback(
            "pilot@example.com",
            conversation["id"],
            assistant_message["id"],
            "review",
            "Corrigir resposta",
            feedback_correction="O fundeadouro principal é o Fundeadouro Norte.",
            feedback_correction_document="IT-015_Fundeadouros.txt",
            feedback_updated_by="pilot@example.com",
        )
        promoted = sync_feedback_correction_eval_case(
            self.store,
            "pilot@example.com",
            conversation["id"],
            assistant_message["id"],
            source="web",
        )
        self.assertIsNone(promoted)
        self.assertEqual(self.store.list_feedback_eval_cases(), [])

        self.store.update_message_feedback(
            "pilot@example.com",
            conversation["id"],
            assistant_message["id"],
            "review",
            "Corrigir resposta",
            feedback_correction="O fundeadouro principal é o Fundeadouro Norte.",
            feedback_correction_document="IT-015_Fundeadouros.txt",
            feedback_updated_by="admin@example.com",
        )
        promoted = sync_feedback_correction_eval_case(
            self.store,
            "pilot@example.com",
            conversation["id"],
            assistant_message["id"],
            source="web",
        )
        self.assertIsNotNone(promoted)
        self.assertEqual(len(self.store.list_feedback_eval_cases()), 1)

    def test_playground_returns_answer_shape(self) -> None:
        fake_answer = {
            "answer": "Fundeadouro Norte é o principal para aguardar cais.",
            "sources": [{"document": "IT-015_Fundeadouros.txt", "snippet": "Fundeadouro Norte"}],
            "answer_origin": "document_companion",
        }
        with app.app.test_client() as client:
            csrf = self._set_admin_session(client)
            with patch("core.chat_runtime.playground_answer", return_value=fake_answer):
                response = client.post(
                    "/admin/bot/playground",
                    data=json.dumps({"question": "Qual é o fundeadouro principal?"}),
                    content_type="application/json",
                    headers={"X-CSRF-Token": csrf},
                )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["answer_origin"], "document_companion")
        self.assertIn("Fundeadouro Norte", body["answer"])
        self.assertEqual(len(body["sources"]), 1)

    def test_playground_rejects_empty_question(self) -> None:
        with app.app.test_client() as client:
            csrf = self._set_admin_session(client)
            response = client.post(
                "/admin/bot/playground",
                data=json.dumps({"question": "   "}),
                content_type="application/json",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 400)

    def test_playground_lisnave_night_loa_question_prefers_structured_profile_answer(self) -> None:
        repo_knowledge = Path(__file__).resolve().parents[1] / "knowledge"
        knowledge_dir = Path(self.store.knowledge_dir)
        (knowledge_dir / "berth_profiles.json").write_text(
            (repo_knowledge / "berth_profiles.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (knowledge_dir / "IT-014_Lisnave.txt").write_text(
            (repo_knowledge / "IT-014_Lisnave.txt").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        companions_dir = knowledge_dir / "companions"
        companions_dir.mkdir(parents=True, exist_ok=True)
        (companions_dir / "IT-014_Lisnave.json").write_text(
            (repo_knowledge / "companions" / "IT-014_Lisnave.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.store.list_documents()

        with app.app.test_client() as client:
            csrf = self._set_admin_session(client)
            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                response = client.post(
                    "/admin/bot/playground",
                    data=json.dumps(
                        {"question": "À noite psso manobrar um navio com 285m de comprimento na Lisnave?"}
                    ),
                    content_type="application/json",
                    headers={"X-CSRF-Token": csrf},
                )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["answer_origin"], "berth_profile")
        self.assertTrue(body["answer"].startswith("Nao."))
        self.assertIn("285 m", body["answer"])
        self.assertIn("280 m", body["answer"])
        self.assertIn("periodo diurno", body["answer"])
        answer_mock.assert_not_called()

    def test_bot_database_export_includes_settings(self) -> None:
        with app.app.test_client() as client:
            csrf = self._set_admin_session(client)
            client.post(
                "/admin/bot/settings",
                data={"csrf_token": csrf, "signals_window_hours": "96"},
            )
            response = client.get("/admin/bot/export")
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.get_data())
        self.assertIn("bot_settings", payload["payload"])
        self.assertEqual(payload["payload"]["bot_settings"]["signals_window_hours"], 96)


if __name__ == "__main__":
    unittest.main()
