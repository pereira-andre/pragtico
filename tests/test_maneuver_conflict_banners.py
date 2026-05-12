from __future__ import annotations

import pytest

from core import services
from core.form_helpers import ensure_portal_berth_is_available, ensure_portal_berth_is_physically_available


class ConflictStore:
    def get_port_activity_snapshot(self, window_days: int = 3650) -> dict:
        return {
            "in_port": [
                {
                    "id": "occupied-scale",
                    "reference_code": "PTSET26OCCUPIED",
                    "vessel_name": "OCCUPIED TEST",
                    "berth_label": "SAPEC Sólidos",
                    "maneuver_history": [],
                }
            ]
        }


class MultiSlotConflictStore:
    def get_port_activity_snapshot(self, window_days: int = 3650) -> dict:
        return {
            "in_port": [
                {
                    "id": "short-c6",
                    "reference_code": "PTSET26SHORTC6",
                    "vessel_name": "SHORT C6",
                    "berth_label": "TMS 1 - Cais 6",
                    "vessel_loa_m": "120",
                    "maneuver_history": [],
                }
            ]
        }


class EmptyConflictStore:
    def get_port_activity_snapshot(self, window_days: int = 3650) -> dict:
        return {"in_port": []}


def test_planning_conflict_message_is_banner_ready(monkeypatch) -> None:
    monkeypatch.setattr(services, "store", ConflictStore())

    with pytest.raises(ValueError) as exc:
        ensure_portal_berth_is_available(
            "SAPEC Sólidos",
            current_port_call_id="new-scale",
            label="Cais destino",
        )

    message = str(exc.value)
    assert message == "Cais destino SAPEC Sólidos já está ocupado por OCCUPIED TEST."


def test_physical_completion_conflict_message_tells_user_next_step(monkeypatch) -> None:
    monkeypatch.setattr(services, "store", ConflictStore())

    with pytest.raises(ValueError) as exc:
        ensure_portal_berth_is_physically_available(
            "SAPEC Sólidos",
            current_port_call_id="new-scale",
            label="Cais",
        )

    message = str(exc.value)
    assert "Cais SAPEC Sólidos ainda está ocupado por OCCUPIED TEST." in message
    assert "Conclui primeiro a saída ou mudança desse navio." in message


def test_physical_completion_uses_target_loa_for_multi_slot_conflict(monkeypatch) -> None:
    monkeypatch.setattr(services, "store", MultiSlotConflictStore())

    assert (
        ensure_portal_berth_is_physically_available(
            "TMS 1 - Cais 5",
            current_port_call_id="new-scale",
            label="Cais",
            target_vessel_loa_m="120",
        )
        == "TMS 1 - Cais 5"
    )

    with pytest.raises(ValueError) as exc:
        ensure_portal_berth_is_physically_available(
            "TMS 1 - Cais 5",
            current_port_call_id="new-scale",
            label="Cais",
            target_vessel_loa_m="230",
        )

    assert "Cais TMS 1 - Cais 5 ainda está ocupado por SHORT C6." in str(exc.value)


def test_capacity_conflict_message_is_not_reported_as_occupied(monkeypatch) -> None:
    monkeypatch.setattr(services, "store", EmptyConflictStore())

    with pytest.raises(ValueError) as exc:
        ensure_portal_berth_is_available(
            "TMS 1 - Cais 8",
            current_port_call_id="new-scale",
            label="Cais destino",
            target_vessel_loa_m="240",
        )

    message = str(exc.value)
    assert "não tem capacidade para o LOA indicado" in message
    assert "já está ocupado" not in message
