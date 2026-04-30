from __future__ import annotations

import unittest

from flask import Flask, session

from core import services
from core.form_helpers import ensure_maneuver_hour_capacity_for_approval
from core.maneuver_context import build_scale_context
from storage.port_call_helpers import (
    _decorate_port_call,
    _normalize_port_call_record,
    can_plan_followup_maneuver_status,
)


class FakeStore:
    def __init__(self, activity: dict | None = None) -> None:
        self.activity = activity or {
            "arrivals": [],
            "in_port": [],
            "departed": [],
            "aborted": [],
            "departure_candidates": [],
            "planned_maneuvers": [],
            "archived_maneuvers": [],
        }

    def get_user_profile(self, username: str) -> dict:
        return {}

    def get_port_activity_snapshot(self, window_days: int = 5) -> dict:
        return self.activity


def _scheduled_port_call() -> dict:
    raw = {
        "id": "pc-scheduled",
        "vessel_name": "PLANNED VESSEL",
        "created_by": "agent",
        "berth": "TMS 2",
        "last_port": "Lisboa",
        "next_port": "Cadiz",
        "vessel_type": "cargo",
        "vessel_loa_m": "120",
        "vessel_beam_m": "20",
        "vessel_gt_t": "8000",
        "vessel_max_draft_m": "7.5",
        "vessel_bow_thruster": "yes",
        "vessel_stern_thruster": "no",
        "maneuver_history": [
            {
                "id": "entry-1",
                "type": "entry",
                "state": "pending",
                "planned_at": "2026-05-01T10:00:00+01:00",
                "origin": "Lisboa",
                "destination": "TMS 2",
                "created_by": "agent",
            }
        ],
    }
    return _decorate_port_call(_normalize_port_call_record(raw))


class ManeuverFollowupPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = services.store
        services.store = FakeStore()
        self.app = Flask(__name__)
        self.app.secret_key = "test"

    def tearDown(self) -> None:
        services.store = self.previous_store

    def test_followup_planning_status_allows_scheduled_and_in_port(self) -> None:
        self.assertTrue(can_plan_followup_maneuver_status("scheduled"))
        self.assertTrue(can_plan_followup_maneuver_status("in_port"))
        self.assertFalse(can_plan_followup_maneuver_status("departed"))
        self.assertFalse(can_plan_followup_maneuver_status(None))

    def test_scheduled_scale_can_plan_departure_and_shift_before_entry_completion(self) -> None:
        with self.app.test_request_context("/"):
            session["role"] = "admin"
            scale_context = build_scale_context(_scheduled_port_call())

        self.assertTrue(scale_context["actions"]["can_plan_departure"])
        self.assertTrue(scale_context["actions"]["can_plan_shift"])
        self.assertFalse(scale_context["actions"]["can_approve_departure"])
        self.assertFalse(scale_context["actions"]["can_approve_shift"])

    def test_approval_capacity_blocks_fifth_maneuver_in_same_hour(self) -> None:
        services.store.activity = {
            "planned_maneuvers": [
                {
                    "maneuver_id": f"approved-{index}",
                    "situation_class": "approved",
                    "planned_value": f"2026-05-01T10:{index:02d}:00+01:00",
                }
                for index in range(4)
            ]
        }
        target = {
            "id": "target",
            "maneuver_history": [
                {
                    "id": "target-entry",
                    "type": "entry",
                    "state": "pending",
                    "planned_at": "2026-05-01T10:45:00+01:00",
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "Já existem 4 manobras aprovadas"):
            ensure_maneuver_hour_capacity_for_approval(target, "entry")

    def test_approval_capacity_allows_fourth_maneuver_in_same_hour(self) -> None:
        services.store.activity = {
            "planned_maneuvers": [
                {
                    "maneuver_id": f"approved-{index}",
                    "situation_class": "approved",
                    "planned_value": f"2026-05-01T10:{index:02d}:00+01:00",
                }
                for index in range(3)
            ]
        }
        target = {
            "id": "target",
            "maneuver_history": [
                {
                    "id": "target-entry",
                    "type": "entry",
                    "state": "pending",
                    "planned_at": "2026-05-01T10:45:00+01:00",
                }
            ],
        }

        self.assertEqual(
            ensure_maneuver_hour_capacity_for_approval(target, "entry")["id"],
            "target-entry",
        )


if __name__ == "__main__":
    unittest.main()
