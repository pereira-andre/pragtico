import unittest

from domain.berth_layout import (
    build_slot_occupancy,
    canonicalize_berth_label,
    find_occupied_berth_conflict,
    is_known_berth_label,
    slot_berth_options,
)


class BerthLayoutTests(unittest.TestCase):
    def test_lisnave_dry_dock_aliases_are_recognized(self) -> None:
        self.assertEqual(canonicalize_berth_label("Doca 21"), "Lisnave - Doca 21")
        self.assertEqual(canonicalize_berth_label("Doca seca 21"), "Lisnave - Doca 21")
        self.assertEqual(canonicalize_berth_label("D33"), "Lisnave - Doca 33")
        self.assertEqual(canonicalize_berth_label("Lisnave D31"), "Lisnave - Doca 31")
        self.assertTrue(is_known_berth_label("Lisnave - Doca 21"))

    def test_lisnave_repair_quay_aliases_are_recognized(self) -> None:
        self.assertEqual(canonicalize_berth_label("Cais 2 A"), "Lisnave - Cais 2 A")
        self.assertEqual(canonicalize_berth_label("cais2a"), "Lisnave - Cais 2 A")
        self.assertEqual(canonicalize_berth_label("C3A"), "Lisnave - Cais 3 A")
        self.assertEqual(canonicalize_berth_label("Lisnave 3A"), "Lisnave - Cais 3 A")
        self.assertTrue(is_known_berth_label("Lisnave - Cais 2 A"))

    def test_tms2_exposes_three_operational_positions(self) -> None:
        self.assertEqual(canonicalize_berth_label("TMS2 A"), "TMS 2 - Posição A")
        self.assertEqual(canonicalize_berth_label("TMS 2 posição C"), "TMS 2 - Posição C")
        self.assertTrue(is_known_berth_label("TMS 2 - Posição B"))
        self.assertEqual(
            [item for item in slot_berth_options() if item.startswith("TMS 2")],
            ["TMS 2 - Posição A", "TMS 2 - Posição B", "TMS 2 - Posição C"],
        )

    def test_tms2_conflict_only_when_all_three_positions_are_occupied(self) -> None:
        two_vessels = [
            {"id": "a", "berth_label": "TMS 2 - Posição A", "vessel_name": "A"},
            {"id": "b", "berth_label": "TMS 2 - Posição B", "vessel_name": "B"},
        ]
        self.assertIsNone(find_occupied_berth_conflict("TMS 2 - Posição C", two_vessels))

        three_vessels = [
            *two_vessels,
            {"id": "c", "berth_label": "TMS 2 - Posição C", "vessel_name": "C"},
        ]
        self.assertIsNotNone(find_occupied_berth_conflict("TMS 2", three_vessels))
        occupancy = build_slot_occupancy(three_vessels)
        self.assertEqual(occupancy["occupied_slot_count"], 3)
        self.assertEqual(occupancy["free_slot_count"], len(slot_berth_options()) - 3)
