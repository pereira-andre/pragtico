import unittest
from pathlib import Path

from domain.knowledge_companions import build_companion_answer, load_document_companion


REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = str(REPO_ROOT / "knowledge")


class RepositoryKnowledgeCompanionTests(unittest.TestCase):
    def test_curated_critical_companions_have_summary_and_faq(self) -> None:
        critical_documents = [
            "IT-015_Fundeadouros.txt",
            "IT-016_Rebocadores.txt",
            "IT-018_NormasEspeciais.txt",
            "IT-041_EntradaSaida.txt",
            "RG-14_RegulamentoInterno.txt",
            "P-13_PlaneamentoGestao.txt",
            "Tarifas_APSS_2024.txt",
        ]

        for document_name in critical_documents:
            with self.subTest(document=document_name):
                companion = load_document_companion(document_name, KNOWLEDGE_DIR)
                self.assertIsNotNone(companion)
                self.assertTrue(companion["summary"])
                self.assertGreaterEqual(len(companion["faq"]), 6)

    def test_tariff_companion_answers_contentores_tup_formula(self) -> None:
        companion = load_document_companion("Tarifas_APSS_2024.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer("Qual é a fórmula da TUP para um navio de contentores?", companion)

        self.assertIn("0,1144", answer)
        self.assertIn("0,0263", answer)

    def test_p13_companion_answers_priority_question(self) -> None:
        companion = load_document_companion("P-13_PlaneamentoGestao.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer("Quando dois navios querem o mesmo cais, quem tem prioridade?", companion)

        self.assertIn("8 milhas", answer)
        self.assertIn("Baliza número 2", answer)
