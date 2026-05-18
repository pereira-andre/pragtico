from __future__ import annotations

from types import SimpleNamespace

from core import bot_insights


class EmptyPortActivityStore:
    def __init__(self, knowledge_dir: str) -> None:
        self.knowledge_dir = knowledge_dir

    def list_documents(self) -> list[dict]:
        return []

    def get_port_activity_snapshot(self, window_days: int = 3650) -> dict:
        return {"arrivals": []}


def test_sources_snapshot_marks_empty_port_activity_as_active(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        bot_insights,
        "services",
        SimpleNamespace(store=EmptyPortActivityStore(str(tmp_path)), KNOWLEDGE_DIR=str(tmp_path)),
    )

    sources = bot_insights.build_sources_snapshot()
    operational_source = next(item for item in sources if item["id"] == "operational_data")

    assert operational_source["label"] == "Escalas e atividade do porto"
    assert operational_source["state"] == "online"
    assert operational_source["action_url"] == "/port-calls/register"
    assert operational_source["meta"] == "Portal acessível; sem escalas resolvidas."
    assert "monitor live" in operational_source["description"]

    tuning = bot_insights.build_tuning_map_snapshot()
    port_activity_component = next(
        component
        for group in tuning["groups"]
        for component in group["components"]
        if component["source_name"] == "port_calls"
    )
    assert port_activity_component["action_url"] == "/port-calls/register"


def test_monitor_live_card_counts_live_services_not_empty_portal(monkeypatch) -> None:
    monkeypatch.setattr(
        bot_insights,
        "services",
        SimpleNamespace(
            rag=None,
            tide_service=SimpleNamespace(csv_path="mares.csv"),
            weather_service=SimpleNamespace(enabled=True),
            wave_service=SimpleNamespace(enabled=True),
            local_warning_service=SimpleNamespace(enabled=True),
        ),
    )
    sources = [
        {
            "id": "operational_data",
            "label": "Escalas e atividade do porto",
            "state": "online",
            "count": 0,
            "meta": "Portal acessível; sem escalas resolvidas.",
        }
    ]

    monitor = bot_insights.build_bot_monitor_snapshot(
        settings={},
        sources=sources,
        quality={"active_cases_total": 1, "passed_total": 1, "failed_total": 0},
        signals={},
        exceptions={"total": 0, "severity_counts": {}},
        health={"state": "online", "score": 100},
    )

    live_card = next(card for card in monitor["runtime_cards"] if card["id"] == "live")
    portal_detail = next(item for item in monitor["live_details"] if item["label"] == "Portal")

    assert live_card["value"] == "4/4"
    assert live_card["detail"] == "marés · meteo · ondulação · avisos"
    assert live_card["state"] == "online"
    assert portal_detail["state"] == "online"
    assert portal_detail["detail"] == "Portal acessível; sem escalas resolvidas."


def test_empty_port_activity_does_not_penalize_source_coverage(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        bot_insights,
        "services",
        SimpleNamespace(store=EmptyPortActivityStore(str(tmp_path)), KNOWLEDGE_DIR=str(tmp_path)),
    )
    sources = bot_insights.build_sources_snapshot()
    health_sources = [
        item if item["id"] == "operational_data" else {**item, "state": "online"}
        for item in sources
    ]

    health = bot_insights.compute_health_score(
        quality={"pass_rate_pct": 100},
        signals={"positives": {"total": 1}, "negatives": {"total": 0}},
        exceptions={"severity_counts": {"high": 0}},
        sources=health_sources,
    )

    assert next(item for item in sources if item["id"] == "operational_data")["state"] == "online"
    assert health["coverage_pct"] == 100
    assert health["score"] == 100
