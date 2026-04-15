import unittest

from integrations.llm_provider import OpenAICompatibleProvider


class _Message:
    content = "ok"


class _Choice:
    message = _Message()


class _CompletionResponse:
    choices = [_Choice()]
    usage = None


class _Completions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _CompletionResponse()


class _Chat:
    def __init__(self) -> None:
        self.completions = _Completions()


class _Client:
    def __init__(self) -> None:
        self.chat = _Chat()


class OpenAICompatibleProviderTests(unittest.TestCase):
    def test_reasoning_chat_model_uses_max_completion_tokens_without_temperature(self) -> None:
        provider = OpenAICompatibleProvider(api_key="", provider_label="OpenAI")
        client = _Client()
        provider.client = client

        result = provider.generate("pergunta", model="openai/o4-mini", max_tokens=1234, temperature=0.1)

        self.assertEqual(result.text, "ok")
        payload = client.chat.completions.calls[0]
        self.assertEqual(payload["model"], "openai/o4-mini")
        self.assertEqual(payload["max_completion_tokens"], 1234)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("temperature", payload)

    def test_regular_chat_model_keeps_max_tokens_and_temperature(self) -> None:
        provider = OpenAICompatibleProvider(api_key="", provider_label="OpenAI")
        client = _Client()
        provider.client = client

        result = provider.generate("pergunta", model="gpt-4.1-mini", max_tokens=1234, temperature=0.1)

        self.assertEqual(result.text, "ok")
        payload = client.chat.completions.calls[0]
        self.assertEqual(payload["model"], "gpt-4.1-mini")
        self.assertEqual(payload["max_tokens"], 1234)
        self.assertEqual(payload["temperature"], 0.1)
        self.assertNotIn("max_completion_tokens", payload)


if __name__ == "__main__":
    unittest.main()
