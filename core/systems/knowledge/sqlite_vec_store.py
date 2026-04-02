"""SQLite-backed lightweight vector store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .vector_store import (
    Document,
    SearchResult,
    VectorStoreBackend,
    cosine_similarity,
    dump_metadata,
    keyword_score,
    load_metadata,
)


class SQLiteVecVectorStore(VectorStoreBackend):
    """SQLite-backed vector store with optional local vector scoring."""

    def __init__(self, persist_dir: str, embedding_function: Any | None = None):
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._persist_dir / "sqlite_vec_store.sqlite3"
        self._embedding_function = embedding_function
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                collection TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT NOT NULL,
                embedding TEXT,
                PRIMARY KEY (collection, doc_id)
            );
            CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
            """
        )
        self._conn.commit()

    def add_documents(self, docs: list[Document], collection: str = "default") -> list[str]:
        ids: list[str] = []
        embeddings = self._embed_documents_if_available(docs)
        with self._conn:
            for index, doc in enumerate(docs):
                doc_id = doc.doc_id or ""
                embedding = embeddings[index] if embeddings is not None and index < len(embeddings) else None
                self._conn.execute(
                    """
                    INSERT INTO documents(collection, doc_id, content, metadata, embedding)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(collection, doc_id) DO UPDATE SET
                        content=excluded.content,
                        metadata=excluded.metadata,
                        embedding=excluded.embedding
                    """,
                    (
                        collection,
                        doc_id,
                        doc.page_content,
                        dump_metadata(doc.metadata),
                        json.dumps(embedding) if embedding is not None else None,
                    ),
                )
                ids.append(doc_id)
        return ids

    def search(self, query: str, collection: str = "default", top_k: int = 5) -> list[SearchResult]:
        rows = self._conn.execute(
            "SELECT doc_id, content, metadata, embedding FROM documents WHERE collection = ?",
            (collection,),
        ).fetchall()
        if not rows:
            return []
        query_embedding = self._embed_query_if_available(query)
        scored: list[SearchResult] = []
        for row in rows:
            text_score = keyword_score(query, row["content"])
            vector_score = None
            if query_embedding is not None and row["embedding"]:
                vector_score = cosine_similarity(query_embedding, list(map(float, json.loads(row["embedding"]))))
            score = vector_score if vector_score is not None else text_score
            scored.append(
                SearchResult(
                    document=Document(
                        page_content=row["content"],
                        metadata=load_metadata(row["metadata"]),
                        doc_id=row["doc_id"],
                    ),
                    score=score,
                    vector_score=vector_score,
                    keyword_score=text_score,
                    collection=collection,
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    def delete(self, ids: list[str], collection: str = "default") -> int:
        with self._conn:
            before = self.count(collection)
            self._conn.executemany(
                "DELETE FROM documents WHERE collection = ? AND doc_id = ?",
                [(collection, doc_id) for doc_id in ids],
            )
        return before - self.count(collection)

    def list_collections(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT collection FROM documents ORDER BY collection").fetchall()
        return [str(row[0]) for row in rows]

    def delete_collection(self, collection: str) -> bool:
        with self._conn:
            self._conn.execute("DELETE FROM documents WHERE collection = ?", (collection,))
        return True

    def count(self, collection: str = "default") -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM documents WHERE collection = ?", (collection,)).fetchone()
        return int(row[0]) if row else 0

    def get_documents(self, collection: str = "default") -> list[Document]:
        rows = self._conn.execute(
            "SELECT doc_id, content, metadata FROM documents WHERE collection = ? ORDER BY doc_id",
            (collection,),
        ).fetchall()
        return [
            Document(page_content=row["content"], metadata=load_metadata(row["metadata"]), doc_id=row["doc_id"])
            for row in rows
        ]

    def _embed_documents_if_available(self, docs: list[Document]) -> list[list[float]] | None:
        if not self._embedding_function or not hasattr(self._embedding_function, "embed_documents"):
            return None
        return self._embedding_function.embed_documents([doc.page_content for doc in docs])

    def _embed_query_if_available(self, query: str) -> list[float] | None:
        if not self._embedding_function or not hasattr(self._embedding_function, "embed_query"):
            return None
        return list(self._embedding_function.embed_query(query))
