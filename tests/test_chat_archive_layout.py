from __future__ import annotations

from pathlib import Path

from flask import Flask
from jinja2 import DictLoader


def _render_chat_archive() -> str:
    app = Flask(__name__)
    app.jinja_loader = DictLoader(
        {
            "base.html": "{% block content %}{% endblock %}",
            "chat_archive.html": Path("templates/chat_archive.html").read_text(encoding="utf-8"),
        }
    )
    app.jinja_env.globals["url_for"] = lambda endpoint, **values: f"/{endpoint}"
    app.jinja_env.globals["csrf_token"] = lambda: "csrf"

    current_conversation = {
        "id": "conv-1",
        "title": "Manobra Tanquisado com rebocadores",
        "updated_at_label": "hoje 10:30",
        "message_count": 4,
    }
    conversations = [
        current_conversation,
        {
            "id": "conv-2",
            "title": "Nevoeiro no canal sul",
            "updated_at_label": "ontem 22:15",
            "message_count": 2,
        },
    ]

    with app.app_context():
        return app.jinja_env.get_template("chat_archive.html").render(
            current_role="admin",
            chatbot_model="operacional",
            current_conversation=current_conversation,
            conversations=conversations,
            messages=[],
        )


def test_chat_archive_has_refined_workspace_and_sidebar_shell() -> None:
    html = _render_chat_archive()

    assert 'class="chat-workspace chat-archive-page"' in html
    assert "chat-archive-hero" in html
    assert "chat-archive-hero-grid" in html
    assert "chat-sidebar-list-shell" in html
    assert "chat-sidebar-search-field" in html
    assert "Nova conversa" in html
    assert "Lista de conversas" in html
    assert "positionSidebarMenu" in html


def test_chat_archive_css_keeps_sidebar_list_wide_and_menu_floating() -> None:
    css = Path("static/css/chat.css").read_text(encoding="utf-8")

    assert ".chat-archive-page .chat-sidebar-card" in css
    assert "grid-template-columns: minmax(0, 1fr) auto;" in css
    assert "height: clamp(430px, 58vh, 760px);" in css
    assert ".chat-archive-page .chat-sidebar-menu[open] .chat-sidebar-menu-body" in css
    assert "position: fixed;" in css
    assert "min-height: clamp(220px, 26vh, 320px);" in css
    assert "min-height: 92px;" in css
    assert "grid-template-columns: repeat(2, minmax(150px, 1fr));" in css
