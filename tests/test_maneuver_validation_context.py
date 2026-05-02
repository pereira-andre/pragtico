from __future__ import annotations

from core import maneuver_context, services
from core.maneuver_context import (
    _build_maneuver_analysis_checklist,
    _build_validation_operational_assessment,
    _format_operational_opinion_answer,
)
from integrations.tide_service import TideService


def test_validation_answer_prioritizes_documental_alerts_without_history() -> None:
    answer = _format_operational_opinion_answer(
        port_call={"vessel_name": "GALBOT"},
        maneuver={"title": "Entrada"},
        recommendation={},
        similar_cases=[],
        checklist=[
            {
                "status": "ok",
                "title": "Destino operacional",
                "detail": "Destino normalizado para Tanquisado.",
            },
            {
                "status": "caution",
                "title": "Regras do cais",
                "detail": (
                    "Terminal da TANQUISADO (IT-010_Tanquisado.txt): calado diurno praticável; "
                    "regra geral de atracação em repontos de maré e período diurno."
                ),
            },
        ],
    )

    assert "Parecer operacional" in answer
    assert "Decisão: NÃO AVANÇAR" in answer
    assert "Pontos críticos verificados" in answer
    assert "Base documental acionada: IT-010_Tanquisado.txt" in answer
    assert "Recomendação operacional" in answer
    assert "histórico não usado como regra principal" in answer
    assert "Sem hora planeada" in answer
    assert "Sem recomendação automática disponível" not in answer
    assert "não foi invocada regra específica" not in answer


def test_pending_past_planned_window_is_a_validation_alert(monkeypatch) -> None:
    monkeypatch.setattr(maneuver_context, "current_resolvable_port_calls", lambda: [])

    checklist, summary = _build_maneuver_analysis_checklist(
        {
            "id": "pc1",
            "vessel_type": "Petroleiro",
            "vessel_loa_m": "110",
            "vessel_beam_m": "18",
            "vessel_gt_t": "7000",
            "vessel_max_draft_m": "6.8",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "m1",
            "type": "entry",
            "state": "pending",
            "planned_at": "2000-01-01T10:00:00+00:00",
            "origin": "Sines",
            "destination": "Tanquisado (lado jusante)",
            "tug_count": "1",
            "constraint_codes": [],
        },
        similar_cases=[],
        casebook_recommendation={},
    )

    planned_window = next(item for item in checklist if item["title"] == "Janela planeada")
    assert planned_window["status"] == "caution"
    assert "já passou" in planned_window["detail"]
    assert summary["caution_count"] >= 1


def test_shift_checklist_uses_origin_and_destination_profiles(monkeypatch) -> None:
    monkeypatch.setattr(maneuver_context, "current_resolvable_port_calls", lambda: [])
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", "knowledge", raising=False)

    checklist, _summary = _build_maneuver_analysis_checklist(
        {
            "id": "pc-shift",
            "vessel_type": "Abastecedor",
            "vessel_loa_m": "95",
            "vessel_beam_m": "18",
            "vessel_gt_t": "5000",
            "vessel_max_draft_m": "5.0",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "shift-1",
            "type": "shift",
            "state": "pending",
            "planned_at": "2099-01-01T10:00:00+00:00",
            "origin": "Lisnave - Cais 1 A",
            "destination": "TMS 1",
            "tug_count": "1",
            "constraint_codes": [],
        },
        similar_cases=[],
        casebook_recommendation={},
    )

    rendered = "\n".join(f"{item['title']}: {item['detail']}" for item in checklist)
    assert "Regras do cais de origem" in rendered
    assert "IT-014_Lisnave.txt" in rendered
    assert "Regras do cais de destino" in rendered
    assert "IT-005_TMS1.txt" in rendered


def test_tanquisado_entry_two_hours_before_reponto_is_valid_tide_window(monkeypatch) -> None:
    monkeypatch.setattr(
        services,
        "tide_service",
        TideService("resources/tides/mares.2026.201.9_setubal_troia.csv"),
        raising=False,
    )
    monkeypatch.setattr(services, "weather_service", None, raising=False)
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", "knowledge", raising=False)

    assessment = _build_validation_operational_assessment(
        {
            "id": "pc-tanquisado",
            "vessel_type": "Graneis liquidos",
            "vessel_loa_m": "101.4",
            "vessel_beam_m": "16.6",
            "vessel_gt_t": "4500",
            "vessel_max_draft_m": "7.5",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "entry-tanquisado",
            "type": "entry",
            "state": "pending",
            "planned_at": "2026-04-30T19:12:00+00:00",
            "planned_input_value": "2026-04-30T19:12",
            "origin": "Sines",
            "destination": "Tanquisado (lado jusante)",
            "tug_count": "3",
            "constraint_codes": [],
        },
        [],
    )

    tide_check = next(item for item in assessment["checks"] if item["title"] == "Maré/tempo")
    assert tide_check["status"] == "ok"
    assert "A marcação acerta a janela de reponto" in tide_check["detail"]
    assert "não cai suficientemente" not in tide_check["detail"]


def test_tanquisado_entry_with_two_tugs_blocks_on_minimum(monkeypatch) -> None:
    monkeypatch.setattr(
        services,
        "tide_service",
        TideService("resources/tides/mares.2026.201.9_setubal_troia.csv"),
        raising=False,
    )
    monkeypatch.setattr(services, "weather_service", None, raising=False)
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", "knowledge", raising=False)

    assessment = _build_validation_operational_assessment(
        {
            "id": "pc-tanquisado",
            "vessel_type": "Graneis liquidos",
            "vessel_loa_m": "101.4",
            "vessel_beam_m": "16.6",
            "vessel_gt_t": "4500",
            "vessel_max_draft_m": "7.5",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "entry-tanquisado",
            "type": "entry",
            "state": "pending",
            "planned_at": "2026-05-02T14:15:00+01:00",
            "planned_input_value": "2026-05-02T14:15",
            "origin": "Sines",
            "destination": "Tanquisado (lado jusante)",
            "tug_count": "2",
            "constraint_codes": [],
        },
        [],
    )

    tug_check = next(item for item in assessment["checks"] if item["title"] == "Rebocadores")
    assert assessment["decision"] == "NÃO AVANÇAR"
    assert tug_check["status"] == "block"
    assert "mínimo prático para Tanquisado" in tug_check["detail"]


def test_validation_answer_does_not_call_applied_berth_rules_alerts(monkeypatch) -> None:
    monkeypatch.setattr(maneuver_context, "current_resolvable_port_calls", lambda: [])
    monkeypatch.setattr(
        services,
        "tide_service",
        TideService("resources/tides/mares.2026.201.9_setubal_troia.csv"),
        raising=False,
    )
    monkeypatch.setattr(services, "weather_service", None, raising=False)
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", "knowledge", raising=False)
    port_call = {
        "id": "pc-tanquisado",
        "vessel_name": "GALBOT",
        "vessel_type": "Graneis liquidos",
        "vessel_loa_m": "101.4",
        "vessel_beam_m": "16.6",
        "vessel_gt_t": "4500",
        "vessel_max_draft_m": "7.5",
        "vessel_bow_thruster": "yes",
        "vessel_stern_thruster": "no",
    }
    maneuver = {
        "id": "entry-tanquisado",
        "title": "Entrada",
        "type": "entry",
        "status": "Pendente",
        "state": "pending",
        "planned_at": "2026-04-30T19:12:00+00:00",
        "planned_input_value": "2026-04-30T19:12",
        "origin": "Sines",
        "destination": "Tanquisado (lado jusante)",
        "tug_count": "3",
        "constraint_codes": [],
    }
    checklist, _summary = _build_maneuver_analysis_checklist(
        port_call,
        maneuver,
        similar_cases=[],
        casebook_recommendation={},
    )

    answer = _format_operational_opinion_answer(
        port_call=port_call,
        maneuver=maneuver,
        recommendation={},
        similar_cases=[],
        checklist=checklist,
    )

    assert "checklist original coerente" in answer
    assert "Decisão: CHECKLIST ORIGINAL OK (JANELA PASSADA)" in answer
    assert "voltar a validar maré, meteorologia e dados live" in answer
    assert "validação condicionada por 2 alerta" not in answer
    assert "Alertas operacionais pendentes" in answer
    assert "Janela planeada:" in answer
    assert "Regras documentais aplicadas" in answer
    assert "Regras do cais:" in answer
