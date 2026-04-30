from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
