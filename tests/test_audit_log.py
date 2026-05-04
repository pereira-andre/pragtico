from __future__ import annotations

import json

from flask import Flask, session

from core import services
from core.audit_log import audit_dir, audit_request_response, iter_audit_events, write_audit_event


def test_audit_event_is_redacted_and_filterable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))

    event = write_audit_event(
        "user.permissions.update",
        category="utilizadores",
        actor="admin@porto.pt",
        actor_role="admin",
        severity="critical",
        resource="app_user",
        resource_id="pilot@example.test",
        details={"new_role": "piloto", "password": "secret", "api_token": "token"},
    )

    assert event["category"] == "utilizadores"
    assert event["details"]["password"] == "[redacted]"
    assert event["details"]["api_token"] == "[redacted]"
    assert audit_dir() == tmp_path

    events = iter_audit_events({"category": "utilizadores", "actor": "admin"}, limit=10)
    assert len(events) == 1
    assert events[0]["action"] == "user.permissions.update"

    raw_lines = list(tmp_path.glob("audit-*.jsonl"))[0].read_text(encoding="utf-8").splitlines()
    assert json.loads(raw_lines[0])["details"]["password"] == "[redacted]"


def test_mutating_request_is_audited(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(services, "DATA_DIR", str(tmp_path))
    app = Flask(__name__)
    app.secret_key = "test"

    with app.test_request_context(
        "/admin/users/pilot@example.test",
        method="POST",
        data={"role": "piloto", "new_password": "secret"},
    ):
        session["username"] = "admin@porto.pt"
        session["role"] = "admin"
        response = app.response_class("", status=302)

        audit_request_response(response)

    events = iter_audit_events({"category": "utilizadores"}, limit=10)
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "request.admin/users/pilot@example.test" or event["action"].startswith("request.")
    assert event["result"] == "success"
    assert event["details"]["form_keys"] == ["new_password", "role"]
    assert "secret" not in json.dumps(event, ensure_ascii=False)
