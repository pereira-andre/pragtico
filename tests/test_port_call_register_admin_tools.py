from __future__ import annotations

from pathlib import Path

from flask import Flask
from jinja2 import DictLoader

from blueprints import port_calls
from core import services


class _RoleStore:
    backend_name = "test"

    def __init__(self, role: str) -> None:
        self.role = role

    def get_user_profile(self, username: str) -> dict:
        return {
            "username": username,
            "role": self.role,
            "full_name": "Utilizador Teste",
            "organization": "APSS",
            "email": username,
            "phone": "+351000000000",
        }


def _render_register_template(role: str) -> str:
    app = Flask(__name__)
    app.jinja_loader = DictLoader(
        {
            "base.html": "{% block content %}{% endblock %}",
            "port_call_register.html": Path("templates/port_call_register.html").read_text(encoding="utf-8"),
        }
    )
    app.jinja_env.globals["url_for"] = lambda endpoint, **values: f"/{endpoint}"
    app.jinja_env.globals["csrf_token"] = lambda: "csrf"

    with app.app_context():
        return app.jinja_env.get_template("port_call_register.html").render(
            current_role=role,
            register_form={},
            tracked_scales=[],
            port_activity={"stats": {"in_port_count": 0, "planned_count": 0, "pending_count": 0}},
            vessel_catalog=[],
            vessel_catalog_json="[]",
            port_call_json_template='{"vessel_name":"MSC Lyria"}',
            constraint_options=[],
        )


def test_port_call_json_admin_tools_are_visible_only_to_admin() -> None:
    admin_html = _render_register_template("admin")
    agent_html = _render_register_template("agente")

    assert "Importação técnica" in admin_html
    assert "Exportar escalas" in admin_html
    assert "Importação técnica" not in agent_html
    assert "Exportar escalas" not in agent_html


def test_port_call_json_routes_reject_agent_role() -> None:
    previous_store = services.store
    services.store = _RoleStore("agente")
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(port_calls.bp)

    try:
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "agent@example.test"
                sess["role"] = "agente"

            export_response = client.get("/port-calls/export-json", headers={"Accept": "application/json"})
            import_response = client.post(
                "/port-calls/import-json",
                data={"payload_json": "{}"},
                headers={"Accept": "application/json"},
            )
    finally:
        services.store = previous_store

    assert export_response.status_code == 403
    assert import_response.status_code == 403
