from __future__ import annotations

from pathlib import Path


def test_profile_page_uses_operational_layout() -> None:
    template = Path("templates/profile.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    for marker in (
        "profile-page",
        "profile-hero",
        "profile-workspace",
        "profile-form-card",
        "profile-side-panel",
        "profile_required",
        "{% if profile_required and not profile_ok %}",
        "{% if profile_required %}required{% endif %}",
    ):
        assert marker in template

    for selector in (
        ".profile-page",
        ".profile-workspace",
        ".profile-form-card",
        ".profile-side-panel",
    ):
        assert selector in stylesheet


def test_admin_users_uses_console_rows_and_admin_create_flow() -> None:
    template = Path("templates/admin_users.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    for marker in (
        "admin-users-v2",
        "admin-user-console",
        "admin-user-console-head",
        "admin-user-console-row",
        "admin.admin_create_user",
        'aria-label="Opções de {{ user.username }}"',
    ):
        assert marker in template

    assert "admin-user-card" not in template
    assert "Diagnóstico WhatsApp" not in template

    for selector in (
        ".admin-user-console",
        ".admin-user-console-head",
        ".admin-user-console-row",
        ".admin-user-menu-body",
    ):
        assert selector in stylesheet
