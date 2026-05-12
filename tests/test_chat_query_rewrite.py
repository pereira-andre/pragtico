from __future__ import annotations

import unittest

from core.chat_planner import build_chat_execution_plan
from core.chat_runtime import _allow_companion_shortcut_for_question, _contextual_lookup_question


class ChatQueryRewriteTests(unittest.TestCase):
    def test_rewrites_sapec_follow_up_to_standalone_question(self) -> None:
        history = [{"role": "user", "content": "Que restrições existem na SAPEC líquidos?"}]

        rewritten = _contextual_lookup_question("E na SAPEC sólidos?", history)

        self.assertEqual(
            rewritten,
            "Que restrições operacionais documentadas existem para SAPEC solidos?",
        )

    def test_rewrites_secil_follow_up_to_specific_entity(self) -> None:
        history = [{"role": "user", "content": "O que diz o documento sobre a Secil W?"}]

        rewritten = _contextual_lookup_question("E a Secil E?", history)

        self.assertEqual(rewritten, "O que diz a documentação sobre Secil E?")

    def test_short_follow_up_does_not_use_companion_shortcut(self) -> None:
        plan = build_chat_execution_plan("E carga não IMO")

        self.assertFalse(_allow_companion_shortcut_for_question("E carga não IMO", plan))

    def test_standalone_question_can_use_companion_shortcut(self) -> None:
        plan = build_chat_execution_plan("Qual o calado máximo no TGL com carga não IMO?")

        self.assertTrue(
            _allow_companion_shortcut_for_question(
                "Qual o calado máximo no TGL com carga não IMO?",
                plan,
            )
        )


if __name__ == "__main__":
    unittest.main()
