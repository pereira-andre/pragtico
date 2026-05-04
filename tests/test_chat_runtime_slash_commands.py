from __future__ import annotations

import unittest

from flask import Flask

from core import services
from core.chat_runtime import handle_chat_turn


class FakeRag:
    client = None
    last_index_error = ""

    def has_active_reindex_worker(self) -> bool:
        return False

    def has_pending_reindex(self) -> bool:
        return False


class FakeStore:
    backend_name = "fake"

    def __init__(self, *, fail_runtime_state: bool = False) -> None:
        self.fail_runtime_state = fail_runtime_state
        self.runtime_state: dict = {}
        self.messages: list[dict] = []
        self.port_call = {
            "id": "pc1",
            "reference_code": "PTSET26GALB123",
            "vessel_name": "GALBOT",
            "last_port": "Sines",
            "next_port": "Lisboa",
            "berth": "Tanquisado (lado jusante)",
            "maneuver_history": [
                {
                    "id": "86683899-6bb9-4539-a54e-25a2d410768d",
                    "type": "departure",
                    "state": "pending",
                    "planned_at": "2026-04-30T19:12:00+01:00",
                    "planned_input_value": "2026-04-30T19:12",
                    "origin": "Tanquisado (lado jusante)",
                    "destination": "Lisboa",
                    "planned_draft_m": "7.1",
                    "tug_count": "1",
                    "constraints": [],
                    "plan_observations": "",
                }
            ],
        }
        self.activity = {
            "stats": {},
            "arrivals": [],
            "in_port": [],
            "departed": [],
            "aborted": [],
            "departure_candidates": [],
            "archived_maneuvers": [],
            "planned_maneuvers": [
                {
                    "port_call_id": "pc1",
                    "reference_code": "PTSET26GALB123",
                    "vessel_name": "GALBOT",
                    "maneuver_id": "86683899-6bb9-4539-a54e-25a2d410768d",
                    "maneuver_label": "Sair",
                    "situation_class": "pending",
                    "situation_label": "Pendente",
                    "date_value": "2026-04-30T19:12:00+01:00",
                }
            ],
        }

    def list_documents(self) -> list:
        return []

    def ensure_conversation(self, username: str, conversation_id: str | None = None) -> dict:
        return {"id": conversation_id or "conv1", "username": username}

    def list_messages(self, username: str, conversation_id: str) -> list:
        return []

    def append_chat_message(self, username: str, conversation_id: str, role: str, content: str, citations=None, **kwargs) -> dict:
        message = {
            "id": f"m{len(self.messages) + 1}",
            "content": content,
            "created_at_label": "30/04/2026, 17:50:17",
            "citations": citations or [],
        }
        self.messages.append(message)
        return message

    def find_feedback_matches(self, *args, **kwargs) -> list:
        return []

    def get_port_activity_snapshot(self, window_days: int = 5) -> dict:
        return self.activity

    def get_port_call(self, port_call_id: str) -> dict:
        if port_call_id != self.port_call["id"]:
            raise KeyError(port_call_id)
        return self.port_call

    def get_user_profile(self, username: str) -> dict:
        return {
            "username": username,
            "role": "admin",
            "display_name": "Administrador",
            "organization": "APSS",
        }

    def get_runtime_state(self, key: str):
        return self.runtime_state.get(key)

    def set_runtime_state(self, key: str, value: dict) -> dict:
        if self.fail_runtime_state:
            raise RuntimeError("runtime state indisponível")
        self.runtime_state[key] = value
        return value

    def delete_runtime_state(self, key: str) -> None:
        self.runtime_state.pop(key, None)


class ChatRuntimeSlashCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = services.store
        self.previous_rag = services.rag
        self.previous_retry_scheduler = services.reindex_retry_scheduler
        services.rag = FakeRag()
        services.reindex_retry_scheduler = None
        self.app = Flask(__name__)
        self.app.secret_key = "test"

    def tearDown(self) -> None:
        services.store = self.previous_store
        services.rag = self.previous_rag
        services.reindex_retry_scheduler = self.previous_retry_scheduler

    def test_edit_maneuver_command_returns_diagnostic_pending_action(self) -> None:
        services.store = FakeStore()
        command = (
            "/editar-manobra\n"
            "ID da manobra: 86683899\n"
            "Tipo de manobra: saída\n"
            "Hora prevista: 01/05/2026, 19:00\n"
            "Motivo da alteração: Carga atrasada\n"
        )

        with self.app.test_request_context("/api/chat"):
            result = handle_chat_turn(username="admin@porto.pt", role="admin", question=command)

        self.assertEqual(result["answer_origin"], "slash_proposal")
        self.assertIn("Elementos reconhecidos:", result["answer"])
        self.assertIn("ID da manobra: 86683899-6bb9-4539-a54e-25a2d410768d", result["answer"])
        self.assertIn("Tipo de manobra: saída", result["answer"])
        self.assertIn("hora prevista: 01/05/2026, 19:00", result["answer"])
        self.assertIn("Confirma para aplicar", result["answer"])

    def test_empty_edit_maneuver_template_returns_missing_fields(self) -> None:
        services.store = FakeStore()
        command = (
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
            "Motivo da alteração:\n"
        )

        with self.app.test_request_context("/api/chat"):
            result = handle_chat_turn(username="admin@porto.pt", role="admin", question=command)

        self.assertEqual(result["answer_origin"], "slash_template")
        self.assertIn("Em falta/corrigir:", result["answer"])
        self.assertIn("motivo da alteração", result["answer"])
        self.assertIn("ref ou nome do navio", result["answer"])
        self.assertIn("tipo de manobra", result["answer"])
        self.assertIn("campo a alterar", result["answer"])

    def test_runtime_state_failure_does_not_escape_as_http_500(self) -> None:
        services.store = FakeStore(fail_runtime_state=True)
        command = (
            "/editar-manobra\n"
            "ID da manobra: 86683899\n"
            "Tipo de manobra: saída\n"
            "Hora prevista: 01/05/2026, 19:00\n"
            "Motivo da alteração: Carga atrasada\n"
        )

        with self.app.test_request_context("/api/chat"), self.assertLogs("core.chat_runtime", level="ERROR"):
            result = handle_chat_turn(username="admin@porto.pt", role="admin", question=command)

        self.assertEqual(result["answer_origin"], "slash_proposal")
        self.assertIn("Não consegui guardar a proposta pendente", result["answer"])
        self.assertIn("Elementos reconhecidos:", result["answer"])

    def test_blackout_emergency_uses_direct_emergency_answer_not_tug_positioning(self) -> None:
        services.store = FakeStore()
        question = (
            "Um navio teve um problema. Blackout, não tem o rebocadores pedidos "
            "nem há nenhum por perto para o ajudar. O que aconselhas de imediato?"
        )

        with self.app.test_request_context("/api/chat"):
            result = handle_chat_turn(username="admin@porto.pt", role="admin", question=question)

        self.assertEqual(result["answer_origin"], "operational_emergency_response")
        self.assertIn("Blackout/sem maquina", result["answer"])
        self.assertIn("largar ferro", result["answer"])
        self.assertIn("VHF 73", result["answer"])
        self.assertNotIn("Regra prática de posicionamento", result["answer"])
        self.assertNotIn("Normalmente so se mete rebocador a proa", result["answer"])

    def test_terse_operational_fragment_asks_for_reformulation(self) -> None:
        services.store = FakeStore()

        with self.app.test_request_context("/api/chat"):
            result = handle_chat_turn(username="admin@porto.pt", role="admin", question="Navio reboques fundear")

        self.assertEqual(result["answer_origin"], "operational_clarification")
        self.assertIn("Reformula", result["answer"])
        self.assertIn("fundear", result["answer"])
        self.assertNotIn("A resposta direta", result["answer"])
        self.assertNotIn("GGp", result["answer"])

    def test_navigation_light_question_uses_direct_light_source(self) -> None:
        services.store = FakeStore()

        with self.app.test_request_context("/api/chat"):
            result = handle_chat_turn(
                username="admin@porto.pt",
                role="admin",
                question="Qual e a caracteristica da Boia 2CS no Canal Sul?",
            )

        self.assertEqual(result["answer_origin"], "navigation_lights")
        self.assertIn("Boia N.º 2CS", result["answer"])
        self.assertIn("Fl R 3s", result["answer"])
        self.assertIn("IALA A", result["answer"])

    def test_colreg_slash_command_uses_structured_rule_answer(self) -> None:
        services.store = FakeStore()

        with self.app.test_request_context("/api/chat"):
            result = handle_chat_turn(username="admin@porto.pt", role="admin", question="/colreg 23")

        self.assertEqual(result["answer_origin"], "slash_colreg")
        self.assertIn("Regra 23 - Navios de propulsão mecânica a navegar", result["answer"])
        self.assertIn("⚪ farol de mastro a vante", result["answer"])
        self.assertIn("🔴 BB + 🟢 EB", result["answer"])


if __name__ == "__main__":
    unittest.main()
