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
