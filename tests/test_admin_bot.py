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
from core.bot_insights import build_sources_snapshot  # noqa: E402
from core.bot_settings import DEFAULTS, load_bot_settings  # noqa: E402
from storage.local import LocalStore  # noqa: E402


class _StubTideService:
    def __init__(self, csv_path: str, location_label: str = "Setúbal / Tróia") -> None:
        self.csv_path = csv_path
        self.location_label = location_label


class _StubWeatherService:
    enabled = True

    def __init__(self, location: str = "Setúbal") -> None:
        self.location = location


class _StubWaveService:
    enabled = True

    def __init__(self, status_payload: dict | None = None, station_name: str = "Sines") -> None:
        self._status_payload = status_payload or {}
        self.station_name = station_name

    def status(self) -> dict:
        return dict(self._status_payload)


class _StubLocalWarningService:
    enabled = True

    def __init__(self, status_payload: dict | None = None) -> None:
        self._status_payload = status_payload or {}

    def status(self) -> dict:
        return dict(self._status_payload)


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
            "De onde o bot aprende",
            "O que o bot está a aprender",
            "Só o que precisa",
            "Evals de conhecimento",
            "Definições e aprendizagem",
            "Pass rate:",
        ):
            self.assertIn(marker, html, msg=f"missing marker: {marker}")

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

    def test_exceptions_link_to_casebooks_focus_for_blocked_corrections(self) -> None:
        conversation = self.store.create_conversation("admin", "Revisao")
        self.store.append_chat_message("admin", conversation["id"], "user", "Qual o calado praticavel?")
        answer = self.store.append_chat_message(
            "admin",
            conversation["id"],
            "assistant",
            "Resposta provisoria.",
            channel="whatsapp",
        )
        self.store.update_message_feedback(
            username="admin",
            conversation_id=conversation["id"],
            message_id=answer["id"],
            feedback_status="review",
            feedback_note="Precisa de correcao.",
            feedback_correction="Corrigir com base no documento certo.",
            feedback_updated_by="admin",
        )

        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get("/admin/bot")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Correção bloqueada", html)
        self.assertIn(f"focus=chat-{answer['id']}", html)
        self.assertIn(f"#chat-{answer['id']}", html)

    def test_quality_failures_expose_document_and_correction_actions(self) -> None:
        knowledge_dir = Path(self.store.knowledge_dir)
        (knowledge_dir / "evals").mkdir(parents=True, exist_ok=True)
        (knowledge_dir / "IT-001_Operacoes.txt").write_text("Conteudo base.", encoding="utf-8")
        (knowledge_dir / "IT-002_Docas.txt").write_text("Conteudo base.", encoding="utf-8")
        (knowledge_dir / "evals" / "critical_document_companion_evals.json").write_text(
            json.dumps(
                [
                    {
                        "document": "IT-001_Operacoes.txt",
                        "question": "Qual o procedimento?",
                        "expected_answer": "Procedimento validado.",
                        "expected_substrings": ["Procedimento"],
                    }
                ],
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        self.store.upsert_feedback_eval_case(
            source_message_id="msg-review",
            document="IT-002_Docas.txt",
            question="Que docas existem?",
            expected_answer="Doca 1 e Doca 2.",
            expected_substrings=["Doca 1"],
            updated_by="admin",
            source="whatsapp",
        )

        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get("/admin/bot")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Origem: Eval estático", html)
        self.assertIn("Origem: Correção promovida", html)
        self.assertIn("Abrir documento", html)
        self.assertIn("Abrir correção", html)

    def test_operational_source_combines_activity_and_live_feed_states(self) -> None:
        tide_csv = Path(self.temp_dir.name) / "setubal_tides.csv"
        tide_csv.write_text("date,height\n", encoding="utf-8")

        with patch.object(services, "tide_service", _StubTideService(str(tide_csv))):
            with patch.object(services, "weather_service", _StubWeatherService("Setúbal")):
                with patch.object(
                    services,
                    "wave_service",
                    _StubWaveService({"cache_updated_at_label": "21/04/2026, 11:20"}),
                ):
                    with patch.object(
                        services,
                        "local_warning_service",
                        _StubLocalWarningService({"count": 2, "cache_updated_at_label": "21/04/2026, 11:18"}),
                    ):
                        sources = build_sources_snapshot()

        operational = next(item for item in sources if item["id"] == "operational_data")
        self.assertEqual(operational["state"], "online")
        self.assertEqual(operational["count"], 4)
        self.assertEqual(operational["count_label"], "sinal(is) ativos")
        self.assertEqual(operational["meta"], "4/4 feed(s) disponíveis")
        self.assertEqual(operational["action_url"], "/dashboard")
        self.assertEqual(operational["detail_lines"][0]["label"], "Atividade")
        self.assertIn("0 escala(s) agendada(s)", operational["detail_lines"][0]["detail"])
        self.assertIn("Setúbal / Tróia", operational["detail_lines"][1]["detail"])
        self.assertIn("Setúbal", operational["detail_lines"][2]["detail"])

    def test_admin_bot_page_renders_operational_feed_breakdown(self) -> None:
        tide_csv = Path(self.temp_dir.name) / "setubal_tides.csv"
        tide_csv.write_text("date,height\n", encoding="utf-8")

        with patch.object(services, "tide_service", _StubTideService(str(tide_csv))):
            with patch.object(services, "weather_service", _StubWeatherService("Setúbal")):
                with patch.object(
                    services,
                    "wave_service",
                    _StubWaveService({"stale": True, "cache_updated_at_label": "21/04/2026, 11:20"}),
                ):
                    with patch.object(
                        services,
                        "local_warning_service",
                        _StubLocalWarningService({"count": 2, "cache_updated_at_label": "21/04/2026, 11:18"}),
                    ):
                        with app.app.test_client() as client:
                            self._set_admin_session(client)
                            response = client.get("/admin/bot")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("4/4 feed(s) disponíveis", html)
        self.assertIn("sinal(is) ativos", html)
        self.assertIn("Atividade:</strong> 0 escala(s) agendada(s)", html)
        self.assertIn("Maré:</strong> Setúbal / Tróia", html)
        self.assertIn("Meteorologia:</strong> Setúbal", html)
        self.assertIn("Ondulação:</strong> Cache local", html)
        self.assertNotIn("0 escala(s) resolvidas", html)


if __name__ == "__main__":
    unittest.main()
