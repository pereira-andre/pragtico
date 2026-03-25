"""LLM provider abstraction layer.

Supports multiple backends through a unified interface:
- Google Gemini (via google-genai SDK)
- OpenRouter (via OpenAI-compatible API)
- Any OpenAI-compatible endpoint (DeepSeek, local, etc.)

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
        if api_key:
            try:
                from google import genai
                self.client = genai.Client(api_key=api_key)
            except Exception as exc:
                logger.warning("Failed to initialize Gemini client: %s", exc)

    @property
    def is_available(self) -> bool:
        return self.client is not None

    def generate(self, prompt: str, model: str, **kwargs) -> GenerationResult:
        if not self.client:
            raise RuntimeError("Gemini API key not configured.")
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
        )
        text = getattr(response, "text", "") or ""
        return GenerationResult(text=text, model=model, usage={})

    def embed(self, texts: List[str], model: str, **kwargs) -> EmbeddingResult:
        if not self.client:
            raise RuntimeError("Gemini API key not configured.")
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
            except ImportError:
                logger.warning(
                    "openai package not installed. Install with: pip install openai"
                )
            except Exception as exc:
                logger.warning("Failed to initialize %s client: %s", provider_label, exc)

    @property
    def is_available(self) -> bool:
        return self.client is not None

    def generate(self, prompt: str, model: str, **kwargs) -> GenerationResult:
        if not self.client:
            raise RuntimeError(f"{self.provider_label} API key not configured.")
        response = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=kwargs.get("max_tokens", 4096),
            temperature=kwargs.get("temperature", 0.3),
        )
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
            raise RuntimeError(f"{self.provider_label} API key not configured.")
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

    # Fallback: treat as OpenAI-compatible with custom base URL
    key = api_key or os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    return OpenAICompatibleProvider(
        api_key=key,
        base_url=base_url,
        provider_label=provider.title(),
    )


# ---------------------------------------------------------------------------
# Local Embedding Provider (sentence-transformers — runs on CPU, zero API cost)
# ---------------------------------------------------------------------------

class LocalEmbeddingProvider:
    """Local embedding provider using sentence-transformers.

    Runs entirely on CPU/GPU. No API calls, no quota limits.
    Supports any HuggingFace model, default: BAAI/bge-m3 (1024 dim, 100+ languages).
    """

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        self.model_name = model_name
        self.model = None
        self._dim = None
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading local embedding model: %s (may take a minute on first run)...", model_name)
            self.model = SentenceTransformer(model_name)
            self._dim = self.model.get_sentence_embedding_dimension()
            logger.info("Local embedding model loaded: %s (%d dimensions)", model_name, self._dim)
        except ImportError:
            logger.warning(
                "sentence-transformers not installed. Install with: "
                "pip install sentence-transformers"
            )
        except Exception as exc:
            logger.warning("Failed to load local embedding model %s: %s", model_name, exc)

    @property
    def is_available(self) -> bool:
        return self.model is not None

    @property
    def dimensions(self) -> int:
        return self._dim or 1024

    def embed(self, texts: List[str], **kwargs) -> EmbeddingResult:
        """Generate embeddings locally. No API calls."""
        if not self.model:
            raise RuntimeError(
                f"Local embedding model {self.model_name} not loaded. "
                "Install: pip install sentence-transformers"
            )
        vectors = self.model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()
        return EmbeddingResult(vectors=vectors, model=self.model_name)


def create_embedding_provider(
    model_name: Optional[str] = None,
) -> Optional[LocalEmbeddingProvider]:
    """Create a local embedding provider if sentence-transformers is available.

    Parameters:
        model_name: HuggingFace model name. Defaults to EMBEDDING_LOCAL_MODEL env var,
                    then "BAAI/bge-m3".

    Returns:
        LocalEmbeddingProvider or None if not available.
    """
    model = model_name or os.getenv("EMBEDDING_LOCAL_MODEL", "BAAI/bge-m3")
    provider = LocalEmbeddingProvider(model_name=model)
    if provider.is_available:
        return provider
    return None

