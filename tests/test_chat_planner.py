import unittest

from core.chat_planner import build_chat_execution_plan


class ChatPlannerTests(unittest.TestCase):
    def test_weather_timeline_question_uses_timeline_mode(self) -> None:
        plan = build_chat_execution_plan(
            "Como vai estar a meteorologia no porto até às 00:00 de 10 abril?"
        )

        self.assertEqual(plan.primary_intent, "live_environment")
        self.assertEqual(plan.weather_mode, "timeline")
        self.assertEqual(plan.live_facets, ("weather",))
        self.assertFalse(plan.requires_llm_synthesis)

    def test_mixed_live_and_document_question_requires_synthesis(self) -> None:
        plan = build_chat_execution_plan(
            "Quais são as marés para hoje e o que diz o IT-036 sobre regulação de agulhas à noite?"
        )

        self.assertEqual(plan.primary_intent, "mixed_live_and_documents")
        self.assertEqual(plan.live_facets, ("tides",))
        self.assertTrue(plan.wants_documents)
        self.assertEqual(plan.explicit_rule_codes, ("036",))
        self.assertTrue(plan.requires_llm_synthesis)

    def test_live_reasoning_question_does_not_short_circuit_to_direct_live_answer(self) -> None:
        plan = build_chat_execution_plan(
            "Sabendo que o preia-mar hoje é às 15:13, a que horas deve embarcar piloto para trazer um navio para a Lisnave?"
        )

        self.assertEqual(plan.primary_intent, "live_reasoning")
        self.assertEqual(plan.live_facets, ("tides",))
        self.assertTrue(plan.requires_live_reasoning)
        self.assertTrue(plan.requires_llm_synthesis)
        self.assertFalse(plan.should_answer_directly)

    def test_weather_followup_about_tug_sufficiency_uses_live_reasoning(self) -> None:
        plan = build_chat_execution_plan(
            "Avalia o vento que está atualmente em porto e diz me se os dois reboques são suficientes."
        )

        self.assertEqual(plan.primary_intent, "live_reasoning")
        self.assertEqual(plan.live_facets, ("weather",))
        self.assertEqual(plan.weather_mode, "current")
        self.assertTrue(plan.requires_live_reasoning)
        self.assertTrue(plan.requires_llm_synthesis)
        self.assertTrue(plan.needs_history_state)
        self.assertTrue(plan.needs_answer_critic)
        self.assertFalse(plan.should_answer_directly)

    def test_initial_weather_tug_recommendation_uses_live_reasoning(self) -> None:
        plan = build_chat_execution_plan(
            "Com o estado de vento atual em porto, se fosse atracar um navio RORO com 180m, quantos reboques recomendavas?"
        )

        self.assertEqual(plan.primary_intent, "live_reasoning")
        self.assertEqual(plan.live_facets, ("weather",))
        self.assertEqual(plan.weather_mode, "current")
        self.assertTrue(plan.wants_documents)
        self.assertTrue(plan.requires_live_reasoning)
        self.assertTrue(plan.requires_llm_synthesis)
        self.assertTrue(plan.needs_answer_critic)
        self.assertFalse(plan.should_answer_directly)

    def test_live_operational_decision_variants_use_reasoning(self) -> None:
        questions = [
            ("Com esta ondulação achas seguro sair com um navio Ro-Ro?", ("waves",)),
            ("A maré de agora permite marcar entrada para a Lisnave?", ("tides",)),
            ("Há avisos da capitania, posso autorizar a saída?", ("warnings",)),
            ("O vento atual condiciona atracar o Ro-Ro?", ("weather",)),
            ("Com a visibilidade atual, devia adiar a manobra?", ("weather",)),
        ]

        for question, expected_facets in questions:
            with self.subTest(question=question):
                plan = build_chat_execution_plan(question)

                self.assertEqual(plan.primary_intent, "live_reasoning")
                self.assertEqual(plan.live_facets, expected_facets)
                self.assertTrue(plan.requires_live_reasoning)
                self.assertTrue(plan.requires_llm_synthesis)
                self.assertTrue(plan.needs_answer_critic)
                self.assertFalse(plan.should_answer_directly)

    def test_factual_current_weather_stays_direct_live_environment(self) -> None:
        plan = build_chat_execution_plan("Qual é o vento atual no porto?")

        self.assertEqual(plan.primary_intent, "live_environment")
        self.assertEqual(plan.live_facets, ("weather",))
        self.assertEqual(plan.weather_mode, "current")
        self.assertFalse(plan.requires_llm_synthesis)
        self.assertFalse(plan.needs_answer_critic)
        self.assertTrue(plan.should_answer_directly)

    def test_followup_tug_recommendation_keeps_history_state(self) -> None:
        plan = build_chat_execution_plan(
            "Com base nisso, quantos reboques aconselharias para atracar o Ro-Ro de 180m?"
        )

        self.assertEqual(plan.primary_intent, "document_synthesis")
        self.assertEqual(plan.live_facets, ())
        self.assertTrue(plan.wants_documents)
        self.assertTrue(plan.requires_llm_synthesis)
        self.assertTrue(plan.needs_history_state)
        self.assertTrue(plan.needs_answer_critic)
        self.assertFalse(plan.should_answer_directly)

    def test_scheduled_operational_question_with_time_uses_tide_reasoning(self) -> None:
        plan = build_chat_execution_plan(
            "Estava a tratar de um navio para sair do cais da Eco Oil. "
            "O navio tem 290 m e 7 m de calado, posso marcar manobra para as 23:00? "
            "É preciso algum reboque?"
        )

        self.assertEqual(plan.primary_intent, "live_reasoning")
        self.assertEqual(plan.live_facets, ("tides",))
        self.assertTrue(plan.wants_documents)
        self.assertTrue(plan.requires_llm_synthesis)
        self.assertTrue(plan.needs_history_state)
        self.assertTrue(plan.needs_answer_critic)
        self.assertFalse(plan.should_answer_directly)

    def test_port_facility_inventory_question_requires_rag_synthesis(self) -> None:
        plan = build_chat_execution_plan(
            "Quais são os terminais que existem no porto de Setúbal?"
        )

        self.assertEqual(plan.primary_intent, "document_synthesis")
        self.assertTrue(plan.wants_documents)
        self.assertTrue(plan.requires_llm_synthesis)
        self.assertFalse(plan.should_answer_directly)
