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


def test_sources_snapshot_marks_empty_port_activity_as_partial(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        bot_insights,
        "services",
        SimpleNamespace(store=EmptyPortActivityStore(str(tmp_path)), KNOWLEDGE_DIR=str(tmp_path)),
    )

    sources = bot_insights.build_sources_snapshot()
    operational_source = next(item for item in sources if item["id"] == "operational_data")

    assert operational_source["label"] == "Escalas e atividade do porto"
    assert operational_source["state"] == "degraded"
    assert operational_source["meta"] == "Sem escalas resolvidas no portal."
    assert "monitor live" in operational_source["description"]


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
            "state": "degraded",
            "count": 0,
            "meta": "Sem escalas resolvidas no portal.",
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
    assert portal_detail["state"] == "degraded"
    assert portal_detail["detail"] == "Sem escalas resolvidas no portal."
