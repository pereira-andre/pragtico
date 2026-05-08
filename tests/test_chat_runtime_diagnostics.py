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

    def __init__(self, messages: list[dict] | None = None) -> None:
        self.messages = list(messages or [])
        self.runtime_state: dict = {}

    def list_documents(self) -> list:
        return []

    def ensure_conversation(self, username: str, conversation_id: str | None = None) -> dict:
        return {"id": conversation_id or "conv1", "username": username}

    def list_messages(self, username: str, conversation_id: str) -> list:
        return list(self.messages)

    def append_chat_message(
        self,
        username: str,
        conversation_id: str,
        role: str,
        content: str,
        citations=None,
        **kwargs,
    ) -> dict:
        message = {
            "id": f"m{len(self.messages) + 1}",
            "role": role,
            "content": content,
            "created_at_label": "08/05/2026, 17:36:00",
            "citations": citations or [],
            "channel_metadata": kwargs.get("channel_metadata") or {},
        }
        self.messages.append(message)
        return message

    def find_feedback_matches(self, *args, **kwargs) -> list:
        return []

    def get_runtime_state(self, key: str):
        return self.runtime_state.get(key)

    def set_runtime_state(self, key: str, value: dict) -> dict:
        self.runtime_state[key] = value
        return value

    def delete_runtime_state(self, key: str) -> None:
        self.runtime_state.pop(key, None)


class ChatRuntimeDiagnosticCommandTests(unittest.TestCase):
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

    def test_web_admin_debug_recomputes_latest_answer_without_stale_case_context(self) -> None:
        services.store = FakeStore(
            [
                {
                    "id": "u1",
                    "role": "user",
                    "content": "Um navio na LISNAVE de 300 m manobra com quantos rebocadores normalmente?",
                },
                {
                    "id": "a1",
                    "role": "assistant",
                    "content": "Recomendo 6 rebocadores grandes.",
                    "channel_metadata": {},
                },
                {
                    "id": "u2",
                    "role": "user",
                    "content": "Com nevoeiro em porto posso avançar?",
                },
                {
                    "id": "a2",
                    "role": "assistant",
                    "content": "Não. Com nevoeiro em porto todas as manobras ficam suspensas.",
                    "channel_metadata": {},
                },
                {
                    "id": "u3",
                    "role": "user",
                    "content": "Marquei manobra de entrada para a Secil E as 1925. Está correto?",
                },
                {
                    "id": "a3",
                    "role": "assistant",
                    "content": (
                        "Para o Cais de Este (Secil E), em marés vivas só se atraca no reponto. "
                        "A preia-mar está prevista para as 20:03."
                    ),
                    "channel_metadata": {
                        "operational_diagnostic": {
                            "present": True,
                            "summary": "Diagnóstico antigo errado: LISNAVE, nevoeiro e 6 rebocadores.",
                        }
                    },
                },
            ]
        )

        with self.app.test_request_context("/api/chat"):
            result = handle_chat_turn(
                username="admin@porto.pt",
                role="admin",
                question="/debug",
                channel="web",
                conversation_id="conv1",
            )

        self.assertEqual(result["answer_origin"], "operational_diagnostic")
        self.assertIn("Local: SECIL", result["answer"])
        self.assertIn("Doca/cais: SECIL E/Este", result["answer"])
        self.assertNotIn("Local: LISNAVE", result["answer"])
        self.assertNotIn("6 rebocador", result["answer"])
        self.assertNotIn("nevoeiro", result["answer"].lower())
        self.assertEqual(
            services.store.messages[-1]["channel_metadata"]["message_kind"],
            "answer_diagnostic",
        )

    def test_web_diagnostic_commands_are_admin_only(self) -> None:
        for command in ("/porque", "/diagnostico", "/debug"):
            with self.subTest(command=command):
                services.store = FakeStore()
                with self.app.test_request_context("/api/chat"):
                    result = handle_chat_turn(
                        username="piloto@porto.pt",
                        role="piloto",
                        question=command,
                        channel="web",
                        conversation_id="conv1",
                    )

                self.assertEqual(result["answer_origin"], "operational_diagnostic_denied")
                self.assertIn("apenas para admin", result["answer"])


if __name__ == "__main__":
    unittest.main()
