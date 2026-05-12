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

    def test_autoeuropa_allows_two_small_vessels_but_large_vessel_is_exclusive(self) -> None:
        small_occupant = {
            "id": "auto-10",
            "vessel_name": "Small RoRo",
            "berth_label": "Cais 10 / Autoeuropa",
            "vessel_loa_m": "180",
        }
        large_occupant = {
            "id": "auto-large",
            "vessel_name": "Large RoRo",
            "berth_label": "Cais 10 / Autoeuropa",
            "vessel_loa_m": "230",
        }

        self.assertIsNone(
            find_occupied_berth_conflict(
                "Cais 11 / Autoeuropa",
                [small_occupant],
                target_vessel_loa_m="180",
            )
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "Cais 11 / Autoeuropa",
                [small_occupant],
                target_vessel_loa_m="230",
            ),
            small_occupant,
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "Cais 11 / Autoeuropa",
                [large_occupant],
                target_vessel_loa_m="180",
            ),
            large_occupant,
        )

    def test_autoeuropa_unknown_loa_keeps_terminal_conservative(self) -> None:
        occupant = {
            "id": "auto-unknown",
            "vessel_name": "Unknown RoRo",
            "berth_label": "Cais 10 / Autoeuropa",
        }

        self.assertEqual(
            find_occupied_berth_conflict(
                "Cais 11 / Autoeuropa",
                [occupant],
                target_vessel_loa_m="180",
            ),
            occupant,
        )

    def test_autoeuropa_shared_vessels_need_30m_clearance(self) -> None:
        occupant = {
            "id": "auto-10",
            "vessel_name": "Small RoRo",
            "berth_label": "Cais 10 / Autoeuropa",
            "vessel_loa_m": "220",
        }

        self.assertEqual(
            find_occupied_berth_conflict(
                "Cais 11 / Autoeuropa",
                [occupant],
                target_vessel_loa_m="220",
            ),
            occupant,
        )
        self.assertIsNone(
            find_occupied_berth_conflict(
                "Cais 11 / Autoeuropa",
                [{**occupant, "vessel_loa_m": "210"}],
                target_vessel_loa_m="210",
            )
        )

    def test_tms1_large_vessel_occupies_more_than_one_cais(self) -> None:
        occupant = {
            "id": "tms1-c4",
            "vessel_name": "Long TMS1",
            "berth_label": "TMS 1 - Cais 4",
            "vessel_loa_m": "230",
        }

        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 5",
                [occupant],
                target_vessel_loa_m="100",
            ),
            occupant,
        )
        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 6",
                [occupant],
                target_vessel_loa_m="100",
            )
        )

    def test_tms1_target_loa_uses_left_when_right_adjacent_is_occupied(self) -> None:
        occupant = {
            "id": "tms1-c6",
            "vessel_name": "Short TMS1",
            "berth_label": "TMS 1 - Cais 6",
            "vessel_loa_m": "120",
        }

        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 5",
                [occupant],
                target_vessel_loa_m="230",
            )
        )
        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 5",
                [occupant],
                target_vessel_loa_m="120",
            )
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 5",
                [
                    occupant,
                    {
                        "id": "tms1-c4",
                        "vessel_name": "Left TMS1",
                        "berth_label": "TMS 1 - Cais 4",
                        "vessel_loa_m": "120",
                    },
                ],
                target_vessel_loa_m="230",
            ),
            occupant,
        )

    def test_tms1_cais7_uses_cais6_not_diagonal_cais8_when_needed(self) -> None:
        occupant = {
            "id": "tms1-c6",
            "vessel_name": "Cais 6",
            "berth_label": "TMS 1 - Cais 6",
            "vessel_loa_m": "80",
        }

        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 7",
                [],
                target_vessel_loa_m="100",
            )
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 7",
                [occupant],
                target_vessel_loa_m="100",
            ),
            occupant,
        )
        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 8",
                [{"id": "tms1-c7", "vessel_name": "Cais 7 100", "berth_label": "TMS 1 - Cais 7", "vessel_loa_m": "100"}],
                target_vessel_loa_m="80",
            )
        )

    def test_tms1_cais7_rejects_when_left_side_cannot_fit(self) -> None:
        conflict = find_occupied_berth_conflict(
            "TMS 1 - Cais 7",
            [],
            target_vessel_loa_m="790",
        )

        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["reference_code"], "capacidade")

    def test_tms1_prefers_right_but_uses_left_when_right_is_occupied(self) -> None:
        right_occupant = {
            "id": "tms1-c5",
            "vessel_name": "Right Occupant",
            "berth_label": "TMS 1 - Cais 5",
            "vessel_loa_m": "100",
        }

        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 4",
                [right_occupant],
                target_vessel_loa_m="230",
            )
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 4",
                [
                    right_occupant,
                    {
                        "id": "tms1-c3",
                        "vessel_name": "Left Occupant",
                        "berth_label": "TMS 1 - Cais 3",
                        "vessel_loa_m": "100",
                    },
                ],
                target_vessel_loa_m="230",
            ),
            right_occupant,
        )

    def test_tms1_shared_vessels_need_30m_clearance(self) -> None:
        occupant = {
            "id": "tms1-c6",
            "vessel_name": "Cais 6",
            "berth_label": "TMS 1 - Cais 6",
            "vessel_loa_m": "170",
        }

        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 4",
                [{**occupant, "vessel_loa_m": "165"}],
                target_vessel_loa_m="330",
            )
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 4",
                [occupant],
                target_vessel_loa_m="330",
            ),
            occupant,
        )

    def test_tms1_blocks_three_large_vessels_including_cais8(self) -> None:
        occupants = [
            {
                "id": "tms1-c4",
                "vessel_name": "Large One",
                "berth_label": "TMS 1 - Cais 4",
                "vessel_loa_m": "230",
            },
            {
                "id": "tms1-c6",
                "vessel_name": "Large Two",
                "berth_label": "TMS 1 - Cais 6",
                "vessel_loa_m": "230",
            },
        ]

        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 8",
                occupants,
                target_vessel_loa_m="210",
            ),
            occupants[0],
        )

    def test_tms1_two_large_vessels_still_allow_small_cais8_if_free(self) -> None:
        occupants = [
            {
                "id": "tms1-c3",
                "vessel_name": "Large One",
                "berth_label": "TMS 1 - Cais 3",
                "vessel_loa_m": "230",
            },
            {
                "id": "tms1-c5",
                "vessel_name": "Large Two",
                "berth_label": "TMS 1 - Cais 5",
                "vessel_loa_m": "230",
            },
        ]

        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 1 - Cais 8",
                occupants,
                target_vessel_loa_m="180",
            )
        )

    def test_tms1_cais8_takes_only_one_vessel_up_to_230m(self) -> None:
        conflict = find_occupied_berth_conflict(
            "TMS 1 - Cais 8",
            [],
            target_vessel_loa_m="240",
        )

        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["reference_code"], "capacidade")

    def test_tms2_long_vessel_occupies_more_than_one_position(self) -> None:
        occupant = {
            "id": "tms2-a",
            "vessel_name": "Long TMS2",
            "berth_label": "TMS 2 - Posição A",
            "vessel_loa_m": "300",
        }

        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 2 - Posição B",
                [occupant],
                target_vessel_loa_m="100",
            ),
            occupant,
        )
        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 2 - Posição C",
                [occupant],
                target_vessel_loa_m="100",
            )
        )

    def test_tms2_prefers_right_but_uses_left_when_right_is_occupied(self) -> None:
        right_occupant = {
            "id": "tms2-c",
            "vessel_name": "Right TMS2",
            "berth_label": "TMS 2 - Posição C",
            "vessel_loa_m": "100",
        }

        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 2 - Posição B",
                [right_occupant],
                target_vessel_loa_m="300",
            )
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 2 - Posição B",
                [
                    right_occupant,
                    {
                        "id": "tms2-a",
                        "vessel_name": "Left TMS2",
                        "berth_label": "TMS 2 - Posição A",
                        "vessel_loa_m": "100",
                    },
                ],
                target_vessel_loa_m="300",
            ),
            right_occupant,
        )

    def test_tms2_allows_three_200m_but_blocks_three_230m_by_total_length(self) -> None:
        two_200m = [
            {
                "id": "tms2-a",
                "vessel_name": "A 200",
                "berth_label": "TMS 2 - Posição A",
                "vessel_loa_m": "200",
            },
            {
                "id": "tms2-b",
                "vessel_name": "B 200",
                "berth_label": "TMS 2 - Posição B",
                "vessel_loa_m": "200",
            },
        ]
        two_230m = [
            {**two_200m[0], "vessel_name": "A 230", "vessel_loa_m": "230"},
            {**two_200m[1], "vessel_name": "B 230", "vessel_loa_m": "230"},
        ]

        self.assertIsNone(
            find_occupied_berth_conflict(
                "TMS 2 - Posição C",
                two_200m,
                target_vessel_loa_m="200",
            )
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 2 - Posição C",
                two_230m,
                target_vessel_loa_m="230",
            ),
            two_230m[0],
        )

    def test_tms2_mid_position_500m_occupies_all_three_positions(self) -> None:
        occupant = {
            "id": "tms2-b",
            "vessel_name": "Central Long TMS2",
            "berth_label": "TMS 2 - Posição B",
            "vessel_loa_m": "500",
        }

        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 2 - Posição A",
                [occupant],
                target_vessel_loa_m="100",
            ),
            occupant,
        )
        self.assertEqual(
            find_occupied_berth_conflict(
                "TMS 2 - Posição C",
                [occupant],
                target_vessel_loa_m="100",
            ),
            occupant,
        )

    def test_tms2_rejects_single_vessel_above_total_length(self) -> None:
        conflict = find_occupied_berth_conflict(
            "TMS 2 - Posição B",
            [],
            target_vessel_loa_m="750",
        )

        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["reference_code"], "capacidade")

    def test_occupancy_counts_multi_slot_vessels(self) -> None:
        occupancy = build_slot_occupancy(
            [
                {
                    "id": "tms1-c4",
                    "vessel_name": "Long TMS1",
                    "berth_label": "TMS 1 - Cais 4",
                    "vessel_loa_m": "230",
                },
                {
                    "id": "auto-10",
                    "vessel_name": "Large RoRo",
                    "berth_label": "Cais 10 / Autoeuropa",
                    "vessel_loa_m": "240",
                },
            ]
        )

        self.assertEqual(occupancy["occupied_slot_count"], 4)

    def test_occupancy_counts_tms2_and_cais7_span(self) -> None:
        occupancy = build_slot_occupancy(
            [
                {
                    "id": "tms2-b",
                    "vessel_name": "Central Long TMS2",
                    "berth_label": "TMS 2 - Posição B",
                    "vessel_loa_m": "500",
                },
                {
                    "id": "tms1-c7",
                    "vessel_name": "Cais 7 Overflow",
                    "berth_label": "TMS 1 - Cais 7",
                    "vessel_loa_m": "100",
                },
            ]
        )

        self.assertEqual(occupancy["occupied_slot_count"], 5)


if __name__ == "__main__":
    unittest.main()
