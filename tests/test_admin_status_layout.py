from __future__ import annotations

from pathlib import Path


def test_admin_status_page_uses_clean_vigilance_layout() -> None:
    template = Path("templates/admin_status.html").read_text(encoding="utf-8")

    assert "admin-status-v2" in template
    assert "admin-status-page-nav" in template
    assert "admin-status-two-column" in template
    assert "admin-status-service-summary" in template
    assert "admin.checked_at_label" in template
    assert "admin.service_counts.online" in template
    assert "Vigilância técnica" in template


def test_admin_status_page_keeps_core_sections_visible() -> None:
    template = Path("templates/admin_status.html").read_text(encoding="utf-8")

    for anchor in ("#services", "#operation", "#database", "#index", "#ais"):
        assert f'href="{anchor}"' in template

    assert "Serviços críticos" in template
    assert "Operação portuária" in template
    assert "Base aplicacional" in template
    assert "Índice documental" in template
    assert "Mapa e referência geográfica" in template
