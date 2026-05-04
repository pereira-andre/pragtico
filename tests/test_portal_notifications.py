from __future__ import annotations

from flask import Flask

from blueprints import api, port_calls
from core import services
from core.portal_notifications import PORTAL_NOTIFICATION_CHANNEL, record_maneuver_notification


class PortalNotificationStore:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def get_user_profile(self, username: str) -> dict:
        if username == "agent@example.test":
            return {
                "username": username,
                "role": "agente",
                "full_name": "Agente Teste",
                "organization": "Agência Teste",
                "email": username,
                "phone": "+351 900 000 000",
            }
        return {"username": username, "role": "admin", "organization": "APSS"}

    def record_channel_event(self, *, channel: str, event_type: str, payload: dict, username: str = "", **_kwargs) -> dict:
        event = {
            "id": f"evt-{len(self.events) + 1}",
            "channel": channel,
            "event_type": event_type,
            "payload": payload,
            "username": username,
            "created_at": f"2026-05-04T10:0{len(self.events)}:00+00:00",
        }
        self.events.append(event)
        return event

    def list_channel_events(self, *, channel: str, since: str = "", limit: int = 20) -> list[dict]:
        matches = [item for item in self.events if item["channel"] == channel and item["created_at"] > since]
        return matches[:limit]


def _app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.register_blueprint(port_calls.bp)
    app.register_blueprint(api.bp)
    return app


def _port_call() -> dict:
    return {
        "id": "pc-1",
        "reference_code": "SET-001",
        "vessel_name": "NAVIO COM NOME MUITO COMPRIDO PARA TESTE",
        "agent_profile": {"organization": "Agência Teste"},
    }


def _maneuver(**overrides) -> dict:
    payload = {
        "id": "mnv123-extra",
        "type": "entry",
        "planned_at": "2026-05-04T14:15:00+01:00",
    }
    payload.update(overrides)
    return payload


def test_maneuver_notification_message_is_short_visual_and_informative(monkeypatch) -> None:
    store = PortalNotificationStore()
    monkeypatch.setattr(services, "store", store)
    app = _app()

    with app.test_request_context("/"):
        event = record_maneuver_notification(
            port_call=_port_call(),
            maneuver=_maneuver(),
            event_type="created",
            actor_username="admin@porto.pt",
        )

    message = event["payload"]["message"]
    assert message.startswith("🟡 ")
    assert "MNV123" in message
    assert "entrada 14:15" in message
    assert "APSS" in message
    assert len(message) <= 90
    assert event["payload"]["url"] == "/port-calls/pc-1/maneuvers/mnv123-extra"


def test_portal_live_feed_returns_toast_payload_for_site_banner(monkeypatch) -> None:
    store = PortalNotificationStore()
    store.record_channel_event(
        channel=PORTAL_NOTIFICATION_CHANNEL,
        event_type="maneuver_approved",
        username="admin@porto.pt",
        payload={
            "message": "✅ MNV123 · WAY FORWARD · entrada aprovada · APSS",
            "url": "/port-calls/pc-1/maneuvers/mnv123-extra",
            "scope_organization_key": "",
        },
    )
    monkeypatch.setattr(services, "store", store)
    app = _app()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin@porto.pt"
            sess["role"] = "admin"

        response = client.get("/api/portal-live-feed?since=2026-05-04T09:59:00+00:00")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["latest_created_at"] == "2026-05-04T10:00:00+00:00"
    assert payload["items"] == [
        {
            "id": "evt-1",
            "event_type": "maneuver_approved",
            "message": "✅ MNV123 · WAY FORWARD · entrada aprovada · APSS",
            "url": "/port-calls/pc-1/maneuvers/mnv123-extra",
            "created_at": "2026-05-04T10:00:00+00:00",
        }
    ]
