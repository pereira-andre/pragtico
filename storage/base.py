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
        """Return all registered users as a list of profile dicts."""
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
        whatsapp_number: str = "",
        whatsapp_opt_in: bool = False,
        whatsapp_opt_in_at: str = "",
    ) -> Dict:
        """Create a new user with the given credentials and profile data."""
        raise NotImplementedError

    @abstractmethod
    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        """Authenticate a user by username and password, returning their profile or None."""
        raise NotImplementedError

    @abstractmethod
    def get_user_profile(self, username: str) -> Optional[Dict]:
        """Return the profile dict for the given username, or None if not found."""
        raise NotImplementedError

    @abstractmethod
    def rename_user(self, username: str, new_username: str) -> Dict:
        """Rename the user's login identifier and return the updated profile."""
        raise NotImplementedError

    @abstractmethod
    def set_user_role(self, username: str, role: str) -> Dict:
        """Update the role for the given user and return the updated profile."""
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
        whatsapp_number: str = "",
        whatsapp_opt_in: bool = False,
        whatsapp_opt_in_at: str = "",
    ) -> Dict:
        """Update profile fields for the given user and return the updated profile."""
        raise NotImplementedError

    @abstractmethod
    def delete_user(self, username: str) -> None:
        """Delete the user account and all associated data."""
        raise NotImplementedError

    @abstractmethod
    def save_document(self, title: str, content: str, created_by: str = "manual") -> str:
        """Save a new text document to the knowledge base and return its filename."""
        raise NotImplementedError

    @abstractmethod
    def save_uploaded_document(self, uploaded_file: FileStorage, created_by: str) -> str:
        """Store an uploaded file in the knowledge base and return its filename."""
        raise NotImplementedError

    @abstractmethod
    def list_documents(self) -> List[Dict]:
        """Return metadata records for all documents in the knowledge base."""
        raise NotImplementedError

    @abstractmethod
    def get_document(self, name: str) -> Optional[Dict]:
        """Return the metadata record for a document by name, or None if not found."""
        raise NotImplementedError

    @abstractmethod
    def get_document_text(self, name: str) -> str:
        """Return the extracted text content of a document."""
        raise NotImplementedError

    @abstractmethod
    def get_document_file_path(self, name: str) -> str:
        """Return the absolute filesystem path for a document file."""
        raise NotImplementedError

    @abstractmethod
    def update_document_text(self, name: str, content: str, updated_by: str) -> Dict:
        """Overwrite the text content of an editable document and return its updated record."""
        raise NotImplementedError

    @abstractmethod
    def delete_document(self, name: str) -> None:
        """Remove a document from the knowledge base and its metadata record."""
        raise NotImplementedError

    @abstractmethod
    def list_conversations(self, username: str) -> List[Dict]:
        """Return all conversations for a user, sorted by most recently updated."""
        raise NotImplementedError

    @abstractmethod
    def create_conversation(self, username: str, title: str = DEFAULT_CONVERSATION_TITLE) -> Dict:
        """Create a new conversation for the user and return it."""
        raise NotImplementedError

    @abstractmethod
    def rename_conversation(self, username: str, conversation_id: str, title: str) -> Dict:
        """Rename a conversation and return the updated record."""
        raise NotImplementedError

    @abstractmethod
    def clear_conversation(self, username: str, conversation_id: str) -> None:
        """Delete all messages in a conversation without removing the conversation itself."""
        raise NotImplementedError

    @abstractmethod
    def delete_conversation(self, username: str, conversation_id: str) -> Optional[str]:
        """Delete a conversation and return the ID of the next conversation to show, if any."""
        raise NotImplementedError

    @abstractmethod
    def ensure_conversation(self, username: str, conversation_id: Optional[str] = None) -> Dict:
        """Return an existing conversation by ID, or create and return a new one."""
        raise NotImplementedError

    @abstractmethod
    def list_messages(self, username: str, conversation_id: str) -> List[Dict]:
        """Return all messages for a given conversation."""
        raise NotImplementedError

    @abstractmethod
    def append_chat_message(
        self,
        username: str,
        conversation_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict]] = None,
        *,
        channel: str = "web",
        channel_user_id: str = "",
        external_message_id: str = "",
        external_reply_to_id: str = "",
        channel_metadata: Optional[Dict] = None,
    ) -> Dict:
        """Append a message to a conversation and return the saved message record."""
        raise NotImplementedError

    @abstractmethod
    def update_message_channel_metadata(
        self,
        username: str,
        conversation_id: str,
        message_id: str,
        *,
        channel: Optional[str] = None,
        channel_user_id: Optional[str] = None,
        external_message_id: Optional[str] = None,
        external_reply_to_id: Optional[str] = None,
        channel_metadata: Optional[Dict] = None,
    ) -> Dict:
        """Update transport/channel metadata for a stored chat message and return the message."""
        raise NotImplementedError

    @abstractmethod
    def find_message_by_channel_message_id(self, channel: str, external_message_id: str) -> Optional[Dict]:
        """Return a stored message by channel/external ID, including owning username if found."""
        raise NotImplementedError

    @abstractmethod
    def record_channel_event(
        self,
        *,
        channel: str,
        event_type: str,
        payload: Dict,
        username: str = "",
        conversation_id: str = "",
        local_message_id: str = "",
        channel_user_id: str = "",
        external_event_id: str = "",
        external_message_id: str = "",
    ) -> Dict:
        """Persist a raw channel event for audit/debug purposes and return the saved record."""
        raise NotImplementedError

    @abstractmethod
    def list_channel_events(
        self,
        *,
        channel: str,
        since: str = "",
        limit: int = 20,
    ) -> List[Dict]:
        """Return channel events for the given channel ordered from oldest to newest."""
        raise NotImplementedError

    @abstractmethod
    def get_runtime_state(self, key: str) -> Optional[Dict]:
        """Return the runtime state dict stored under the given key, or None."""
        raise NotImplementedError

    @abstractmethod
    def set_runtime_state(self, key: str, value: Dict) -> Dict:
        """Persist a runtime state dict under the given key and return it."""
        raise NotImplementedError

    @abstractmethod
    def delete_runtime_state(self, key: str) -> None:
        """Remove the runtime state entry for the given key."""
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
        """Update feedback status and note for a chat message and return the updated record."""
        raise NotImplementedError

    @abstractmethod
    def find_feedback_matches(
        self,
        username: str,
        question: str,
        limit: int = 3,
        feedback_statuses: Optional[set[str]] = None,
    ) -> List[Dict]:
        """Return previously reviewed chat answers whose question closely matches the given text."""
        raise NotImplementedError

    @abstractmethod
    def get_port_activity_snapshot(self, window_days: int = 5) -> Dict:
        """Return a snapshot of port activity covering the specified number of days."""
        raise NotImplementedError

    @abstractmethod
    def list_maneuver_cases(
        self,
        *,
        limit: int = 100,
        maneuver_type: Optional[str] = None,
        state: Optional[str] = None,
        port_call_id: Optional[str] = None,
    ) -> List[Dict]:
        """Return persisted maneuver cases, newest first."""
        raise NotImplementedError

    @abstractmethod
    def get_maneuver_case(self, maneuver_id: str) -> Optional[Dict]:
        """Return a persisted maneuver case by maneuver id, if any."""
        raise NotImplementedError

    @abstractmethod
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
        """Return the best matching historical maneuver cases for the given profile."""
        raise NotImplementedError

    @abstractmethod
    def update_maneuver_case_feedback(
        self,
        *,
        maneuver_id: str,
        feedback_status: str,
        feedback_note: str = "",
        feedback_by: str = "",
    ) -> Dict:
        """Persist validated operational feedback on a maneuver case and return the updated case."""
        raise NotImplementedError

    @abstractmethod
    def clear_port_calls(self) -> int:
        """Delete all port calls and maneuver history, returning the number of removed records."""
        raise NotImplementedError

    @abstractmethod
    def get_port_call(self, port_call_id: str) -> Dict:
        """Return the decorated port call record for the given ID."""
        raise NotImplementedError

    @abstractmethod
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
        """Edit the scale and vessel data for an existing port call."""
        raise NotImplementedError

    @abstractmethod
    def delete_port_call(self, port_call_id: str) -> Dict:
        """Delete a port call and return the removed decorated record."""
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
        vessel_bow_thruster: str = "unknown",
        vessel_stern_thruster: str = "unknown",
    ) -> Dict:
        """Create a new port call record and return it."""
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
        """Record the vessel's arrival and transition the port call to in-port status."""
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
        """Record the vessel's departure and transition the port call to departed status."""
        raise NotImplementedError

    @abstractmethod
    def approve_port_call(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        """Approve the pending maneuver for a port call and return the updated record."""
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
        maneuver_id: Optional[str] = None,
    ) -> Dict:
        """Attach a pilot entry report to the port call's entry maneuver."""
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
        maneuver_id: Optional[str] = None,
    ) -> Dict:
        """Attach a pilot departure report to the port call's departure maneuver."""
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
        """Schedule a berth-shift maneuver plan for a port call."""
        raise NotImplementedError

    @abstractmethod
    def approve_shift_plan(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        """Approve a pending berth-shift plan and return the updated port call."""
        raise NotImplementedError

    @abstractmethod
    def abort_shift_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        """Abort an active berth-shift plan and return the updated port call."""
        raise NotImplementedError

    @abstractmethod
    def mark_shift_completed(
        self,
        port_call_id: str,
        shifted_at: str,
        updated_by: str,
    ) -> Dict:
        """Mark a berth-shift maneuver as completed and return the updated port call."""
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
        maneuver_id: Optional[str] = None,
    ) -> Dict:
        """Attach a pilot shift report to the port call's shift maneuver."""
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
        """Edit the plan fields of an existing maneuver and log the change."""
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
        """Edit the report fields of a completed maneuver and log the change."""
        raise NotImplementedError

    @abstractmethod
    def delete_maneuver(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
    ) -> Dict:
        """Delete a maneuver from a port call and return the updated or removed record."""
        raise NotImplementedError

    @abstractmethod
    def delete_maneuver_report(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
    ) -> Dict:
        """Delete the report fields from an existing maneuver and return the updated record."""
        raise NotImplementedError

    @abstractmethod
    def abort_port_call(
        self,
        port_call_id: str,
        decided_by: str,
        aborted_reason: str,
        approval_note: str = "",
    ) -> Dict:
        """Abort the entire port call and return the updated record."""
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
        """Schedule a departure plan for a port call and return the updated record."""
        raise NotImplementedError

    @abstractmethod
    def abort_departure_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        """Abort the scheduled departure plan and return the updated port call."""
        raise NotImplementedError
