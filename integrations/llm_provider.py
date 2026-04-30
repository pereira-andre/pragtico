"""LLM provider abstraction layer.

Supports multiple backends through a unified interface:
- Google Gemini (via google-genai SDK)
- OpenRouter (via OpenAI-compatible API)
- Any OpenAI-compatible endpoint such as DeepSeek

The provider is selected via the LLM_PROVIDER environment variable:
- "gemini" (default) — uses google-genai SDK
- "openrouter" — uses OpenAI SDK pointed at OpenRouter
- "openai" — uses OpenAI SDK directly
"""

from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


def _is_reasoning_chat_model(model: str) -> bool:
    model_name = (model or "").split("/")[-1].strip().lower()
    return model_name.startswith(("o1", "o3", "o4"))


@dataclass
class GenerationResult:
    """Unified generation result across providers."""
    text: str
    model: str
    usage: dict


@dataclass
class EmbeddingResult:
    """Unified embedding result across providers."""
    vectors: List[List[float]]
    model: str


class BaseLLMProvider(ABC):
    """Abstract base for LLM providers."""

    provider_name: str = "base"

    @abstractmethod
    def generate(self, prompt: str, model: str, **kwargs) -> GenerationResult:
        """Generate text from a prompt.

        Parameters:
            prompt: The input prompt text.
            model: Model identifier.

        Returns:
            GenerationResult with the generated text.
        """
        raise NotImplementedError

    @abstractmethod
    def embed(self, texts: List[str], model: str, **kwargs) -> EmbeddingResult:
        """Generate embeddings for a list of texts.

        Parameters:
            texts: List of texts to embed.
            model: Embedding model identifier.

        Returns:
            EmbeddingResult with vectors.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is configured and available."""
        raise NotImplementedError


class GeminiProvider(BaseLLMProvider):
    """Google Gemini provider using google-genai SDK."""

    provider_name = "gemini"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.client = None
        self.unavailable_reason = "Gemini API key not configured."
        if api_key:
            try:
                from google import genai
                self.client = genai.Client(api_key=api_key)
                self.unavailable_reason = ""
            except Exception as exc:
                self.unavailable_reason = f"Failed to initialize Gemini client: {exc}"
                logger.warning("Failed to initialize Gemini client: %s", exc)

    @property
    def is_available(self) -> bool:
        return self.client is not None

    def generate(self, prompt: str, model: str, **kwargs) -> GenerationResult:
        if not self.client:
            raise RuntimeError(self.unavailable_reason or "Gemini API key not configured.")
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
        )
        text = getattr(response, "text", "") or ""
        return GenerationResult(text=text, model=model, usage={})

    def embed(self, texts: List[str], model: str, **kwargs) -> EmbeddingResult:
        if not self.client:
            raise RuntimeError(self.unavailable_reason or "Gemini API key not configured.")
        result = self.client.models.embed_content(
            model=model,
            contents=texts,
        )
        embeddings = getattr(result, "embeddings", result)
        vectors = []
        for item in embeddings:
            if isinstance(item, dict):
                vectors.append(list(item.get("values", [])))
            elif hasattr(item, "values"):
                vectors.append(list(item.values))
            else:
                vectors.append(list(getattr(item, "embedding", [])))
        return EmbeddingResult(vectors=vectors, model=model)


class OpenAICompatibleProvider(BaseLLMProvider):
    """OpenAI-compatible provider (works with OpenRouter, DeepSeek, etc.)."""

    provider_name = "openai_compatible"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        provider_label: str = "OpenRouter",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.provider_label = provider_label
        self.provider_name = provider_label.lower().replace(" ", "_")
        self.client = None
        self.unavailable_reason = f"{provider_label} API key not configured."
        if api_key:
            try:
                from openai import OpenAI

                # OpenRouter requires extra headers for authentication
                extra_headers = {}
                if "openrouter" in base_url.lower():
                    extra_headers = {
                        "HTTP-Referer": os.getenv("OPENROUTER_REFERER", "https://pragtico.up.railway.app"),
                        "X-Title": os.getenv("OPENROUTER_TITLE", "PRAGtico"),
                    }

                self.client = OpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    default_headers=extra_headers if extra_headers else None,
                )
                self.unavailable_reason = ""
            except ImportError:
                self.unavailable_reason = (
                    f"{provider_label} SDK unavailable: openai package not installed."
                )
                logger.warning(
                    "openai package not installed. Install with: pip install openai"
                )
            except Exception as exc:
                self.unavailable_reason = f"Failed to initialize {provider_label} client: {exc}"
                logger.warning("Failed to initialize %s client: %s", provider_label, exc)

    @property
    def is_available(self) -> bool:
        return self.client is not None

    def generate(self, prompt: str, model: str, **kwargs) -> GenerationResult:
        if not self.client:
            raise RuntimeError(
                self.unavailable_reason or f"{self.provider_label} API key not configured."
            )
        request_payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if _is_reasoning_chat_model(model):
            request_payload["max_completion_tokens"] = kwargs.get(
                "max_completion_tokens",
                kwargs.get("max_tokens", 4096),
            )
        else:
            request_payload["max_tokens"] = kwargs.get("max_tokens", 4096)
            request_payload["temperature"] = kwargs.get("temperature", 0.3)
        response = self.client.chat.completions.create(**request_payload)
        text = response.choices[0].message.content if response.choices else ""
        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                "total_tokens": getattr(response.usage, "total_tokens", 0),
            }
        return GenerationResult(text=text or "", model=model, usage=usage)

    def embed(self, texts: List[str], model: str, **kwargs) -> EmbeddingResult:
        if not self.client:
            raise RuntimeError(
                self.unavailable_reason or f"{self.provider_label} API key not configured."
            )
        response = self.client.embeddings.create(
            model=model,
            input=texts,
        )
        vectors = [item.embedding for item in response.data]
        return EmbeddingResult(vectors=vectors, model=model)


def create_llm_provider(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
) -> BaseLLMProvider:
    """Factory function to create the appropriate LLM provider.

    Parameters:
        provider: Provider name ("gemini", "openrouter", "openai", "deepseek").
                  Defaults to LLM_PROVIDER env var, then "gemini".
        api_key: API key override. Defaults to provider-specific env var.

    Returns:
        Configured LLM provider instance.
    """
    provider = (provider or os.getenv("LLM_PROVIDER", "gemini")).strip().lower()

    if provider == "gemini":
        key = api_key or os.getenv("GEMINI_API_KEY", "")
        return GeminiProvider(api_key=key)

    if provider == "openrouter":
        key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        return OpenAICompatibleProvider(
            api_key=key,
            base_url=base_url,
            provider_label="OpenRouter",
        )

    if provider in ("openai", "deepseek"):
        label_map = {"openai": "OpenAI", "deepseek": "DeepSeek"}
        url_map = {
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com",
        }
        key_env_map = {"openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
        key = api_key or os.getenv(key_env_map[provider], "")
        return OpenAICompatibleProvider(
            api_key=key,
            base_url=url_map[provider],
            provider_label=label_map[provider],
        )

    # Fallback: treat as OpenAI-compatible with custom base URL.
    key = api_key or os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    return OpenAICompatibleProvider(
        api_key=key,
        base_url=base_url,
        provider_label=provider.title(),
    )
