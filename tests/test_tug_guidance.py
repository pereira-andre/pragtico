import unittest
from pathlib import Path

from domain.tug_guidance import build_tug_operational_guidance_source


KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"


class TugGuidanceTests(unittest.TestCase):
    def _snippet_for(self, question: str) -> str:
        source = build_tug_operational_guidance_source(question, str(KNOWLEDGE_DIR))
        self.assertIsNotNone(source)
        self.assertEqual(source["retrieval_mode"], "operational_tug_guidance")
        return source["snippet"]

    def test_roro_north_entry_uses_three_tugs_as_practical_baseline(self) -> None:
        snippet = self._snippet_for("Quantos reboques para entrada de RORO de 180m com vento norte?")

        self.assertIn("baseline operacional", snippet)
        self.assertIn("IT-016", snippet)
        self.assertIn("Ro-Ro com vento Norte a entrar: 3 rebocadores.", snippet)
        self.assertIn("LOA inferido: 180 m", snippet)

    def test_tug_context_without_explicit_question_word_still_gets_guidance(self) -> None:
        snippet = self._snippet_for("Reboques para RORO de 180m a entrar com vento norte?")

        self.assertIn("Ro-Ro com vento Norte a entrar: 3 rebocadores.", snippet)

    def test_bulk_like_south_departure_uses_two_large_tugs(self) -> None:
        snippet = self._snippet_for("Um reefer de 190m a sair com vento sul, quantos rebocadores?")

        self.assertIn("tipo/grupo inferido: graneleiro/reefer/estilha/contentores grande", snippet)
        self.assertIn("Graneleiros, reefers, estilha e contentores grandes com vento Sul a sair: 2 rebocadores.", snippet)

    def test_lisnave_over_250_m_uses_six_tugs(self) -> None:
        snippet = self._snippet_for("Lisnave, navio de 260m, quantos reboques?")

        self.assertIn("Lisnave acima de 250 m: 6 rebocadores.", snippet)

    def test_no_bowthruster_over_150_m_uses_three_tugs_minimum(self) -> None:
        snippet = self._snippet_for("Navio sem bowthruster com 160m, quantos reboques?")

        self.assertIn("sem bowthruster", snippet)
        self.assertIn("Navio sem bowthruster acima de 150 m: pelo menos 3 rebocadores", snippet)


if __name__ == "__main__":
    unittest.main()
