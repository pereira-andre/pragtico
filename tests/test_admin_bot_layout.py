from __future__ import annotations

import re
from pathlib import Path


def test_admin_navigation_labels_bot_entry() -> None:
    base = Path("templates/base.html").read_text(encoding="utf-8")

    assert 'href="{{ url_for(\'admin.admin_bot\') }}">Bot</a>' in base
    assert 'href="{{ url_for(\'admin.admin_bot\') }}">Admin</a>' not in base


def test_admin_bot_page_uses_clean_control_layout() -> None:
    template = Path("templates/admin_bot.html").read_text(encoding="utf-8")

    assert "Centro de controlo do motor de resposta" in template
    assert "admin-bot-page-nav" in template
    assert "admin-bot-control-grid" in template
    assert '<a href="#casebooks">Governança detalhada</a>' in template
    assert "Teste rápido" in template
    assert "Contexto disponível para resposta" in template
    assert "Definições e calibração do bot" in template
    assert "admin-bot-settings-block" in template
    assert "Contexto disponível ao RAG" not in template
    assert "aprendizagem automática" not in template.lower()


def test_admin_bot_detailed_sections_are_collapsed_by_default() -> None:
    template = Path("templates/admin_bot.html").read_text(encoding="utf-8")

    open_detail_classes = re.findall(
        r"<details[^>]+class=\"[^\"]*(admin-bot-run-history|admin-bot-scenario-collapse|admin-bot-protected-collapse|admin-bot-governance-collapse)[^\"]*\"[^>]*open",
        template,
    )
    assert open_detail_classes == []
