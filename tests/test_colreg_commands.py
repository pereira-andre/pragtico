from __future__ import annotations

from core.operational_actions import answer_slash_query
from domain.chat_actions import parse_slash_command
from domain.chat_action_templates import build_slash_help
from domain.colreg_rules import format_colreg_catalog, format_colreg_rule


def test_colreg_catalog_lists_rules_by_number_and_title() -> None:
    answer = format_colreg_catalog()

    assert "RIEAM/COLREG - regras por número e título:" in answer
    assert "01. Campo de aplicação" in answer
    assert "19. Conduta em visibilidade reduzida" in answer
    assert "35. Sinais sonoros em visibilidade reduzida" in answer
    assert "38. Isenções" in answer


def test_colreg_rule_23_shows_lights_with_visual_markers() -> None:
    answer = format_colreg_rule(23)

    assert "Regra 23 - Navios de propulsão mecânica a navegar" in answer
    assert "⚪ farol de mastro a vante" in answer
    assert "🔴 BB + 🟢 EB" in answer
    assert "⚪ luz de popa" in answer
    assert "Legenda visual" in answer


def test_colreg_rule_35_shows_fog_sound_signals() -> None:
    answer = format_colreg_rule(35)

    assert "Regra 35 - Sinais sonoros em visibilidade reduzida" in answer
    assert "📣 em marcha com seguimento: 1 prolongado" in answer
    assert "📣 pairando/sem seguimento: 2 prolongados" in answer
    assert "🔔 fundeado/encalhado" in answer


def test_colreg_slash_commands_parse_and_answer() -> None:
    catalog = parse_slash_command("/colreg-lista", "piloto")
    rule = parse_slash_command("/colreg 24", "piloto")

    assert catalog == {"intent": "query", "command": "colreg_list", "argument": ""}
    assert rule == {"intent": "query", "command": "colreg_rule", "argument": "24"}

    catalog_payload = answer_slash_query("colreg_list", "", "piloto")
    rule_payload = answer_slash_query("colreg_rule", "regra 24", "piloto")

    assert catalog_payload["answer_origin"] == "slash_colreg"
    assert "24. Reboque e empurrar" in catalog_payload["answer"]
    assert rule_payload["answer_origin"] == "slash_colreg"
    assert "⚪⚪ faróis de mastro verticais" in rule_payload["answer"]
    assert "◆ marca biconica" in rule_payload["answer"]


def test_help_mentions_colreg_commands() -> None:
    help_text = build_slash_help("piloto")

    assert "/colreg-lista" in help_text
    assert "/colreg 19" in help_text
    assert "/regra-colreg" in help_text
