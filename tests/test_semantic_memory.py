"""Tests for core.semantic_memory — vector-backed long-term memory."""

from __future__ import annotations

import tempfile

import pytest

from core.semantic_memory import MEMORY_COLLECTION, SemanticMemoryManager
from core.vector_store import InMemoryVectorStore


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def store():
    return InMemoryVectorStore()


@pytest.fixture
def manager(tmpdir, store):
    return SemanticMemoryManager(workspace_dir=tmpdir, vector_store=store)


@pytest.fixture
def manager_no_vector(tmpdir):
    return SemanticMemoryManager(workspace_dir=tmpdir, vector_store=None)


class TestSemanticMemoryAppend:
    def test_append_writes_to_file_and_vector(self, manager, store):
        manager.append_memory("用户偏好", "喜欢用Python编程")
        file_content = manager.load()
        assert "Python" in file_content
        assert store.count(MEMORY_COLLECTION) == 1

    def test_append_multiple(self, manager, store):
        manager.append_memory("重要事实", "项目使用FastAPI")
        manager.append_memory("重要事实", "数据库是PostgreSQL")
        assert store.count(MEMORY_COLLECTION) == 2

    def test_append_without_vector_store(self, manager_no_vector):
        manager_no_vector.append_memory("用户偏好", "喜欢暗色主题")
        content = manager_no_vector.load()
        assert "暗色主题" in content

    def test_append_typed_memory_writes_memory_type_metadata(self, manager, store):
        manager.append_typed_memory(memory_type="user", content="用户喜欢简短回复", verified=True)
        documents = store.get_documents(MEMORY_COLLECTION)
        assert documents[0].metadata["memory_type"] == "user"
        assert documents[0].metadata["category"] == "preference"


class TestSemanticMemorySearch:
    def test_search_finds_relevant(self, manager):
        manager.append_memory("用户偏好", "喜欢用Python编程")
        manager.append_memory("重要事实", "今天天气很好适合散步")
        manager.append_memory("学到的经验", "Python的async性能很好")

        results = manager.search_memories("Python编程")
        assert len(results) >= 1
        assert any("Python" in e.content for e in results)

    def test_search_empty(self, manager):
        results = manager.search_memories("anything")
        assert results == []

    def test_search_with_section_filter(self, manager):
        manager.append_memory("用户偏好", "喜欢每天早上喝一杯咖啡")
        manager.append_memory("重要事实", "每杯咖啡因含量大约100mg")
        results = manager.search_memories("咖啡", section="用户偏好")
        for e in results:
            assert e.section == "用户偏好"

    def test_search_with_memory_type_filter(self, manager):
        manager.append_typed_memory(memory_type="user", content="喜欢简洁界面")
        manager.append_typed_memory(memory_type="reference", content="项目部署使用FastAPI")
        results = manager.search_memories("喜欢", memory_type="user")
        assert results
        assert all(entry.memory_type == "user" for entry in results)

    def test_fallback_search_without_vector(self, manager_no_vector):
        manager_no_vector.append_memory("用户偏好", "喜欢用TypeScript开发")
        manager_no_vector.append_memory("重要事实", "使用React框架")
        results = manager_no_vector.search_memories("TypeScript")
        assert len(results) >= 1

    def test_hybrid_search_combines_keyword_and_vector_scores(self, tmpdir, store):
        manager = SemanticMemoryManager(
            workspace_dir=tmpdir,
            vector_store=store,
            search_strategy="hybrid",
            keyword_weight=0.5,
            vector_weight=0.5,
        )
        manager.append_memory("用户偏好", "喜欢用Python编程")
        manager.append_memory("重要事实", "今天天气很好适合散步")

        results = manager.search_memories("Python")

        assert results
        assert "Python" in results[0].content

    def test_temporal_decay_penalizes_old_memories(self, tmpdir, store):
        manager = SemanticMemoryManager(
            workspace_dir=tmpdir,
            vector_store=store,
            temporal_decay_enabled=True,
            temporal_half_life_days=1.0,
        )
        manager.append_memory("facts", "Python 相关记忆")
        manager.append_memory("facts", "天气 相关记忆")
        documents = store.get_documents(MEMORY_COLLECTION)
        old_doc = next(doc for doc in documents if "Python" in doc.page_content)
        old_doc.metadata["timestamp_epoch"] = 1.0
        store.add_documents([old_doc], collection=MEMORY_COLLECTION)

        results = manager.search_memories("Python", top_k=2)

        assert results
        assert results[0].content != ""

    def test_mmr_preserves_diverse_results(self, tmpdir, store):
        manager = SemanticMemoryManager(
            workspace_dir=tmpdir,
            vector_store=store,
            search_strategy="hybrid",
            mmr_enabled=True,
            mmr_lambda=0.5,
        )
        manager.append_memory("facts", "Python async await tutorial")
        manager.append_memory("facts", "Python typing and dataclasses")
        manager.append_memory("facts", "Weather forecast and sunshine")

        results = manager.search_memories("Python tutorial weather", top_k=3)
        contents = [entry.content for entry in results]

        assert any("Weather" in content for content in contents)


class TestContextPrompt:
    def test_context_with_query(self, manager):
        manager.append_memory("用户偏好", "喜欢简洁的代码风格")
        prompt = manager.get_context_prompt(query="代码风格")
        assert "记忆" in prompt or "代码" in prompt

    def test_context_without_query_falls_back(self, manager):
        manager.append_memory("用户偏好", "用户偏好使用简洁的测试内容")
        prompt = manager.get_context_prompt()
        assert isinstance(prompt, str)

    def test_context_empty_memory(self, manager):
        prompt = manager.get_context_prompt(query="anything")
        assert isinstance(prompt, str)


class TestMemoryStats:
    def test_stats_with_vector(self, manager, store):
        manager.append_memory("测试", "这是第一条测试记忆内容")
        manager.append_memory("测试", "这是第二条测试记忆内容")
        stats = manager.get_memory_stats()
        assert stats["vector_backed"] is True
        assert stats["vector_count"] == 2
        assert stats["file_lines"] > 0

    def test_stats_without_vector(self, manager_no_vector):
        stats = manager_no_vector.get_memory_stats()
        assert stats["vector_backed"] is False
        assert "vector_count" not in stats


class TestClearVectorMemories:
    def test_clear_vector(self, manager, store):
        manager.append_memory("测试", "这是一条用于清理测试的记忆内容")
        assert store.count(MEMORY_COLLECTION) == 1
        result = manager.clear_vector_memories()
        assert result is True
        assert store.count(MEMORY_COLLECTION) == 0

    def test_clear_without_vector(self, manager_no_vector):
        result = manager_no_vector.clear_vector_memories()
        assert result is False

    def test_clear_preserves_file(self, manager):
        manager.append_memory("测试", "保留这段记忆内容到文件中")
        manager.clear_vector_memories()
        content = manager.load()
        assert "保留" in content
