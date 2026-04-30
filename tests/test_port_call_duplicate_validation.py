from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from core.operational_actions import build_tracked_scales
from storage.port_call_helpers import _build_port_activity_snapshot
from storage.postgres_port_calls import _find_active_duplicate_port_call


class PortCallDuplicateValidationTests(unittest.TestCase):
    def test_departed_record_with_stale_status_does_not_block_new_scale(self) -> None:
        stale_departed = {
            "id": "arklow-old",
            "vessel_name": "ARKLOW GLOBE",
            "vessel_imo": "9874105",
            "vessel_call_sign": "ABC123",
            "status": "in_port",
            "created_by": "agent",
            "berth": "Secil W",
            "last_port": "Setubal",
            "next_port": "Vigo",
            "eta": "2026-04-12T20:00:00+01:00",
            "maneuver_history": [
                {
                    "id": "entry-old",
                    "type": "entry",
                    "state": "completed",
                    "planned_at": "2026-04-12T18:00:00+01:00",
                    "completed_at": "2026-04-12T18:20:00+01:00",
                    "origin": "Sines",
                    "destination": "Secil W",
                    "created_by": "agent",
                },
                {
                    "id": "departure-old",
                    "type": "departure",
                    "state": "completed",
                    "planned_at": "2026-04-12T21:00:00+01:00",
                    "completed_at": "2026-04-12T21:20:00+01:00",
                    "origin": "Secil W",
                    "destination": "Vigo",
                    "created_by": "agent",
                },
            ],
        }

        self.assertIsNone(
            _find_active_duplicate_port_call(
                [stale_departed],
                clean_imo="9874105",
                clean_call_sign="ABC123",
            )
        )

    def test_scheduled_record_still_blocks_duplicate_imo(self) -> None:
        scheduled = {
            "id": "active",
            "vessel_name": "ARKLOW GLOBE",
            "vessel_imo": "9874105",
            "vessel_call_sign": "ABC123",
            "status": "scheduled",
            "created_by": "agent",
            "berth": "Secil W",
            "last_port": "Sines",
            "next_port": "Vigo",
            "eta": "2026-05-01T10:00:00+01:00",
            "maneuver_history": [
                {
                    "id": "entry-active",
                    "type": "entry",
                    "state": "pending",
                    "planned_at": "2026-05-01T10:00:00+01:00",
                    "origin": "Sines",
                    "destination": "Secil W",
                    "created_by": "agent",
                }
            ],
        }

        duplicate = _find_active_duplicate_port_call(
            [scheduled],
            clean_imo="9874105",
            clean_call_sign="",
        )

        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate[0], "imo")
        self.assertEqual(duplicate[1]["id"], "active")

    def test_aborted_scheduled_record_does_not_block_duplicate_imo(self) -> None:
        aborted = {
            "id": "aborted",
            "vessel_name": "ARKLOW GLOBE",
            "vessel_imo": "9874105",
            "vessel_call_sign": "ABC123",
            "status": "scheduled",
            "approval_status": "aborted",
            "created_by": "agent",
            "berth": "Secil W",
            "last_port": "Sines",
            "next_port": "Vigo",
            "eta": "2026-04-30T15:00:00+01:00",
            "maneuver_history": [
                {
                    "id": "entry-aborted",
                    "type": "entry",
                    "state": "aborted",
                    "planned_at": "2026-04-30T15:00:00+01:00",
                    "origin": "Sines",
                    "destination": "Secil W",
                    "created_by": "agent",
                    "decided_at": "2026-04-30T15:10:00+01:00",
                }
            ],
        }

        self.assertIsNone(
            _find_active_duplicate_port_call(
                [aborted],
                clean_imo="9874105",
                clean_call_sign="ABC123",
            )
        )

    def test_tracking_list_includes_pending_arrivals_without_planned_row(self) -> None:
        tracked = build_tracked_scales(
            {
                "in_port": [],
                "planned_maneuvers": [],
                "arrivals": [
                    {
                        "id": "arklow-hidden",
                        "reference_code": "PTSET26ARKL0001",
                        "vessel_name": "ARKLOW GLOBE",
                        "berth_label": "Secil W",
                        "eta_label": "12 Abril 2026 às 20:00",
                        "agent_label": "Administrador",
                    }
                ],
            }
        )

        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["id"], "arklow-hidden")
        self.assertIn("ARKLOW GLOBE", tracked[0]["vessel_name"])

    def test_overdue_scheduled_port_call_is_visible_in_activity_window(self) -> None:
        eta = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        activity = _build_port_activity_snapshot(
            [
                {
                    "id": "overdue",
                    "vessel_name": "ARKLOW GLOBE",
                    "vessel_imo": "9874105",
                    "vessel_call_sign": "ABC123",
                    "status": "scheduled",
                    "created_by": "agent",
                    "berth": "Secil W",
                    "last_port": "Lisboa",
                    "next_port": "Faro",
                    "eta": eta,
                    "maneuver_history": [
                        {
                            "id": "entry-overdue",
                            "type": "entry",
                            "state": "pending",
                            "planned_at": eta,
                            "origin": "Lisboa",
                            "destination": "Secil W",
                            "created_by": "agent",
                        }
                    ],
                }
            ],
            window_days=5,
        )

        self.assertEqual([item["id"] for item in activity["arrivals"]], ["overdue"])

    def test_scheduled_port_call_without_parseable_eta_is_visible_in_activity_window(self) -> None:
        activity = _build_port_activity_snapshot(
            [
                {
                    "id": "missing-eta",
                    "vessel_name": "ARKLOW GLOBE",
                    "vessel_imo": "9874105",
                    "vessel_call_sign": "ABC123",
                    "status": "scheduled",
                    "approval_status": "pending",
                    "created_by": "agent",
                    "berth": "Secil W",
                    "last_port": "Lisboa",
                    "next_port": "Faro",
                    "eta": "",
                    "maneuver_history": [],
                }
            ],
            window_days=5,
        )

        self.assertEqual([item["id"] for item in activity["arrivals"]], ["missing-eta"])


if __name__ == "__main__":
    unittest.main()
