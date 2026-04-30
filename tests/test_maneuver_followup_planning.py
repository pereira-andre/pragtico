from __future__ import annotations

import unittest

from flask import Flask, session

from core import services
from core.maneuver_context import build_scale_context
from storage.port_call_helpers import (
    _decorate_port_call,
    _normalize_port_call_record,
    can_plan_followup_maneuver_status,
)


class FakeStore:
    def get_user_profile(self, username: str) -> dict:
        return {}

    def get_port_activity_snapshot(self, window_days: int = 5) -> dict:
        return {
            "arrivals": [],
            "in_port": [],
            "departed": [],
            "aborted": [],
            "departure_candidates": [],
            "planned_maneuvers": [],
            "archived_maneuvers": [],
        }


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


if __name__ == "__main__":
    unittest.main()
