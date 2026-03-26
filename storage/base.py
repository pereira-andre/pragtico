"""Abstract base class for storage backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from werkzeug.datastructures import FileStorage

from .constants import DEFAULT_CONVERSATION_TITLE


class BaseStore(ABC):
    backend_name = "base"

    @abstractmethod
    def list_users(self) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        raise NotImplementedError

    @abstractmethod
    def get_user_profile(self, username: str) -> Optional[Dict]:
        raise NotImplementedError

    @abstractmethod
    def set_user_role(self, username: str, role: str) -> Dict:
        raise NotImplementedError

    def reset_user_password(self, username: str, new_password: str) -> bool:
        """Reset a user's password. Returns True if successful."""
        raise NotImplementedError

    @abstractmethod
    def update_user_profile(
        self,
        username: str,
        *,
        full_name: str,
        organization: str,
        email: str,
        phone: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def delete_user(self, username: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_document(self, title: str, content: str, created_by: str = "manual") -> str:
        raise NotImplementedError

    @abstractmethod
    def save_uploaded_document(self, uploaded_file: FileStorage, created_by: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def list_documents(self) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def get_document(self, name: str) -> Optional[Dict]:
        raise NotImplementedError

    @abstractmethod
    def get_document_text(self, name: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_document_file_path(self, name: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def update_document_text(self, name: str, content: str, updated_by: str) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def delete_document(self, name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_conversations(self, username: str) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def create_conversation(self, username: str, title: str = DEFAULT_CONVERSATION_TITLE) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def rename_conversation(self, username: str, conversation_id: str, title: str) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def clear_conversation(self, username: str, conversation_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_conversation(self, username: str, conversation_id: str) -> Optional[str]:
        raise NotImplementedError

    @abstractmethod
    def ensure_conversation(self, username: str, conversation_id: Optional[str] = None) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def list_messages(self, username: str, conversation_id: str) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def append_chat_message(
        self,
        username: str,
        conversation_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict]] = None,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def get_runtime_state(self, key: str) -> Optional[Dict]:
        raise NotImplementedError

    @abstractmethod
    def set_runtime_state(self, key: str, value: Dict) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def delete_runtime_state(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def update_message_feedback(
        self,
        username: str,
        conversation_id: str,
        message_id: str,
        feedback_status: str,
        feedback_note: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def find_feedback_matches(self, username: str, question: str, limit: int = 3) -> List[Dict]:
        raise NotImplementedError

    @abstractmethod
    def get_port_activity_snapshot(self, window_days: int = 5) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def get_port_call(self, port_call_id: str) -> Dict:
        raise NotImplementedError

    @abstractmethod
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
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def mark_port_call_arrived(
        self,
        port_call_id: str,
        arrived_at: str,
        updated_by: str,
        berth: str = "",
        notes: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def mark_port_call_departed(
        self,
        port_call_id: str,
        departed_at: str,
        updated_by: str,
        next_port: str = "",
        notes: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def approve_port_call(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        raise NotImplementedError

    @abstractmethod
    def attach_entry_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def attach_departure_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def schedule_shift_plan(
        self,
        port_call_id: str,
        planned_shift_at: str,
        updated_by: str,
        destination_berth: str,
        constraints: Optional[List[str]] = None,
        shift_plan_note: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def approve_shift_plan(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        raise NotImplementedError

    @abstractmethod
    def abort_shift_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def mark_shift_completed(
        self,
        port_call_id: str,
        shifted_at: str,
        updated_by: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def attach_shift_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def abort_port_call(
        self,
        port_call_id: str,
        decided_by: str,
        aborted_reason: str,
        approval_note: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def schedule_departure_plan(
        self,
        port_call_id: str,
        planned_departure_at: str,
        updated_by: str,
        next_port: str = "",
        constraints: Optional[List[str]] = None,
        departure_plan_note: str = "",
    ) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def abort_departure_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        raise NotImplementedError
