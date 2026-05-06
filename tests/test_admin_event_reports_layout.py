from __future__ import annotations

from pathlib import Path


def test_admin_event_reports_uses_v2_operational_layout() -> None:
    template = Path("templates/admin_event_reports.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    for marker in (
        "event-reports-v2",
        "event-reports-hero",
        "event-report-catalog-panel",
        "event-report-filter-actions",
        "event-report-selection-actions",
        "event-report-window-table",
    ):
        assert marker in template

    for selector in (
        ".event-reports-v2",
        ".event-reports-v2 .event-report-selection-actions",
        ".event-reports-v2 .event-report-window-table",
        ".event-reports-v2 .document-row-menu .chat-sidebar-menu-toggle",
    ):
        assert selector in stylesheet


def test_admin_event_reports_bulk_buttons_are_symmetric() -> None:
    template = Path("templates/admin_event_reports.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    assert "event-report-selection-actions" in template
    assert "grid-template-columns: repeat(5, minmax(138px, 1fr));" in stylesheet
    assert ".event-reports-v2 .event-report-selection-actions .mini-button" in stylesheet
    assert ".event-reports-v2 .event-report-selection-actions .danger-button" in stylesheet
    assert "min-height: 44px;" in stylesheet
