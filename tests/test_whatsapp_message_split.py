from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from flask import Flask

from core import services
from core.operational_actions import pending_action_state_key
from domain.event_reports import pending_event_report_key
from blueprints.whatsapp import (
    _active_whatsapp_conversation_key,
    _claim_inbound_processing,
    _ensure_whatsapp_conversation,
    _pending_feedback_correction_key,
    _process_whatsapp_context_reset_command,
    _process_whatsapp_start_command,
    _whatsapp_context_reset_requested,
    _is_duplicate_inbound,
    _mark_inbound_processed,
    _processed_inbound_key,
    _send_and_record_outbound_message,
    _split_whatsapp_text,
    _welcome_sent_key,
    _whatsapp_start_requested,
)


class WhatsappMessageSplitTests(unittest.TestCase):
    def test_short_message_is_not_numbered(self) -> None:
        parts = _split_whatsapp_text("Resposta curta.", limit=80)

        self.assertEqual(parts, ["Resposta curta."])

    def test_long_message_is_split_and_numbered_below_limit(self) -> None:
        text = "\n\n".join(f"Bloco {index}: " + ("texto operacional " * 12) for index in range(1, 8))

        parts = _split_whatsapp_text(text, limit=180)

        self.assertGreater(len(parts), 1)
        self.assertTrue(parts[0].startswith("(1/"))
        self.assertTrue(parts[-1].startswith(f"({len(parts)}/{len(parts)}) "))
        self.assertTrue(all(len(part) <= 180 for part in parts))
        self.assertIn("Bloco 1", parts[0])
        self.assertTrue(any("Bloco 7" in part for part in parts))

    def test_oversized_unbroken_text_uses_hard_split(self) -> None:
        parts = _split_whatsapp_text("A" * 420, limit=120)

        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 120 for part in parts))
        self.assertEqual("".join(part.split(" ", 1)[1] for part in parts), "A" * 420)

    def test_sentence_split_keeps_punctuation_with_previous_part(self) -> None:
        text = "Primeira frase operacional. Segunda frase operacional com mais detalhe."

        parts = _split_whatsapp_text(text, limit=42)

        self.assertTrue(parts[0].endswith("operacional."))
        self.assertIn("Segunda frase", parts[1])

    def test_send_records_each_split_part_and_aliases_extra_parts(self) -> None:
        class FakeService:
            def __init__(self) -> None:
                self.sent: list[dict] = []

            def send_text_message(self, to_number: str, text: str, *, reply_to_message_id: str = "") -> dict:
                self.sent.append({"to": to_number, "text": text, "reply_to": reply_to_message_id})
                return {"messages": [{"id": f"wamid-{len(self.sent)}"}]}

        class FakeStore:
            def __init__(self) -> None:
                self.metadata: dict = {}
                self.events: list[dict] = []
                self.runtime_state: dict = {}

            def update_message_channel_metadata(self, username, conversation_id, local_message_id, **kwargs):
                self.metadata = kwargs

            def record_channel_event(self, **kwargs):
                self.events.append(kwargs)

            def set_runtime_state(self, key, value):
                self.runtime_state[key] = value

        previous_store = services.store
        services.store = FakeStore()
        app = Flask(__name__)
        try:
            with app.app_context():
                _send_and_record_outbound_message(
                    FakeService(),
                    username="u",
                    conversation_id="c",
                    local_message_id="m",
                    content="A" * 8000,
                    to_number="351900000000",
                    reply_to_message_id="inbound-1",
                    event_type="outgoing_text",
                )
        finally:
            fake_store = services.store
            services.store = previous_store

        self.assertGreater(len(fake_store.events), 1)
        self.assertEqual(fake_store.metadata["external_message_id"], "wamid-1")
        self.assertEqual(fake_store.metadata["channel_metadata"]["message_count"], len(fake_store.events))
        self.assertEqual(fake_store.events[0]["payload"]["part_index"], 1)
        self.assertIn("whatsapp:outbound:wamid-2", fake_store.runtime_state)

    def test_inbound_processing_claim_blocks_parallel_duplicate(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.runtime_state: dict = {}

            def get_runtime_state(self, key):
                return self.runtime_state.get(key)

            def set_runtime_state(self, key, value):
                self.runtime_state[key] = value

        previous_store = services.store
        services.store = FakeStore()
        app = Flask(__name__)
        try:
            with app.app_context():
                self.assertTrue(_claim_inbound_processing("wamid-in-1", from_number="351900000000"))
                self.assertFalse(_claim_inbound_processing("wamid-in-1", from_number="351900000000"))
                self.assertTrue(_is_duplicate_inbound("wamid-in-1"))
                self.assertEqual(
                    services.store.runtime_state[_processed_inbound_key("wamid-in-1")]["status"],
                    "processing",
                )
        finally:
            services.store = previous_store

    def test_stale_inbound_processing_claim_can_be_retried(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.runtime_state: dict = {
                    _processed_inbound_key("wamid-in-2"): {
                        "status": "processing",
                        "processing_started_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
                    }
                }

            def get_runtime_state(self, key):
                return self.runtime_state.get(key)

            def set_runtime_state(self, key, value):
                self.runtime_state[key] = value

        previous_store = services.store
        services.store = FakeStore()
        app = Flask(__name__)
        try:
            with app.app_context():
                self.assertFalse(_is_duplicate_inbound("wamid-in-2"))
                self.assertTrue(_claim_inbound_processing("wamid-in-2", from_number="351900000000"))
                self.assertTrue(_is_duplicate_inbound("wamid-in-2"))
        finally:
            services.store = previous_store

    def test_processed_inbound_remains_duplicate_after_answer(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.runtime_state: dict = {}

            def get_runtime_state(self, key):
                return self.runtime_state.get(key)

            def set_runtime_state(self, key, value):
                self.runtime_state[key] = value

        previous_store = services.store
        services.store = FakeStore()
        app = Flask(__name__)
        try:
            with app.app_context():
                _mark_inbound_processed(
                    "wamid-in-3",
                    from_number="351900000000",
                    conversation_id="conv-1",
                    answer="Resposta final",
                )
                self.assertTrue(_is_duplicate_inbound("wamid-in-3"))
                self.assertFalse(_claim_inbound_processing("wamid-in-3", from_number="351900000000"))
                self.assertEqual(
                    services.store.runtime_state[_processed_inbound_key("wamid-in-3")]["status"],
                    "processed",
                )
        finally:
            services.store = previous_store

    def test_whatsapp_context_command_aliases_are_detected(self) -> None:
        for command in ("/new", "/nova", "/nova-conversa", "/reset-contexto", "/limpar contexto"):
            with self.subTest(command=command):
                self.assertTrue(_whatsapp_context_reset_requested(command))

        self.assertFalse(_whatsapp_context_reset_requested("/new-manobra"))
        self.assertTrue(_whatsapp_start_requested("/start"))
        self.assertTrue(_whatsapp_start_requested("/iniciar"))

    def test_context_reset_creates_new_conversation_and_clears_transient_state(self) -> None:
        class FakeService:
            def __init__(self) -> None:
                self.sent: list[dict] = []

            def send_text_message(self, to_number: str, text: str, *, reply_to_message_id: str = "") -> dict:
                self.sent.append({"to": to_number, "text": text, "reply_to": reply_to_message_id})
                return {"messages": [{"id": f"wamid-{len(self.sent)}"}]}

        class FakeStore:
            def __init__(self) -> None:
                self.conversations = [
                    {"id": "old-conv", "username": "whatsapp-351900000000@pragtico.local", "title": "Secil"}
                ]
                self.messages: list[dict] = []
                self.events: list[dict] = []
                self.metadata_updates: list[dict] = []
                self.runtime_state: dict = {}

            def ensure_conversation(self, username: str, conversation_id: str | None = None) -> dict:
                if conversation_id:
                    return next(item for item in self.conversations if item["id"] == conversation_id)
                return self.conversations[-1]

            def create_conversation(self, username: str, title: str = "Nova conversa") -> dict:
                conversation = {"id": f"new-conv-{len(self.conversations)}", "username": username, "title": title}
                self.conversations.append(conversation)
                return conversation

            def append_chat_message(self, username, conversation_id, role, content, **kwargs):
                message = {
                    "id": f"m{len(self.messages) + 1}",
                    "username": username,
                    "conversation_id": conversation_id,
                    "role": role,
                    "content": content,
                    "channel_metadata": kwargs.get("channel_metadata") or {},
                }
                self.messages.append(message)
                return message

            def record_channel_event(self, **kwargs):
                self.events.append(kwargs)

            def update_message_channel_metadata(self, username, conversation_id, local_message_id, **kwargs):
                self.metadata_updates.append(
                    {
                        "username": username,
                        "conversation_id": conversation_id,
                        "local_message_id": local_message_id,
                        **kwargs,
                    }
                )

            def get_runtime_state(self, key):
                return self.runtime_state.get(key)

            def set_runtime_state(self, key, value):
                self.runtime_state[key] = value
                return value

            def delete_runtime_state(self, key):
                self.runtime_state.pop(key, None)

        from_number = "351900000000"
        username = "whatsapp-351900000000@pragtico.local"
        old_conversation_id = "old-conv"
        previous_store = services.store
        fake_store = FakeStore()
        fake_store.runtime_state[pending_action_state_key(username, old_conversation_id)] = {"pending": True}
        fake_store.runtime_state[
            pending_event_report_key(
                channel="whatsapp",
                username=username,
                conversation_id=old_conversation_id,
                channel_user_id=from_number,
            )
        ] = {"pending": True}
        fake_store.runtime_state[_pending_feedback_correction_key(from_number)] = {"pending": True}
        services.store = fake_store
        app = Flask(__name__)
        service = FakeService()
        try:
            with app.app_context():
                result = _process_whatsapp_context_reset_command(
                    service,
                    profile={"username": username},
                    from_number=from_number,
                    inbound_message_id="wamid-in-new",
                    event={"raw": {"id": "wamid-in-new"}, "profile_name": "Andre", "timestamp": "1"},
                    text="/new",
                )
        finally:
            services.store = previous_store

        self.assertEqual(result["conversation"]["id"], "new-conv-1")
        self.assertEqual(fake_store.messages[0]["conversation_id"], "new-conv-1")
        self.assertEqual(fake_store.messages[0]["role"], "user")
        self.assertEqual(fake_store.messages[1]["channel_metadata"]["message_kind"], "context_reset")
        self.assertIn("Nova conversa iniciada", service.sent[0]["text"])
        self.assertEqual(
            fake_store.runtime_state[_active_whatsapp_conversation_key(from_number)]["conversation_id"],
            "new-conv-1",
        )
        self.assertNotIn(pending_action_state_key(username, old_conversation_id), fake_store.runtime_state)
        self.assertNotIn(_pending_feedback_correction_key(from_number), fake_store.runtime_state)
        self.assertEqual(fake_store.events[0]["event_type"], "incoming_context_reset")
        self.assertEqual(fake_store.runtime_state[_processed_inbound_key("wamid-in-new")]["conversation_id"], "new-conv-1")

    def test_start_command_sends_welcome_and_marks_it_sent(self) -> None:
        class FakeService:
            def __init__(self) -> None:
                self.sent: list[dict] = []

            def send_text_message(self, to_number: str, text: str, *, reply_to_message_id: str = "") -> dict:
                self.sent.append({"to": to_number, "text": text, "reply_to": reply_to_message_id})
                return {"messages": [{"id": f"wamid-{len(self.sent)}"}]}

        class FakeStore:
            def __init__(self) -> None:
                self.conversation = {"id": "conv-start", "username": "u", "title": "WhatsApp"}
                self.messages: list[dict] = []
                self.events: list[dict] = []
                self.runtime_state: dict = {}

            def ensure_conversation(self, username: str, conversation_id: str | None = None) -> dict:
                return self.conversation

            def append_chat_message(self, username, conversation_id, role, content, **kwargs):
                message = {
                    "id": f"m{len(self.messages) + 1}",
                    "conversation_id": conversation_id,
                    "role": role,
                    "content": content,
                    "channel_metadata": kwargs.get("channel_metadata") or {},
                }
                self.messages.append(message)
                return message

            def record_channel_event(self, **kwargs):
                self.events.append(kwargs)

            def update_message_channel_metadata(self, *args, **kwargs):
                return None

            def get_runtime_state(self, key):
                return self.runtime_state.get(key)

            def set_runtime_state(self, key, value):
                self.runtime_state[key] = value
                return value

        previous_store = services.store
        fake_store = FakeStore()
        services.store = fake_store
        app = Flask(__name__)
        service = FakeService()
        try:
            with app.app_context():
                _process_whatsapp_start_command(
                    service,
                    profile={"username": "u"},
                    from_number="351900000000",
                    inbound_message_id="wamid-start",
                    event={"raw": {"id": "wamid-start"}, "profile_name": "Andre", "timestamp": "1"},
                    text="/start",
                )
        finally:
            services.store = previous_store

        self.assertIn("Sou o PRAGtico", service.sent[0]["text"])
        self.assertEqual(fake_store.messages[-1]["channel_metadata"]["message_kind"], "start")
        self.assertIn(_welcome_sent_key("351900000000"), fake_store.runtime_state)

    def test_active_whatsapp_conversation_survives_old_conversation_status_updates(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.conversations = {
                    "new-conv": {"id": "new-conv", "username": "u", "title": "Nova"},
                    "old-conv": {"id": "old-conv", "username": "u", "title": "Antiga"},
                }
                self.runtime_state = {
                    _active_whatsapp_conversation_key("351900000000"): {
                        "username": "u",
                        "conversation_id": "new-conv",
                    }
                }

            def ensure_conversation(self, username: str, conversation_id: str | None = None) -> dict:
                if conversation_id:
                    return self.conversations.get(conversation_id) or self.conversations["old-conv"]
                return self.conversations["old-conv"]

            def get_runtime_state(self, key):
                return self.runtime_state.get(key)

            def set_runtime_state(self, key, value):
                self.runtime_state[key] = value
                return value

        previous_store = services.store
        services.store = FakeStore()
        app = Flask(__name__)
        try:
            with app.app_context():
                conversation = _ensure_whatsapp_conversation("u", "351900000000")
        finally:
            services.store = previous_store

        self.assertEqual(conversation["id"], "new-conv")


if __name__ == "__main__":
    unittest.main()
