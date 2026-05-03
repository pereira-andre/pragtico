from __future__ import annotations

from pathlib import Path

from flask import Flask, session
from jinja2 import DictLoader, Environment

from core import services
from core.operational_sources import answer_direct_operational_query
from core.operational_test_suite import (
    _critical_berth_profile_text,
    _critical_json_source_text,
    _critical_text_source_text,
    _missing_expected_tokens,
    critical_bot_test_matrix,
    critical_maneuver_checklist_text,
    critical_slash_validation_text,
    operational_test_inventory,
)


class FakeStore:
    knowledge_dir = "knowledge"

    def list_port_calls(self) -> list[dict]:
        return []

    def get_user_profile(self, username: str) -> dict:
        return {}

    def get_port_activity_snapshot(self, window_days: int = 5) -> dict:
        return {
            "arrivals": [],
            "in_port": [],
            "departed": [],
            "aborted": [],
            "departure_candidates": [],
            "planned_maneuvers": [],
            "archived_maneuvers": [],
        }


def _install_fake_store(monkeypatch) -> None:
    monkeypatch.setattr(services, "store", FakeStore())
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", "knowledge", raising=False)


def test_operational_test_inventory_exposes_critical_matrix(monkeypatch) -> None:
    _install_fake_store(monkeypatch)

    inventory = operational_test_inventory()

    assert inventory["bot_matrix_count"] >= 10
    assert inventory["bot_matrix_automatic_count"] > inventory["bot_matrix_manual_count"]
    assert any(item["id"] == "ecooil-checklist" for item in inventory["bot_matrix"])
    assert any(group["name"] == "Checklist de manobras" for group in inventory["bot_matrix_groups"])


def test_operational_tests_page_renders_matrix(monkeypatch) -> None:
    _install_fake_store(monkeypatch)
    source = Path("templates/admin_operational_tests.html").read_text(encoding="utf-8")
    environment = Environment(
        loader=DictLoader(
            {
                "admin_operational_tests.html": source,
                "base.html": "{% block content %}{% endblock %}",
            }
        )
    )
    environment.globals["url_for"] = lambda endpoint, **values: f"/{endpoint}"
    environment.globals["csrf_token"] = lambda: "csrf"

    html = environment.get_template("admin_operational_tests.html").render(
        result=None,
        inventory=operational_test_inventory(),
    )

    assert "Matriz crítica" in html
    assert "Eco-Oil na checklist" in html
    assert "Perda de maquina: ferro e VTS" in html
    assert "Emergencia: perda de bow" in html
    assert "4.º rebocador: costado ou standby" in html
    assert "D31/D32/D33 Lisnave com proa a sul" in html
    assert "Notes on Shiphandling incorporado" in html
    assert "Lista de Luzes Setubal incorporada" in html
    assert "Caracteristica da Boia 1CN" in html
    assert "Setubal usa IALA A" in html
    assert "Historia e cultura de Setubal" in html
    assert "/validar-manobra Tanquisado com 2 rebocadores" in html
    assert "/validar-manobra Tanquisado fora do reponto" in html
    assert "/validar-manobra doca Lisnave" in html
    assert "/validar-manobra mudança" in html


def test_direct_operational_matrix_cases_pass(monkeypatch) -> None:
    _install_fake_store(monkeypatch)

    for item in critical_bot_test_matrix():
        if item.get("runner") != "direct_operational":
            continue
        payload = answer_direct_operational_query(item["question"]) or {}
        answer = payload.get("answer", "")
        missing = _missing_expected_tokens(answer, item.get("expected_tokens") or ())

        assert payload.get("answer_origin") == item.get("expected_origin")
        assert not missing, item["id"]


def test_source_and_checklist_matrix_cases_pass(monkeypatch) -> None:
    _install_fake_store(monkeypatch)
    app = Flask(__name__)
    app.secret_key = "test"

    with app.test_request_context("/"):
        session["role"] = "admin"
        for item in critical_bot_test_matrix():
            runner = item.get("runner")
            if runner == "knowledge_json":
                text = _critical_json_source_text(item["source_path"])
            elif runner == "knowledge_text":
                text = _critical_text_source_text(item["source_path"])
            elif runner == "berth_profile":
                text = _critical_berth_profile_text(item["profile_query"])
            elif runner == "maneuver_checklist":
                text = critical_maneuver_checklist_text(item["fixture"])
            elif runner == "slash_validation":
                text = critical_slash_validation_text(item["fixture"])
            else:
                continue
            missing = _missing_expected_tokens(text, item.get("expected_tokens") or ())

            assert text.strip(), item["id"]
            assert not missing, item["id"]
