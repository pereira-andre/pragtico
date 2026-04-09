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
