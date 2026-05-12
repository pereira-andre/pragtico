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


def test_secil_diagnostic_includes_reponto_rules_and_side() -> None:
    diagnostic = build_operational_diagnostic(
        "Entrada para a SECIL W marcada para as 13:30, tenho de ir ao reponto?"
    )
    rendered = format_operational_diagnostic(diagnostic)

    assert "Local: SECIL" in rendered
    assert "Doca/cais: SECIL W/Oeste" in rendered
    assert "todos os navios atracam proximo do reponto" in rendered
    assert "entradas 30-45 min antes do reponto" in rendered


def test_generic_secil_diagnostic_asks_for_west_or_east() -> None:
    diagnostic = build_operational_diagnostic("Entrada para a SECIL tem de ser ao reponto?")
    rendered = format_operational_diagnostic(diagnostic)

    assert "SECIL W/Oeste" in rendered
    assert "SECIL E/Este" in rendered
    assert "Confirmar se e SECIL W/Oeste ou SECIL E/Este" in rendered


def test_explicit_secil_diagnostic_does_not_reuse_old_lisnave_context() -> None:
    diagnostic = build_operational_diagnostic(
        "Marquei manobra de entrada para a Secil E as 1925. Está correto?",
        history=[
            {"role": "user", "content": "Um navio na LISNAVE de 300 m manobra com quantos rebocadores?"},
            {"role": "assistant", "content": "Recomendo 6 rebocadores."},
            {"role": "user", "content": "Com nevoeiro em porto posso sair?"},
        ],
    )
    rendered = format_operational_diagnostic(diagnostic)

    assert "Local: SECIL" in rendered
    assert "Doca/cais: SECIL E/Este" in rendered
    assert "LISNAVE" not in rendered
    assert "6 rebocador" not in rendered
    assert "nevoeiro" not in rendered.lower()


def test_alstom_diagnostic_includes_mandatory_rules_and_wind_block() -> None:
    diagnostic = build_operational_diagnostic(
        "Entrada para a Alstom desde a Barra com vento 15 kts pode avançar?"
    )
    rendered = format_operational_diagnostic(diagnostic)

    assert "Local: ALSTOM" in rendered
    assert "atracam apenas por estibordo" in rendered
    assert "reponto de preia-mar" in rendered
    assert "1h30" in rendered
    assert "inferior a 15 kt" in rendered
    assert "atinge/excede o limite pratico" in rendered
