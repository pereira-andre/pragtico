import unittest

from domain.chat_response_formatting import add_contextual_response_emojis


class ChatResponseFormattingTests(unittest.TestCase):
    def test_question_echo_is_stripped_even_without_emoji_decoration(self) -> None:
        payload = {
            "answer": (
                "O que me podes dizer sobre o cais da Teporset em termos gerais?\n"
                "A TEPORSET é o Terminal Portuário de Setúbal."
            ),
            "sources": [{"retrieval_mode": "document_companion"}],
            "answer_origin": "document_companion",
        }

        formatted = add_contextual_response_emojis(
            payload,
            "O que me podes dizer sobre o cais da Teporset em termos gerais?",
        )

        self.assertEqual(formatted["answer"], "A TEPORSET é o Terminal Portuário de Setúbal.")
        self.assertTrue(payload["answer"].startswith("O que me podes dizer"))

    def test_slash_live_query_gets_contextual_prefix(self) -> None:
        payload = {
            "answer": "Meteorologia para Setúbal: vento NW 15 kts.",
            "sources": [],
            "answer_origin": "slash_weather",
        }

        formatted = add_contextual_response_emojis(payload, "/meteo Setúbal")

        self.assertEqual(formatted["answer"], "🌦️ Meteorologia para Setúbal: vento NW 15 kts.")

    def test_operational_live_response_decorates_each_context_section(self) -> None:
        payload = {
            "answer": (
                "Marés para 15/04/2026 em Setúbal / Tróia:\n"
                "- 08:00 — Preia-mar\n\n"
                "Condições meteorológicas atuais em Setúbal:\n"
                "- Vento: NW 15 kts"
            ),
            "sources": [],
            "answer_origin": "operational_live",
        }

        formatted = add_contextual_response_emojis(payload, "mares e meteorologia")

        self.assertIn("🌕 Marés para 15/04/2026", formatted["answer"])
        self.assertIn("🌦️ Condições meteorológicas atuais", formatted["answer"])

    def test_llm_with_live_sources_gets_prefix_without_changing_normal_rag(self) -> None:
        live_payload = {
            "answer": "A previsão indica vento fraco durante a janela pedida.",
            "sources": [{"retrieval_mode": "live_planner"}],
            "answer_origin": "llm",
        }
        static_payload = {
            "answer": "A pilotagem é obrigatória nas condições previstas.",
            "sources": [{"retrieval_mode": "document_companion"}],
            "answer_origin": "llm",
        }

        live_formatted = add_contextual_response_emojis(live_payload, "Como está a meteorologia?")
        static_formatted = add_contextual_response_emojis(static_payload, "O que é a pilotagem?")

        self.assertTrue(live_formatted["answer"].startswith("🌦️ "))
        self.assertEqual(static_formatted["answer"], static_payload["answer"])

    def test_llm_with_operational_archive_source_gets_archive_prefix(self) -> None:
        payload = {
            "answer": "Foram encontradas duas manobras concluídas para esse navio.",
            "sources": [{"retrieval_mode": "operational_archive"}],
            "answer_origin": "llm",
        }

        formatted = add_contextual_response_emojis(payload, "mostra o arquivo de manobras")

        self.assertTrue(formatted["answer"].startswith("📂 "))

    def test_static_slash_rule_is_not_decorated(self) -> None:
        payload = {
            "answer": "IT-015 — Fundeadouros: define as zonas de fundeio.",
            "sources": [],
            "answer_origin": "slash_rule",
        }

        formatted = add_contextual_response_emojis(payload, "/regra 015")

        self.assertEqual(formatted["answer"], payload["answer"])


if __name__ == "__main__":
    unittest.main()
