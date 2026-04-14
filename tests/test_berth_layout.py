import unittest

from domain.berth_layout import canonicalize_berth_label, is_known_berth_label


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
