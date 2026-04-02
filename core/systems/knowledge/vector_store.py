"""Pluggable vector store backends for RAG and semantic memory."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def keyword_score(query: str, content: str) -> float:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0
    content_tokens = _tokenize(content)
    if not content_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens)
    return overlap / max(len(query_tokens), 1)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(v * v for v in left))
    right_norm = math.sqrt(sum(v * v for v in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


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
    """A single search result with relevance scores."""

    document: Document
    score: float
    collection: str = ""
    vector_score: float | None = None
    keyword_score: float | None = None


@runtime_checkable
class VectorStoreBackend(Protocol):
    """Protocol for vector store implementations."""

    def add_documents(self, docs: list[Document], collection: str = "default") -> list[str]:
        """Add documents and return their IDs."""
        ...

    def search(self, query: str, collection: str = "default", top_k: int = 5) -> list[SearchResult]:
        """Semantic or hybrid search, sorted by relevance."""
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

    def get_documents(self, collection: str = "default") -> list[Document]:
        """Return stored documents for hybrid search and inspection."""
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
                vector_score = 1.0 / (1.0 + distance)
                search_results.append(
                    SearchResult(
                        document=Document(page_content=content, metadata=metadata, doc_id=doc_id),
                        score=vector_score,
                        vector_score=vector_score,
                        keyword_score=keyword_score(query, content),
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

    def get_documents(self, collection: str = "default") -> list[Document]:
        col = self._get_collection(collection)
        payload = col.get()
        ids = payload.get("ids", [])
        docs = payload.get("documents", [])
        metadatas = payload.get("metadatas", [])
        documents: list[Document] = []
        for index, doc_id in enumerate(ids):
            documents.append(
                Document(
                    page_content=docs[index] if index < len(docs) else "",
                    metadata=metadatas[index] if index < len(metadatas) and isinstance(metadatas[index], dict) else {},
                    doc_id=doc_id,
                )
            )
        return documents


class InMemoryVectorStore:
    """Simple in-memory vector store for testing and lightweight local use."""

    def __init__(self, embedding_function: Any | None = None):
        self._store: dict[str, list[tuple[str, Document]]] = {}
        self._embeddings: dict[str, dict[str, list[float]]] = {}
        self._embedding_function = embedding_function

    def add_documents(self, docs: list[Document], collection: str = "default") -> list[str]:
        if collection not in self._store:
            self._store[collection] = []
        if collection not in self._embeddings:
            self._embeddings[collection] = {}
        ids = []
        embeddings = self._embed_documents_if_available(docs)
        for index, doc in enumerate(docs):
            doc_id = doc.doc_id or hashlib.sha256(doc.page_content.encode()).hexdigest()[:16]
            self._store[collection] = [(did, d) for did, d in self._store[collection] if did != doc_id]
            self._store[collection].append((doc_id, doc))
            if embeddings is not None and index < len(embeddings):
                self._embeddings[collection][doc_id] = embeddings[index]
            ids.append(doc_id)
        return ids

    def search(self, query: str, collection: str = "default", top_k: int = 5) -> list[SearchResult]:
        entries = self._store.get(collection, [])
        if not entries:
            return []
        query_embedding = self._embed_query_if_available(query)
        scored = []
        for doc_id, doc in entries:
            text_score = keyword_score(query, doc.page_content)
            vector_score = None
            if query_embedding is not None:
                stored = self._embeddings.get(collection, {}).get(doc_id)
                if stored is not None:
                    vector_score = cosine_similarity(query_embedding, stored)
            score = vector_score if vector_score is not None else text_score
            scored.append((score, vector_score, text_score, doc_id, doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            SearchResult(
                document=Document(page_content=doc.page_content, metadata=doc.metadata, doc_id=did),
                score=score,
                vector_score=vector_score,
                keyword_score=text_score,
                collection=collection,
            )
            for score, vector_score, text_score, did, doc in scored[:top_k]
        ]

    def delete(self, ids: list[str], collection: str = "default") -> int:
        if collection not in self._store:
            return 0
        id_set = set(ids)
        before = len(self._store[collection])
        self._store[collection] = [(did, d) for did, d in self._store[collection] if did not in id_set]
        for doc_id in id_set:
            self._embeddings.get(collection, {}).pop(doc_id, None)
        return before - len(self._store[collection])

    def delete_collection(self, collection: str) -> bool:
        deleted = False
        if collection in self._store:
            del self._store[collection]
            deleted = True
        if collection in self._embeddings:
            del self._embeddings[collection]
            deleted = True
        return deleted

    def list_collections(self) -> list[str]:
        return list(self._store.keys())

    def count(self, collection: str = "default") -> int:
        return len(self._store.get(collection, []))

    def get_documents(self, collection: str = "default") -> list[Document]:
        return [
            Document(page_content=doc.page_content, metadata=doc.metadata, doc_id=doc_id)
            for doc_id, doc in self._store.get(collection, [])
        ]

    def _embed_documents_if_available(self, docs: list[Document]) -> list[list[float]] | None:
        if not self._embedding_function or not hasattr(self._embedding_function, "embed_documents"):
            return None
        return self._embedding_function.embed_documents([doc.page_content for doc in docs])

    def _embed_query_if_available(self, query: str) -> list[float] | None:
        if not self._embedding_function or not hasattr(self._embedding_function, "embed_query"):
            return None
        return list(self._embedding_function.embed_query(query))


def create_vector_store(
    backend: str = "chroma",
    persist_dir: str = "workspace/vector_store",
    embedding_function: Any | None = None,
) -> VectorStoreBackend:
    """Factory for vector store backends."""
    if backend == "memory":
        return InMemoryVectorStore(embedding_function=embedding_function)
    if backend == "chroma":
        return ChromaVectorStore(persist_dir=persist_dir, embedding_function=embedding_function)
    if backend == "sqlite-vec":
        from .sqlite_vec_store import SQLiteVecVectorStore

        return SQLiteVecVectorStore(persist_dir=persist_dir, embedding_function=embedding_function)
    if backend == "faiss":
        from .faiss_store import FAISSVectorStore

        return FAISSVectorStore(persist_dir=persist_dir, embedding_function=embedding_function)
    raise ValueError(f"Unknown vector store backend: {backend!r}. Supported: chroma, memory, sqlite-vec, faiss")


def dump_metadata(metadata: dict[str, Any]) -> str:
    """Serialize document metadata into a stable JSON string."""
    return json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)


def load_metadata(raw: str | bytes | None) -> dict[str, Any]:
    """Deserialize metadata from persisted storage."""
    if raw is None:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}
