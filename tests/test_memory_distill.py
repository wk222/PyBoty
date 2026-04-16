"""Tests for the MemoryDistill memory distillation pipeline.

Covers:
- Stage 1 (Journal): conversation → daily Markdown diary with categories
- Stage 2 (Distill): daily diaries → MEMORY.md with importance scoring
- Stage 3 (Archive): processed diary files moved to archive/
- Hash-based deduplication of journal writes
- Separate journal / distill LLM callers
- Tool message extraction in journal
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.systems.memory.memory_distill import MemoryDistillManager


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def messages() -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "帮我看看这个Python文件有没有问题"},
        {"role": "assistant", "content": "好的，我来分析。主要有两个问题：①缺少类型注解 ②循环复杂度过高。"},
        {"role": "user", "content": "具体怎么改？"},
        {"role": "assistant", "content": "可以把大函数拆分成小函数，并加上 typing 模块的类型提示。"},
        {"role": "user", "content": "好，按你说的做了，谢谢"},
    ]


def make_manager(workspace: Path, *, journal_calls: list, distill_calls: list) -> MemoryDistillManager:
    """Build a manager with mock LLM callers that record invocations."""
    def _journal_caller(system: str, user: str) -> str:
        journal_calls.append((system[:30], user[:50]))
        return "- [偏好] 用户偏好 Python 类型注解\n- [决策] 建议拆分函数并加类型注解\n- [事实] 用户接受代码重构建议"

    def _distill_caller(system: str, user: str) -> str:
        distill_calls.append((system[:30], user[:50]))
        return (
            "[MEMORY]\n"
            "- [偏好|★★★] 用户偏好 Python 类型注解\n"
            "- [决策|★★] 用户接受代码重构建议\n"
            "\n"
            "[INSIGHT]\n"
            "用户注重代码质量，乐于接受架构建议。"
        )

    return MemoryDistillManager(
        workspace_dir=workspace,
        journal_llm_caller=_journal_caller,
        distill_llm_caller=_distill_caller,
    )


# ─── Stage 1: Journal ─────────────────────────────────────────────────────────

class TestJournal:
    def test_journal_writes_daily_file(self, workspace: Path, messages):
        jc, dc = [], []
        mgr = make_manager(workspace, journal_calls=jc, distill_calls=dc)

        mgr.journal_async(messages, date_str="2026-01-01")
        time.sleep(0.3)

        daily_dir = workspace / "memory" / "daily"
        assert daily_dir.exists(), "daily/ dir should be created"
        files = list(daily_dir.glob("*.md"))
        assert len(files) == 1, "exactly one daily file should exist"
        assert "2026-01-01" in files[0].name

    def test_journal_content_has_categories(self, workspace: Path, messages):
        jc, dc = [], []
        mgr = make_manager(workspace, journal_calls=jc, distill_calls=dc)
        mgr.journal_async(messages, date_str="2026-01-02")
        time.sleep(0.3)

        content = (workspace / "memory" / "daily" / "2026-01-02.md").read_text(encoding="utf-8")
        assert "[偏好]" in content or "类型注解" in content

    def test_journal_deduplication(self, workspace: Path, messages):
        """Same messages → same hash → journal written only once."""
        jc, dc = [], []
        mgr = make_manager(workspace, journal_calls=jc, distill_calls=dc)
        mgr.journal_async(messages, date_str="2026-01-03")
        mgr.journal_async(messages, date_str="2026-01-03")
        time.sleep(0.3)

        assert len(jc) == 1, "LLM should only be called once for identical messages"

    def test_journal_skips_too_few_messages(self, workspace: Path):
        jc, dc = [], []
        mgr = make_manager(workspace, journal_calls=jc, distill_calls=dc)
        short = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        mgr.journal_async(short, date_str="2026-01-04")
        time.sleep(0.3)
        assert len(jc) == 0, "Should not call LLM for < MIN_MESSAGES_TO_JOURNAL messages"

    def test_journal_uses_dedicated_caller(self, workspace: Path, messages):
        jc, dc = [], []
        mgr = make_manager(workspace, journal_calls=jc, distill_calls=dc)
        mgr.journal_async(messages, date_str="2026-01-05")
        time.sleep(0.3)
        assert len(jc) == 1
        assert len(dc) == 0, "distill caller should not be invoked during journal"

    def test_tool_messages_formatted(self, workspace: Path):
        """Tool/function role messages should be labeled as 工具结果."""
        msgs = [
            {"role": "user", "content": "查一下天气"},
            {"role": "assistant", "content": "好的，让我查一下"},
            {"role": "tool", "content": "北京: 晴, 25°C"},
            {"role": "assistant", "content": "北京今天晴天，25度。"},
            {"role": "user", "content": "谢谢"},
        ]
        formatted = MemoryDistillManager._format_messages(msgs)
        assert "工具结果" in formatted


# ─── Stage 2: Distill ─────────────────────────────────────────────────────────

class TestDistill:
    def _write_daily(self, workspace: Path, date: str, content: str) -> None:
        d = workspace / "memory" / "daily"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{date}.md").write_text(f"# 每日日记 {date}\n\n{content}\n", encoding="utf-8")

    def test_distill_creates_memory_md(self, workspace: Path):
        self._write_daily(workspace, "2026-02-01", "- [偏好] 用户喜欢简洁代码")
        jc, dc = [], []
        mgr = make_manager(workspace, journal_calls=jc, distill_calls=dc)
        mgr.distill_async(force=True)
        time.sleep(0.3)

        memory_path = workspace / "MEMORY.md"
        assert memory_path.exists(), "MEMORY.md should be created by distill"
        content = memory_path.read_text(encoding="utf-8")
        assert "用户偏好 Python 类型注解" in content

    def test_distill_uses_dedicated_caller(self, workspace: Path):
        self._write_daily(workspace, "2026-02-02", "- [事实] some event")
        jc, dc = [], []
        mgr = make_manager(workspace, journal_calls=jc, distill_calls=dc)
        mgr.distill_async(force=True)
        time.sleep(0.3)
        assert len(dc) == 1
        assert len(jc) == 0, "journal caller should not be invoked during distill"

    def test_get_memory_context_returns_content(self, workspace: Path):
        self._write_daily(workspace, "2026-02-03", "- event")
        jc, dc = [], []
        mgr = make_manager(workspace, journal_calls=jc, distill_calls=dc)
        mgr.distill_async(force=True)
        time.sleep(0.3)

        ctx = mgr.get_memory_context()
        assert ctx, "get_memory_context should return non-empty string after distill"
        assert "---" in ctx or "记忆" in ctx


# ─── Stage 3: Archive ─────────────────────────────────────────────────────────

class TestArchive:
    def _write_daily(self, workspace: Path, date: str) -> Path:
        d = workspace / "memory" / "daily"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{date}.md"
        p.write_text(f"# {date}\n\n- some event\n", encoding="utf-8")
        return p

    def test_daily_files_archived_after_distill(self, workspace: Path):
        p = self._write_daily(workspace, "2026-03-01")
        jc, dc = [], []
        mgr = make_manager(workspace, journal_calls=jc, distill_calls=dc)
        mgr.distill_async(force=True)
        time.sleep(0.3)

        archive_dir = workspace / "memory" / "archive"
        assert archive_dir.exists(), "archive/ dir should be created"
        archived = list(archive_dir.glob("*.md"))
        assert any("2026-03-01" in f.name for f in archived), "daily file should be moved to archive"
        assert not p.exists(), "original daily file should no longer exist"


# ─── Separate LLM callers ─────────────────────────────────────────────────────

class TestSeparateCallers:
    def test_fallback_to_shared_caller(self, workspace: Path):
        """When no dedicated callers given, shared llm_caller is used for both."""
        calls = []

        def _shared(system: str, user: str) -> str:
            calls.append("shared")
            if "事件日记" in system:
                return "- [事实] 一件事"
            return "[MEMORY]\n- [事实|★★] fact\n[INSIGHT]\nok"

        mgr = MemoryDistillManager(workspace_dir=workspace, llm_caller=_shared)
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
        mgr.journal_async(msgs, date_str="2026-04-01")
        time.sleep(0.3)

        d = workspace / "memory" / "daily"
        d.mkdir(parents=True, exist_ok=True)
        (d / "2026-04-01.md").write_text("# 2026-04-01\n\n- 一件事\n", encoding="utf-8")
        mgr.distill_async(force=True)
        time.sleep(0.3)

        assert "shared" in calls, "shared caller should be invoked"


# ─── Backward compatibility alias ─────────────────────────────────────────────

class TestBackwardCompat:
    def test_deep_digest_manager_alias(self):
        """DeepDigestManager should be importable as a backward-compatible alias."""
        from core.systems.memory.memory_distill import DeepDigestManager
        assert DeepDigestManager is MemoryDistillManager
