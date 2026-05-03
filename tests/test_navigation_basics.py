from __future__ import annotations

import unittest

from domain.navigation_basics import answer_navigation_basics_direct, build_navigation_basics_source


class NavigationBasicsTests(unittest.TestCase):
    def test_converts_km_to_nautical_miles(self) -> None:
        payload = answer_navigation_basics_direct("20 km são quantas milhas náuticas?") or {}

        self.assertEqual(payload.get("answer_origin"), "navigation_basics")
        self.assertIn("20 km = 10,80 milhas náuticas", payload.get("answer", ""))
        self.assertIn("1 milha náutica = 1852 m", payload.get("answer", ""))

    def test_converts_shackle_to_meters(self) -> None:
        payload = answer_navigation_basics_direct("1 manilha são quantos metros?") or {}

        self.assertEqual(payload.get("answer_origin"), "navigation_basics")
        self.assertIn("1 manilha = 27,5 metros", payload.get("answer", ""))

    def test_converts_yards_to_meters(self) -> None:
        payload = answer_navigation_basics_direct("100 jardas são quantos metros?") or {}

        self.assertEqual(payload.get("answer_origin"), "navigation_basics")
        self.assertIn("100 jardas = 91,44 metros", payload.get("answer", ""))

    def test_answers_beaufort_force(self) -> None:
        payload = answer_navigation_basics_direct("Força 6 Beaufort equivale a quantos nós?") or {}

        self.assertEqual(payload.get("answer_origin"), "navigation_basics")
        self.assertIn("Beaufort 6 = 22-27 kt (39-49 km/h)", payload.get("answer", ""))
        self.assertIn("vento fresco", payload.get("answer", ""))

    def test_builds_source_for_beaufort_query(self) -> None:
        source = build_navigation_basics_source("Tabela Beaufort")

        self.assertIsNotNone(source)
        self.assertIn("Beaufort 6: 22-27 kt", source.get("text", ""))
        self.assertIn("1 manilha = 27,5 m", source.get("text", ""))


if __name__ == "__main__":
    unittest.main()
