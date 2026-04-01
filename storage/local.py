"""Local JSON-file storage backend."""

from __future__ import annotations

import json
import os
import uuid
from typing import Dict, List, Optional

from werkzeug.datastructures import FileStorage
from werkzeug.security import check_password_hash, generate_password_hash
from core.validators import normalize_thruster_state, validate_datetime_range

from domain.document_processing import (
    build_preview,
    ensure_unique_filename,
    extract_text_from_path,
    file_metadata,
    format_bytes,
    infer_document_type,
    is_allowed_document,
    is_text_editable,
    iso_now,
    sanitize_upload_filename,
    slugify,
)

from .base import BaseStore
from .maneuver_case_helpers import (
    _capture_live_environment_sources,
    build_maneuver_case,
    decorate_maneuver_case,
    rank_similar_maneuver_cases,
)
from .constants import (
    ALLOWED_FEEDBACK_STATUSES,
    DEFAULT_CONVERSATION_TITLE,
    FEEDBACK_APPROVED,
    PASSWORD_HASH_METHOD,
    PORT_CALL_APPROVAL_ABORTED,
    PORT_CALL_APPROVAL_APPROVED,
    PORT_CALL_APPROVAL_PENDING,
    PORT_CALL_STATUS_IN_PORT,
    PORT_CALL_STATUS_SCHEDULED,
)
from .port_call_helpers import (
    _build_port_activity_snapshot,
    _can_abort_departure_plan,
    _can_abort_port_call,
    _can_abort_shift_plan,
    _can_edit_maneuver_plan,
    _decorate_port_call,
    _default_port_calls,
    _latest_maneuver,
    _latest_reportable_maneuver,
    _normalize_maneuver_record,
    _normalize_port_call_record,
    _sync_port_call_from_history,
    _append_maneuver_change_log,
)
from .utils import (
    _build_actor_snapshot,
    _clean_text,
    _conversation_title_from_text,
    _normalize_email,
    _normalize_phone,
    _normalize_user_profile_payload,
    _normalize_username,
    _text_similarity,
    _utc_iso_to_label,
    _validate_required_operational_profile,
    _validate_required_vessel_profile,
    is_user_profile_complete,
    normalize_constraint_codes,
)


class LocalStore(BaseStore):
    backend_name = "local"

    def __init__(self, data_dir: str, knowledge_dir: str) -> None:
        self.data_dir = data_dir
        self.knowledge_dir = knowledge_dir
        self.users_path = os.path.join(data_dir, "users.json")
        self.documents_path = os.path.join(data_dir, "documents.json")
        self.conversations_path = os.path.join(data_dir, "conversations.json")
        self.messages_path = os.path.join(data_dir, "messages.json")
        self.runtime_state_path = os.path.join(data_dir, "runtime_state.json")
        self.port_calls_path = os.path.join(data_dir, "port_calls.json")
        self.maneuver_cases_path = os.path.join(data_dir, "maneuver_cases.json")
        self.legacy_chats_path = os.path.join(data_dir, "chats.json")
        self._ensure_dirs()
        self._seed_defaults()
        self._migrate_legacy_chats()
        self._sync_document_records()
        self._sync_maneuver_cases_from_port_calls(self._read_port_calls(), capture_live_environment=False)

    def _ensure_dirs(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.knowledge_dir, exist_ok=True)

    def _read_json(self, path: str, fallback):
        if not os.path.exists(path):
            return fallback
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_json(self, path: str, payload) -> None:
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)

    def _seed_defaults(self) -> None:
        users = self._read_json(self.users_path, [])
        if not users:
            users = [
                self._build_user("admin", "admin123", "admin"),
                self._build_user("agente", "agente123", "agente"),
                self._build_user("piloto", "piloto123", "piloto"),
            ]
            self._write_json(self.users_path, users)

        for path, fallback in (
            (self.documents_path, []),
            (self.conversations_path, []),
            (self.messages_path, []),
            (self.runtime_state_path, {}),
            (self.port_calls_path, _default_port_calls()),
            (self.maneuver_cases_path, []),
        ):
            if not os.path.exists(path):
                self._write_json(path, fallback)

    def _migrate_legacy_chats(self) -> None:
        legacy = self._read_json(self.legacy_chats_path, {})
        conversations = self._read_json(self.conversations_path, [])
        messages = self._read_json(self.messages_path, [])
        if not legacy or conversations or messages:
            return

        migrated_conversations = []
        migrated_messages = []
        for username, history in legacy.items():
            conversation = self._build_conversation_record(
                username=username,
                title="Conversa importada",
            )
            migrated_conversations.append(conversation)
            for entry in history:
                migrated_messages.append(
                    self._build_message_record(
                        conversation_id=conversation["id"],
                        role=entry.get("role", "assistant"),
                        content=entry.get("content", ""),
                        citations=entry.get("citations", []),
                    )
                )

        self._write_json(self.conversations_path, migrated_conversations)
        self._write_json(self.messages_path, migrated_messages)

    def _build_user(
        self,
        username: str,
        password: str,
        role: str,
        *,
        full_name: str = "",
        organization: str = "",
        email: str = "",
        phone: str = "",
    ) -> Dict:
        return {
            "password_hash": generate_password_hash(password, method=PASSWORD_HASH_METHOD),
            **_normalize_user_profile_payload(
                {
                    "username": username,
                    "role": role,
                    "full_name": full_name,
                    "organization": organization,
                    "email": email,
                    "phone": phone,
                }
            ),
        }

    def _build_conversation_record(
        self,
        username: str,
        title: str = DEFAULT_CONVERSATION_TITLE,
        created_at: Optional[str] = None,
    ) -> Dict:
        stamp = created_at or iso_now()
        return {
            "id": str(uuid.uuid4()),
            "username": username,
            "title": title,
            "created_at": stamp,
            "updated_at": stamp,
        }

    def _build_message_record(
        self,
        conversation_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict]] = None,
        feedback_status: Optional[str] = None,
        feedback_note: str = "",
        feedback_updated_at: Optional[str] = None,
    ) -> Dict:
        return {
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "citations": citations or [],
            "created_at": iso_now(),
            "feedback_status": feedback_status,
            "feedback_note": feedback_note,
            "feedback_updated_at": feedback_updated_at,
        }

    def _read_document_records(self) -> List[Dict]:
        return self._read_json(self.documents_path, [])

    def _write_document_records(self, records: List[Dict]) -> None:
        self._write_json(self.documents_path, records)

    def _read_conversations(self) -> List[Dict]:
        return self._read_json(self.conversations_path, [])

    def _write_conversations(self, records: List[Dict]) -> None:
        self._write_json(self.conversations_path, records)

    def _read_messages(self) -> List[Dict]:
        return self._read_json(self.messages_path, [])

    def _write_messages(self, records: List[Dict]) -> None:
        self._write_json(self.messages_path, records)

    def _read_runtime_state(self) -> Dict:
        return self._read_json(self.runtime_state_path, {})

    def _write_runtime_state(self, payload: Dict) -> None:
        self._write_json(self.runtime_state_path, payload)

    def _read_users(self) -> List[Dict]:
        records = self._read_json(self.users_path, [])
        normalized = []
        changed = False
        for record in records:
            normalized_record = {
                "password_hash": record.get("password_hash", ""),
                **_normalize_user_profile_payload(record),
            }
            normalized.append(normalized_record)
            if normalized_record != record:
                changed = True
        if changed:
            self._write_json(self.users_path, normalized)
        return normalized

    def _write_users(self, records: List[Dict]) -> None:
        self._write_json(self.users_path, records)

    def _read_port_calls(self) -> List[Dict]:
        records = self._read_json(self.port_calls_path, [])
        normalized_records = [_normalize_port_call_record(item) for item in records]
        if normalized_records != records:
            self._write_port_calls(normalized_records, capture_live_environment=False)
        return normalized_records

    def _write_port_calls(self, records: List[Dict], *, capture_live_environment: bool = True) -> None:
        self._write_json(self.port_calls_path, records)
        self._sync_maneuver_cases_from_port_calls(records, capture_live_environment=capture_live_environment)

    def _read_maneuver_cases(self) -> List[Dict]:
        return self._read_json(self.maneuver_cases_path, [])

    def _write_maneuver_cases(self, records: List[Dict]) -> None:
        self._write_json(self.maneuver_cases_path, records)

    def _sync_maneuver_cases_from_port_calls(
        self,
        records: List[Dict],
        *,
        capture_live_environment: bool,
    ) -> None:
        existing_cases = {item.get("maneuver_id"): item for item in self._read_maneuver_cases() if item.get("maneuver_id")}
        weather_forecast = None
        wave_conditions = None
        if capture_live_environment:
            weather_forecast, wave_conditions = _capture_live_environment_sources()

        synced_cases: List[Dict] = []
        for record in records:
            decorated = _decorate_port_call(record)
            for maneuver in decorated.get("maneuver_history", []) or []:
                maneuver_id = maneuver.get("id")
                if not maneuver_id:
                    continue
                synced_cases.append(
                    build_maneuver_case(
                        decorated,
                        maneuver,
                        existing_case=existing_cases.get(maneuver_id),
                        capture_live_environment=capture_live_environment,
                        weather_forecast=weather_forecast,
                        wave_conditions=wave_conditions,
                    )
                )
        synced_cases.sort(key=lambda item: item.get("latest_event_at") or "", reverse=True)
        self._write_maneuver_cases(synced_cases)

    def _find_port_call_index(self, port_call_id: str) -> int:
        for index, item in enumerate(self._read_port_calls()):
            if item["id"] == port_call_id:
                return index
        raise ValueError("Manobra não encontrada.")

    def _message_owned_by_user(
        self, username: str, conversation_id: str, message_id: str
    ) -> Optional[Dict]:
        if not self._conversation_owned_by_user(username, conversation_id):
            return None
        for message in self._read_messages():
            if message["id"] == message_id and message["conversation_id"] == conversation_id:
                return message
        return None

    def _upsert_document_record(self, record: Dict) -> None:
        records = self._read_document_records()
        replaced = False
        for index, current in enumerate(records):
            if current["name"] == record["name"]:
                records[index] = record
                replaced = True
                break
        if not replaced:
            records.append(record)
        records.sort(key=lambda item: item["name"])
        self._write_document_records(records)

    def _sync_document_records(self) -> None:
        records_by_name = {record["name"]: record for record in self._read_document_records()}
        synced = []
        for name in sorted(os.listdir(self.knowledge_dir)):
            path = os.path.join(self.knowledge_dir, name)
            if not os.path.isfile(path) or not is_allowed_document(name):
                continue

            meta = file_metadata(path)
            previous = records_by_name.get(name, {})
            preview = previous.get("preview", "")
            if (
                previous.get("size_bytes") != meta["size_bytes"]
                or previous.get("updated_at") != meta["updated_at"]
                or not preview
            ):
                try:
                    text = extract_text_from_path(path)
                    preview = build_preview(text)
                except Exception as exc:
                    preview = f"Erro ao extrair conteúdo: {exc}"
            synced.append(
                {
                    "name": name,
                    "original_name": previous.get("original_name", name),
                    "doc_type": infer_document_type(name),
                    "size_bytes": meta["size_bytes"],
                    "size_label": format_bytes(meta["size_bytes"]),
                    "updated_at": meta["updated_at"],
                    "updated_at_label": _utc_iso_to_label(meta["updated_at"]),
                    "created_at": previous.get("created_at", meta["updated_at"]),
                    "uploaded_by": previous.get("uploaded_by", "system"),
                    "preview": preview,
                    "editable": is_text_editable(name),
                }
            )

        self._write_document_records(synced)

    def _conversation_owned_by_user(self, username: str, conversation_id: str) -> Optional[Dict]:
        for conversation in self._read_conversations():
            if conversation["id"] == conversation_id and conversation["username"] == username:
                return conversation
        return None

    def _touch_conversation(self, conversation_id: str, title_hint: Optional[str] = None) -> None:
        conversations = self._read_conversations()
        messages = self._read_messages()
        user_message_count = sum(
            1
            for item in messages
            if item["conversation_id"] == conversation_id and item["role"] == "user"
        )
        for conversation in conversations:
            if conversation["id"] != conversation_id:
                continue
            conversation["updated_at"] = iso_now()
            if title_hint and (
                conversation["title"] == DEFAULT_CONVERSATION_TITLE or user_message_count <= 1
            ):
                conversation["title"] = _conversation_title_from_text(title_hint)
        self._write_conversations(conversations)

    def list_users(self) -> List[Dict]:
        """Return all registered users as a list of profile dicts."""
        return self._read_users()

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        full_name: str = "",
        organization: str = "",
        email: str = "",
        phone: str = "",
    ) -> Dict:
        """Create a new user record in the local JSON store and return it without the password hash."""
        username = _normalize_username(username)
        if len(username) < 3:
            raise ValueError("O email deve ter pelo menos 3 caracteres.")
        if len(password) < 6:
            raise ValueError("A password deve ter pelo menos 6 caracteres.")
        if role not in {"admin", "agente", "piloto"}:
            raise ValueError("Role invalido.")

        users = self._read_users()
        if any(user["username"] == username for user in users):
            raise ValueError("Esse utilizador ja existe.")

        user = self._build_user(
            username,
            password,
            role,
            full_name=full_name,
            organization=organization,
            email=email,
            phone=phone,
        )
        users.append(user)
        self._write_users(users)
        return {key: user[key] for key in user if key != "password_hash"}

    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        """Verify credentials against the local store and return the profile dict or None."""
        users = self._read_users()
        for user in users:
            if user["username"] == _normalize_username(username) and check_password_hash(
                user["password_hash"], password
            ):
                return {key: user[key] for key in user if key != "password_hash"}
        return None

    def get_user_profile(self, username: str) -> Optional[Dict]:
        """Return the profile dict for the given username without the password hash, or None."""
        users = self._read_users()
        for user in users:
            if user["username"] == _normalize_username(username):
                return {key: user[key] for key in user if key != "password_hash"}
        return None

    def set_user_role(self, username: str, role: str) -> Dict:
        """Update the role for the given user and return the updated profile."""
        username = _normalize_username(username)
        if role not in {"admin", "agente", "piloto"}:
            raise ValueError("Role invalido.")
        users = self._read_users()
        updated = None
        for user in users:
            if user["username"] == username:
                user["role"] = role
                if is_user_profile_complete(user):
                    user["profile_completed_at"] = user.get("profile_completed_at") or iso_now()
                updated = user
                break
        if not updated:
            raise ValueError("Utilizador não encontrado.")
        self._write_users(users)
        return {key: updated[key] for key in updated if key != "password_hash"}

    def reset_user_password(self, username: str, new_password: str) -> bool:
        """Reset the user's password hash and return True if the user was found."""
        username = _normalize_username(username)
        if len(new_password) < 6:
            raise ValueError("A password deve ter pelo menos 6 caracteres.")
        users = self._read_users()
        for user in users:
            if user["username"] == username:
                user["password_hash"] = generate_password_hash(new_password, method=PASSWORD_HASH_METHOD)
                self._write_users(users)
                return True
        return False

    def delete_user(self, username: str) -> None:
        """Remove the user and all their conversations and messages from the local store."""
        normalized_username = _normalize_username(username)
        users = self._read_users()
        target = next((user for user in users if user["username"] == normalized_username), None)
        if not target:
            raise ValueError("Utilizador não encontrado.")
        if target.get("role") == "admin":
            admin_count = sum(1 for user in users if user.get("role") == "admin")
            if admin_count <= 1:
                raise ValueError("Não podes apagar o último admin.")

        remaining_users = [user for user in users if user["username"] != normalized_username]
        self._write_users(remaining_users)

        conversations = [item for item in self._read_conversations() if item["username"] != normalized_username]
        conversation_ids = {item["id"] for item in self._read_conversations() if item["username"] == normalized_username}
        self._write_conversations(conversations)

        if conversation_ids:
            messages = [item for item in self._read_messages() if item["conversation_id"] not in conversation_ids]
            self._write_messages(messages)

    def update_user_profile(
        self,
        username: str,
        *,
        full_name: str,
        organization: str,
        email: str,
        phone: str,
    ) -> Dict:
        """Update profile contact fields for the user and return the updated record."""
        users = self._read_users()
        updated = None
        for user in users:
            if user["username"] != _normalize_username(username):
                continue
            user["full_name"] = _clean_text(full_name)
            user["organization"] = _clean_text(organization)
            user["email"] = _normalize_email(email)
            user["phone"] = _normalize_phone(phone)
            user["profile_completed_at"] = iso_now() if is_user_profile_complete(user) else None
            updated = user
            break
        if not updated:
            raise ValueError("Utilizador não encontrado.")
        self._write_users(users)
        return {key: updated[key] for key in updated if key != "password_hash"}

    def save_document(self, title: str, content: str, created_by: str = "manual") -> str:
        """Save a text document to the knowledge directory and register its metadata record."""
        filename = ensure_unique_filename(self.knowledge_dir, f"{slugify(title)}.md")
        path = os.path.join(self.knowledge_dir, filename)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content.strip() + "\n")

        meta = file_metadata(path)
        self._upsert_document_record(
            {
                "name": filename,
                "original_name": filename,
                "doc_type": infer_document_type(filename),
                "size_bytes": meta["size_bytes"],
                "size_label": format_bytes(meta["size_bytes"]),
                "updated_at": meta["updated_at"],
                "updated_at_label": _utc_iso_to_label(meta["updated_at"]),
                "created_at": iso_now(),
                "uploaded_by": created_by,
                "preview": build_preview(content),
                "editable": is_text_editable(filename),
            }
        )
        return filename

    def save_uploaded_document(self, uploaded_file: FileStorage, created_by: str) -> str:
        """Validate, store, and register an uploaded file in the knowledge directory."""
        filename = sanitize_upload_filename(uploaded_file.filename or "")
        if not is_allowed_document(filename):
            raise ValueError("Formato não suportado. Usa .pdf, .md, .txt, .docx ou .csv.")

        path = os.path.join(self.knowledge_dir, filename)
        stem, suffix = os.path.splitext(path)
        temp_path = f"{stem}.upload-{uuid.uuid4().hex}{suffix}"
        uploaded_file.save(temp_path)

        try:
            text = extract_text_from_path(temp_path)
        except Exception as exc:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise ValueError(f"Falha ao processar ficheiro: {exc}") from exc
        if not text.strip():
            os.remove(temp_path)
            raise ValueError("Não foi possível extrair texto útil do ficheiro.")

        os.replace(temp_path, path)

        meta = file_metadata(path)
        self._upsert_document_record(
            {
                "name": filename,
                "original_name": uploaded_file.filename or filename,
                "doc_type": infer_document_type(filename),
                "size_bytes": meta["size_bytes"],
                "size_label": format_bytes(meta["size_bytes"]),
                "updated_at": meta["updated_at"],
                "updated_at_label": _utc_iso_to_label(meta["updated_at"]),
                "created_at": iso_now(),
                "uploaded_by": created_by,
                "preview": build_preview(text),
                "editable": is_text_editable(filename),
            }
        )
        return filename

    def list_documents(self) -> List[Dict]:
        """Sync metadata with the knowledge directory and return all document records."""
        self._sync_document_records()
        return self._read_document_records()

    def get_document(self, name: str) -> Optional[Dict]:
        """Return the metadata record for a document by filename, or None if not found."""
        self._sync_document_records()
        for record in self._read_document_records():
            if record["name"] == name:
                return record
        return None

    def get_document_text(self, name: str) -> str:
        """Extract and return the text content of a document from the knowledge directory."""
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        path = os.path.join(self.knowledge_dir, record["name"])
        return extract_text_from_path(path)

    def get_document_file_path(self, name: str) -> str:
        """Return the absolute path of the document file in the knowledge directory."""
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        return os.path.join(self.knowledge_dir, record["name"])

    def update_document_text(self, name: str, content: str, updated_by: str) -> Dict:
        """Overwrite the text content of an editable document and update its metadata record."""
        if not content.strip():
            raise ValueError("O conteúdo não pode estar vazio.")
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        if not is_text_editable(name):
            raise ValueError("Este tipo de ficheiro não pode ser editado no browser.")

        path = os.path.join(self.knowledge_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content.strip() + "\n")

        meta = file_metadata(path)
        updated = {
            **record,
            "size_bytes": meta["size_bytes"],
            "size_label": format_bytes(meta["size_bytes"]),
            "updated_at": meta["updated_at"],
            "updated_at_label": _utc_iso_to_label(meta["updated_at"]),
            "uploaded_by": updated_by,
            "preview": build_preview(content),
            "editable": True,
        }
        self._upsert_document_record(updated)
        return updated

    def delete_document(self, name: str) -> None:
        """Remove a document file and its metadata record from the knowledge store."""
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        path = os.path.join(self.knowledge_dir, name)
        if os.path.exists(path):
            os.remove(path)
        records = [item for item in self._read_document_records() if item["name"] != name]
        self._write_document_records(records)

    def list_conversations(self, username: str) -> List[Dict]:
        """Return all conversations for the user sorted by most recently updated."""
        conversations = [item for item in self._read_conversations() if item["username"] == username]
        conversations.sort(key=lambda item: item["updated_at"], reverse=True)
        return [
            {
                **item,
                "created_at_label": _utc_iso_to_label(item["created_at"]),
                "updated_at_label": _utc_iso_to_label(item["updated_at"]),
            }
            for item in conversations
        ]

    def create_conversation(self, username: str, title: str = DEFAULT_CONVERSATION_TITLE) -> Dict:
        """Create a new conversation for the user and return it."""
        conversations = self._read_conversations()
        conversation = self._build_conversation_record(username=username, title=title)
        conversations.append(conversation)
        self._write_conversations(conversations)
        return {
            **conversation,
            "created_at_label": _utc_iso_to_label(conversation["created_at"]),
            "updated_at_label": _utc_iso_to_label(conversation["updated_at"]),
        }

    def rename_conversation(self, username: str, conversation_id: str, title: str) -> Dict:
        """Rename a conversation and return the updated record."""
        clean_title = " ".join(title.strip().split())
        if not clean_title:
            raise ValueError("O título da conversa não pode ficar vazio.")

        conversations = self._read_conversations()
        updated = None
        for conversation in conversations:
            if conversation["id"] == conversation_id and conversation["username"] == username:
                conversation["title"] = clean_title
                conversation["updated_at"] = iso_now()
                updated = conversation
                break

        if not updated:
            raise ValueError("Conversa não encontrada.")

        self._write_conversations(conversations)
        return {
            **updated,
            "created_at_label": _utc_iso_to_label(updated["created_at"]),
            "updated_at_label": _utc_iso_to_label(updated["updated_at"]),
        }

    def clear_conversation(self, username: str, conversation_id: str) -> None:
        """Delete all messages in a conversation without removing the conversation record."""
        conversation = self._conversation_owned_by_user(username, conversation_id)
        if not conversation:
            raise ValueError("Conversa não encontrada.")
        messages = [
            item for item in self._read_messages() if item["conversation_id"] != conversation_id
        ]
        self._write_messages(messages)
        self._touch_conversation(conversation_id)

    def delete_conversation(self, username: str, conversation_id: str) -> Optional[str]:
        """Delete a conversation and its messages, returning the ID of the next conversation if any."""
        conversation = self._conversation_owned_by_user(username, conversation_id)
        if not conversation:
            raise ValueError("Conversa não encontrada.")

        conversations = [
            item
            for item in self._read_conversations()
            if not (item["id"] == conversation_id and item["username"] == username)
        ]
        self._write_conversations(conversations)
        messages = [
            item for item in self._read_messages() if item["conversation_id"] != conversation_id
        ]
        self._write_messages(messages)

        remaining = self.list_conversations(username)
        return remaining[0]["id"] if remaining else None

    def ensure_conversation(self, username: str, conversation_id: Optional[str] = None) -> Dict:
        """Return an existing conversation by ID or the most recent one, creating one if needed."""
        if conversation_id:
            existing = self._conversation_owned_by_user(username, conversation_id)
            if existing:
                return {
                    **existing,
                    "created_at_label": _utc_iso_to_label(existing["created_at"]),
                    "updated_at_label": _utc_iso_to_label(existing["updated_at"]),
                }
        conversations = self.list_conversations(username)
        if conversations:
            return conversations[0]
        return self.create_conversation(username)

    def list_messages(self, username: str, conversation_id: str) -> List[Dict]:
        """Return all messages in a conversation sorted by creation time."""
        conversation = self._conversation_owned_by_user(username, conversation_id)
        if not conversation:
            return []

        messages = [
            item
            for item in self._read_messages()
            if item["conversation_id"] == conversation_id
        ]
        messages.sort(key=lambda item: item["created_at"])
        for message in messages:
            message.setdefault("feedback_status", None)
            message.setdefault("feedback_note", "")
            message.setdefault("feedback_updated_at", None)
            message["created_at_label"] = _utc_iso_to_label(message["created_at"])
            message["feedback_updated_at_label"] = (
                _utc_iso_to_label(message["feedback_updated_at"])
                if message.get("feedback_updated_at") else ""
            )
        return messages

    def append_chat_message(
        self,
        username: str,
        conversation_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict]] = None,
    ) -> Dict:
        """Append a chat message to the conversation and return the saved record."""
        if not self._conversation_owned_by_user(username, conversation_id):
            raise ValueError("Conversa inválida para este utilizador.")

        messages = self._read_messages()
        message = self._build_message_record(
            conversation_id=conversation_id,
            role=role,
            content=content,
            citations=citations,
        )
        messages.append(message)
        self._write_messages(messages)
        if role == "user":
            self._touch_conversation(conversation_id, title_hint=content)
        else:
            self._touch_conversation(conversation_id)
        return message

    def get_runtime_state(self, key: str) -> Optional[Dict]:
        """Return the runtime state dict stored under the given key, or None."""
        payload = self._read_runtime_state()
        value = payload.get(key)
        return value if isinstance(value, dict) else None

    def set_runtime_state(self, key: str, value: Dict) -> Dict:
        """Persist a runtime state dict under the given key and return it."""
        payload = self._read_runtime_state()
        payload[key] = value
        self._write_runtime_state(payload)
        return value

    def delete_runtime_state(self, key: str) -> None:
        """Remove the runtime state entry for the given key if it exists."""
        payload = self._read_runtime_state()
        if key in payload:
            payload.pop(key, None)
            self._write_runtime_state(payload)

    def update_message_feedback(
        self,
        username: str,
        conversation_id: str,
        message_id: str,
        feedback_status: str,
        feedback_note: str = "",
    ) -> Dict:
        """Update feedback status and note for a chat message and return the updated record."""
        if feedback_status not in ALLOWED_FEEDBACK_STATUSES:
            raise ValueError("Estado de feedback inválido.")
        if not self._message_owned_by_user(username, conversation_id, message_id):
            raise ValueError("Mensagem não encontrada.")

        messages = self._read_messages()
        updated = None
        for message in messages:
            if message["id"] != message_id or message["conversation_id"] != conversation_id:
                continue
            if message["role"] != "assistant":
                raise ValueError("Só podes classificar respostas do assistente.")
            message["feedback_status"] = feedback_status
            message["feedback_note"] = feedback_note.strip()
            message["feedback_updated_at"] = iso_now()
            updated = message
            break

        if not updated:
            raise ValueError("Mensagem não encontrada.")

        self._write_messages(messages)
        self._touch_conversation(conversation_id)
        return updated

    def find_feedback_matches(self, username: str, question: str, limit: int = 3) -> List[Dict]:
        """Return previously approved messages whose question best matches the given text."""
        conversations = {
            item["id"]: item
            for item in self._read_conversations()
            if item["username"] == username
        }
        if not conversations:
            return []

        conversation_messages: Dict[str, List[Dict]] = {}
        for message in self._read_messages():
            if message["conversation_id"] in conversations:
                conversation_messages.setdefault(message["conversation_id"], []).append(message)

        matches = []
        for conversation_id, messages in conversation_messages.items():
            messages.sort(key=lambda item: item["created_at"])
            previous_user = None
            for message in messages:
                if message["role"] == "user":
                    previous_user = message
                    continue
                if message["role"] != "assistant":
                    continue
                if message.get("feedback_status") != FEEDBACK_APPROVED:
                    continue
                if not previous_user:
                    continue
                score = _text_similarity(question, previous_user.get("content", ""))
                if score < 0.35:
                    continue
                matches.append(
                    {
                        "message_id": message["id"],
                        "conversation_id": conversation_id,
                        "question": previous_user.get("content", ""),
                        "answer": message.get("content", ""),
                        "citations": message.get("citations", []),
                        "feedback_note": message.get("feedback_note", ""),
                        "feedback_updated_at": message.get("feedback_updated_at"),
                        "similarity": round(score, 3),
                    }
                )

        matches.sort(
            key=lambda item: (
                item["similarity"],
                item.get("feedback_updated_at") or "",
            ),
            reverse=True,
        )
        return matches[:limit]

    def get_port_activity_snapshot(self, window_days: int = 5) -> Dict:
        """Return a decorated port activity snapshot covering the specified number of days."""
        return _build_port_activity_snapshot(self._read_port_calls(), window_days=window_days)

    def list_maneuver_cases(
        self,
        *,
        limit: int = 100,
        maneuver_type: Optional[str] = None,
        state: Optional[str] = None,
        port_call_id: Optional[str] = None,
    ) -> List[Dict]:
        cases = self._read_maneuver_cases()
        if maneuver_type:
            clean_type = maneuver_type.strip().lower()
            cases = [item for item in cases if (item.get("maneuver_type") or "").strip().lower() == clean_type]
        if state:
            clean_state = state.strip().lower()
            cases = [item for item in cases if (item.get("current_state") or "").strip().lower() == clean_state]
        if port_call_id:
            cases = [item for item in cases if item.get("port_call_id") == port_call_id]
        return [decorate_maneuver_case(item) for item in cases[: max(limit, 0)]]

    def get_maneuver_case(self, maneuver_id: str) -> Optional[Dict]:
        for item in self._read_maneuver_cases():
            if item.get("maneuver_id") == maneuver_id:
                return decorate_maneuver_case(item)
        return None

    def find_similar_maneuver_cases(
        self,
        *,
        maneuver_type: str,
        origin: str = "",
        destination: str = "",
        vessel_type: str = "",
        vessel_loa_m: str = "",
        bow_thruster: str = "",
        stern_thruster: str = "",
        tug_count: str = "",
        limit: int = 5,
    ) -> List[Dict]:
        return rank_similar_maneuver_cases(
            self._read_maneuver_cases(),
            maneuver_type=maneuver_type,
            origin=origin,
            destination=destination,
            vessel_type=vessel_type,
            vessel_loa_m=vessel_loa_m,
            bow_thruster=bow_thruster,
            stern_thruster=stern_thruster,
            tug_count=tug_count,
            limit=limit,
        )

    def clear_port_calls(self) -> int:
        removed = len(self._read_port_calls())
        self._write_port_calls(_default_port_calls())
        return removed

    def get_port_call(self, port_call_id: str) -> Dict:
        """Return the decorated port call record for the given ID, raising ValueError if not found."""
        for item in self._read_port_calls():
            if item["id"] == port_call_id:
                return _decorate_port_call(item)
        raise ValueError("Escala não encontrada.")

    def edit_port_call(
        self,
        port_call_id: str,
        *,
        updated_by: str,
        vessel_name: Optional[str] = None,
        eta: Optional[str] = None,
        berth: Optional[str] = None,
        last_port: Optional[str] = None,
        next_port: Optional[str] = None,
        notes: Optional[str] = None,
        constraints: Optional[List[str]] = None,
        vessel_short_name: Optional[str] = None,
        vessel_imo: Optional[str] = None,
        vessel_call_sign: Optional[str] = None,
        vessel_flag: Optional[str] = None,
        vessel_type: Optional[str] = None,
        vessel_loa_m: Optional[str] = None,
        vessel_beam_m: Optional[str] = None,
        vessel_gt_t: Optional[str] = None,
        vessel_max_draft_m: Optional[str] = None,
        vessel_dwt_t: Optional[str] = None,
        vessel_bow_thruster: Optional[str] = None,
        vessel_stern_thruster: Optional[str] = None,
    ) -> Dict:
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry")
            if not entry:
                raise ValueError("Escala sem manobra de entrada associada.")

            updated_vessel_name = _clean_text(vessel_name) if vessel_name is not None else current.get("vessel_name", "")
            if len(updated_vessel_name) < 2:
                raise ValueError("Indica o nome do navio.")
            updated_berth = _clean_text(berth) if berth is not None else current.get("berth", "")
            updated_last_port = _clean_text(last_port) if last_port is not None else current.get("last_port", "")
            updated_next_port = _clean_text(next_port) if next_port is not None else current.get("next_port", "")
            updated_eta = eta.strip() if eta is not None else (entry.get("planned_at") or current.get("eta") or "")
            if not updated_eta:
                raise ValueError("O ETA é obrigatório.")

            vessel_profile = {
                "vessel_short_name": _clean_text(vessel_short_name) if vessel_short_name is not None else current.get("vessel_short_name", ""),
                "vessel_imo": _clean_text(vessel_imo) if vessel_imo is not None else current.get("vessel_imo", ""),
                "vessel_call_sign": _clean_text(vessel_call_sign) if vessel_call_sign is not None else current.get("vessel_call_sign", ""),
                "vessel_flag": _clean_text(vessel_flag) if vessel_flag is not None else current.get("vessel_flag", ""),
                "vessel_type": _clean_text(vessel_type) if vessel_type is not None else current.get("vessel_type", ""),
                "vessel_loa_m": _clean_text(vessel_loa_m) if vessel_loa_m is not None else current.get("vessel_loa_m", ""),
                "vessel_beam_m": _clean_text(vessel_beam_m) if vessel_beam_m is not None else current.get("vessel_beam_m", ""),
                "vessel_gt_t": _clean_text(vessel_gt_t) if vessel_gt_t is not None else current.get("vessel_gt_t", ""),
                "vessel_max_draft_m": _clean_text(vessel_max_draft_m) if vessel_max_draft_m is not None else current.get("vessel_max_draft_m", ""),
                "vessel_dwt_t": _clean_text(vessel_dwt_t) if vessel_dwt_t is not None else current.get("vessel_dwt_t", ""),
                "vessel_bow_thruster": normalize_thruster_state(
                    vessel_bow_thruster if vessel_bow_thruster is not None else current.get("vessel_bow_thruster", "unknown"),
                    "Bow thruster",
                ),
                "vessel_stern_thruster": normalize_thruster_state(
                    vessel_stern_thruster if vessel_stern_thruster is not None else current.get("vessel_stern_thruster", "unknown"),
                    "Stern thruster",
                ),
            }
            _validate_required_vessel_profile(vessel_profile)
            _validate_required_operational_profile(
                {
                    "berth": updated_berth,
                    "last_port": updated_last_port,
                    "next_port": updated_next_port,
                },
                (
                    ("berth", "cais previsto"),
                    ("last_port", "porto anterior"),
                    ("next_port", "próximo destino"),
                ),
            )

            current.update(
                {
                    "vessel_name": updated_vessel_name,
                    **vessel_profile,
                    "berth": updated_berth,
                    "last_port": updated_last_port,
                    "next_port": updated_next_port,
                    "updated_at": iso_now(),
                }
            )
            if notes is not None:
                current["notes"] = notes.strip()
                entry["plan_note"] = notes.strip()
            entry["planned_at"] = updated_eta
            entry["origin"] = updated_last_port
            entry["destination"] = updated_berth
            if constraints is not None:
                entry["constraints"] = normalize_constraint_codes(constraints)
            entry["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Escala não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def delete_port_call(self, port_call_id: str) -> Dict:
        records = self._read_port_calls()
        for index, item in enumerate(records):
            if item["id"] != port_call_id:
                continue
            removed = _decorate_port_call(item)
            records.pop(index)
            self._write_port_calls(records)
            return removed
        raise ValueError("Escala não encontrada.")

    def create_port_call(
        self,
        vessel_name: str,
        eta: str,
        created_by: str,
        constraints: Optional[List[str]] = None,
        berth: str = "",
        last_port: str = "",
        next_port: str = "",
        notes: str = "",
        vessel_short_name: str = "",
        vessel_imo: str = "",
        vessel_call_sign: str = "",
        vessel_flag: str = "",
        vessel_type: str = "",
        vessel_loa_m: str = "",
        vessel_beam_m: str = "",
        vessel_gt_t: str = "",
        vessel_max_draft_m: str = "",
        vessel_dwt_t: str = "",
        vessel_bow_thruster: str = "unknown",
        vessel_stern_thruster: str = "unknown",
    ) -> Dict:
        """Create a new port call record with an initial entry maneuver and return the decorated result."""
        clean_name = _clean_text(vessel_name)
        creator_username = _normalize_username(created_by) or "system"
        creator_profile = self.get_user_profile(creator_username)
        if len(clean_name) < 2:
            raise ValueError("Indica o nome do navio.")
        if not eta.strip():
            raise ValueError("O ETA é obrigatório.")
        vessel_profile = {
            "vessel_short_name": _clean_text(vessel_short_name),
            "vessel_imo": _clean_text(vessel_imo),
            "vessel_call_sign": _clean_text(vessel_call_sign),
            "vessel_flag": _clean_text(vessel_flag),
            "vessel_type": _clean_text(vessel_type),
            "vessel_loa_m": _clean_text(vessel_loa_m),
            "vessel_beam_m": _clean_text(vessel_beam_m),
            "vessel_gt_t": _clean_text(vessel_gt_t),
            "vessel_max_draft_m": _clean_text(vessel_max_draft_m),
            "vessel_dwt_t": _clean_text(vessel_dwt_t),
            "vessel_bow_thruster": normalize_thruster_state(vessel_bow_thruster, "Bow thruster"),
            "vessel_stern_thruster": normalize_thruster_state(vessel_stern_thruster, "Stern thruster"),
        }
        _validate_required_vessel_profile(vessel_profile)
        _validate_required_operational_profile(
            {
                "berth": berth,
                "last_port": last_port,
                "next_port": next_port,
            },
            (
                ("berth", "cais previsto"),
                ("last_port", "porto anterior"),
                ("next_port", "próximo destino"),
            ),
        )

        record = {
            "id": str(uuid.uuid4()),
            "vessel_name": clean_name,
            **vessel_profile,
            "status": PORT_CALL_STATUS_SCHEDULED,
            "approval_status": PORT_CALL_APPROVAL_PENDING,
            "approval_note": "",
            "aborted_reason": "",
            "decided_by": None,
            "decided_at": None,
            "eta": eta,
            "ata": None,
            "planned_departure_at": None,
            "departure_plan_note": "",
            "departure_at": None,
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": _clean_text(berth),
            "last_port": _clean_text(last_port),
            "next_port": _clean_text(next_port),
            "created_by": creator_username,
            "created_by_profile": _build_actor_snapshot(creator_profile, username=creator_username),
            "notes": notes.strip(),
            "created_at": iso_now(),
            "updated_at": iso_now(),
        }
        record["maneuver_history"] = [
            _normalize_maneuver_record(
                {
                    "type": "entry",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": eta,
                    "completed_at": None,
                    "origin": record["last_port"],
                    "destination": record["berth"],
                    "plan_note": notes.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": record["created_by"],
                    "created_by_profile": record["created_by_profile"],
                    "created_at": record["created_at"],
                    "updated_at": record["updated_at"],
                },
                fallback_created_by=record["created_by"],
            )
        ]
        record = _sync_port_call_from_history(record)
        records = self._read_port_calls()
        records.append(record)
        records.sort(key=lambda item: item.get("eta") or "")
        self._write_port_calls(records)
        return _decorate_port_call(record)

    def mark_port_call_arrived(
        self,
        port_call_id: str,
        arrived_at: str,
        updated_by: str,
        berth: str = "",
        notes: str = "",
    ) -> Dict:
        """Record the vessel's arrival and transition the entry maneuver to completed."""
        if not arrived_at.strip():
            raise ValueError("A hora real de chegada é obrigatória.")
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_SCHEDULED or not entry:
                raise ValueError("Só podes confirmar entrada de manobras previstas.")
            entry["state"] = "completed"
            entry["completed_at"] = arrived_at
            if berth.strip():
                entry["destination"] = " ".join(berth.strip().split())
            entry["updated_at"] = iso_now()
            current["maneuver_history"] = current["maneuver_history"]
            if berth.strip():
                current["berth"] = " ".join(berth.strip().split())
            if notes.strip():
                existing = current.get("notes", "").strip()
                current["notes"] = f"{existing} | {notes.strip()}".strip(" |")
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def schedule_departure_plan(
        self,
        port_call_id: str,
        planned_departure_at: str,
        updated_by: str,
        next_port: str = "",
        constraints: Optional[List[str]] = None,
        departure_plan_note: str = "",
    ) -> Dict:
        """Add a pending departure maneuver to the port call and return the updated record."""
        if not planned_departure_at.strip():
            raise ValueError("A hora prevista de saída é obrigatória.")
        destination = " ".join(next_port.strip().split())
        if not destination:
            raise ValueError("Indica o próximo destino da saída.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            if current["status"] != PORT_CALL_STATUS_IN_PORT:
                raise ValueError("Só podes planear saída para navios que estão em porto.")
            if _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED}):
                raise ValueError("Já existe uma saída ativa para esta escala.")
            departure = _normalize_maneuver_record(
                {
                    "type": "departure",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": planned_departure_at,
                    "completed_at": None,
                    "origin": current.get("berth", ""),
                    "destination": destination,
                    "plan_note": departure_plan_note.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": actor_username or current.get("created_by", "system"),
                    "created_by_profile": _build_actor_snapshot(actor_profile, username=actor_username),
                    "created_at": iso_now(),
                    "updated_at": iso_now(),
                },
                fallback_created_by=actor_username or current.get("created_by", "system"),
            )
            current["maneuver_history"].append(departure)
            current["next_port"] = destination
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def abort_departure_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        """Abort the active departure plan and return the updated port call."""
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de aborto da saída é obrigatório.")
        actor_username = _normalize_username(updated_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            departure = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not departure:
                raise ValueError("Não existe manobra de saída planeada para este navio.")
            if (
                departure.get("state") == PORT_CALL_APPROVAL_PENDING
                and not _can_abort_departure_plan({"planned_departure_at": departure.get("planned_at")})
            ):
                raise ValueError("A saída só pode ser abortada com pelo menos 1 hora de antecedência.")
            departure["state"] = PORT_CALL_APPROVAL_ABORTED
            departure["aborted_reason"] = reason
            departure["decided_by"] = actor_username
            departure["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            departure["decided_at"] = iso_now()
            departure["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def mark_port_call_departed(
        self,
        port_call_id: str,
        departed_at: str,
        updated_by: str,
        next_port: str = "",
        notes: str = "",
    ) -> Dict:
        """Record the vessel's departure and transition the departure maneuver to completed."""
        if not departed_at.strip():
            raise ValueError("A hora de saída é obrigatória.")
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            departure = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not departure:
                raise ValueError("Só podes registar saída de navios que estão em porto.")
            departure["state"] = "completed"
            departure["completed_at"] = departed_at
            if next_port.strip():
                departure["destination"] = " ".join(next_port.strip().split())
                current["next_port"] = " ".join(next_port.strip().split())
            if notes.strip():
                existing = current.get("notes", "").strip()
                current["notes"] = f"{existing} | {notes.strip()}".strip(" |")
            departure["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def approve_port_call(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        """Approve the pending entry or departure maneuver for a port call."""
        actor_username = _normalize_username(decided_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            target = None
            if current["status"] == PORT_CALL_STATUS_SCHEDULED:
                target = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_PENDING})
            elif current["status"] == PORT_CALL_STATUS_IN_PORT:
                target = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING})
            else:
                raise ValueError("Só podes aprovar manobras ainda não executadas.")
            if not target:
                raise ValueError("Não existe manobra pendente para aprovar.")
            target["state"] = PORT_CALL_APPROVAL_APPROVED
            target["approval_note"] = approval_note.strip()
            target["aborted_reason"] = ""
            target["decided_by"] = actor_username
            target["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            target["decided_at"] = iso_now()
            target["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def attach_entry_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        maneuver_id: Optional[str] = None,
    ) -> Dict:
        """Attach a pilot entry report to the entry maneuver and return the updated port call."""
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        validate_datetime_range(
            maneuver_started_at,
            maneuver_finished_at,
            started_label="Início da manobra",
            finished_label="Fim da manobra",
        )
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            entry = (
                next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
                if maneuver_id else
                _latest_reportable_maneuver(current.get("maneuver_history", []), "entry")
            )
            if not entry:
                raise ValueError("Só podes registar a entrada depois da manobra estar aprovada.")
            if maneuver_id and entry.get("type") != "entry":
                raise ValueError("O ID indicado não corresponde a uma manobra de entrada.")
            if maneuver_id and entry.get("state") not in {"approved", "completed"}:
                raise ValueError("Só podes registar a entrada depois da manobra estar aprovada.")
            if maneuver_id and (entry.get("report_note") or "").strip():
                raise ValueError("Essa manobra já tem registo. Usa editar registo.")
            if entry.get("state") == "approved":
                entry["state"] = "completed"
                entry["completed_at"] = maneuver_finished_at
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            entry["report_note"] = note
            entry["execution_started_at"] = maneuver_started_at
            entry["execution_finished_at"] = maneuver_finished_at
            entry["reported_draft_m"] = draft_m.strip()
            entry["reported_by"] = actor_username
            entry["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            entry["reported_at"] = iso_now()
            entry["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def attach_departure_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        maneuver_id: Optional[str] = None,
    ) -> Dict:
        """Attach a pilot departure report to the departure maneuver and return the updated port call."""
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        validate_datetime_range(
            maneuver_started_at,
            maneuver_finished_at,
            started_label="Início da manobra",
            finished_label="Fim da manobra",
        )
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            departure = (
                next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
                if maneuver_id else
                _latest_reportable_maneuver(current.get("maneuver_history", []), "departure")
            )
            if not departure:
                raise ValueError("Só podes registar a saída depois da manobra estar aprovada.")
            if maneuver_id and departure.get("type") != "departure":
                raise ValueError("O ID indicado não corresponde a uma manobra de saída.")
            if maneuver_id and departure.get("state") not in {"approved", "completed"}:
                raise ValueError("Só podes registar a saída depois da manobra estar aprovada.")
            if maneuver_id and (departure.get("report_note") or "").strip():
                raise ValueError("Essa manobra já tem registo. Usa editar registo.")
            if departure.get("state") == "approved":
                departure["state"] = "completed"
                departure["completed_at"] = maneuver_finished_at
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            departure["report_note"] = note
            departure["execution_started_at"] = maneuver_started_at
            departure["execution_finished_at"] = maneuver_finished_at
            departure["reported_draft_m"] = draft_m.strip()
            departure["reported_by"] = actor_username
            departure["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            departure["reported_at"] = iso_now()
            departure["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def schedule_shift_plan(
        self,
        port_call_id: str,
        planned_shift_at: str,
        updated_by: str,
        destination_berth: str,
        constraints: Optional[List[str]] = None,
        shift_plan_note: str = "",
    ) -> Dict:
        """Add a pending berth-shift maneuver to the port call and return the updated record."""
        if not planned_shift_at.strip():
            raise ValueError("A hora prevista da mudança é obrigatória.")
        destination = " ".join(destination_berth.strip().split())
        if not destination:
            raise ValueError("Indica o cais de destino da mudança.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            if current["status"] != PORT_CALL_STATUS_IN_PORT:
                raise ValueError("Só podes planear mudança para navios em porto.")
            if _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED}):
                raise ValueError("Já existe uma mudança ativa para esta escala.")
            shift = _normalize_maneuver_record(
                {
                    "type": "shift",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": planned_shift_at,
                    "completed_at": None,
                    "origin": current.get("berth", ""),
                    "destination": destination,
                    "plan_note": shift_plan_note.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": actor_username or current.get("created_by", "system"),
                    "created_by_profile": _build_actor_snapshot(actor_profile, username=actor_username),
                    "created_at": iso_now(),
                    "updated_at": iso_now(),
                },
                fallback_created_by=actor_username or current.get("created_by", "system"),
            )
            current["maneuver_history"].append(shift)
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def approve_shift_plan(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        """Approve a pending berth-shift plan and return the updated port call."""
        actor_username = _normalize_username(decided_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("Só podes aprovar mudanças ainda não executadas.")
            shift["state"] = PORT_CALL_APPROVAL_APPROVED
            shift["approval_note"] = approval_note.strip()
            shift["aborted_reason"] = ""
            shift["decided_by"] = actor_username
            shift["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["decided_at"] = iso_now()
            shift["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def abort_shift_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        """Abort an active berth-shift plan and return the updated port call."""
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de aborto da mudança é obrigatório.")
        actor_username = _normalize_username(updated_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("Não existe manobra de mudança planeada para este navio.")
            if (
                shift.get("state") == PORT_CALL_APPROVAL_PENDING
                and not _can_abort_shift_plan({"planned_shift_at": shift.get("planned_at")})
            ):
                raise ValueError("A mudança só pode ser abortada com pelo menos 1 hora de antecedência.")
            shift["state"] = PORT_CALL_APPROVAL_ABORTED
            shift["aborted_reason"] = reason
            shift["decided_by"] = actor_username
            shift["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["decided_at"] = iso_now()
            shift["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def mark_shift_completed(
        self,
        port_call_id: str,
        shifted_at: str,
        updated_by: str,
    ) -> Dict:
        """Mark a berth-shift maneuver as completed and update the current berth."""
        if not shifted_at.strip():
            raise ValueError("A hora real da mudança é obrigatória.")
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("Só podes concluir mudanças planeadas de navios em porto.")
            shift["state"] = "completed"
            shift["completed_at"] = shifted_at
            shift["updated_at"] = iso_now()
            if shift.get("destination"):
                current["berth"] = shift["destination"]
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def attach_shift_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        maneuver_id: Optional[str] = None,
    ) -> Dict:
        """Attach a pilot shift report to the shift maneuver and return the updated port call."""
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        validate_datetime_range(
            maneuver_started_at,
            maneuver_finished_at,
            started_label="Início da manobra",
            finished_label="Fim da manobra",
        )
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            shift = (
                next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
                if maneuver_id else
                _latest_reportable_maneuver(current.get("maneuver_history", []), "shift")
            )
            if not shift:
                raise ValueError("Só podes registar a mudança depois da manobra estar aprovada.")
            if maneuver_id and shift.get("type") != "shift":
                raise ValueError("O ID indicado não corresponde a uma manobra de mudança.")
            if maneuver_id and shift.get("state") not in {"approved", "completed"}:
                raise ValueError("Só podes registar a mudança depois da manobra estar aprovada.")
            if maneuver_id and (shift.get("report_note") or "").strip():
                raise ValueError("Essa manobra já tem registo. Usa editar registo.")
            if shift.get("state") == "approved":
                shift["state"] = "completed"
                shift["completed_at"] = maneuver_finished_at
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            shift["report_note"] = note
            shift["execution_started_at"] = maneuver_started_at
            shift["execution_finished_at"] = maneuver_finished_at
            shift["reported_draft_m"] = draft_m.strip()
            shift["reported_by"] = actor_username
            shift["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["reported_at"] = iso_now()
            shift["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def edit_maneuver_plan(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        actor_role: str,
        planned_at: str,
        origin: str,
        destination: str,
        draft_m: str,
        tug_count: str,
        constraints: Optional[List[str]] = None,
        plan_note: str = "",
        change_reason: str,
    ) -> Dict:
        """Edit the plan fields of a maneuver, log the change, and return the updated port call."""
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada na escala.")
            if target.get("state") == "completed" and (actor_role or "").strip().lower() != "admin":
                raise ValueError("A manobra concluída já só pode ser ajustada no registo.")
            if not _can_edit_maneuver_plan(target, actor_role):
                raise ValueError("Depois de validada, esta manobra só pode ser editada por piloto.")
            target["planned_at"] = planned_at
            target["origin"] = _clean_text(origin)
            target["destination"] = _clean_text(destination)
            target["planned_draft_m"] = (draft_m or "").strip()
            target["tug_count"] = (tug_count or "").strip()
            target["plan_observations"] = (plan_note or "").strip()
            target["constraints"] = normalize_constraint_codes(constraints)
            target["plan_note"] = (plan_note or "").strip()
            target["updated_at"] = iso_now()
            _append_maneuver_change_log(
                target,
                actor_username=actor_username,
                actor_profile=actor_profile,
                reason=change_reason,
                summary="Planeamento atualizado.",
            )
            if target.get("type") == "entry":
                current["last_port"] = target["origin"]
                current["berth"] = target["destination"]
            elif target.get("type") == "departure":
                current["next_port"] = target["destination"]
            elif target.get("type") == "shift":
                current["shift_origin_berth"] = target["origin"]
                current["shift_destination_berth"] = target["destination"]
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def edit_maneuver_report(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        change_reason: str,
    ) -> Dict:
        """Edit the report fields of a completed maneuver, log the change, and return the updated port call."""
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        validate_datetime_range(
            maneuver_started_at,
            maneuver_finished_at,
            started_label="Início da manobra",
            finished_label="Fim da manobra",
        )
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada na escala.")
            if target.get("state") != "completed":
                raise ValueError("Só podes editar o registo de manobras já concluídas.")
            target["report_note"] = (notes or "").strip()
            target["execution_started_at"] = maneuver_started_at
            target["execution_finished_at"] = maneuver_finished_at
            target["reported_draft_m"] = draft_m.strip()
            target["reported_by"] = target.get("reported_by") or actor_username
            target["reported_by_profile"] = target.get("reported_by_profile") or _build_actor_snapshot(actor_profile, username=actor_username)
            target["reported_at"] = target.get("reported_at") or iso_now()
            target["updated_at"] = iso_now()
            _append_maneuver_change_log(
                target,
                actor_username=actor_username,
                actor_profile=actor_profile,
                reason=change_reason,
                summary=f"Registo revisto. Calado: {draft_m}.",
            )
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def delete_maneuver(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
    ) -> Dict:
        records = self._read_port_calls()
        for index, item in enumerate(records):
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada.")
            if target.get("type") == "entry":
                removed = _decorate_port_call(current)
                records.pop(index)
                self._write_port_calls(records)
                return removed
            current["maneuver_history"] = [m for m in current.get("maneuver_history", []) if m.get("id") != maneuver_id]
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[index] = updated
            self._write_port_calls(records)
            return _decorate_port_call(updated)
        raise ValueError("Escala não encontrada.")

    def delete_maneuver_report(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
    ) -> Dict:
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada.")
            target["report_note"] = ""
            target["execution_started_at"] = None
            target["execution_finished_at"] = None
            target["reported_draft_m"] = ""
            target["reported_by"] = None
            target["reported_by_profile"] = _build_actor_snapshot(None)
            target["reported_at"] = None
            target["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Escala não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)

    def abort_port_call(
        self,
        port_call_id: str,
        decided_by: str,
        aborted_reason: str,
        approval_note: str = "",
    ) -> Dict:
        """Abort the port call's entry maneuver and return the updated record."""
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de manobra abortada é obrigatório.")
        actor_username = _normalize_username(decided_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        records = self._read_port_calls()
        updated = None
        for item in records:
            if item["id"] != port_call_id:
                continue
            current = _normalize_port_call_record(item)
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_SCHEDULED or not entry:
                raise ValueError("Só podes abortar manobras ainda não executadas.")
            if (
                entry.get("state") == PORT_CALL_APPROVAL_PENDING
                and not _can_abort_port_call({"eta": entry.get("planned_at")})
            ):
                raise ValueError("A manobra só pode ser abortada com pelo menos 2 horas de antecedência.")
            entry["state"] = PORT_CALL_APPROVAL_ABORTED
            entry["approval_note"] = approval_note.strip()
            entry["aborted_reason"] = reason
            entry["decided_by"] = actor_username
            entry["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            entry["decided_at"] = iso_now()
            entry["updated_at"] = iso_now()
            current["updated_at"] = iso_now()
            updated = _sync_port_call_from_history(current)
            records[records.index(item)] = updated
            break
        if not updated:
            raise ValueError("Manobra não encontrada.")
        self._write_port_calls(records)
        return _decorate_port_call(updated)
