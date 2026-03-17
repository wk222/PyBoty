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
        manager.append_memory("用户偏好", "喜欢咖啡")
        manager.append_memory("重要事实", "咖啡因含量100mg")
        results = manager.search_memories("咖啡", section="用户偏好")
        for e in results:
            assert e.section == "用户偏好"

    def test_fallback_search_without_vector(self, manager_no_vector):
        manager_no_vector.append_memory("用户偏好", "喜欢用TypeScript开发")
        manager_no_vector.append_memory("重要事实", "使用React框架")
        results = manager_no_vector.search_memories("TypeScript")
        assert len(results) >= 1


class TestContextPrompt:
    def test_context_with_query(self, manager):
        manager.append_memory("用户偏好", "喜欢简洁的代码风格")
        prompt = manager.get_context_prompt(query="代码风格")
        assert "记忆" in prompt or "代码" in prompt

    def test_context_without_query_falls_back(self, manager):
        manager.append_memory("用户偏好", "测试内容")
        prompt = manager.get_context_prompt()
        assert isinstance(prompt, str)

    def test_context_empty_memory(self, manager):
        prompt = manager.get_context_prompt(query="anything")
        assert isinstance(prompt, str)


class TestMemoryStats:
    def test_stats_with_vector(self, manager, store):
        manager.append_memory("测试", "内容一")
        manager.append_memory("测试", "内容二")
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
        manager.append_memory("测试", "内容")
        assert store.count(MEMORY_COLLECTION) == 1
        result = manager.clear_vector_memories()
        assert result is True
        assert store.count(MEMORY_COLLECTION) == 0

    def test_clear_without_vector(self, manager_no_vector):
        result = manager_no_vector.clear_vector_memories()
        assert result is False

    def test_clear_preserves_file(self, manager):
        manager.append_memory("测试", "保留内容")
        manager.clear_vector_memories()
        content = manager.load()
        assert "保留内容" in content
