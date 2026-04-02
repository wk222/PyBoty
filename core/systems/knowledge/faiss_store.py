"""FAISS-backed vector store with graceful local fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .vector_store import Document, SearchResult, VectorStoreBackend, cosine_similarity, dump_metadata, keyword_score


class FAISSVectorStore(VectorStoreBackend):
    """FAISS-backed store that degrades to exhaustive local search when FAISS is unavailable."""

    def __init__(self, persist_dir: str, embedding_function: Any | None = None):
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._persist_dir / "faiss_store.json"
        self._embedding_function = embedding_function
        self._records: dict[str, dict[str, dict[str, Any]]] = {}
        self._faiss = None
        self._numpy = None
        self._index = None
        self._index_doc_ids: dict[str, list[str]] = {}
        self._load_backend()
        self._load_state()

    def _load_backend(self) -> None:
        try:
            import faiss  # type: ignore
            import numpy as np

            self._faiss = faiss
            self._numpy = np
        except ImportError:
            self._faiss = None
            self._numpy = None

    def _load_state(self) -> None:
        if not self._meta_path.exists():
            return
        payload = json.loads(self._meta_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            raw_records = payload.get("records", {})
            if isinstance(raw_records, dict):
                self._records = raw_records
        self._rebuild_indexes()

    def _save_state(self) -> None:
        self._meta_path.write_text(
            json.dumps({"records": self._records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _rebuild_indexes(self) -> None:
        self._index_doc_ids = {}
        self._index = {}
        if not self._faiss or not self._numpy:
            return
        for collection, docs in self._records.items():
            vectors = [item["embedding"] for item in docs.values() if item.get("embedding")]
            if not vectors:
                continue
            matrix = self._numpy.array(vectors, dtype="float32")
            index = self._faiss.IndexFlatIP(matrix.shape[1])
            index.add(matrix)
            self._index[collection] = index
            self._index_doc_ids[collection] = [doc_id for doc_id, item in docs.items() if item.get("embedding")]

    def add_documents(self, docs: list[Document], collection: str = "default") -> list[str]:
        bucket = self._records.setdefault(collection, {})
        embeddings = self._embed_documents_if_available(docs)
        ids: list[str] = []
        for index, doc in enumerate(docs):
            doc_id = doc.doc_id or ""
            bucket[doc_id] = {
                "content": doc.page_content,
                "metadata": dump_metadata(doc.metadata),
                "embedding": embeddings[index] if embeddings is not None and index < len(embeddings) else None,
            }
            ids.append(doc_id)
        self._save_state()
        self._rebuild_indexes()
        return ids

    def search(self, query: str, collection: str = "default", top_k: int = 5) -> list[SearchResult]:
        bucket = self._records.get(collection, {})
        if not bucket:
            return []
        query_embedding = self._embed_query_if_available(query)
        results: list[SearchResult] = []
        if self._faiss and self._numpy and query_embedding is not None and collection in self._index:
            index = self._index[collection]
            matrix = self._numpy.array([query_embedding], dtype="float32")
            distances, indices = index.search(matrix, min(top_k, len(self._index_doc_ids.get(collection, []))))
            for distance, idx in zip(distances[0], indices[0], strict=False):
                if idx < 0:
                    continue
                doc_id = self._index_doc_ids[collection][int(idx)]
                item = bucket[doc_id]
                content = str(item["content"])
                results.append(
                    SearchResult(
                        document=Document(
                            page_content=content,
                            metadata=json.loads(str(item["metadata"])),
                            doc_id=doc_id,
                        ),
                        score=float(distance),
                        vector_score=float(distance),
                        keyword_score=keyword_score(query, content),
                        collection=collection,
                    )
                )
            return results

        for doc_id, item in bucket.items():
            content = str(item["content"])
            text_score = keyword_score(query, content)
            vector_score = None
            if query_embedding is not None and item.get("embedding"):
                vector_score = cosine_similarity(query_embedding, list(map(float, item["embedding"])))
            score = vector_score if vector_score is not None else text_score
            results.append(
                SearchResult(
                    document=Document(
                        page_content=content,
                        metadata=json.loads(str(item["metadata"])),
                        doc_id=doc_id,
                    ),
                    score=score,
                    vector_score=vector_score,
                    keyword_score=text_score,
                    collection=collection,
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    def delete(self, ids: list[str], collection: str = "default") -> int:
        bucket = self._records.get(collection, {})
        deleted = 0
        for doc_id in ids:
            if doc_id in bucket:
                deleted += 1
                del bucket[doc_id]
        if deleted:
            self._save_state()
            self._rebuild_indexes()
        return deleted

    def list_collections(self) -> list[str]:
        return sorted(self._records)

    def delete_collection(self, collection: str) -> bool:
        if collection in self._records:
            del self._records[collection]
            self._save_state()
            self._rebuild_indexes()
            return True
        return False

    def count(self, collection: str = "default") -> int:
        return len(self._records.get(collection, {}))

    def get_documents(self, collection: str = "default") -> list[Document]:
        bucket = self._records.get(collection, {})
        return [
            Document(page_content=str(item["content"]), metadata=json.loads(str(item["metadata"])), doc_id=doc_id)
            for doc_id, item in bucket.items()
        ]

    def _embed_documents_if_available(self, docs: list[Document]) -> list[list[float]] | None:
        if not self._embedding_function or not hasattr(self._embedding_function, "embed_documents"):
            return None
        return self._embedding_function.embed_documents([doc.page_content for doc in docs])

    def _embed_query_if_available(self, query: str) -> list[float] | None:
        if not self._embedding_function or not hasattr(self._embedding_function, "embed_query"):
            return None
        return list(self._embedding_function.embed_query(query))
