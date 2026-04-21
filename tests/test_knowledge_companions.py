import unittest
from pathlib import Path

from domain.knowledge_companions import build_companion_answer, load_document_companion


REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = str(REPO_ROOT / "knowledge")


class RepositoryKnowledgeCompanionTests(unittest.TestCase):
    def test_curated_critical_companions_have_summary_and_faq(self) -> None:
        critical_documents = [
            "Porto_Setubal_Terminais_Cais.txt",
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

    def test_it062_companion_distinguishes_overview_rules_and_length(self) -> None:
        companion = load_document_companion("IT-062_Teporset.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        overview = build_companion_answer(
            "O que me podes dizer sobre o cais da Teporset em termos gerais?",
            companion,
        )
        rules = build_companion_answer("Quais são as regras para o cais da Teporset?", companion)
        restrictions = build_companion_answer("Que restrições existem no cais da Teporset?", companion)
        length = build_companion_answer("Qual o comprimento do cais da Teporset?", companion)
        unsupported = build_companion_answer("Qual é a cor do cais da Teporset?", companion)

        self.assertIn("Terminal Portuário de Setúbal", overview)
        self.assertIn("calado calculado", overview)
        self.assertIn("7,4 metros", rules)
        self.assertIn("11,0 metros", rules)
        self.assertIn("Piloto Coordenador", rules)
        self.assertIn("200 metros", restrictions)
        self.assertIn("calado máximo", restrictions)
        self.assertIn("164 metros", length)
        self.assertIn("não ler esse número isoladamente", length)
        self.assertIn("200 metros de LOA", length)
        self.assertEqual(unsupported, "")

    def test_brief_companion_answers_are_operationalized_for_common_fact_types(self) -> None:
        tms2 = load_document_companion("IT-006_TMS2.txt", KNOWLEDGE_DIR)
        pilotagem_assistida = load_document_companion("IT-017_PilotagemAssistida.txt", KNOWLEDGE_DIR)
        tms1 = load_document_companion("IT-005_TMS1.txt", KNOWLEDGE_DIR)
        marcacao = load_document_companion("Marcar_manobra_repontos_mare.txt", KNOWLEDGE_DIR)
        entrada_saida = load_document_companion("IT-041_EntradaSaida.txt", KNOWLEDGE_DIR)
        ecooil = load_document_companion("IT-008_EcoOil.txt", KNOWLEDGE_DIR)

        length = build_companion_answer("Qual é o comprimento total do TMS2?", tms2)
        distance = build_companion_answer(
            "A que distância da Barra o VTS inicia a aquisição do navio?",
            pilotagem_assistida,
        )
        validator = build_companion_answer("Quem valida as requisições de manobra no TMS1?", tms1)
        lead_time = build_companion_answer(
            "Com quanto tempo de antecedência se marca uma entrada de fora da barra?",
            marcacao,
        )
        vhf = build_companion_answer(
            "Em que canal VHF se faz a transferência de responsabilidade entre OVTS e Piloto?",
            entrada_saida,
        )
        boolean_rule = build_companion_answer("É possível atracar de noite no ECO-OIL?", ecooil)

        self.assertIn("723 metros", length)
        self.assertIn("referência documental", length)
        self.assertIn("4 milhas", distance)
        self.assertIn("ponto de origem e destino", distance)
        self.assertEqual(validator, "A validação fica com o Piloto Coordenador.")
        self.assertIn("2 horas", lead_time)
        self.assertIn("restantes condicionantes", lead_time)
        self.assertIn("canal VHF 14", vhf)
        self.assertTrue(vhf.startswith("Usa esta referência:"))
        self.assertTrue(boolean_rule.startswith("Neste caso, a resposta é: Não."))
        self.assertNotIn("O valor a reter é Não", boolean_rule)

    def test_overview_questions_do_not_collapse_to_short_scalar_faqs(self) -> None:
        cases = [
            ("IT-008_EcoOil.txt", "Fala me do cais da Eco-oil em termos gerais", "Terminal da ECO-OIL"),
            ("IT-012_PraiasSado.txt", "O que sabes sobre o cais das Praias do Sado?", "Terminal Praias do Sado"),
            ("IT-038_Alstom.txt", "O que sabes sobre o cais da alstom?", "Cais da ALSTOM"),
            ("IT-029_SAPEC.txt", "O que sabes sobre o cais da SAPEC?", "dois terminais da SAPEC"),
        ]

        for document_name, question, expected_text in cases:
            with self.subTest(document=document_name):
                companion = load_document_companion(document_name, KNOWLEDGE_DIR)
                self.assertIsNotNone(companion)

                answer = build_companion_answer(question, companion)

                self.assertIn(expected_text, answer)
                self.assertFalse(answer.startswith("A resposta direta:"))
                self.assertFalse(answer.startswith("O valor a reter é"))

    def test_global_companion_does_not_match_generic_teporset_faq_when_specific_cais_is_named(self) -> None:
        from domain.knowledge_companions import find_best_global_companion_match

        answer = find_best_global_companion_match(
            "Fala me do cais da Secil em termos gerais",
            KNOWLEDGE_DIR,
        )

        self.assertIsNone(answer)

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

    def test_notas_pilotagem_companion_answers_pilot_embarkment_distance(self) -> None:
        companion = load_document_companion("Notas_Pilotagem.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "A que distância está a posição de embarque dos pilotos da entrada da barra?",
            companion,
        )

        self.assertIn("1 milha náutica", answer)
        self.assertIn("Pilar n.º 2", answer)

    def test_port_inventory_companion_answers_terminal_inventory(self) -> None:
        companion = load_document_companion("Porto_Setubal_Terminais_Cais.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Quais são os terminais que existem no porto de Setúbal?",
            companion,
        )

        self.assertIn("TMS1", answer)
        self.assertIn("TMS2", answer)
        self.assertIn("Autoeuropa", answer)
        self.assertIn("SAPEC", answer)
        self.assertIn("TEPORSET", answer)

    def test_port_inventory_companion_answers_quay_names_with_expected_grouping(self) -> None:
        companion = load_document_companion("Porto_Setubal_Terminais_Cais.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Quantos cais existem em Setúbal? E quais os seus nomes?",
            companion,
        )

        self.assertFalse(answer.startswith("Quantos cais"))
        self.assertIn("36 slots operacionais", answer)
        self.assertIn("\n- **Cais SECIL:**", answer)
        self.assertIn("Terminal Multiusos 1 (TMS1 / Cais das Fontainhas)", answer)
        self.assertIn("Terminal Multiusos 2 (TMS2 / Terminal de Contentores)", answer)
        self.assertIn("Terminal Autoeuropa / Ro-Ro", answer)
        self.assertIn("SAPEC", answer)
        self.assertIn("TANQUISADO", answer)
        self.assertIn("TERMITRENA", answer)
        self.assertIn("TEPORSET", answer)
        self.assertIn("Hidrolift com acesso as docas secas 31, 32 e 33", answer)
        self.assertIn("\n\nNota:", answer)
        self.assertNotIn("Terminal Multiusos Norte", answer)
        self.assertNotIn("Terminal Multiusos Sul", answer)
        self.assertNotIn("Berço Ro-Ro (na extremidade leste", answer)
        self.assertNotIn("Plataformas / Docas", answer)
        self.assertNotIn("Existem quatro zonas de fundeio definidas", answer)

    def test_port_inventory_companion_answers_ungrammatical_quay_names_question(self) -> None:
        companion = load_document_companion("Porto_Setubal_Terminais_Cais.txt", KNOWLEDGE_DIR)
        self.assertIsNotNone(companion)

        answer = build_companion_answer(
            "Quantos cais existem em Setúbal e qual os seus nomes?",
            companion,
        )

        self.assertFalse(answer.startswith("Quantos cais"))
        self.assertIn("36 slots operacionais", answer)
        self.assertIn("Terminal Multiusos 1 (TMS1 / Cais das Fontainhas)", answer)
        self.assertIn("Terminal Multiusos 2 (TMS2 / Terminal de Contentores)", answer)
        self.assertIn("Terminal Autoeuropa / Ro-Ro", answer)
        self.assertIn("Hidrolift com acesso as docas secas 31, 32 e 33", answer)
        self.assertNotIn("Terminal Multiusos Norte", answer)
        self.assertNotIn("Terminal Multiusos Sul", answer)

    def test_port_inventory_companion_does_not_confuse_quays_with_anchorages(self) -> None:
        from domain.knowledge_companions import find_best_global_companion_match

        answer = find_best_global_companion_match(
            "Quantos cais existem em Setúbal?",
            KNOWLEDGE_DIR,
        )

        self.assertIsNotNone(answer)
        self.assertEqual(answer["companion"]["document"], "Porto_Setubal_Terminais_Cais.txt")
        self.assertIn("fundeadouros nao contam como cais", answer["answer"].lower())
        self.assertNotIn("Existem quatro zonas de fundeio definidas", answer["answer"])
