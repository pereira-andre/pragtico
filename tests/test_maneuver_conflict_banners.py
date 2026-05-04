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
