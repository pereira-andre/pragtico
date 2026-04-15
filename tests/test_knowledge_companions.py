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

    def test_it036_companion_answers_generic_night_question_conditionally(self) -> None:
        companion = load_document_companion("IT-036_RegulacaoAgulhas.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Tenho um navio para fazer regulação de agulhas à noite, o que dizem as regras sobre isso?",
            companion,
        )

        self.assertIn("LOA igual ou superior a 225 metros", answer)
        self.assertIn("0,7 milhas", answer)

    def test_it014_companion_answers_generic_lisnave_night_length_question(self) -> None:
        companion = load_document_companion("IT-014_Lisnave.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Qual é o comprimento máximo que um navio pode manobrar durante noite na LISNAVE?",
            companion,
        )

        self.assertIn("280 metros", answer)
        self.assertIn("período diurno", answer)
        self.assertNotIn("Não.", answer[:6])

    def test_it014_companion_answers_lisnave_draft_question_without_loa_shortcut(self) -> None:
        companion = load_document_companion("IT-014_Lisnave.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Qual é o calado máximo para um navio que vai para a LISNAVE?",
            companion,
        )

        self.assertIn("Não há um calado máximo único", answer)
        self.assertIn("Cais III-B 8,60 m", answer)
        self.assertIn("Docas 21 e 22 6,10 m", answer)
        self.assertIn("Docas secas 31/32/33 5,5 m", answer)
        self.assertIn("Hidrolift", answer)
        self.assertNotIn("período diurno", answer)

    def test_it014_companion_answers_lisnave_quays_and_docks_inventory(self) -> None:
        companion = load_document_companion("IT-014_Lisnave.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Quais são os cais e as docas da LISNAVE?",
            companion,
        )

        self.assertIn("Pontes-Cais de reparação I, II e III", answer)
        self.assertIn("Docas secas 20, 21 e 22", answer)
        self.assertIn("Cais 0", answer)
        self.assertIn("Hidrolift com acesso às Docas secas 31, 32 e 33", answer)
        self.assertNotEqual("O Cais III-B, com sonda ao ZH de 8,60 metros a 10 metros da face do cais.", answer)

    def test_it014_companion_answers_lisnave_counts_with_counting_context(self) -> None:
        companion = load_document_companion("IT-014_Lisnave.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Existem quantos cais e quantas docas na LISNAVE?",
            companion,
        )

        self.assertIn("Depende da forma de contar", answer)
        self.assertIn("6 faces operacionais", answer)
        self.assertIn("Docas 20, 21 e 22", answer)
        self.assertIn("Docas secas 31, 32 e 33", answer)
        self.assertIn("Hidrolift", answer)

    def test_it014_companion_answers_lisnave_details_as_overview(self) -> None:
        companion = load_document_companion("IT-014_Lisnave.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer("Dá-me mais detalhes sobre a LISNAVE", companion)

        self.assertIn("zona de reparação e construção naval", answer)
        self.assertIn("Pontes-Cais I, II e III", answer)
        self.assertIn("Docas secas 20, 21 e 22", answer)
        self.assertIn("Hidrolift com acesso às Docas secas 31, 32 e 33", answer)
        self.assertIn("Para calado", answer)
        self.assertNotEqual("280 metros. Na LISNAVE, navios com LOA até 280 metros podem manobrar em qualquer reponto de maré, tanto de dia como de noite. Acima de 280 metros, as manobras ficam limitadas ao período diurno.", answer)

    def test_notas_pilotagem_companion_answers_lisnave_distance_question(self) -> None:
        companion = load_document_companion("Notas_Pilotagem.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Qual é a distância da entrada da Barra até ao estaleiro da LISNAVE?",
            companion,
        )

        self.assertIn("10,5", answer)
        self.assertIn("10,0", answer)
        self.assertNotIn("1000 metros", answer)

    def test_port_inventory_companion_answers_terminal_inventory(self) -> None:
        companion = load_document_companion("Porto_Setubal_Terminais_Cais.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Quais são os terminais que existem no porto de Setúbal?",
            companion,
        )

        self.assertIn("TMS1", answer)
        self.assertIn("TMS2", answer)
        self.assertIn("AUTO-EUROPA", answer)
        self.assertIn("SAPEC (TPS e TGL)", answer)
        self.assertIn("TEPORSET", answer)

    def test_port_inventory_companion_answers_quay_names_with_expected_grouping(self) -> None:
        companion = load_document_companion("Porto_Setubal_Terminais_Cais.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Quantos cais existem em Setúbal? E quais os seus nomes?",
            companion,
        )

        self.assertIn("34 slots de cais operacionais", answer)
        self.assertIn("Cais SECIL W e E", answer)
        self.assertIn("Terminal Multiusos 1 ou Cais das Fontainhas", answer)
        self.assertIn("Terminal de Contentores ou Multiusos 2", answer)
        self.assertIn("Terminal AUTO-EUROPA ou Ro-Ro", answer)
        self.assertIn("SAPEC Solidos e SAPEC Liquidos", answer)
        self.assertIn("Hidrolift com acesso as Docas secas 31/32/33", answer)
        self.assertNotIn("Existem quatro zonas de fundeio definidas", answer)

    def test_port_inventory_companion_does_not_confuse_quays_with_anchorages(self) -> None:
        from domain.knowledge_companions import find_best_global_companion_match

        answer = find_best_global_companion_match(
            "Quantos cais existem em Setúbal?",
            KNOWLEDGE_DIR,
        )

        self.assertIsNotNone(answer)
        self.assertEqual(answer["companion"]["document"], "Porto_Setubal_Terminais_Cais.txt")
        self.assertIn("Fundeadouros nao contam como cais", answer["answer"])
        self.assertNotIn("Existem quatro zonas de fundeio definidas", answer["answer"])
