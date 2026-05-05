from __future__ import annotations

from pathlib import Path


def test_admin_documents_page_uses_operational_index_console() -> None:
    template = Path("templates/admin_documents.html").read_text(encoding="utf-8")
    stylesheet = Path("static/css/pages.css").read_text(encoding="utf-8")

    for class_name in (
        "documents-admin-v2",
        "index-console-masthead",
        "index-console-gauge-row",
        "index-console-counter-grid",
        "index-console-health",
        "index-console-notes",
    ):
        assert class_name in template
        assert f".{class_name}" in stylesheet


def test_admin_documents_provider_label_is_operational_not_vendor_specific() -> None:
    template = Path("templates/admin_documents.html").read_text(encoding="utf-8")
    runtime = Path("core/knowledge_runtime.py").read_text(encoding="utf-8")

    assert "formatProviderLabel" in template
    assert "motor de embeddings indisponível" in runtime
    assert "{{ reindex_status.embedding_provider" not in template
