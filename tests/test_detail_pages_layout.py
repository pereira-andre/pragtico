from __future__ import annotations

from pathlib import Path


def test_port_call_detail_uses_polished_detail_layout() -> None:
    template = Path("templates/port_call_detail.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    for class_name in (
        "scale-detail-v2",
        "scale-hero-v2",
        "scale-main-panel",
        "scale-change-table-wrap",
        "scale-side-panel",
        "scale-side-card",
    ):
        assert class_name in template

    for selector in (
        ".scale-hero-v2",
        ".scale-main-panel",
        ".scale-change-table",
        ".scale-detail-page .mini-button",
        ".scale-fact-row strong",
    ):
        assert selector in stylesheet


def test_maneuver_detail_uses_polished_operational_layout() -> None:
    template = Path("templates/maneuver_detail.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    for class_name in (
        "maneuver-detail-v2",
        "maneuver-hero-v2",
        "maneuver-main-panel",
        "maneuver-summary-panel",
        "maneuver-checklist-panel",
        "maneuver-side-panel",
        "maneuver-side-card",
    ):
        assert class_name in template

    assert ".maneuver-hero-v2" in stylesheet
    assert ".maneuver-action-row" in stylesheet
    assert ".maneuver-detail-page .mini-button" in stylesheet
    assert "justify-items: start;" in stylesheet


def test_vessel_print_detail_uses_polished_profile_layout() -> None:
    template = Path("templates/vessel_print.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    assert "vessel-print-v2" in template
    assert "vessel-print-card" in template
    assert ".vessel-print-card" in stylesheet
    assert ".vessel-print-v2 .mini-button" in stylesheet
    assert ".vessel-print-v2 .print-actions" in stylesheet
