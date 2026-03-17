"""Tests for core.knowledge_tools — Agent-facing knowledge retrieval tools."""

from __future__ import annotations

import pytest

from core.document_pipeline import DocumentPipeline
from core.knowledge_tools import get_knowledge_tools
from core.vector_store import InMemoryVectorStore


@pytest.fixture
def store():
    return InMemoryVectorStore()


@pytest.fixture
def tools(store):
    pipeline = DocumentPipeline(store)
    return {t.name: t for t in get_knowledge_tools(store, pipeline)}


class TestKnowledgeSearch:
    def test_search_empty_collection(self, tools):
        result = tools["knowledge_search"].invoke({"query": "test"})
        assert "未" in result or "找到" in result

    def test_search_returns_results(self, store, tools):
        from core.vector_store import Document

        store.add_documents(
            [
                Document(page_content="Python is a great programming language"),
                Document(page_content="JavaScript runs in browsers"),
            ]
        )
        result = tools["knowledge_search"].invoke({"query": "Python programming"})
        assert "Python" in result

    def test_search_custom_collection(self, store, tools):
        from core.vector_store import Document

        store.add_documents([Document(page_content="Private data")], collection="private")
        result = tools["knowledge_search"].invoke({"query": "data", "collection": "private"})
        assert "Private" in result


class TestKnowledgeIngestText:
    def test_ingest_text(self, tools, store):
        result = tools["knowledge_ingest_text"].invoke({"text": "Important knowledge to remember"})
        assert "成功" in result
        assert store.count() >= 1

    def test_ingest_empty_text(self, tools):
        result = tools["knowledge_ingest_text"].invoke({"text": ""})
        assert "失败" in result


class TestKnowledgeList:
    def test_list_empty(self, tools):
        result = tools["knowledge_list"].invoke({})
        assert "空" in result

    def test_list_with_collections(self, store, tools):
        from core.vector_store import Document

        store.add_documents([Document(page_content="a")], collection="docs")
        store.add_documents([Document(page_content="b")], collection="notes")
        result = tools["knowledge_list"].invoke({})
        assert "docs" in result
        assert "notes" in result


class TestKnowledgeDelete:
    def test_delete_collection(self, store, tools):
        from core.vector_store import Document

        store.add_documents([Document(page_content="x")], collection="temp")
        result = tools["knowledge_delete"].invoke({"collection": "temp"})
        assert "删除" in result
        assert store.count("temp") == 0

    def test_delete_by_ids(self, store, tools):
        from core.vector_store import Document

        store.add_documents(
            [
                Document(page_content="a", doc_id="id1"),
                Document(page_content="b", doc_id="id2"),
            ]
        )
        result = tools["knowledge_delete"].invoke({"collection": "default", "doc_ids": ["id1"]})
        assert "1" in result
        assert store.count() == 1

    def test_delete_nonexistent_collection(self, tools):
        result = tools["knowledge_delete"].invoke({"collection": "nope"})
        assert "失败" in result
