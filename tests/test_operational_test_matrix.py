from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from flask import Flask, session
from jinja2 import DictLoader, Environment

from core import services
from core.chat_planner import build_chat_execution_plan
from core.chat_reasoning import build_conversation_reasoning_state
from core.operational_sources import answer_direct_operational_query
from core.operational_test_suite import (
    _critical_berth_profile_text,
    _critical_json_source_text,
    _critical_text_source_text,
    _evaluate_berth_capacity_case,
    _missing_expected_tokens,
    _present_forbidden_tokens,
    berth_capacity_test_matrix,
    critical_bot_test_matrix,
    critical_maneuver_checklist_text,
    critical_slash_validation_text,
    operational_test_inventory,
    railway_bot_test_export_bytes,
)
from core.operational_diagnostics import build_operational_diagnostic, format_operational_diagnostic


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


class FakeWeatherService:
    enabled = True
    forecast = {
        "location": {"name": "Setúbal", "localtime": "2026-05-09 10:40"},
        "current": {
            "condition": "Parcialmente nublado",
            "temp_c": 18,
            "wind_kts": 13,
            "gust_kts": 20,
            "wind_dir": "S",
            "humidity": 70,
            "vis_km": 10,
            "precip_mm": 0,
        },
        "forecast_days": [],
        "hourly_groups": [],
    }

    def __init__(self) -> None:
        self.forecast = deepcopy(self.forecast)

    def get_forecast(self, days: int = 3) -> dict:
        return self.forecast

    def context_for_question(self, question: str) -> dict:
        return {
            "document": "Meteorologia teste Setúbal",
            "retrieval_mode": "live_api",
            "snippet": "vento S 13 kt, rajadas 20 kt",
            "text": "vento S 13 kt, rajadas 20 kt",
        }


def _install_fake_store(monkeypatch) -> None:
    monkeypatch.setattr(services, "store", FakeStore())
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", "knowledge", raising=False)
    monkeypatch.setattr(services, "weather_service", FakeWeatherService())


def test_operational_test_inventory_exposes_critical_matrix(monkeypatch) -> None:
    _install_fake_store(monkeypatch)

    inventory = operational_test_inventory()

    assert inventory["bot_matrix_count"] >= 10
    assert inventory["bot_matrix_automatic_count"] > inventory["bot_matrix_manual_count"]
    assert any(item["id"] == "ecooil-checklist" for item in inventory["bot_matrix"])
    assert any(group["name"] == "Checklist de manobras" for group in inventory["bot_matrix_groups"])
    assert inventory["railway_log"]["count"] == 150
    assert inventory["railway_log"]["passed_count"] == 150
    assert inventory["berth_capacity_test_count"] >= 20


def test_berth_capacity_matrix_is_executable(monkeypatch) -> None:
    _install_fake_store(monkeypatch)

    matrix = berth_capacity_test_matrix()
    results = {item["id"]: _evaluate_berth_capacity_case(item) for item in matrix}

    assert len(matrix) >= 20
    assert all(result["state"] == "passed" for result in results.values())
    assert results["tms1-target-230-c5-spans-c6"]["observed"] == "Sem conflito"
    assert results["tms1-right-and-left-occupied-blocked"]["observed"].startswith("Conflito")
    assert results["tms2-three-230-blocked"]["observed"].startswith("Conflito")
    assert "Slots ocupados: 2" in results["occupancy-autoeuropa-large"]["observed"]


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
    assert "150 perguntas de bug hunting" in html
    assert "Download JSON" in html
    assert "Download CSV" in html
    assert "Download PDF" in html
    assert "TMS 1, TMS 2 e Autoeuropa" in html
    assert "Autoeuropa permite 2 navios abaixo de 230 m" in html
    assert "TMS 1 permite 3 navios grandes incluindo Cais 8" in html
    assert "TMS 2 ocupa posições adjacentes por LOA" in html
    assert "TMS 1 usa a esquerda se a direita está ocupada" in html
    assert "TMS 1 bloqueia se direita e esquerda estão ocupadas" in html
    assert "TMS 2 bloqueia três navios de 230 m" in html
    assert "Ocupação conta Autoeuropa >=230 m como 2 slots" in html
    assert "operational-tests-matrix-group\" open" not in html
    assert "Eco-Oil na checklist" in html
    assert "Perda de maquina: ferro e VTS" in html
    assert "Emergencia: perda de bow" in html
    assert "Nevoeiro súbito em navegação: COLREG" in html
    assert "COLREG ultrapassagem canal estreito" in html
    assert "COLREG dragagem bordo livre" in html
    assert "4.º rebocador: costado ou standby" in html
    assert "Autoeuropa Ro-Ro com meteorologia atual" in html
    assert "Diagnostico Lisnave 300 m" in html
    assert "Diagnostico Hidrolift boca 45 m" in html
    assert "Diagnostico Eco-Oil com 2 rebocadores" in html
    assert "Diagnostico Tanquisado com 2 rebocadores" in html
    assert "Diagnostico percurso e reponto" in html
    assert "Diagnostico SECIL com reponto" in html
    assert "Diagnostico ALSTOM regras obrigatorias" in html
    assert "Diagnostico SECIL sem herdar Lisnave" in html
    assert "Follow-up SAPEC carga não IMO" in html
    assert "Mudança de caso sem herança indevida" in html
    assert "Entrada Secil E 19:25 validada contra reponto" in html
    assert "ALSTOM desde a Barra para preia-mar" in html
    assert "Distancia TMS 1 - ALSTOM" in html
    assert "Distancia TMS 1 - fora da Barra" in html
    assert "Planeamento Canal Norte Joao Farto - ALSTOM" in html
    assert "Canal Norte completo desde embarque" in html
    assert "Referencia TMS2 no Canal Norte" in html
    assert "Canal Sul ate Boia 14CS" in html
    assert "ETA Boia 12CS - Lisnave" in html
    assert "Rumos inversos Lisnave - Pilar 2" in html
    assert "Ligacao Lisnave - TMS1 via Joao Farto" in html
    assert "D31/D32/D33 Lisnave com proa a sul" in html
    assert "Notes on Shiphandling incorporado" in html
    assert "Lista de Luzes Setubal incorporada" in html
    assert "Caracteristica da Boia 1CN" in html
    assert "Setubal usa IALA A" in html
    assert "Historia e cultura de Setubal" in html
    assert "RIEAM/COLREG regras da estrada" in html
    assert "Unidades náuticas e Beaufort incorporadas" in html
    assert "Conversão km para milhas náuticas" in html
    assert "Beaufort força 6" in html
    assert "/validar-manobra Tanquisado com 2 rebocadores" in html
    assert "/validar-manobra Tanquisado fora do reponto" in html
    assert "/validar-manobra doca Lisnave" in html
    assert "/validar-manobra mudança" in html


def test_railway_bot_test_exports_include_debug_payload(monkeypatch) -> None:
    _install_fake_store(monkeypatch)

    json_payload, json_mimetype, json_filename = railway_bot_test_export_bytes("json")
    csv_payload, csv_mimetype, csv_filename = railway_bot_test_export_bytes("csv")
    pdf_payload, pdf_mimetype, pdf_filename = railway_bot_test_export_bytes("pdf")

    assert json_mimetype.startswith("application/json")
    assert json_filename.endswith(".json")
    assert b'"total": 150' in json_payload
    assert csv_mimetype.startswith("text/csv")
    assert csv_filename.endswith(".csv")
    assert b"question" in csv_payload
    assert pdf_mimetype == "application/pdf"
    assert pdf_filename.endswith(".pdf")
    assert pdf_payload.startswith(b"%PDF-1.4")


def test_operational_test_matrix_runs_context_follow_up_case(monkeypatch) -> None:
    _install_fake_store(monkeypatch)
    item = next(
        case
        for case in critical_bot_test_matrix()
        if case["id"] == "conversation-context-sapec-non-imo-follow-up"
    )
    plan = build_chat_execution_plan(item["question"])
    state = build_conversation_reasoning_state(item["question"], item["history"], plan)

    assert state is not None
    text = state["summary"]
    assert not _missing_expected_tokens(text, item["expected_tokens"])


def test_direct_operational_matrix_cases_pass(monkeypatch) -> None:
    _install_fake_store(monkeypatch)

    for item in critical_bot_test_matrix():
        if item.get("runner") != "direct_operational":
            continue
        payload = answer_direct_operational_query(item["question"]) or {}
        answer = payload.get("answer", "")
        missing = _missing_expected_tokens(answer, item.get("expected_tokens") or ())
        forbidden = _present_forbidden_tokens(answer, item.get("forbidden_tokens") or ())

        assert payload.get("answer_origin") == item.get("expected_origin")
        assert not missing, item["id"]
        assert not forbidden, item["id"]


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
            elif runner == "operational_diagnostic":
                text = format_operational_diagnostic(
                    build_operational_diagnostic(
                        item["question"],
                        history=item.get("history") or [],
                    )
                )
            else:
                continue
            missing = _missing_expected_tokens(text, item.get("expected_tokens") or ())
            forbidden = _present_forbidden_tokens(text, item.get("forbidden_tokens") or ())

            assert text.strip(), item["id"]
            assert not missing, item["id"]
            assert not forbidden, item["id"]
