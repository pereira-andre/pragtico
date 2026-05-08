from __future__ import annotations

from core.operational_diagnostics import build_operational_diagnostic, format_operational_diagnostic
from core.operational_sources import answer_direct_operational_query


def test_lisnave_300_diagnostic_uses_six_tugs_before_generic_minimum() -> None:
    payload = answer_direct_operational_query(
        "Um navio na LISNAVE de 300 m manobra com quantos rebocadores normalmente?"
    )

    assert payload is not None
    diagnostic = payload.get("operational_diagnostic") or {}
    rendered = format_operational_diagnostic(diagnostic)

    assert "Minimo critico identificado: 6 rebocador" in rendered
    assert "Lisnave acima de 250 m: 6 rebocadores" in rendered
    assert "Recomendo 3 rebocadores" not in payload["answer"]


def test_hidrolift_diagnostic_blocks_over_beam_limit() -> None:
    diagnostic = build_operational_diagnostic(
        "Tenho um navio para entrar no hidrolift no preia-mar das 20:03. "
        "O navio tem 45 m de boca, pode manobrar?"
    )
    rendered = format_operational_diagnostic(diagnostic)

    assert "Bloqueio dimensional" in rendered
    assert "boca maxima 32 m" in rendered
    assert "Boca: 45 m" in rendered


def test_diagnostic_uses_recent_user_context_for_follow_up() -> None:
    diagnostic = build_operational_diagnostic(
        "Mas o navio tem 300 m, quantos rebocadores leva?",
        history=[
            {
                "role": "user",
                "content": "Um navio vai mudar do fundeadouro de Troia para a LISNAVE.",
            }
        ],
    )
    rendered = format_operational_diagnostic(diagnostic)

    assert "Lisnave acima de 250 m: 6 rebocadores" in rendered
