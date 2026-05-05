from __future__ import annotations

from pathlib import Path

from flask import Flask
from jinja2 import DictLoader


def _render_local_warnings() -> str:
    app = Flask(__name__)
    app.jinja_loader = DictLoader(
        {
            "base.html": "{% block content %}{% endblock %}",
            "local_warnings.html": Path("templates/local_warnings.html").read_text(encoding="utf-8"),
        }
    )
    app.jinja_env.globals["url_for"] = lambda endpoint, **values: f"/{endpoint}"

    with app.app_context():
        return app.jinja_env.get_template("local_warnings.html").render(
            warnings=[],
            warnings_total=0,
            warnings_error="",
            warnings_runtime={"label": "Atualizado", "detail": "Fonte oficial"},
            warnings_status={
                "stale": False,
                "cache_updated_at_label": "",
                "last_attempt_at_label": "",
            },
            warning_filters={
                "q": "",
                "status": "",
                "statuses": [],
                "location": "",
                "locations": [],
                "attachments": "",
            },
        )


def test_local_warnings_hero_explains_notice_meaning() -> None:
    html = _render_local_warnings()

    assert "Comunicados em vigor da Capitania" in html
    assert "condicionamentos, trabalhos, interdições, eventos, perigos" in html
    assert "Consulta operacional com filtros" not in html
