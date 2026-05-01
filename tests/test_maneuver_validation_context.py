from __future__ import annotations

from core import maneuver_context
from core.maneuver_context import _build_maneuver_analysis_checklist, _format_operational_opinion_answer


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

    assert "Base documental acionada: IT-010_Tanquisado.txt" in answer
    assert "Recomendação operacional" in answer
    assert "Usar a base documental como critério principal" in answer
    assert "histórico não usado como regra principal" in answer
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
