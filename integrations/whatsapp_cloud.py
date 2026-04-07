from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Any

import requests

DEFAULT_WELCOME_MESSAGE = (
    "👋 Bem-vindo ao PRAGtico\n\n"
    "O teu assistente inteligente para coordenação eficiente de manobras portuárias.\n"
    "Em que posso ajudar? 🤖"
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _normalize_multiline_env(value: str | None) -> str:
    return str(value or "").replace("\\n", "\n").strip()


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
        welcome_enabled: bool = False,
        welcome_message: str = "",
        welcome_template_name: str = "",
        welcome_template_language: str = "pt_PT",
        timeout: int = 15,
    ) -> None:
        self.enabled = bool(enabled)
        self.verify_token = (verify_token or "").strip()
        self.access_token = (access_token or "").strip()
        self.phone_number_id = (phone_number_id or "").strip()
        self.business_account_id = (business_account_id or "").strip()
        self.graph_api_version = (graph_api_version or "v25.0").strip()
        self.default_role = (default_role or "agente").strip().lower() or "agente"
        self.welcome_enabled = bool(welcome_enabled)
        self.welcome_message = str(welcome_message or "").strip()
        self.welcome_template_name = str(welcome_template_name or "").strip()
        self.welcome_template_language = str(welcome_template_language or "pt_PT").strip() or "pt_PT"
        self.timeout = max(int(timeout or 15), 1)
        self.allowed_numbers = {
            self.normalize_phone_number(item)
            for item in str(allowed_numbers or "").split(",")
            if self.normalize_phone_number(item)
        }

    @classmethod
    def from_env(cls) -> "WhatsAppCloudService":
        return cls(
            enabled=_env_flag("WHATSAPP_ENABLED", default=False),
            verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN", ""),
            access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
            phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
            business_account_id=os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", ""),
            graph_api_version=os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0"),
            allowed_numbers=os.getenv("WHATSAPP_ALLOWED_NUMBERS", ""),
            default_role=os.getenv("WHATSAPP_DEFAULT_ROLE", "agente"),
            welcome_enabled=_env_flag("WHATSAPP_WELCOME_ENABLED", default=True),
            welcome_message=(
                _normalize_multiline_env(os.getenv("WHATSAPP_WELCOME_MESSAGE"))
                or DEFAULT_WELCOME_MESSAGE
            ),
            welcome_template_name=os.getenv("WHATSAPP_WELCOME_TEMPLATE_NAME", ""),
            welcome_template_language=os.getenv("WHATSAPP_WELCOME_TEMPLATE_LANGUAGE", "pt_PT"),
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

    @property
    def business_profile_ready(self) -> bool:
        return self.send_ready

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

    def build_welcome_message(self, inbound: dict[str, Any] | None = None) -> str:
        if not self.welcome_enabled:
            return ""
        return self.welcome_message

    def should_send_welcome_template(self) -> bool:
        return self.welcome_enabled and bool(self.welcome_template_name)

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

    def get_business_profile(self, *, fields: list[str] | None = None) -> dict[str, Any]:
        if not self.business_profile_ready:
            raise RuntimeError("WhatsApp Cloud API sem credenciais de business profile configuradas.")
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(item.strip() for item in fields if item.strip())
        response = requests.get(
            f"https://graph.facebook.com/{self.graph_api_version}/{self.phone_number_id}/whatsapp_business_profile",
            headers={
                "Authorization": f"Bearer {self.access_token}",
            },
            params=params or None,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def create_upload_session(self, *, file_name: str, file_type: str, file_length: int) -> str:
        if not self.business_profile_ready:
            raise RuntimeError("WhatsApp Cloud API sem credenciais de business profile configuradas.")
        response = requests.post(
            f"https://graph.facebook.com/{self.graph_api_version}/app/uploads",
            headers={
                "Authorization": f"OAuth {self.access_token}",
            },
            params={
                "file_name": file_name,
                "file_type": file_type,
                "file_length": int(file_length),
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        upload_id = str(payload.get("id") or "").strip()
        if not upload_id:
            raise RuntimeError("A Meta não devolveu um upload session id.")
        return upload_id

    def upload_file_data(self, upload_id: str, *, file_bytes: bytes, file_type: str) -> str:
        if not self.business_profile_ready:
            raise RuntimeError("WhatsApp Cloud API sem credenciais de business profile configuradas.")
        response = requests.post(
            f"https://graph.facebook.com/{self.graph_api_version}/{upload_id}",
            headers={
                "Authorization": f"OAuth {self.access_token}",
                "Content-Type": file_type,
                "file_offset": "0",
            },
            data=file_bytes,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        handle = str(payload.get("h") or payload.get("handle") or "").strip()
        if not handle:
            raise RuntimeError("A Meta não devolveu o profile picture handle.")
        return handle

    def upload_profile_picture(self, file_path: str | os.PathLike[str]) -> str:
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Ficheiro de imagem não encontrado: {path}")
        file_type = mimetypes.guess_type(path.name)[0] or ""
        if file_type not in {"image/png", "image/jpeg"}:
            raise ValueError("A imagem de perfil deve ser PNG ou JPEG.")
        file_bytes = path.read_bytes()
        upload_id = self.create_upload_session(
            file_name=path.name,
            file_type=file_type,
            file_length=len(file_bytes),
        )
        return self.upload_file_data(upload_id, file_bytes=file_bytes, file_type=file_type)

    def update_business_profile(
        self,
        *,
        about: str | None = None,
        address: str | None = None,
        description: str | None = None,
        email: str | None = None,
        profile_picture_handle: str | None = None,
        vertical: str | None = None,
        websites: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.business_profile_ready:
            raise RuntimeError("WhatsApp Cloud API sem credenciais de business profile configuradas.")

        payload: dict[str, Any] = {"messaging_product": "whatsapp"}
        optional_fields = {
            "about": about,
            "address": address,
            "description": description,
            "email": email,
            "profile_picture_handle": profile_picture_handle,
            "vertical": vertical,
        }
        for field_name, field_value in optional_fields.items():
            clean_value = str(field_value or "").strip()
            if clean_value:
                payload[field_name] = clean_value
        if websites:
            clean_websites = [str(item).strip() for item in websites if str(item).strip()]
            if clean_websites:
                payload["websites"] = clean_websites[:2]

        response = requests.post(
            f"https://graph.facebook.com/{self.graph_api_version}/{self.phone_number_id}/whatsapp_business_profile",
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def update_profile_picture(self, file_path: str | os.PathLike[str]) -> dict[str, Any]:
        handle = self.upload_profile_picture(file_path)
        return self.update_business_profile(profile_picture_handle=handle)

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

    def send_template_message(
        self,
        to_number: str,
        *,
        template_name: str,
        language_code: str = "pt_PT",
        reply_to_message_id: str = "",
    ) -> dict[str, Any]:
        if not self.send_ready:
            raise RuntimeError("WhatsApp Cloud API sem credenciais de envio configuradas.")
        target_number = self.normalize_phone_number(to_number)
        if not target_number:
            raise ValueError("Número de destino inválido.")
        clean_name = str(template_name or "").strip()
        if not clean_name:
            raise ValueError("Template WhatsApp inválido.")
        clean_language = str(language_code or "pt_PT").strip() or "pt_PT"
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": target_number,
            "type": "template",
            "template": {
                "name": clean_name,
                "language": {"code": clean_language},
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
