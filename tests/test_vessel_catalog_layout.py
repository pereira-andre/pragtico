from __future__ import annotations

from pathlib import Path

from flask import Flask
from jinja2 import DictLoader


def _render_vessel_catalog() -> str:
    app = Flask(__name__)
    app.jinja_loader = DictLoader(
        {
            "base.html": "{% block content %}{% endblock %}",
            "vessel_catalog.html": Path("templates/vessel_catalog.html").read_text(encoding="utf-8"),
        }
    )
    app.jinja_env.globals["url_for"] = lambda endpoint, **values: f"/{endpoint}"
    app.jinja_env.globals["csrf_token"] = lambda: "csrf"

    with app.app_context():
        return app.jinja_env.get_template("vessel_catalog.html").render(
            current_role="admin",
            vessels=[],
            vessel_summary={
                "total_count": 12,
                "filtered_count": 3,
                "type_count": 4,
                "scale_count": 28,
            },
            vessel_filters={"q": "msc", "vessel_type": "Porta-contentores"},
            vessel_type_options=[{"label": "Porta-contentores"}, {"label": "Ro-Ro"}],
            vessel_catalog_json_template='{"vessels":[]}',
        )


def test_vessel_catalog_search_uses_compact_toolbar() -> None:
    html = _render_vessel_catalog()

    assert "vessel-search-panel" in html
    assert "vessel-search-bar" in html
    assert "vessel-filter-state" in html
    assert "archive-filter-grid" not in html
    assert "Limpar filtros" in html
    assert "Exportar JSON" in html
