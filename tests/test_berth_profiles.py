import unittest
from pathlib import Path

from domain.berth_profiles import (
    build_berth_profile_answer,
    build_berth_profile_sources,
    find_best_berth_profile,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = str(REPO_ROOT / "knowledge")


class BerthProfileTests(unittest.TestCase):
    def test_overview_questions_use_structured_profile_not_single_fact(self) -> None:
        cases = [
            ("Fala me do cais da Eco-oil em termos gerais", "eco_oil", "reponto"),
            ("O que sabes sobre o cais das Praias do Sado?", "praias_sado", "dupla restricao"),
            ("O que sabes sobre o cais da SAPEC?", "sapec", "TPS"),
            ("O que sabes sobre o TMS 2", "tms2", "723 m"),
        ]

        for question, expected_id, expected_text in cases:
            with self.subTest(question=question):
                match = find_best_berth_profile(question, KNOWLEDGE_DIR)
                self.assertIsNotNone(match)
                self.assertEqual(match["profile"]["id"], expected_id)

                answer = build_berth_profile_answer(question, match)

                self.assertIn(expected_text.lower(), answer.lower())
                self.assertGreater(len(answer), 220)
                self.assertFalse(answer.startswith("A resposta direta:"))
                self.assertFalse(answer.startswith("O valor a reter é"))

    def test_restriction_and_tug_questions_focus_profile_sections(self) -> None:
        restriction_match = find_best_berth_profile("Que restrições existem na Eco-Oil?", KNOWLEDGE_DIR)
        tug_match = find_best_berth_profile("É preciso algum reboque na Eco Oil?", KNOWLEDGE_DIR)

        restriction_answer = build_berth_profile_answer("Que restrições existem na Eco-Oil?", restriction_match)
        tug_answer = build_berth_profile_answer("É preciso algum reboque na Eco Oil?", tug_match)

        self.assertIn("restrições principais", restriction_answer)
        self.assertIn("Atracacao noturna proibida", restriction_answer)
        self.assertIn("para rebocadores", tug_answer)
        self.assertIn("IT-016", tug_answer)

    def test_lisnave_profile_is_available_for_general_berth_data(self) -> None:
        match = find_best_berth_profile("Quais são os dados gerais da LISNAVE?", KNOWLEDGE_DIR)

        self.assertIsNotNone(match)
        self.assertEqual(match["profile"]["id"], "lisnave")
        answer = build_berth_profile_answer("Quais são os dados gerais da LISNAVE?", match)
        self.assertIn("LISNAVE / Estaleiros Mitrena", answer)
        self.assertIn("Calado", answer)
        self.assertIn("Noite", answer)

    def test_lisnave_night_loa_question_interprets_specific_vessel_length(self) -> None:
        match = find_best_berth_profile(
            "À noite psso manobrar um navio com 285m de comprimento na Lisnave?",
            KNOWLEDGE_DIR,
        )

        self.assertIsNotNone(match)
        answer = build_berth_profile_answer(
            "À noite psso manobrar um navio com 285m de comprimento na Lisnave?",
            match,
        )

        self.assertTrue(answer.startswith("Nao."))
        self.assertIn("285 m", answer)
        self.assertIn("280 m", answer)
        self.assertIn("periodo diurno", answer)

    def test_lisnave_night_loa_max_question_uses_structured_limit(self) -> None:
        match = find_best_berth_profile(
            "Qual o comprimento maximo para manobrar na Lisnave a noite?",
            KNOWLEDGE_DIR,
        )

        self.assertIsNotNone(match)
        answer = build_berth_profile_answer(
            "Qual o comprimento maximo para manobrar na Lisnave a noite?",
            match,
        )

        self.assertIn("comprimento maximo", answer.lower())
        self.assertIn("280 m", answer)
        self.assertIn("reponto de mare", answer)

    def test_profile_sources_are_available_for_rag_synthesis(self) -> None:
        match = find_best_berth_profile("sair da Eco Oil às 23:00 com 290 m", KNOWLEDGE_DIR)

        sources = build_berth_profile_sources(match)

        self.assertEqual(sources[0]["retrieval_mode"], "berth_profile")
        self.assertIn("Navios acima de 255 m", sources[0]["snippet"])


if __name__ == "__main__":
    unittest.main()
