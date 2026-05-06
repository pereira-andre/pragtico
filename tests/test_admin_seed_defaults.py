from __future__ import annotations

import logging
from pathlib import Path

from core.runtime import seed_admin


class FakeStore:
    def __init__(self) -> None:
        self.users = {
            "admin": {"username": "admin", "role": "admin"},
            "agente": {"username": "agente", "role": "agente"},
            "piloto": {"username": "piloto", "role": "piloto"},
        }

    def get_user_profile(self, username: str) -> dict | None:
        return self.users.get(username)

    def create_user(self, **payload) -> dict:
        self.users[payload["username"]] = dict(payload)
        return self.users[payload["username"]]

    def set_user_role(self, username: str, role: str) -> dict:
        self.users[username]["role"] = role
        return self.users[username]

    def reset_user_password(self, username: str, password: str) -> bool:
        self.users[username]["password"] = password
        return True

    def update_user_profile(self, username: str, **payload) -> dict:
        self.users[username].update(payload)
        return self.users[username]

    def delete_user(self, username: str) -> None:
        self.users.pop(username)


def test_seed_admin_removes_legacy_default_users_and_sets_whatsapp(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_EMAIL", "admin@porto.pt")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret123")
    monkeypatch.setenv("WHATSAPP_TEST_TO", "+351912345678")
    monkeypatch.delenv("ADMIN_WHATSAPP_NUMBER", raising=False)
    monkeypatch.delenv("ADMIN_PHONE", raising=False)
    store = FakeStore()

    seed_admin(store, logging.getLogger("test"))

    assert set(store.users) == {"admin@porto.pt"}
    assert store.users["admin@porto.pt"]["role"] == "admin"
    assert store.users["admin@porto.pt"]["whatsapp_number"] == "351912345678"
    assert store.users["admin@porto.pt"]["whatsapp_opt_in"] is True
    assert store.users["admin@porto.pt"]["phone"] == "+351912345678"


def test_postgres_seed_does_not_create_legacy_login_users() -> None:
    source = Path("storage/postgres.py").read_text(encoding="utf-8")

    assert "admin123" not in source
    assert "agente123" not in source
    assert "piloto123" not in source
