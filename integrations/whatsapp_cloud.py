from __future__ import annotations

import mimetypes
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from storage.constants import (
    WHATSAPP_STATUS_API_ERROR,
    WHATSAPP_STATUS_DISABLED,
    WHATSAPP_STATUS_INTERNAL_ERROR,
    WHATSAPP_STATUS_INVALID_NUMBER,
    WHATSAPP_STATUS_LOCAL_DENIED,
    WHATSAPP_STATUS_NETWORK_ERROR,
    WHATSAPP_STATUS_RECIPIENT_NOT_ALLOWED,
    WHATSAPP_STATUS_SENT,
    WHATSAPP_STATUS_TEMPLATE_MISSING,
    WHATSAPP_STATUS_TOKEN_INVALID,
)

DEFAULT_WELCOME_MESSAGE = (
    "👋 Bem-vindo ao PRAGtico\n\n"
    "O teu assistente inteligente para coordenação eficiente de manobras portuárias.\n"
    "Em que posso ajudar? 🤖"
)

WHATSAPP_STATUS_STATE_PREFIX = "whatsapp:status:"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _normalize_multiline_env(value: str | None) -> str:
    return str(value or "").replace("\\n", "\n").strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    @classmethod
    def status_state_key(cls, number: str | None) -> str:
        normalized = cls.normalize_phone_number(number)
        return f"{WHATSAPP_STATUS_STATE_PREFIX}{normalized}" if normalized else ""

    @staticmethod
    def extract_message_id(payload: dict[str, Any] | None) -> str:
        messages = (payload or {}).get("messages") or []
        if not messages:
            return ""
        first = messages[0] or {}
        return str(first.get("id") or "").strip()

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

    @staticmethod
    def _safe_response_json(response: requests.Response | None) -> dict[str, Any]:
        if response is None:
            return {}
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def describe_exception(self, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, requests.HTTPError):
            response = getattr(exc, "response", None)
            payload = self._safe_response_json(response)
            error = payload.get("error") if isinstance(payload, dict) else {}
            error_data = error.get("error_data") if isinstance(error, dict) else {}
            message = str((error or {}).get("message") or "").strip()
            details = str((error_data or {}).get("details") or "").strip()
            combined = " ".join(part for part in (message, details) if part).lower()
            meta_code = int(error.get("code") or 0) if str(error.get("code") or "").isdigit() else error.get("code")
            http_status = getattr(response, "status_code", 0) or 0

            category = WHATSAPP_STATUS_API_ERROR
            summary = "Falha na Meta ao validar ou enviar WhatsApp."
            if http_status in {401, 403} or meta_code == 190 or "access token" in combined or "token" in combined:
                category = WHATSAPP_STATUS_TOKEN_INVALID
                summary = "Token WhatsApp inválido ou expirado."
            elif meta_code == 131030 or "allowed list" in combined or "lista de permiss" in combined:
                category = WHATSAPP_STATUS_RECIPIENT_NOT_ALLOWED
                summary = "Número não autorizado na lista de destinatários da Meta."
            elif (
                meta_code in {132001, 132007, 132012, 132015}
                or ("template" in combined and any(token in combined for token in ("not exist", "não existe", "paused", "disabled", "invalid")))
            ):
                category = WHATSAPP_STATUS_TEMPLATE_MISSING
                summary = "Template WhatsApp em falta, inválido ou indisponível."
            elif meta_code == 100 or "invalid" in combined or "inválido" in combined:
                category = WHATSAPP_STATUS_INVALID_NUMBER
                summary = "Parâmetros inválidos para o envio WhatsApp."

            return {
                "ok": False,
                "category": category,
                "summary": summary,
                "details": details or message or "Erro devolvido pela Meta sem detalhe adicional.",
                "http_status": http_status,
                "meta_code": meta_code or "",
                "meta_type": str((error or {}).get("type") or "").strip(),
                "fbtrace_id": str((error or {}).get("fbtrace_id") or "").strip(),
                "meta_message": message,
                "meta_payload": payload,
            }

        if isinstance(exc, requests.RequestException):
            summary = "Falha de rede ao contactar a WhatsApp Cloud API."
            if isinstance(exc, requests.Timeout):
                summary = "Timeout ao contactar a WhatsApp Cloud API."
            return {
                "ok": False,
                "category": WHATSAPP_STATUS_NETWORK_ERROR,
                "summary": summary,
                "details": str(exc).strip() or "Erro de rede sem detalhe adicional.",
                "http_status": 0,
                "meta_code": "",
                "meta_type": "",
                "fbtrace_id": "",
                "meta_message": "",
                "meta_payload": {},
            }

        return {
            "ok": False,
            "category": WHATSAPP_STATUS_INTERNAL_ERROR,
            "summary": "Falha interna ao preparar o envio WhatsApp.",
            "details": str(exc).strip() or repr(exc),
            "http_status": 0,
            "meta_code": "",
            "meta_type": "",
            "fbtrace_id": "",
            "meta_message": "",
            "meta_payload": {},
        }

    def attempt_template_message(
        self,
        to_number: str,
        *,
        template_name: str,
        language_code: str = "pt_PT",
        reply_to_message_id: str = "",
        source: str = "manual",
    ) -> dict[str, Any]:
        target_number = self.normalize_phone_number(to_number)
        local_allowed = bool(target_number) and self.is_allowed_number(target_number)
        payload = {
            "checked_at": _utc_now_iso(),
            "to_number": target_number,
            "source": str(source or "manual").strip() or "manual",
            "template_name": str(template_name or "").strip(),
            "template_language": str(language_code or "pt_PT").strip() or "pt_PT",
            "local_allowed": local_allowed,
            "send_ready": self.send_ready,
            "enabled": self.enabled,
        }
        if not self.enabled:
            return {
                **payload,
                "ok": False,
                "category": WHATSAPP_STATUS_DISABLED,
                "summary": "WhatsApp está desativado na configuração atual.",
                "details": "Define WHATSAPP_ENABLED=1 para permitir envios.",
            }
        if not target_number:
            return {
                **payload,
                "ok": False,
                "category": WHATSAPP_STATUS_INVALID_NUMBER,
                "summary": "Número WhatsApp inválido.",
                "details": "Indica um número em formato internacional, por exemplo 3519xxxxxxxx.",
            }
        if not self.access_token.strip():
            return {
                **payload,
                "ok": False,
                "category": WHATSAPP_STATUS_TOKEN_INVALID,
                "summary": "WHATSAPP_ACCESS_TOKEN em falta.",
                "details": "Sem access token não é possível validar nem enviar mensagens.",
            }
        if not self.phone_number_id.strip():
            return {
                **payload,
                "ok": False,
                "category": WHATSAPP_STATUS_API_ERROR,
                "summary": "WHATSAPP_PHONE_NUMBER_ID em falta.",
                "details": "Sem phone number id a API da Meta não sabe qual é o número emissor.",
            }
        if not payload["template_name"]:
            return {
                **payload,
                "ok": False,
                "category": WHATSAPP_STATUS_TEMPLATE_MISSING,
                "summary": "Template de verificação não configurado.",
                "details": "Define WHATSAPP_WELCOME_TEMPLATE_NAME ou indica um template válido.",
            }
        if not local_allowed:
            return {
                **payload,
                "ok": False,
                "category": WHATSAPP_STATUS_LOCAL_DENIED,
                "summary": "Número não autorizado pela whitelist local do backend.",
                "details": "Atualiza WHATSAPP_ALLOWED_NUMBERS para permitir este destino neste ambiente.",
            }
        try:
            response = self.send_template_message(
                target_number,
                template_name=payload["template_name"],
                language_code=payload["template_language"],
                reply_to_message_id=reply_to_message_id,
            )
        except Exception as exc:
            return {
                **payload,
                **self.describe_exception(exc),
            }
        return {
            **payload,
            "ok": True,
            "category": WHATSAPP_STATUS_SENT,
            "summary": "Template enviado com sucesso pela Meta.",
            "details": "O número respondeu positivamente à validação de envio.",
            "message_id": self.extract_message_id(response),
            "meta_payload": response,
        }

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
                    elif message_type == "button":
                        button_payload = message.get("button") or {}
                        button_text = str(button_payload.get("text") or "").strip()
                        button_reply_id = str(button_payload.get("payload") or "").strip()
                        normalized_text = button_text or button_reply_id
                        if normalized_text:
                            parsed.append(
                                {
                                    "event_type": "message_text",
                                    "message_id": (message.get("id") or "").strip(),
                                    "from_number": from_number,
                                    "profile_name": (profile.get("name") or "").strip(),
                                    "text": normalized_text,
                                    "timestamp": str(message.get("timestamp") or "").strip(),
                                    "raw": message,
                                }
                            )
                    elif message_type == "interactive":
                        interactive = message.get("interactive") or {}
                        interactive_type = str(interactive.get("type") or "").strip().lower()
                        normalized_text = ""
                        if interactive_type == "button_reply":
                            button_reply = interactive.get("button_reply") or {}
                            normalized_text = (
                                str(button_reply.get("title") or "").strip()
                                or str(button_reply.get("id") or "").strip()
                            )
                        elif interactive_type == "list_reply":
                            list_reply = interactive.get("list_reply") or {}
                            normalized_text = (
                                str(list_reply.get("title") or "").strip()
                                or str(list_reply.get("id") or "").strip()
                            )
                        if normalized_text:
                            parsed.append(
                                {
                                    "event_type": "message_text",
                                    "message_id": (message.get("id") or "").strip(),
                                    "from_number": from_number,
                                    "profile_name": (profile.get("name") or "").strip(),
                                    "text": normalized_text,
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
                    elif message_type in {"image", "document"}:
                        media = message.get(message_type) or {}
                        media_id = str(media.get("id") or "").strip()
                        if media_id:
                            parsed.append(
                                {
                                    "event_type": "message_media",
                                    "message_id": (message.get("id") or "").strip(),
                                    "from_number": from_number,
                                    "profile_name": (profile.get("name") or "").strip(),
                                    "media_kind": message_type,
                                    "media_id": media_id,
                                    "mime_type": str(media.get("mime_type") or "").strip(),
                                    "sha256": str(media.get("sha256") or "").strip(),
                                    "caption": str(media.get("caption") or "").strip(),
                                    "filename": str(media.get("filename") or "").strip(),
                                    "timestamp": str(message.get("timestamp") or "").strip(),
                                    "raw": message,
                                }
                            )
                    elif message_type == "location":
                        location = message.get("location") or {}
                        parsed.append(
                            {
                                "event_type": "message_location",
                                "message_id": (message.get("id") or "").strip(),
                                "from_number": from_number,
                                "profile_name": (profile.get("name") or "").strip(),
                                "latitude": location.get("latitude"),
                                "longitude": location.get("longitude"),
                                "location_name": str(location.get("name") or "").strip(),
                                "location_address": str(location.get("address") or "").strip(),
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

    def download_media(self, media_id: str) -> dict[str, Any]:
        clean_media_id = str(media_id or "").strip()
        if not clean_media_id:
            raise ValueError("Media id WhatsApp em falta.")
        if not self.send_ready:
            raise RuntimeError("WhatsApp Cloud API sem credenciais para descarregar media.")

        metadata_response = requests.get(
            f"https://graph.facebook.com/{self.graph_api_version}/{clean_media_id}",
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=self.timeout,
        )
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        media_url = str(metadata.get("url") or "").strip()
        if not media_url:
            raise RuntimeError("A Meta não devolveu URL para o media recebido.")

        file_response = requests.get(
            media_url,
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=self.timeout,
        )
        file_response.raise_for_status()
        return {
            "bytes": file_response.content,
            "mime_type": str(
                metadata.get("mime_type")
                or file_response.headers.get("Content-Type")
                or ""
            ).strip(),
            "filename": str(metadata.get("file_name") or metadata.get("filename") or "").strip(),
            "metadata": metadata,
        }

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
