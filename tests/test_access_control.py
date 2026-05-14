from __future__ import annotations

from flask import Flask, session

from core import services
from core.access_control import ensure_session_user_profile, filter_port_activity_for_session


class FakeStore:
    def __init__(self, profile: dict | None) -> None:
        self.profile = profile
        self.set_user_role_calls: list[tuple[str, str]] = []

    def get_user_profile(self, username: str) -> dict | None:
        return self.profile

    def set_user_role(self, username: str, role: str) -> dict:
        self.set_user_role_calls.append((username, role))
        return {**(self.profile or {}), "username": username, "role": role}


class ScopeStore:
    def __init__(self) -> None:
        self.profiles = {
            "agent-a@example.test": {"username": "agent-a@example.test", "role": "agente", "organization": "Agência A"},
            "pilot@example.test": {"username": "pilot@example.test", "role": "piloto", "organization": "Pilotos"},
            "admin@example.test": {"username": "admin@example.test", "role": "admin", "organization": "APSS"},
            "owner-a@example.test": {"username": "owner-a@example.test", "role": "agente", "organization": "Agência A"},
            "owner-b@example.test": {"username": "owner-b@example.test", "role": "agente", "organization": "Agência B"},
        }

    def get_user_profile(self, username: str) -> dict | None:
        return self.profiles.get(str(username or "").strip().lower())


def test_session_role_is_synced_from_stored_profile() -> None:
    app = Flask(__name__)
    app.secret_key = "test"
    previous_store = services.store
    fake_store = FakeStore({"username": "pilot@example.test", "role": "piloto"})
    services.store = fake_store
    try:
        with app.test_request_context("/"):
            session["username"] = "pilot@example.test"
            session["role"] = "admin"

            assert ensure_session_user_profile() is True

            assert session["role"] == "piloto"
            assert fake_store.set_user_role_calls == []
    finally:
        services.store = previous_store


def test_missing_profile_clears_session() -> None:
    app = Flask(__name__)
    app.secret_key = "test"
    previous_store = services.store
    services.store = FakeStore(None)
    try:
        with app.test_request_context("/"):
            session["username"] = "missing@example.test"
            session["role"] = "admin"

            assert ensure_session_user_profile() is False

            assert "username" not in session
            assert "role" not in session
    finally:
        services.store = previous_store


def test_archive_activity_scope_admin_and_pilot_see_all_agent_sees_own_agency() -> None:
    app = Flask(__name__)
    app.secret_key = "test"
    previous_store = services.store
    services.store = ScopeStore()
    activity = {
        "arrivals": [],
        "in_port": [],
        "departed": [],
        "aborted": [],
        "planned_maneuvers": [],
        "archived_maneuvers": [
            {"port_call_id": "a1", "vessel_name": "A", "created_by": "owner-a@example.test"},
            {"port_call_id": "b1", "vessel_name": "B", "created_by": "owner-b@example.test"},
        ],
        "archived_scales": [
            {"port_call_id": "a1", "vessel_name": "A", "created_by": "owner-a@example.test"},
            {"port_call_id": "b1", "vessel_name": "B", "created_by": "owner-b@example.test"},
        ],
        "departure_candidates": [],
        "maneuvers": [],
        "stats": {},
    }
    try:
        with app.test_request_context("/"):
            session["username"] = "admin@example.test"
            session["role"] = "admin"
            admin_view = filter_port_activity_for_session(activity)
            assert [item["vessel_name"] for item in admin_view["archived_scales"]] == ["A", "B"]

        with app.test_request_context("/"):
            session["username"] = "pilot@example.test"
            session["role"] = "piloto"
            pilot_view = filter_port_activity_for_session(activity)
            assert [item["vessel_name"] for item in pilot_view["archived_maneuvers"]] == ["A", "B"]

        with app.test_request_context("/"):
            session["username"] = "agent-a@example.test"
            session["role"] = "agente"
            agent_view = filter_port_activity_for_session(activity)
            assert [item["vessel_name"] for item in agent_view["archived_scales"]] == ["A"]
            assert [item["vessel_name"] for item in agent_view["archived_maneuvers"]] == ["A"]
    finally:
        services.store = previous_store
