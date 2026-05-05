from __future__ import annotations

from pathlib import Path


def test_admin_casebooks_uses_row_based_operational_layout() -> None:
    template = Path("templates/admin_casebooks.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    for marker in (
        "admin-casebooks-v2",
        "admin-casebook-row-list",
        "admin-casebook-row",
        "admin-casebook-row-menu",
        "render_casebook_case_row",
        "render_chat_row",
        "render_practice_row",
        "Prática importada",
    ):
        assert marker in template

    for selector in (
        ".admin-casebooks-v2",
        ".admin-casebook-row-list",
        ".admin-casebook-row",
        ".admin-casebook-row-menu",
        ".admin-casebook-menu-body",
    ):
        assert selector in stylesheet


def test_admin_casebooks_hides_large_inline_case_forms() -> None:
    template = Path("templates/admin_casebooks.html").read_text(encoding="utf-8")

    assert "admin-bot-case-grid" not in template
    assert "admin-casebook-menu-form" in template
    assert 'aria-label="Opções do caso {{ item.reference_code }}"' in template
    assert 'aria-label="Opções da mensagem {{ item.id }}"' in template
    assert 'aria-label="Opções da prática {{ item.reference_code }}"' in template
