"""Tests for core.vector_store — InMemoryVectorStore."""

from __future__ import annotations

import pytest

from core.vector_store import Document, InMemoryVectorStore, create_vector_store


@pytest.fixture
def store():
    return InMemoryVectorStore()


class TestInMemoryVectorStore:
    def test_add_and_count(self, store):
        docs = [Document(page_content="hello world"), Document(page_content="foo bar")]
        ids = store.add_documents(docs)
        assert len(ids) == 2
        assert store.count() == 2

    def test_search_returns_relevant(self, store):
        store.add_documents(
            [
                Document(page_content="Python is a programming language"),
                Document(page_content="The weather is nice today"),
                Document(page_content="Python programming tutorial"),
            ]
        )
        results = store.search("Python programming")
        assert len(results) == 3
        assert results[0].score >= results[1].score
        assert "Python" in results[0].document.page_content

    def test_search_empty_collection(self, store):
        results = store.search("anything")
        assert results == []

    def test_search_nonexistent_collection(self, store):
        results = store.search("anything", collection="nonexistent")
        assert results == []

    def test_delete_by_ids(self, store):
        store.add_documents(
            [
                Document(page_content="doc1", doc_id="id1"),
                Document(page_content="doc2", doc_id="id2"),
                Document(page_content="doc3", doc_id="id3"),
            ]
        )
        deleted = store.delete(["id1", "id2"])
        assert deleted == 2
        assert store.count() == 1

    def test_delete_nonexistent_ids(self, store):
        store.add_documents([Document(page_content="doc1", doc_id="id1")])
        deleted = store.delete(["nonexistent"])
        assert deleted == 0
        assert store.count() == 1

    def test_delete_collection(self, store):
        store.add_documents([Document(page_content="x")], collection="test")
        assert store.delete_collection("test") is True
        assert store.count("test") == 0

    def test_delete_nonexistent_collection(self, store):
        assert store.delete_collection("nope") is False

    def test_list_collections(self, store):
        store.add_documents([Document(page_content="a")], collection="alpha")
        store.add_documents([Document(page_content="b")], collection="beta")
        assert sorted(store.list_collections()) == ["alpha", "beta"]

    def test_upsert_deduplicates(self, store):
        store.add_documents([Document(page_content="v1", doc_id="same")])
        store.add_documents([Document(page_content="v2", doc_id="same")])
        assert store.count() == 1
        results = store.search("v2")
        assert results[0].document.page_content == "v2"

    def test_multiple_collections(self, store):
        store.add_documents([Document(page_content="a")], collection="c1")
        store.add_documents([Document(page_content="b")], collection="c2")
        assert store.count("c1") == 1
        assert store.count("c2") == 1
        assert store.count("c3") == 0

    def test_top_k_limit(self, store):
        for i in range(10):
            store.add_documents([Document(page_content=f"doc {i}")])
        results = store.search("doc", top_k=3)
        assert len(results) == 3

    def test_metadata_preserved(self, store):
        store.add_documents([Document(page_content="test", metadata={"source": "file.txt", "page": 1})])
        results = store.search("test")
        assert results[0].document.metadata["source"] == "file.txt"
        assert results[0].document.metadata["page"] == 1


class TestCreateVectorStore:
    def test_memory_backend(self):
        store = create_vector_store(backend="memory")
        assert isinstance(store, InMemoryVectorStore)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown vector store"):
            create_vector_store(backend="nonexistent")
