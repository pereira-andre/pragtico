from __future__ import annotations

from domain.chat_response_formatting import add_contextual_response_emojis


def test_deterministic_operational_answers_receive_restrained_theme_emoji() -> None:
    payload = {
        "answer_origin": "operational_route_transit",
        "answer": "Do Fundeadouro Norte para a LISNAVE conta 1h30.",
        "sources": [],
    }

    decorated = add_contextual_response_emojis(payload, "Quanto demora do Fundeadouro Norte para a Lisnave?")

    assert decorated["answer"].startswith("🧭 ")


def test_existing_emoji_is_not_duplicated() -> None:
    payload = {
        "answer_origin": "operational_safety_limit",
        "answer": "🌬️ Não. Com 31 kt as manobras ficam suspensas.",
        "sources": [],
    }

    decorated = add_contextual_response_emojis(payload, "Posso manobrar com 31 kt?")

    assert decorated["answer"] == payload["answer"]


def test_moon_and_daylight_slash_origins_use_specific_emoji() -> None:
    moon = add_contextual_response_emojis(
        {"answer_origin": "slash_moon", "answer": "Fase da lua em Setúbal: Lua cheia.", "sources": []},
        "/lua hoje",
    )
    daylight = add_contextual_response_emojis(
        {"answer_origin": "slash_daylight", "answer": "Período luminoso em Setúbal: 06:40-20:23.", "sources": []},
        "/luz hoje",
    )

    assert moon["answer"].startswith("🌙 ")
    assert daylight["answer"].startswith("☀️ ")


def test_rule_and_companion_deterministic_origins_receive_list_emoji() -> None:
    for origin in ("slash_rule", "berth_profile", "document_companion", "document_companion_global"):
        payload = {"answer_origin": origin, "answer": "Resumo operacional da regra.", "sources": []}

        decorated = add_contextual_response_emojis(payload, "/it 029")

        assert decorated["answer"].startswith("📋 ")


def test_local_culture_origin_uses_place_marker() -> None:
    payload = {"answer_origin": "local_culture", "answer": "O Outão é estratégico na barra do Sado.", "sources": []}

    decorated = add_contextual_response_emojis(payload, "Curiosidade sobre o Outão")

    assert decorated["answer"].startswith("📍 ")
