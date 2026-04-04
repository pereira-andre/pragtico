from __future__ import annotations

import os
import re
from typing import Any

import requests


class WhatsAppCloudService:
    """Minimal WhatsApp Cloud API adapter for webhook verification and test replies."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        verify_token: str = "",
        access_token: str = "",
        phone_number_id: str = "",
        business_account_id: str = "",
        graph_api_version: str = "v25.0",
        allowed_numbers: str = "",
        default_role: str = "agente",
        timeout: int = 15,
    ) -> None:
        self.enabled = bool(enabled)
        self.verify_token = (verify_token or "").strip()
        self.access_token = (access_token or "").strip()
        self.phone_number_id = (phone_number_id or "").strip()
        self.business_account_id = (business_account_id or "").strip()
        self.graph_api_version = (graph_api_version or "v25.0").strip()
        self.default_role = (default_role or "agente").strip().lower() or "agente"
        self.timeout = max(int(timeout or 15), 1)
        self.allowed_numbers = {
            self.normalize_phone_number(item)
            for item in str(allowed_numbers or "").split(",")
            if self.normalize_phone_number(item)
        }

    @classmethod
    def from_env(cls) -> "WhatsAppCloudService":
        return cls(
            enabled=os.getenv("WHATSAPP_ENABLED", "0").strip().lower() not in {"0", "false", "no", "off"},
            verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN", ""),
            access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
            phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
            business_account_id=os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", ""),
            graph_api_version=os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0"),
            allowed_numbers=os.getenv("WHATSAPP_ALLOWED_NUMBERS", ""),
            default_role=os.getenv("WHATSAPP_DEFAULT_ROLE", "agente"),
            timeout=int(os.getenv("WHATSAPP_TIMEOUT_SECONDS", "15")),
        )

    @staticmethod
    def normalize_phone_number(value: str | None) -> str:
        return re.sub(r"\D+", "", str(value or ""))

    @property
    def webhook_ready(self) -> bool:
        return self.enabled and bool(self.verify_token)

    @property
    def send_ready(self) -> bool:
        return self.enabled and bool(self.access_token) and bool(self.phone_number_id)

    def verify_webhook(self, mode: str | None, token: str | None) -> bool:
        return (mode or "").strip() == "subscribe" and (token or "").strip() == self.verify_token

    def is_allowed_number(self, number: str | None) -> bool:
        normalized = self.normalize_phone_number(number)
        if not normalized:
            return False
        if not self.allowed_numbers:
            return True
        return normalized in self.allowed_numbers

    def build_test_reply(self, inbound: dict[str, Any]) -> str:
        sender = inbound.get("profile_name") or inbound.get("from_number") or "utilizador"
        text = (inbound.get("text") or "").strip()
        if text.lower() in {"/ping", "ping", "teste", "test"}:
            return "PRAGtico WhatsApp teste ativo. Receção e envio operacional confirmados."
        if text:
            return (
                f"PRAGtico WhatsApp em modo de teste.\n"
                f"Recebi a tua mensagem, {sender}:\n"
                f"“{text}”\n\n"
                "Se esta resposta chegou, o webhook e o envio pela Cloud API estão a funcionar."
            )
        return "PRAGtico WhatsApp teste ativo. Mensagem recebida sem texto utilizável."

    def parse_inbound_messages(self, payload: dict[str, Any] | None) -> list[dict[str, Any]]:
        return [
            event
            for event in self.parse_webhook_events(payload)
            if event.get("event_type") == "message_text"
        ]

    def parse_webhook_events(self, payload: dict[str, Any] | None) -> list[dict[str, Any]]:
        entries = payload.get("entry") if isinstance(payload, dict) else []
        parsed: list[dict[str, Any]] = []
        for entry in entries or []:
            for change in (entry.get("changes") or []):
                value = change.get("value") or {}
                contacts = value.get("contacts") or []
                contact_map = {
                    self.normalize_phone_number(contact.get("wa_id")): contact
                    for contact in contacts
                    if self.normalize_phone_number(contact.get("wa_id"))
                }
                for message in (value.get("messages") or []):
                    from_number = self.normalize_phone_number(message.get("from"))
                    contact = contact_map.get(from_number, {})
                    profile = contact.get("profile") or {}
                    message_type = (message.get("type") or "").strip().lower()
                    if message_type == "text":
                        parsed.append(
                            {
                                "event_type": "message_text",
                                "message_id": (message.get("id") or "").strip(),
                                "from_number": from_number,
                                "profile_name": (profile.get("name") or "").strip(),
                                "text": str((message.get("text") or {}).get("body") or "").strip(),
                                "timestamp": str(message.get("timestamp") or "").strip(),
                                "raw": message,
                            }
                        )
                    elif message_type == "reaction":
                        reaction = message.get("reaction") or {}
                        parsed.append(
                            {
                                "event_type": "message_reaction",
                                "message_id": (message.get("id") or "").strip(),
                                "target_message_id": (reaction.get("message_id") or "").strip(),
                                "from_number": from_number,
                                "profile_name": (profile.get("name") or "").strip(),
                                "emoji": str(reaction.get("emoji") or "").strip(),
                                "timestamp": str(message.get("timestamp") or "").strip(),
                                "raw": message,
                            }
                        )
                for status in (value.get("statuses") or []):
                    status_message_id = (status.get("id") or "").strip()
                    status_value = (status.get("status") or "").strip().lower()
                    status_timestamp = str(status.get("timestamp") or "").strip()
                    parsed.append(
                        {
                            "event_type": "message_status",
                            "event_id": ":".join(
                                part
                                for part in (status_message_id, status_value, status_timestamp)
                                if part
                            ),
                            "message_id": status_message_id,
                            "status": status_value,
                            "timestamp": status_timestamp,
                            "recipient_id": self.normalize_phone_number(status.get("recipient_id")),
                            "conversation_id": str((status.get("conversation") or {}).get("id") or "").strip(),
                            "raw": status,
                        }
                    )
        return parsed

    def send_text_message(self, to_number: str, text: str, *, reply_to_message_id: str = "") -> dict[str, Any]:
        if not self.send_ready:
            raise RuntimeError("WhatsApp Cloud API sem credenciais de envio configuradas.")
        target_number = self.normalize_phone_number(to_number)
        if not target_number:
            raise ValueError("Número de destino inválido.")
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": target_number,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": str(text or "").strip()[:4096],
            },
        }
        if reply_to_message_id.strip():
            payload["context"] = {"message_id": reply_to_message_id.strip()}

        response = requests.post(
            f"https://graph.facebook.com/{self.graph_api_version}/{self.phone_number_id}/messages",
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
