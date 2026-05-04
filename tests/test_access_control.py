from __future__ import annotations

from flask import Flask, session

from core import services
from core.access_control import ensure_session_user_profile


class FakeStore:
    def __init__(self, profile: dict | None) -> None:
        self.profile = profile
        self.set_user_role_calls: list[tuple[str, str]] = []

    def get_user_profile(self, username: str) -> dict | None:
        return self.profile

    def set_user_role(self, username: str, role: str) -> dict:
        self.set_user_role_calls.append((username, role))
        return {**(self.profile or {}), "username": username, "role": role}


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
