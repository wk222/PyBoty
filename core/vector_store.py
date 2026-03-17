"""Pluggable vector store backend for RAG and semantic memory.

Provides a protocol-based abstraction over vector databases.
Default implementation uses ChromaDB (embedded, zero external services).
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class Document:
    """A text chunk with metadata for vector storage."""

    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    doc_id: str | None = None

    def __post_init__(self):
        if self.doc_id is None:
            content_hash = hashlib.sha256(self.page_content.encode()).hexdigest()[:16]
            self.doc_id = f"doc_{content_hash}"


@dataclass
class SearchResult:
    """A single search result with relevance score."""

    document: Document
    score: float
    collection: str = ""


@runtime_checkable
class VectorStoreBackend(Protocol):
    """Protocol for vector store implementations."""

    def add_documents(self, docs: list[Document], collection: str = "default") -> list[str]:
        """Add documents and return their IDs."""
        ...

    def search(self, query: str, collection: str = "default", top_k: int = 5) -> list[SearchResult]:
        """Semantic search, returns results sorted by relevance."""
        ...

    def delete(self, ids: list[str], collection: str = "default") -> int:
        """Delete documents by ID, return count deleted."""
        ...

    def list_collections(self) -> list[str]:
        """List all collection names."""
        ...

    def delete_collection(self, collection: str) -> bool:
        """Delete an entire collection."""
        ...

    def count(self, collection: str = "default") -> int:
        """Return document count in a collection."""
        ...


class ChromaVectorStore:
    """ChromaDB-backed vector store (embedded mode, no external services)."""

    def __init__(
        self,
        persist_dir: str,
        embedding_function: Any | None = None,
    ):
        self._persist_dir = persist_dir
        self._embedding_function = embedding_function
        self._client = None
        self._collections: dict[str, Any] = {}

    def _get_client(self):
        if self._client is None:
            try:
                import chromadb
            except ImportError:
                raise ImportError(
                    "ChromaDB is required for vector storage. Install with: pip install chromadb"
                ) from None
            os.makedirs(self._persist_dir, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self._persist_dir)
        return self._client

    def _get_collection(self, name: str):
        if name not in self._collections:
            client = self._get_client()
            kwargs: dict[str, Any] = {"name": name}
            if self._embedding_function is not None:
                kwargs["embedding_function"] = self._embedding_function
            self._collections[name] = client.get_or_create_collection(**kwargs)
        return self._collections[name]

    def add_documents(self, docs: list[Document], collection: str = "default") -> list[str]:
        col = self._get_collection(collection)
        ids = []
        documents = []
        metadatas = []
        for doc in docs:
            doc_id = doc.doc_id or hashlib.sha256(doc.page_content.encode()).hexdigest()[:16]
            ids.append(doc_id)
            documents.append(doc.page_content)
            metadatas.append(doc.metadata or {})

        col.upsert(ids=ids, documents=documents, metadatas=metadatas)
        logger.info("Added %d documents to collection %r", len(docs), collection)
        return ids

    def search(self, query: str, collection: str = "default", top_k: int = 5) -> list[SearchResult]:
        col = self._get_collection(collection)
        if col.count() == 0:
            return []

        effective_k = min(top_k, col.count())
        results = col.query(query_texts=[query], n_results=effective_k)

        search_results = []
        if results and results.get("ids"):
            for i, doc_id in enumerate(results["ids"][0]):
                content = results["documents"][0][i] if results.get("documents") else ""
                metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
                distance = results["distances"][0][i] if results.get("distances") else 0.0
                score = 1.0 / (1.0 + distance)
                search_results.append(
                    SearchResult(
                        document=Document(page_content=content, metadata=metadata, doc_id=doc_id),
                        score=score,
                        collection=collection,
                    )
                )
        return search_results

    def delete(self, ids: list[str], collection: str = "default") -> int:
        col = self._get_collection(collection)
        existing = set(col.get(ids=ids)["ids"])
        if existing:
            col.delete(ids=list(existing))
        return len(existing)

    def delete_collection(self, collection: str) -> bool:
        try:
            client = self._get_client()
            client.delete_collection(collection)
            self._collections.pop(collection, None)
            return True
        except Exception:
            return False

    def list_collections(self) -> list[str]:
        client = self._get_client()
        return [c.name for c in client.list_collections()]

    def count(self, collection: str = "default") -> int:
        col = self._get_collection(collection)
        return col.count()


class InMemoryVectorStore:
    """Simple in-memory vector store for testing (no dependencies)."""

    def __init__(self):
        self._store: dict[str, list[tuple[str, Document]]] = {}

    def add_documents(self, docs: list[Document], collection: str = "default") -> list[str]:
        if collection not in self._store:
            self._store[collection] = []
        ids = []
        for doc in docs:
            doc_id = doc.doc_id or hashlib.sha256(doc.page_content.encode()).hexdigest()[:16]
            self._store[collection] = [(did, d) for did, d in self._store[collection] if did != doc_id]
            self._store[collection].append((doc_id, doc))
            ids.append(doc_id)
        return ids

    def search(self, query: str, collection: str = "default", top_k: int = 5) -> list[SearchResult]:
        entries = self._store.get(collection, [])
        query_lower = query.lower()
        scored = []
        for doc_id, doc in entries:
            content_lower = doc.page_content.lower()
            words_matched = sum(1 for w in query_lower.split() if w in content_lower)
            score = words_matched / max(len(query_lower.split()), 1)
            scored.append((score, doc_id, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            SearchResult(
                document=Document(page_content=doc.page_content, metadata=doc.metadata, doc_id=did),
                score=score,
                collection=collection,
            )
            for score, did, doc in scored[:top_k]
        ]

    def delete(self, ids: list[str], collection: str = "default") -> int:
        if collection not in self._store:
            return 0
        before = len(self._store[collection])
        self._store[collection] = [(did, d) for did, d in self._store[collection] if did not in set(ids)]
        return before - len(self._store[collection])

    def delete_collection(self, collection: str) -> bool:
        if collection in self._store:
            del self._store[collection]
            return True
        return False

    def list_collections(self) -> list[str]:
        return list(self._store.keys())

    def count(self, collection: str = "default") -> int:
        return len(self._store.get(collection, []))


def create_vector_store(
    backend: str = "chroma",
    persist_dir: str = "workspace/vector_store",
    embedding_function: Any | None = None,
) -> VectorStoreBackend:
    """Factory for vector store backends."""
    if backend == "memory":
        return InMemoryVectorStore()
    if backend == "chroma":
        return ChromaVectorStore(persist_dir=persist_dir, embedding_function=embedding_function)
    raise ValueError(f"Unknown vector store backend: {backend!r}. Supported: chroma, memory")
