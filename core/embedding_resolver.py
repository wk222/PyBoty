"""Embedding model resolver for RAG vector storage.

Resolves embedding specifications into ChromaDB-compatible embedding functions.
Supports: OpenAI, sentence-transformers (local), and custom endpoints.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Raised when embedding resolution or execution fails."""


def resolve_embeddings(
    spec: str | dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
) -> Any:
    """Resolve an embedding specification into a ChromaDB embedding function.

    Args:
        spec: Embedding specification. Examples:
            - None or "default" -> ChromaDB default (all-MiniLM-L6-v2)
            - "openai:text-embedding-3-small"
            - "sentence-transformers:all-MiniLM-L6-v2"
            - {"provider": "openai", "model": "text-embedding-3-small", "api_key": "..."}
        api_key: API key override.

    Returns:
        A ChromaDB-compatible embedding function, or None for ChromaDB defaults.
    """
    if spec is None or spec == "default":
        return None

    if isinstance(spec, dict):
        provider = spec.get("provider", "openai")
        model = spec.get("model", "text-embedding-3-small")
        resolved_key = spec.get("api_key", api_key)
        base_url = spec.get("api_base") or spec.get("base_url")
        return _build_embedding_function(provider, model, api_key=resolved_key, base_url=base_url)

    if isinstance(spec, str):
        if ":" in spec:
            provider, _, model = spec.partition(":")
            return _build_embedding_function(provider.strip(), model.strip(), api_key=api_key)
        return _build_embedding_function("openai", spec, api_key=api_key)

    raise EmbeddingError(f"Unsupported embedding spec type: {type(spec).__name__}")


def _build_embedding_function(
    provider: str,
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Any:
    """Build a ChromaDB embedding function for a given provider."""
    provider = provider.lower()

    if provider == "openai":
        return _build_openai_embeddings(model, api_key=api_key, base_url=base_url)
    if provider in ("sentence-transformers", "st", "local"):
        return _build_sentence_transformer_embeddings(model)
    if provider == "huggingface":
        return _build_sentence_transformer_embeddings(model)

    raise EmbeddingError(
        f"Unknown embedding provider: {provider!r}. Supported: openai, sentence-transformers, huggingface"
    )


def _build_openai_embeddings(
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Any:
    """Build OpenAI embedding function for ChromaDB."""
    try:
        from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
    except ImportError:
        raise EmbeddingError("ChromaDB is required for OpenAI embeddings. Install with: pip install chromadb") from None

    kwargs: dict[str, Any] = {"model_name": model}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["api_base"] = base_url

    return OpenAIEmbeddingFunction(**kwargs)


def _build_sentence_transformer_embeddings(model: str) -> Any:
    """Build sentence-transformers embedding function for ChromaDB."""
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError:
        raise EmbeddingError("ChromaDB is required. Install with: pip install chromadb") from None

    return SentenceTransformerEmbeddingFunction(model_name=model)
