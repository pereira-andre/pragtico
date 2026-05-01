from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from flask import Flask

from core import services
from blueprints.whatsapp import (
    _claim_inbound_processing,
    _is_duplicate_inbound,
    _mark_inbound_processed,
    _processed_inbound_key,
    _send_and_record_outbound_message,
    _split_whatsapp_text,
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


if __name__ == "__main__":
    unittest.main()
