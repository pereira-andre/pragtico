from __future__ import annotations

from pathlib import Path


def test_admin_backups_uses_compact_action_buttons() -> None:
    template = Path("templates/admin_backups.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    for class_name in (
        "admin-backups-v2",
        "admin-backup-action-panel",
        "admin-backup-primary-button",
        "admin-backup-table-wrap",
        "admin-backup-form-button",
        "admin-backup-wipe-button",
    ):
        assert class_name in template

    assert ".admin-backup-primary-button" in stylesheet
    assert ".admin-backup-form-button" in stylesheet
    assert ".admin-backup-actions .danger-button" in stylesheet
    assert "min-width: 236px;" in stylesheet


def test_admin_audit_console_has_non_overlapping_columns() -> None:
    template = Path("templates/admin_audit.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    for class_name in (
        "admin-audit-v2",
        "admin-audit-filter-panel",
        "admin-audit-log-panel",
        "admin-audit-log-resource",
        "admin-audit-log-detail",
    ):
        assert class_name in template

    assert "grid-template-columns: 132px 112px 92px minmax(150px, 210px) minmax(420px, 1fr);" in stylesheet
    assert "min-width: 1020px;" in stylesheet
    assert ".admin-audit-log-detail" in stylesheet
    assert "overflow-x: auto;" in stylesheet
