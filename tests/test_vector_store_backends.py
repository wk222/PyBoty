from __future__ import annotations

from pathlib import Path

from core.systems.knowledge.vector_store import Document, create_vector_store


class DummyEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        lowered = text.lower()
        return [
            1.0 if "python" in lowered else 0.0,
            1.0 if "weather" in lowered else 0.0,
            float(len(lowered.split())),
        ]


def test_sqlite_vec_store_add_search_delete(tmp_path: Path):
    store = create_vector_store(
        backend="sqlite-vec",
        persist_dir=str(tmp_path / "sqlite-vec"),
        embedding_function=DummyEmbeddings(),
    )
    store.add_documents(
        [
            Document(page_content="Python async patterns", doc_id="py"),
            Document(page_content="Weather forecast today", doc_id="weather"),
        ]
    )

    results = store.search("python", top_k=1)

    assert results[0].document.doc_id == "py"
    assert store.count() == 2
    assert store.delete(["py"]) == 1
    assert store.count() == 1


def test_faiss_store_persists_metadata_even_without_native_faiss(tmp_path: Path):
    persist_dir = tmp_path / "faiss"
    store = create_vector_store(
        backend="faiss",
        persist_dir=str(persist_dir),
        embedding_function=DummyEmbeddings(),
    )
    store.add_documents(
        [
            Document(page_content="Python runtime internals", metadata={"kind": "code"}, doc_id="py"),
            Document(page_content="Weather alerts", metadata={"kind": "ops"}, doc_id="weather"),
        ]
    )

    reloaded = create_vector_store(
        backend="faiss",
        persist_dir=str(persist_dir),
        embedding_function=DummyEmbeddings(),
    )
    results = reloaded.search("python", top_k=1)

    assert reloaded.count() == 2
    assert results[0].document.doc_id == "py"
    assert results[0].document.metadata["kind"] == "code"
