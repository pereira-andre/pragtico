from __future__ import annotations

import unittest

from domain.berth_layout import (
    build_slot_occupancy,
    canonicalize_berth_label,
    dropdown_berth_options,
    find_occupied_berth_conflict,
)


class BerthLayoutTests(unittest.TestCase):
    def test_legacy_labels_are_canonicalized(self) -> None:
        self.assertEqual(canonicalize_berth_label("sapec-solidos"), "SAPEC Sólidos")
        self.assertEqual(canonicalize_berth_label("Cais 7"), "TMS 1 - Cais 7")
        self.assertEqual(canonicalize_berth_label("TMS 2"), "TMS 2 - Posição A")

    def test_dropdown_uses_tms2_slots_not_base_label(self) -> None:
        options = dropdown_berth_options()

        self.assertNotIn("TMS 2", options)
        self.assertIn("TMS 2 - Posição A", options)
        self.assertIn("TMS 2 - Posição B", options)
        self.assertIn("TMS 2 - Posição C", options)

    def test_occupancy_groups_legacy_labels_under_canonical_names(self) -> None:
        occupancy = build_slot_occupancy(
            [
                {"id": "sapec", "vessel_name": "A", "berth_label": "sapec-solidos"},
                {"id": "cais7", "vessel_name": "B", "berth_label": "Cais 7"},
                {"id": "tms2", "vessel_name": "C", "berth_label": "TMS 2"},
            ]
        )
        berth_names = [item["berth"] for item in occupancy["berthed"]]

        self.assertIn("SAPEC Sólidos", berth_names)
        self.assertIn("TMS 1 - Cais 7", berth_names)
        self.assertIn("TMS 2 - Posição A", berth_names)
        self.assertNotIn("sapec-solidos", berth_names)
        self.assertNotIn("Cais 7", berth_names)
        self.assertNotIn("TMS 2", berth_names)

    def test_approved_departure_releases_berth_for_later_approval(self) -> None:
        occupant = {
            "id": "in-port",
            "vessel_name": "Loaded Vessel",
            "berth_label": "SAPEC Sólidos",
            "maneuver_history": [
                {
                    "type": "departure",
                    "state": "approved",
                    "planned_at": "2026-04-30T12:00:00+01:00",
                }
            ],
        }

        self.assertIsNone(
            find_occupied_berth_conflict(
                "sapec-solidos",
                [occupant],
                target_planned_at="2026-04-30T13:00:00+01:00",
            )
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "sapec-solidos",
                [occupant],
                target_planned_at="2026-04-30T11:00:00+01:00",
            ),
            occupant,
        )

    def test_approved_departure_does_not_release_berth_for_physical_completion(self) -> None:
        occupant = {
            "id": "in-port",
            "vessel_name": "Loaded Vessel",
            "berth_label": "SAPEC Sólidos",
            "maneuver_history": [
                {
                    "type": "departure",
                    "state": "approved",
                    "planned_at": "2026-04-30T12:00:00+01:00",
                }
            ],
        }

        self.assertEqual(
            find_occupied_berth_conflict(
                "SAPEC Sólidos",
                [occupant],
                target_planned_at="2026-04-30T13:00:00+01:00",
                release_states=("completed",),
            ),
            occupant,
        )

        occupant["maneuver_history"][0]["state"] = "completed"
        self.assertIsNone(
            find_occupied_berth_conflict(
                "SAPEC Sólidos",
                [occupant],
                target_planned_at="2026-04-30T13:00:00+01:00",
                release_states=("completed",),
            )
        )

    def test_pending_departure_keeps_berth_occupied(self) -> None:
        occupant = {
            "id": "in-port",
            "vessel_name": "Loaded Vessel",
            "berth_label": "SAPEC Sólidos",
            "maneuver_history": [
                {
                    "type": "departure",
                    "state": "pending",
                    "planned_at": "2026-04-30T12:00:00+01:00",
                }
            ],
        }

        self.assertEqual(
            find_occupied_berth_conflict(
                "SAPEC Sólidos",
                [occupant],
                target_planned_at="2026-04-30T13:00:00+01:00",
            ),
            occupant,
        )

    def test_approved_shift_releases_berth_for_later_approval(self) -> None:
        occupant = {
            "id": "in-port",
            "vessel_name": "Loaded Vessel",
            "berth_label": "SAPEC Sólidos",
            "maneuver_history": [
                {
                    "type": "shift",
                    "state": "approved",
                    "planned_at": "2026-04-30T12:00:00+01:00",
                    "origin": "SAPEC Sólidos",
                    "destination": "TMS 2 - Posição A",
                }
            ],
        }

        self.assertIsNone(
            find_occupied_berth_conflict(
                "SAPEC Sólidos",
                [occupant],
                target_planned_at="2026-04-30T13:00:00+01:00",
            )
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "SAPEC Sólidos",
                [occupant],
                target_planned_at="2026-04-30T11:00:00+01:00",
            ),
            occupant,
        )

    def test_pending_shift_keeps_berth_occupied(self) -> None:
        occupant = {
            "id": "in-port",
            "vessel_name": "Loaded Vessel",
            "berth_label": "SAPEC Sólidos",
            "maneuver_history": [
                {
                    "type": "shift",
                    "state": "pending",
                    "planned_at": "2026-04-30T12:00:00+01:00",
                    "origin": "SAPEC Sólidos",
                    "destination": "TMS 2 - Posição A",
                }
            ],
        }

        self.assertEqual(
            find_occupied_berth_conflict(
                "SAPEC Sólidos",
                [occupant],
                target_planned_at="2026-04-30T13:00:00+01:00",
            ),
            occupant,
        )


if __name__ == "__main__":
    unittest.main()
