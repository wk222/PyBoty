"""Embedding provider resolver for RAG and semantic memory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx


class EmbeddingError(Exception):
    """Raised when embedding resolution or execution fails."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Common embedding provider interface."""

    model: str
    provider: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        ...

    def __call__(self, input: list[str]) -> list[list[float]]:
        """Chroma-compatible callable surface."""
        ...


@dataclass
class BaseEmbeddingProvider:
    """Base class for embedding providers."""

    model: str
    provider: str
    batch_size: int = 32

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        return vectors[0] if vectors else []

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self.embed_documents(list(input))


@dataclass
class SentenceTransformerEmbeddingProvider(BaseEmbeddingProvider):
    provider: str = "sentence-transformers"
    _model_instance: Any | None = None

    def _get_model(self) -> Any:
        if self._model_instance is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError(
                    "sentence-transformers provider requires `sentence-transformers` to be installed"
                ) from exc
            self._model_instance = SentenceTransformer(self.model)
        return self._model_instance

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        vectors = model.encode(texts, convert_to_numpy=False, normalize_embeddings=True)
        return [list(map(float, vector)) for vector in vectors]


@dataclass
class RemoteEmbeddingProvider(BaseEmbeddingProvider):
    api_base: str = ""
    api_key: str | None = None
    timeout: float = 20.0
    default_headers: dict[str, str] | None = None

    def _post_json(
        self,
        url: str,
        *,
        json_payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged_headers = {"content-type": "application/json"}
        merged_headers.update(self.default_headers or {})
        merged_headers.update(headers or {})
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, json=json_payload, headers=merged_headers, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise EmbeddingError(f"{self.provider} embeddings returned invalid payload type")
        return payload

    def _split_batches(self, texts: list[str]) -> list[list[str]]:
        if not texts:
            return []
        step = max(int(self.batch_size or 1), 1)
        return [texts[index : index + step] for index in range(0, len(texts), step)]


@dataclass
class OpenAIEmbeddingProvider(RemoteEmbeddingProvider):
    provider: str = "openai"
    api_base: str = "https://api.openai.com/v1"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {}
        resolved_key = self.api_key or os.getenv("OPENAI_API_KEY")
        if resolved_key:
            headers["authorization"] = f"Bearer {resolved_key}"
        vectors: list[list[float]] = []
        for batch in self._split_batches(texts):
            payload = self._post_json(
                f"{self.api_base.rstrip('/')}/embeddings",
                json_payload={"model": self.model, "input": batch},
                headers=headers,
            )
            data = payload.get("data")
            if not isinstance(data, list):
                raise EmbeddingError(f"openai embeddings failed: {payload}")
            vectors.extend([list(map(float, item.get("embedding", []))) for item in data])
        return vectors


@dataclass
class VoyageEmbeddingProvider(RemoteEmbeddingProvider):
    provider: str = "voyage"
    api_base: str = "https://api.voyageai.com/v1"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resolved_key = self.api_key or os.getenv("VOYAGE_API_KEY")
        if not resolved_key:
            raise EmbeddingError("Voyage embeddings require api_key or VOYAGE_API_KEY")
        vectors: list[list[float]] = []
        for batch in self._split_batches(texts):
            payload = self._post_json(
                f"{self.api_base.rstrip('/')}/embeddings",
                json_payload={"model": self.model, "input": batch},
                headers={"authorization": f"Bearer {resolved_key}"},
            )
            data = payload.get("data")
            if not isinstance(data, list):
                raise EmbeddingError(f"voyage embeddings failed: {payload}")
            vectors.extend([list(map(float, item.get("embedding", []))) for item in data])
        return vectors


@dataclass
class OllamaEmbeddingProvider(RemoteEmbeddingProvider):
    provider: str = "ollama"
    api_base: str = "http://127.0.0.1:11434"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            payload = self._post_json(
                f"{self.api_base.rstrip('/')}/api/embeddings",
                json_payload={"model": self.model, "prompt": text},
            )
            embedding = payload.get("embedding", [])
            vectors.append(list(map(float, embedding)))
        return vectors


@dataclass
class GeminiEmbeddingProvider(RemoteEmbeddingProvider):
    provider: str = "gemini"
    api_base: str = "https://generativelanguage.googleapis.com/v1beta"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resolved_key = self.api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not resolved_key:
            raise EmbeddingError("Gemini embeddings require api_key or GOOGLE_API_KEY / GEMINI_API_KEY")
        vectors: list[list[float]] = []
        for batch in self._split_batches(texts):
            payload = self._post_json(
                f"{self.api_base.rstrip('/')}/models/{self.model}:batchEmbedContents",
                json_payload={
                    "requests": [
                        {
                            "model": f"models/{self.model}",
                            "content": {"parts": [{"text": text}]},
                        }
                        for text in batch
                    ]
                },
                params={"key": resolved_key},
            )
            embeddings = payload.get("embeddings")
            if not isinstance(embeddings, list):
                raise EmbeddingError(f"gemini embeddings failed: {payload}")
            vectors.extend([list(map(float, item.get("values", []))) for item in embeddings])
        return vectors


def resolve_embeddings(
    spec: str | dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
) -> EmbeddingProvider | None:
    """Resolve an embedding specification into a provider instance."""
    if spec is None or spec == "default":
        return None

    if isinstance(spec, dict):
        provider = str(spec.get("provider", "openai"))
        model = str(spec.get("model", "text-embedding-3-small"))
        resolved_key = spec.get("api_key", api_key)
        base_url = spec.get("api_base") or spec.get("base_url")
        batch_size = int(spec.get("batch_size", 32))
        return _build_embedding_provider(
            provider,
            model,
            api_key=resolved_key,
            base_url=base_url,
            batch_size=batch_size,
        )

    if isinstance(spec, str):
        if ":" in spec:
            provider, _, model = spec.partition(":")
            return _build_embedding_provider(provider.strip(), model.strip(), api_key=api_key)
        return _build_embedding_provider("openai", spec, api_key=api_key)

    raise EmbeddingError(f"Unsupported embedding spec type: {type(spec).__name__}")


def _build_embedding_provider(
    provider: str,
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    batch_size: int = 32,
) -> EmbeddingProvider:
    provider = provider.lower()
    kwargs = {"model": model, "api_key": api_key, "batch_size": batch_size}
    if provider == "openai":
        return OpenAIEmbeddingProvider(api_base=base_url or OpenAIEmbeddingProvider.api_base, **kwargs)
    if provider in {"sentence-transformers", "st", "local", "huggingface"}:
        return SentenceTransformerEmbeddingProvider(model=model, batch_size=batch_size)
    if provider == "ollama":
        return OllamaEmbeddingProvider(api_base=base_url or OllamaEmbeddingProvider.api_base, **kwargs)
    if provider == "voyage":
        return VoyageEmbeddingProvider(api_base=base_url or VoyageEmbeddingProvider.api_base, **kwargs)
    if provider == "gemini":
        return GeminiEmbeddingProvider(api_base=base_url or GeminiEmbeddingProvider.api_base, **kwargs)

    raise EmbeddingError(
        f"Unknown embedding provider: {provider!r}. Supported: openai, sentence-transformers, ollama, voyage, gemini"
    )
