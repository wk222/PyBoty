"""Integration tests using MockLLM harness.

These tests exercise real PyBot subsystems (memory distill, tool creation,
agent delegation) with deterministic mock LLM responses — no API keys
or network access required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mock_llm import (
    AIMessageCompat,
    MockLLM,
    MockLLMFactory,
    ToolCallCompat,
    mock_llm_caller,
)


class TestMockLLMBasics:
    def test_sequential_responses(self):
        llm = MockLLM(responses=["first", "second", "third"])
        assert llm.invoke("a").content == "first"
        assert llm.invoke("b").content == "second"
        assert llm.invoke("c").content == "third"
        assert llm.call_count == 3

    def test_default_fallback(self):
        llm = MockLLM(default_response="fallback")
        assert llm.invoke("anything").content == "fallback"

    def test_pattern_matching(self):
        llm = MockLLM(pattern_responses={
            r"weather": "It's sunny",
            r"time": "It's noon",
        })
        assert llm.invoke("What's the weather?").content == "It's sunny"
        assert llm.invoke("What time is it?").content == "It's noon"
        assert llm.invoke("hello").content == "Mock response"

    def test_tool_call_simulation(self):
        llm = MockLLM(tool_call_responses={
            r"calculate": [ToolCallCompat(name="calculator", args={"expr": "2+2"})],
        })
        result = llm.invoke("Please calculate 2+2")
        assert result.content == ""
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "calculator"

    def test_history_recording(self):
        llm = MockLLM(default_response="ok")
        llm.invoke("first prompt")
        llm.invoke("second prompt")
        assert len(llm.history) == 2
        assert llm.history[0]["prompt"] == "first prompt"

    def test_reset(self):
        llm = MockLLM(responses=["a", "b"])
        llm.invoke("x")
        llm.reset()
        assert llm.call_count == 0
        assert llm.invoke("y").content == "a"

    def test_bind_tools_returns_self(self):
        llm = MockLLM()
        assert llm.bind_tools([]) is llm


class TestMockLLMFactory:
    def test_factory_creates_configured_instances(self):
        factory = MockLLMFactory(default_response="factory_default")
        llm = factory(model="gpt-4o", temperature=0.5)
        assert llm.invoke("test").content == "factory_default"
        assert len(factory.created) == 1

    def test_factory_with_patterns(self):
        factory = MockLLMFactory(
            pattern_responses={r"code": "```python\nprint('hi')\n```"},
            default_response="no code",
        )
        llm = factory()
        assert "python" in llm.invoke("Write code").content
        assert llm.invoke("hello").content == "no code"


class TestMockLLMCaller:
    def test_simple_caller(self):
        caller = mock_llm_caller(response="distilled memory")
        result = caller("system prompt", "user input")
        assert result == "distilled memory"
        assert len(caller.call_log) == 1

    def test_pattern_caller(self):
        caller = mock_llm_caller(
            pattern_responses={
                r"归纳": "- [偏好] 用户喜欢Python",
                r"蒸馏|distill": "[MEMORY]\n## 技术偏好\n- Python",
            },
        )
        assert "偏好" in caller("sys", "请归纳对话")
        assert "[MEMORY]" in caller("sys", "请蒸馏记忆")


class TestMemoryDistillWithMock:
    def test_journal_creates_daily_file(self, tmp_path):
        from core.systems.memory.memory_distill import MemoryDistillManager

        journal_response = (
            "- [偏好] 用户偏好 Python 类型注解\n"
            "- [决策] 建议拆分函数\n"
            "- [事实] 用户接受了建议"
        )
        caller = mock_llm_caller(response=journal_response)
        mgr = MemoryDistillManager(workspace_dir=tmp_path, llm_caller=caller)

        messages = [
            {"role": "user", "content": "帮我重构这个函数"},
            {"role": "assistant", "content": "好的，我建议拆分为两个函数"},
            {"role": "user", "content": "可以"},
            {"role": "assistant", "content": "已完成重构"},
            {"role": "user", "content": "谢谢"},
        ]
        mgr._journal_sync(messages, None, "test_hash_001")

        daily_dir = tmp_path / "memory" / "daily"
        assert daily_dir.exists()
        daily_files = list(daily_dir.glob("*.md"))
        assert len(daily_files) >= 1
        content = daily_files[0].read_text(encoding="utf-8")
        assert "偏好" in content

    def test_distill_writes_memory_md(self, tmp_path):
        from core.systems.memory.memory_distill import MemoryDistillManager

        daily_dir = tmp_path / "memory" / "daily"
        daily_dir.mkdir(parents=True)
        (daily_dir / "2025-01-01.md").write_text(
            "# 每日日记 2025-01-01\n\n## 10:00\n\n- [偏好] 用户喜欢类型注解\n",
            encoding="utf-8",
        )

        distill_response = (
            "[MEMORY]\n"
            "## 技术偏好\n"
            "- 用户偏好 Python 类型注解\n"
            "- 用户倾向函数式风格\n"
        )
        caller = mock_llm_caller(response=distill_response)
        mgr = MemoryDistillManager(workspace_dir=tmp_path, llm_caller=caller)
        mgr._distill_sync(force=True)

        memory_path = tmp_path / "MEMORY.md"
        assert memory_path.exists()
        text = memory_path.read_text(encoding="utf-8")
        assert "技术偏好" in text
        assert "类型注解" in text

    def test_get_memory_context_after_distill(self, tmp_path):
        from core.systems.memory.memory_distill import MemoryDistillManager

        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(
            "# MEMORY — 长期记忆\n\n> 最后蒸馏：2025-01-15\n\n"
            "## 偏好\n- Python\n- 类型注解\n",
            encoding="utf-8",
        )
        mgr = MemoryDistillManager(workspace_dir=tmp_path)
        ctx = mgr.get_memory_context()
        assert "Python" in ctx
        assert "长期记忆" in ctx

    def test_today_journal_empty_when_no_file(self, tmp_path):
        from core.systems.memory.memory_distill import MemoryDistillManager

        mgr = MemoryDistillManager(workspace_dir=tmp_path)
        assert mgr.get_today_journal() == ""


class TestToolStorageWithMock:
    def test_create_and_retrieve_tool(self, tmp_path):
        from core.assets.tools import ToolStorage

        storage = ToolStorage(str(tmp_path))
        storage.upsert_tool("greeting", {
            "name": "greeting",
            "description": "Say hello",
            "parameters": [{"name": "name", "type": "string", "description": "Name"}],
            "code": "def run(name):\n    return f'Hello, {name}!'",
            "dependencies": [],
            "usage_guide": "Pass a name to greet",
        })

        tools = storage.list_tools()
        assert "greeting" in tools

        tool_data = storage.get_tool("greeting")
        assert tool_data["description"] == "Say hello"


@pytest.mark.asyncio
class TestAsyncMockLLM:
    async def test_ainvoke(self):
        llm = MockLLM(responses=["async response"])
        result = await llm.ainvoke("async prompt")
        assert result.content == "async response"
        assert llm.call_count == 1
