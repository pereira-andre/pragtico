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


def test_tanquisado_entry_outside_required_reponto_blocks_validation(monkeypatch) -> None:
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
            "vessel_max_draft_m": "5.0",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "entry-tanquisado",
            "type": "entry",
            "state": "pending",
            "planned_at": "2026-04-30T18:00:00+00:00",
            "planned_input_value": "2026-04-30T18:00",
            "origin": "Sines",
            "destination": "Tanquisado (lado jusante)",
            "tug_count": "3",
            "constraint_codes": [],
        },
        [],
    )

    tide_check = next(item for item in assessment["checks"] if item["title"] == "Maré/tempo")
    assert assessment["decision"] == "NÃO AVANÇAR"
    assert tide_check["status"] == "block"
    assert "19:30-20:00" in tide_check["detail"]
    assert "A marcação não cai suficientemente em cima do reponto" in tide_check["detail"]


def test_tanquisado_departure_outside_reponto_allows_documented_ebb_exception(monkeypatch) -> None:
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
            "vessel_max_draft_m": "5.0",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "departure-tanquisado",
            "type": "departure",
            "state": "pending",
            "planned_at": "2026-05-05T08:00:00+01:00",
            "planned_input_value": "2026-05-05T08:00",
            "origin": "Tanquisado (lado jusante)",
            "destination": "Sines",
            "tug_count": "3",
            "constraint_codes": [],
        },
        [],
    )

    tide_check = next(item for item in assessment["checks"] if item["title"] == "Maré/tempo")
    assert tide_check["status"] == "ok"
    assert "exceção de saída em vazante" in tide_check["detail"]
    assert "2.9 m <= 3,0 m" in tide_check["detail"]


def test_shift_from_ecooil_to_lisnave_uses_one_hour_reponto_window(monkeypatch) -> None:
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
            "id": "pc-slops",
            "vessel_type": "Graneis liquidos",
            "vessel_loa_m": "120",
            "vessel_beam_m": "20",
            "vessel_gt_t": "8000",
            "vessel_max_draft_m": "5.0",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "shift-ecooil-lisnave",
            "type": "shift",
            "state": "pending",
            "planned_at": "2026-04-30T20:12:00+01:00",
            "planned_input_value": "2026-04-30T20:12",
            "origin": "Eco-Oil",
            "destination": "Lisnave - Cais 1 A",
            "tug_count": "3",
            "constraint_codes": [],
        },
        [],
    )

    tide_check = next(item for item in assessment["checks"] if item["title"] == "Maré/tempo")
    assert tide_check["status"] == "ok"
    assert "1h de Tanquisado/Eco-Oil para Lisnave" in tide_check["detail"]
    assert "A marcação acerta a janela de reponto" in tide_check["detail"]


def test_tms_high_draft_entry_uses_preia_mar_window(monkeypatch) -> None:
    monkeypatch.setattr(
        services,
        "tide_service",
        TideService("resources/tides/mares.2026.201.9_setubal_troia.csv"),
        raising=False,
    )
    monkeypatch.setattr(services, "weather_service", None, raising=False)
    monkeypatch.setattr(services, "wave_service", None, raising=False)
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", "knowledge", raising=False)

    assessment = _build_validation_operational_assessment(
        {
            "id": "pc-tms-high-draft",
            "vessel_type": "Contentores",
            "vessel_loa_m": "210",
            "vessel_beam_m": "32",
            "vessel_gt_t": "35000",
            "vessel_max_draft_m": "10.5",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "entry-tms-high-draft",
            "type": "entry",
            "state": "pending",
            "planned_at": "2026-05-08T18:33:00+01:00",
            "planned_input_value": "2026-05-08T18:33",
            "origin": "Sines",
            "destination": "TMS 1 - Cais 4",
            "draft": "10.5",
            "tug_count": "2",
            "constraint_codes": [],
        },
        [],
    )

    tide_check = next(item for item in assessment["checks"] if item["title"] == "Maré/tempo")
    assert tide_check["status"] == "ok"
    assert "1h a 1h30 antes da preia-mar para TMS 1/TMS 2" in tide_check["detail"]
    assert "preia-mar às 20:03" in tide_check["detail"]


def test_sapec_high_draft_departure_uses_thirty_minutes_before_reponto(monkeypatch) -> None:
    monkeypatch.setattr(
        services,
        "tide_service",
        TideService("resources/tides/mares.2026.201.9_setubal_troia.csv"),
        raising=False,
    )
    monkeypatch.setattr(services, "weather_service", None, raising=False)
    monkeypatch.setattr(services, "wave_service", None, raising=False)
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", "knowledge", raising=False)

    assessment = _build_validation_operational_assessment(
        {
            "id": "pc-sapec-high-draft",
            "vessel_type": "Graneis solidos",
            "vessel_loa_m": "180",
            "vessel_beam_m": "28",
            "vessel_gt_t": "26000",
            "vessel_max_draft_m": "9.6",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "departure-sapec-high-draft",
            "type": "departure",
            "state": "pending",
            "planned_at": "2026-05-08T19:33:00+01:00",
            "planned_input_value": "2026-05-08T19:33",
            "origin": "SAPEC Sólidos",
            "destination": "Sines",
            "draft": "9.6",
            "tug_count": "2",
            "constraint_codes": [],
        },
        [],
    )

    tide_check = next(item for item in assessment["checks"] if item["title"] == "Maré/tempo")
    assert tide_check["status"] == "ok"
    assert "30 min antes do reponto para saída SAPEC" in tide_check["detail"]
    assert "preia-mar às 20:03" in tide_check["detail"]


def test_teporset_departure_uses_fifteen_minutes_before_reponto(monkeypatch) -> None:
    monkeypatch.setattr(
        services,
        "tide_service",
        TideService("resources/tides/mares.2026.201.9_setubal_troia.csv"),
        raising=False,
    )
    monkeypatch.setattr(services, "weather_service", None, raising=False)
    monkeypatch.setattr(services, "wave_service", None, raising=False)
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", "knowledge", raising=False)

    assessment = _build_validation_operational_assessment(
        {
            "id": "pc-teporset",
            "vessel_type": "Carga geral",
            "vessel_loa_m": "130",
            "vessel_beam_m": "22",
            "vessel_gt_t": "9000",
            "vessel_max_draft_m": "7.5",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "departure-teporset",
            "type": "departure",
            "state": "pending",
            "planned_at": "2026-05-08T19:48:00+01:00",
            "planned_input_value": "2026-05-08T19:48",
            "origin": "Teporset",
            "destination": "Sines",
            "tug_count": "2",
            "constraint_codes": [],
        },
        [],
    )

    tide_check = next(item for item in assessment["checks"] if item["title"] == "Maré/tempo")
    assert tide_check["status"] == "ok"
    assert "15 min antes do reponto para saída Teporset/Termitrena" in tide_check["detail"]
    assert "preia-mar às 20:03" in tide_check["detail"]


def test_lisnave_dock_21_departure_uses_two_hours_before_high_tide(monkeypatch) -> None:
    monkeypatch.setattr(
        services,
        "tide_service",
        TideService("resources/tides/mares.2026.201.9_setubal_troia.csv"),
        raising=False,
    )
    monkeypatch.setattr(services, "weather_service", None, raising=False)
    monkeypatch.setattr(services, "wave_service", None, raising=False)
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", "knowledge", raising=False)

    assessment = _build_validation_operational_assessment(
        {
            "id": "pc-lisnave-dock",
            "vessel_type": "Tanque",
            "vessel_loa_m": "250",
            "vessel_beam_m": "36",
            "vessel_gt_t": "50000",
            "vessel_max_draft_m": "8.5",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "no",
        },
        {
            "id": "departure-lisnave-dock",
            "type": "departure",
            "state": "pending",
            "planned_at": "2026-05-08T18:03:00+01:00",
            "planned_input_value": "2026-05-08T18:03",
            "origin": "Lisnave - Doca 21",
            "destination": "Sines",
            "tug_count": "5",
            "constraint_codes": [],
        },
        [],
    )

    tide_check = next(item for item in assessment["checks"] if item["title"] == "Maré/tempo")
    assert tide_check["status"] == "ok"
    assert "2h00 antes da preia-mar para saída das Docas 21/22" in tide_check["detail"]
    assert "preia-mar às 20:03" in tide_check["detail"]


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
