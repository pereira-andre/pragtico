from __future__ import annotations

from domain.navigation_lights import build_navigation_lights_source, load_navigation_lights


def test_loads_setubal_navigation_lights_dataset() -> None:
    payload = load_navigation_lights("knowledge")

    assert payload["iala_region"] == "A"
    assert "IALA A" in payload["iala_note"]
    assert payload["entry_count"] == 102
    assert any(entry["name"] == "Boia N.º 1CN" for entry in payload["entries"])


def test_builds_exact_source_for_1cn_characteristic() -> None:
    source = build_navigation_lights_source("Qual e a caracteristica da Boia 1CN?", "knowledge")

    assert source is not None
    snippet = source["snippet"]
    assert "IALA A" in snippet
    assert "Boia N.º 1CN" in snippet
    assert "Fl G 3s" in snippet
    assert "38º30,33'N" in snippet
    assert "8º51,46'W" in snippet
    assert "alcance 3 M" in snippet


def test_builds_iala_note_without_specific_aid() -> None:
    source = build_navigation_lights_source("Que sistema IALA usamos em Setubal?", "knowledge")

    assert source is not None
    snippet = source["snippet"]
    assert "IALA A" in snippet
    assert "bombordo" in snippet
    assert "vermelhas" in snippet
    assert "estibordo" in snippet
    assert "verdes" in snippet
