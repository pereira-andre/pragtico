from __future__ import annotations

from pathlib import Path

from flask import Flask
from jinja2 import DictLoader


def _render_archive_template() -> str:
    app = Flask(__name__)
    app.jinja_loader = DictLoader(
        {
            "base.html": "{% block content %}{% endblock %}",
            "maneuver_archive.html": Path("templates/maneuver_archive.html").read_text(encoding="utf-8"),
        }
    )
    app.jinja_env.globals["url_for"] = lambda endpoint, **values: f"/{endpoint}"

    with app.app_context():
        return app.jinja_env.get_template("maneuver_archive.html").render(
            port_activity={"stats": {"archive_scale_count": 0}},
            archive_filters={
                "q": "",
                "years": [2026],
                "year": 2026,
                "months": [{"value": 5, "label": "Maio"}],
                "month": 5,
                "agents": [],
                "agent": "",
                "maneuver_type": "",
                "selection": "scales",
            },
            archived_scales=[],
            archive_summary={
                "scale_count": 0,
                "maneuver_count": 0,
                "period_label": "Maio 2026",
                "period_hint": "período selecionado",
                "total_cost_label": "0,00 €",
                "total_pilotage_label": "0,00 €",
                "total_tup_label": "0,00 €",
            },
        )


def test_archive_simulator_is_collapsed_after_workspace() -> None:
    html = _render_archive_template()

    assert 'class="ops-shell archive-page"' in html
    assert 'class="panel planned-panel archive-workspace-panel"' in html
    assert 'class="panel archive-simulator-panel" id="cost-simulator-panel"' in html
    assert html.find("archive-workspace-panel") < html.find("archive-simulator-panel")
    assert "<summary" in html
    assert "window.location.hash === \"#cost-simulator-panel\"" in html


def test_archive_uses_refined_filter_and_selection_layout() -> None:
    html = _render_archive_template()

    assert "archive-filter-card" in html
    assert "archive-filter-grid-compact" in html
    assert "archive-filter-actions" in html
    assert "archive-report-panel" in html
    assert "archive-simulator-actions" in html
