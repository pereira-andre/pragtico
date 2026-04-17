import unittest

from domain.error_catalog import error_payload, error_ref, user_error_message


class ErrorCatalogTests(unittest.TestCase):
    def test_error_payload_exposes_searchable_code_and_reference(self) -> None:
        payload = error_payload("EMPTY_QUESTION")

        self.assertEqual(payload["error_code"], 1001)
        self.assertEqual(payload["error_ref"], "#ERR-1001")
        self.assertEqual(payload["error"], "Pergunta vazia.")

    def test_unknown_error_falls_back_to_internal_code(self) -> None:
        self.assertEqual(error_ref("DOES_NOT_EXIST"), "#ERR-9000")

    def test_whatsapp_user_message_contains_visible_error_reference(self) -> None:
        message = user_error_message("CHAT_RUNTIME_FAILED", channel="whatsapp")

        self.assertIn("*#ERR-9001*", message)
        self.assertIn("Contacta o suporte", message)


if __name__ == "__main__":
    unittest.main()
