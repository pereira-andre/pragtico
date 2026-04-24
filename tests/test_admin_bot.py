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
from blueprints import admin as admin_blueprint  # noqa: E402
from core import services  # noqa: E402
from core.bot_insights import build_sources_snapshot  # noqa: E402
from core.bot_settings import DEFAULTS, list_bot_settings_history, load_bot_settings  # noqa: E402
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
        admin_blueprint._invalidate_admin_bot_snapshot_cache()

    def tearDown(self) -> None:
        admin_blueprint._invalidate_admin_bot_snapshot_cache()
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
            response = client.get("/admin/bot?source_type=chat&chat_feedback=review")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for marker in (
            "Painel operacional",
            "Snapshot do painel atualizado",
            "Playground",
            "Como decidiu",
            "De onde o bot aprende",
            "O que o bot está a aprender",
            "Só o que precisa",
            "Evals de conhecimento",
            "Definições e aprendizagem",
            "Histórico recente",
            "Pass rate:",
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

    def test_settings_history_supports_rollback(self) -> None:
        with app.app.test_client() as client:
            csrf = self._set_admin_session(client)
            client.post(
                "/admin/bot/settings",
                data={"csrf_token": csrf, "signals_window_hours": "48"},
            )
            first_revision = list_bot_settings_history(limit=1)[0]["revision_id"]
            client.post(
                "/admin/bot/settings",
                data={"csrf_token": csrf, "signals_window_hours": "72"},
            )
            response = client.post(
                f"/admin/bot/settings/rollback/{first_revision}",
                data={"csrf_token": csrf},
            )

        self.assertIn(response.status_code, (200, 302))
        self.assertEqual(load_bot_settings()["signals_window_hours"], 48)
        history = list_bot_settings_history(limit=3)
        self.assertEqual(history[0]["action"], "rollback")
        self.assertEqual(history[1]["action"], "update")

    def test_admin_bot_snapshot_cache_reuses_payload_within_ttl(self) -> None:
        with patch("blueprints.admin._build_admin_bot_snapshot_payload", wraps=admin_blueprint._build_admin_bot_snapshot_payload) as builder:
            with app.app.test_client() as client:
                self._set_admin_session(client)
                first = client.get("/admin/bot")
                second = client.get("/admin/bot")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(builder.call_count, 1)

    def test_playground_returns_answer_shape(self) -> None:
        fake_answer = {
            "answer": "Fundeadouro Norte é o principal para aguardar cais.",
            "sources": [{"document": "IT-015_Fundeadouros.txt", "snippet": "Fundeadouro Norte"}],
            "answer_origin": "document_companion",
            "trace": {
                "route_label": "Companion do documento",
                "route_detail": "Resposta direta.",
                "rows": [{"label": "Documento alvo", "detail": "IT-015_Fundeadouros.txt"}],
                "flags": [{"label": "Síntese", "value": "Não"}],
            },
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
        self.assertEqual(body["trace"]["route_label"], "Companion do documento")
        self.assertEqual(body["trace"]["rows"][0]["label"], "Documento alvo")

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
        self.assertIn("bot_settings_history", payload["payload"])
        self.assertEqual(payload["payload"]["bot_settings"]["signals_window_hours"], 96)
        self.assertEqual(len(payload["payload"]["bot_settings_history"]), 1)

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
            response = client.get("/admin/bot?source_type=chat&chat_feedback=review")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Correção sem documento alvo", html)
        self.assertIn(f"focus=chat-{answer['id']}", html)
        self.assertIn(f"#chat-{answer['id']}", html)

    def test_ready_review_correction_leaves_exceptions_and_is_marked_as_ready(self) -> None:
        conversation = self.store.create_conversation("admin", "Revisao")
        self.store.append_chat_message(
            "admin",
            conversation["id"],
            "user",
            "A que distância está a posição de embarque dos pilotos da entrada da barra?",
        )
        answer = self.store.append_chat_message(
            "admin",
            conversation["id"],
            "assistant",
            "Resposta errada.",
            citations=[{"document": "Notas_Pilotagem.txt", "snippet": "Distância operacional."}],
            channel="whatsapp",
        )
        self.store.update_message_feedback(
            username="admin",
            conversation_id=conversation["id"],
            message_id=answer["id"],
            feedback_status="review",
            feedback_note="Correção pronta.",
            feedback_correction="A posição de embarque dos pilotos encontra-se a 1 milha náutica da entrada da barra.",
            feedback_updated_by="admin",
        )

        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get("/admin/bot?source_type=chat&chat_feedback=review")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Correção supervisionada pronta", html)
        self.assertIn("Notas_Pilotagem.txt", html)
        self.assertNotIn("Correção bloqueada", html)

    def test_archived_chat_feedback_leaves_exception_queue_and_has_own_state(self) -> None:
        conversation = self.store.create_conversation("admin", "Arquivo")
        self.store.append_chat_message("admin", conversation["id"], "user", "Pergunta antiga")
        answer = self.store.append_chat_message(
            "admin",
            conversation["id"],
            "assistant",
            "Resposta antiga.",
            channel="whatsapp",
        )
        self.store.update_message_feedback(
            username="admin",
            conversation_id=conversation["id"],
            message_id=answer["id"],
            feedback_status="ignored",
            feedback_note="Já não acrescenta valor.",
            feedback_updated_by="admin",
        )

        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get("/admin/bot?source_type=chat&chat_feedback=ignored")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Arquivada", html)
        self.assertIn("Já não acrescenta valor.", html)
        self.assertNotIn("Correção bloqueada", html)

    def test_archiving_chat_feedback_removes_promoted_eval_case(self) -> None:
        conversation = self.store.create_conversation("admin", "Arquivo eval")
        self.store.append_chat_message("admin", conversation["id"], "user", "Qual é a distância?")
        answer = self.store.append_chat_message(
            "admin",
            conversation["id"],
            "assistant",
            "Resposta errada.",
            citations=[{"document": "Notas_Pilotagem.txt", "snippet": "Distância operacional."}],
            channel="whatsapp",
        )
        self.store.update_message_feedback(
            username="admin",
            conversation_id=conversation["id"],
            message_id=answer["id"],
            feedback_status="review",
            feedback_note="Corrigir.",
            feedback_correction="A posição de embarque dos pilotos encontra-se a 1 milha náutica da entrada da barra.",
            feedback_updated_by="admin",
        )
        self.store.upsert_feedback_eval_case(
            source_message_id=answer["id"],
            document="Notas_Pilotagem.txt",
            question="Qual é a distância?",
            expected_answer="A posição de embarque dos pilotos encontra-se a 1 milha náutica da entrada da barra.",
            expected_substrings=["1 milha náutica"],
            updated_by="admin",
            source="web",
        )

        with app.app.test_client() as client:
            csrf = self._set_admin_session(client)
            response = client.post(
                f"/admin/casebooks/messages/{answer['id']}/feedback",
                data={
                    "csrf_token": csrf,
                    "owner_username": "admin",
                    "conversation_id": conversation["id"],
                    "return_to": "/admin/bot#casebooks",
                    "feedback_status": "ignored",
                },
            )

        self.assertEqual(response.status_code, 302)
        updated = next(item for item in self.store.list_messages("admin", conversation["id"]) if item["id"] == answer["id"])
        self.assertEqual(updated["feedback_status"], "ignored")
        self.assertEqual(updated["feedback_correction"], "")
        self.assertEqual(self.store.list_feedback_eval_cases(), [])

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
        self.assertIn("tem de existir também no companion do documento", html)

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
