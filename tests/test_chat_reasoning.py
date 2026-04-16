import unittest

from core.chat_planner import build_chat_execution_plan
from core.chat_reasoning import (
    build_compound_message_analysis_source,
    build_conversation_reasoning_state,
)


class ChatReasoningTests(unittest.TestCase):
    def test_compound_message_analysis_extracts_operational_facts_and_questions(self) -> None:
        question = (
            "Estava também a tratar de um navio para sair do cais da Eco Oil. "
            "O navio tem 290 m e 7 m de calado, posso marcar manobra para as 23:00? "
            "É preciso algum reboque?"
        )

        source = build_compound_message_analysis_source(question)

        self.assertIsNotNone(source)
        snippet = source["snippet"]
        self.assertIn("Mensagem composta detetada", snippet)
        self.assertIn("Cais/terminal referido: Terminal ECO-OIL", snippet)
        self.assertIn("Operação pretendida: saída/desatracação", snippet)
        self.assertIn("LOA / comprimento: 290 m", snippet)
        self.assertIn("Calado: 7 m", snippet)
        self.assertIn("Hora planeada/referida: 23:00", snippet)
        self.assertIn("É preciso algum reboque?", snippet)

    def test_conversation_state_includes_current_compound_message_facts_for_tug_reasoning(self) -> None:
        question = (
            "Estava a tratar de um navio para sair do cais da Eco Oil. "
            "O navio tem 290 m e 7 m de calado, posso marcar manobra para as 23:00? "
            "É preciso algum reboque?"
        )
        plan = build_chat_execution_plan(question)

        state = build_conversation_reasoning_state(question, [{"role": "user", "content": question}], plan)

        self.assertIsNotNone(state)
        self.assertIn("Terminal ECO-OIL", state["summary"])
        self.assertIn("LOA / comprimento: 290 m", state["summary"])
        self.assertIn("Calado: 7 m", state["summary"])
        self.assertIn("Hora planeada/referida: 23:00", state["summary"])
        self.assertIn("rebocadores", state["summary"])


if __name__ == "__main__":
    unittest.main()
