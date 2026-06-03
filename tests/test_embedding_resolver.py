from __future__ import annotations

import core.systems.knowledge.embedding_resolver as embedding_resolver
from core.systems.knowledge.embedding_resolver import (
    GeminiEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
    VoyageEmbeddingProvider,
    resolve_embeddings,
)


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _DummyClient:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None, headers=None, params=None):  # noqa: ANN001,ARG002
        return _DummyResponse(self.payload)


def test_resolve_embeddings_supports_new_remote_providers():
    assert isinstance(resolve_embeddings("ollama:nomic-embed-text"), OllamaEmbeddingProvider)
    assert isinstance(resolve_embeddings("voyage:voyage-3-lite", api_key="k"), VoyageEmbeddingProvider)
    assert isinstance(resolve_embeddings("gemini:text-embedding-004", api_key="k"), GeminiEmbeddingProvider)


def test_openai_embedding_provider_batches_remote_requests(monkeypatch):
    provider = resolve_embeddings(
        {"provider": "openai", "model": "text-embedding-3-small", "api_key": "key", "batch_size": 2}
    )
    assert isinstance(provider, OpenAIEmbeddingProvider)

    monkeypatch.setattr(
        embedding_resolver.httpx,
        "Client",
        lambda timeout=20.0: _DummyClient({"data": [{"embedding": [1.0, 0.0]}, {"embedding": [0.0, 1.0]}]}),
    )

    vectors = provider.embed_documents(["alpha", "beta"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]


def test_gemini_embedding_provider_reads_values_payload(monkeypatch):
    provider = resolve_embeddings(
        {"provider": "gemini", "model": "text-embedding-004", "api_key": "key", "batch_size": 2}
    )
    assert isinstance(provider, GeminiEmbeddingProvider)

    monkeypatch.setattr(
        embedding_resolver.httpx,
        "Client",
        lambda timeout=20.0: _DummyClient({"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]}),
    )

    vectors = provider.embed_documents(["one", "two"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
