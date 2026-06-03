"""Tests for Context Strategies, Training Feedback loop, OpenClaw Compat, and Memory Engine mock integrations.

Consolidates:
1. test_context_strategies.py
2. test_training.py
3. test_openclaw_compat.py
4. test_mock_llm_integration.py (memory and storage integrations)
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

# Context strategies
from core.systems.context import (
    BufferedChatContext,
    CompositeContextStrategy,
    HeadAndTailChatContext,
    TokenLimitedChatContext,
)

# Training Feedback loop
from core.systems.runtime.training import FeedbackRecord, FeedbackStore, format_feedback_prompt

# OpenClaw Compat and definitions
from core.assets.skills.openclaw_compat import (
    build_openclaw_runtime_env,
    build_openclaw_skill_bridge_report,
    build_openclaw_source_specs,
    import_openclaw_channels_for_pybot,
)
from core.assets.skills.skill_models import SkillDefinition

# Memory engine with mock
from tests.mock_llm import mock_llm_caller


# Helper function
def _msg(role, content):
    return {"role": role, "content": content}


def _conversation(n, *, with_system=True):
    msgs = []
    if with_system:
        msgs.append(_msg("system", "You are a helpful assistant."))
    for i in range(n):
        msgs.append(_msg("user", f"User message {i}"))
        msgs.append(_msg("assistant", f"Assistant response {i}"))
    return msgs


# ── Section 1: Chat Context Strategies ────────────────────────────────

class TestBufferedChatContext:
    def test_under_limit(self):
        msgs = _conversation(3)
        ctx = BufferedChatContext(buffer_size=20)
        result = ctx.apply(msgs)
        assert len(result) == len(msgs)

    def test_trims_old(self):
        msgs = _conversation(10)
        ctx = BufferedChatContext(buffer_size=4)
        result = ctx.apply(msgs)
        non_sys = [m for m in result if m["role"] != "system"]
        assert len(non_sys) == 4
        assert result[0]["role"] == "system"

    def test_preserves_system(self):
        msgs = _conversation(10)
        ctx = BufferedChatContext(buffer_size=2)
        result = ctx.apply(msgs)
        sys_msgs = [m for m in result if m["role"] == "system"]
        assert len(sys_msgs) == 1

    def test_empty(self):
        assert BufferedChatContext().apply([]) == []

    def test_recent_messages_kept(self):
        msgs = _conversation(5)
        ctx = BufferedChatContext(buffer_size=4)
        result = ctx.apply(msgs)
        non_sys = [m for m in result if m["role"] != "system"]
        assert non_sys[-1]["content"] == "Assistant response 4"
        assert non_sys[-2]["content"] == "User message 4"


class TestTokenLimitedChatContext:
    def test_under_limit(self):
        msgs = _conversation(2)
        ctx = TokenLimitedChatContext(max_tokens=100000)
        result = ctx.apply(msgs)
        assert len(result) == len(msgs)

    def test_trims_to_fit(self):
        msgs = _conversation(20)
        ctx = TokenLimitedChatContext(max_tokens=200)
        result = ctx.apply(msgs)
        assert len(result) < len(msgs)
        assert result[0]["role"] == "system"

    def test_preserves_system(self):
        msgs = _conversation(20)
        ctx = TokenLimitedChatContext(max_tokens=100)
        result = ctx.apply(msgs)
        sys_msgs = [m for m in result if m["role"] == "system"]
        assert len(sys_msgs) >= 1

    def test_keeps_recent(self):
        msgs = _conversation(10)
        ctx = TokenLimitedChatContext(max_tokens=300)
        result = ctx.apply(msgs)
        non_sys = [m for m in result if m["role"] != "system"]
        if non_sys:
            assert "9" in non_sys[-1]["content"]

    def test_very_small_budget(self):
        msgs = _conversation(5)
        ctx = TokenLimitedChatContext(max_tokens=10)
        result = ctx.apply(msgs)
        assert all(m["role"] == "system" for m in result)


class TestHeadAndTailChatContext:
    def test_under_limit(self):
        msgs = _conversation(3)
        ctx = HeadAndTailChatContext(head_count=3, tail_count=10)
        result = ctx.apply(msgs)
        assert len(result) == len(msgs)

    def test_keeps_head_and_tail(self):
        msgs = _conversation(10)
        ctx = HeadAndTailChatContext(head_count=2, tail_count=2)
        result = ctx.apply(msgs)
        non_sys = [m for m in result if m["role"] != "system"]
        content_texts = [m["content"] for m in non_sys if "省略" not in m.get("content", "")]
        assert "User message 0" in content_texts
        assert "Assistant response 9" in content_texts

    def test_separator_message(self):
        msgs = _conversation(10)
        ctx = HeadAndTailChatContext(head_count=2, tail_count=2)
        result = ctx.apply(msgs)
        separators = [m for m in result if "省略" in m.get("content", "")]
        assert len(separators) == 1
        assert "16" in separators[0]["content"]

    def test_preserves_system(self):
        msgs = _conversation(10)
        ctx = HeadAndTailChatContext(head_count=1, tail_count=1)
        result = ctx.apply(msgs)
        assert result[0]["role"] == "system"


class TestCompositeContextStrategy:
    def test_pipeline(self):
        msgs = _conversation(20)
        strategy = CompositeContextStrategy(
            [
                BufferedChatContext(buffer_size=10),
                TokenLimitedChatContext(max_tokens=500),
            ]
        )
        result = strategy.apply(msgs)
        assert len(result) < len(msgs)

    def test_empty_strategies(self):
        msgs = _conversation(3)
        strategy = CompositeContextStrategy([])
        result = strategy.apply(msgs)
        assert result == msgs

    def test_single_strategy(self):
        msgs = _conversation(5)
        strategy = CompositeContextStrategy([BufferedChatContext(buffer_size=4)])
        result = strategy.apply(msgs)
        non_sys = [m for m in result if m["role"] != "system"]
        assert len(non_sys) == 4


# ── Section 2: Feedback & Training loop ───────────────────────────────

class TestFeedbackRecord:
    def test_basic_creation(self):
        r = FeedbackRecord(
            agent_name="analyst",
            task_summary="Analyze sales data",
            output_summary="Generated report with charts",
            score=4,
            feedback_text="Good analysis",
        )
        assert r.agent_name == "analyst"
        assert r.score == 4
        assert r.timestamp > 0

    def test_score_clamped_high(self):
        r = FeedbackRecord(
            agent_name="a",
            task_summary="t",
            output_summary="o",
            score=10,
            feedback_text="f",
        )
        assert r.score == 5

    def test_score_clamped_low(self):
        r = FeedbackRecord(
            agent_name="a",
            task_summary="t",
            output_summary="o",
            score=-1,
            feedback_text="f",
        )
        assert r.score == 1


class TestFeedbackStoreInMemory:
    def setup_method(self):
        self.store = FeedbackStore()

    def _rec(self, agent="bot", score=3, task="task"):
        return FeedbackRecord(
            agent_name=agent,
            task_summary=task,
            output_summary="out",
            score=score,
            feedback_text="fb",
        )

    def test_add_and_count(self):
        self.store.add(self._rec())
        self.store.add(self._rec())
        assert self.store.count() == 2

    def test_count_by_agent(self):
        self.store.add(self._rec(agent="a"))
        self.store.add(self._rec(agent="b"))
        self.store.add(self._rec(agent="a"))
        assert self.store.count("a") == 2
        assert self.store.count("b") == 1

    def test_get_for_agent(self):
        r1 = self._rec(agent="a", task="t1")
        r1.timestamp = 100.0
        r2 = self._rec(agent="b", task="t2")
        r3 = self._rec(agent="a", task="t3")
        r3.timestamp = 200.0
        self.store.add(r1)
        self.store.add(r2)
        self.store.add(r3)
        records = self.store.get_for_agent("a")
        assert len(records) == 2
        assert records[0].task_summary == "t3"

    def test_get_for_agent_limit(self):
        for i in range(10):
            self.store.add(self._rec(agent="a", task=f"t{i}"))
        assert len(self.store.get_for_agent("a", limit=3)) == 3

    def test_get_best_examples(self):
        self.store.add(self._rec(agent="a", score=5, task="great"))
        self.store.add(self._rec(agent="a", score=2, task="bad"))
        self.store.add(self._rec(agent="a", score=4, task="good"))
        best = self.store.get_best_examples("a", min_score=4)
        assert len(best) == 2
        assert best[0].score >= best[1].score

    def test_get_worst_patterns(self):
        self.store.add(self._rec(agent="a", score=1, task="terrible"))
        self.store.add(self._rec(agent="a", score=5, task="great"))
        self.store.add(self._rec(agent="a", score=2, task="meh"))
        worst = self.store.get_worst_patterns("a", max_score=2)
        assert len(worst) == 2
        assert worst[0].score <= worst[1].score

    def test_export_training_data(self):
        self.store.add(self._rec(agent="a", task="t1"))
        self.store.add(self._rec(agent="b", task="t2"))
        data = self.store.export_training_data("a")
        assert len(data) == 1
        assert data[0]["task_summary"] == "t1"

    def test_get_all(self):
        self.store.add(self._rec())
        self.store.add(self._rec())
        assert len(self.store.get_all()) == 2


class TestFeedbackStorePersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "feedback.json"

            store1 = FeedbackStore(path)
            store1.add(
                FeedbackRecord(
                    agent_name="a",
                    task_summary="t",
                    output_summary="o",
                    score=4,
                    feedback_text="nice",
                )
            )
            store1.add(
                FeedbackRecord(
                    agent_name="b",
                    task_summary="t2",
                    output_summary="o2",
                    score=2,
                    feedback_text="meh",
                )
            )

            store2 = FeedbackStore(path)
            assert store2.count() == 2
            assert store2.count("a") == 1

    def test_load_nonexistent(self):
        store = FeedbackStore("/nonexistent/path/feedback.json")
        assert store.count() == 0

    def test_load_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "feedback.json"
            path.write_text("not json!!!", encoding="utf-8")
            store = FeedbackStore(path)
            assert store.count() == 0


class TestFormatFeedbackPrompt:
    def _rec(self, score, task="task", feedback="fb"):
        return FeedbackRecord(
            agent_name="a",
            task_summary=task,
            output_summary="o",
            score=score,
            feedback_text=feedback,
        )

    def test_empty(self):
        assert format_feedback_prompt([]) == ""

    def test_good_feedback(self):
        result = format_feedback_prompt([self._rec(5, "analysis", "excellent work")])
        assert "正面评价" in result
        assert "analysis" in result
        assert "excellent work" in result

    def test_bad_feedback(self):
        result = format_feedback_prompt([self._rec(1, "report", "too vague")])
        assert "改进建议" in result
        assert "report" in result
        assert "too vague" in result

    def test_mixed_feedback(self):
        records = [
            self._rec(5, "good_task", "great"),
            self._rec(1, "bad_task", "terrible"),
            self._rec(3, "mid_task", "ok"),
        ]
        result = format_feedback_prompt(records)
        assert "正面评价" in result
        assert "改进建议" in result
        assert "good_task" in result
        assert "bad_task" in result
        assert "mid_task" not in result

    def test_limits_to_3_each(self):
        records = [self._rec(5, f"good_{i}", "great") for i in range(10)]
        records += [self._rec(1, f"bad_{i}", "awful") for i in range(10)]
        result = format_feedback_prompt(records)
        assert result.count("good_") == 3
        assert result.count("bad_") == 3


# ── Section 3: OpenClaw Compatibility ─────────────────────────────────

def test_build_openclaw_source_specs_resolves_relative_extra_dirs(tmp_path: Path):
    repo_root = tmp_path / "openclaw-main"
    repo_skill = repo_root / "skills" / "weather"
    repo_skill.mkdir(parents=True, exist_ok=True)
    (repo_skill / "SKILL.md").write_text(
        """---
name: weather
description: Weather
---
""",
        encoding="utf-8",
    )

    config_path = tmp_path / ".openclaw" / "openclaw.json"
    extra_dir = config_path.parent / "shared-skills"
    extra_skill = extra_dir / "ops"
    extra_skill.mkdir(parents=True, exist_ok=True)
    (extra_skill / "SKILL.md").write_text(
        """---
name: ops
description: Ops
---
""",
        encoding="utf-8",
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
skills:
  load:
    extraDirs:
      - ./shared-skills
""".strip(),
        encoding="utf-8",
    )

    bridge = build_openclaw_source_specs(
        repo_root,
        config_path=config_path,
        source_name="vendor",
        include_extra_dirs=True,
    )

    assert bridge["config_loaded"] is True
    assert [item["name"] for item in bridge["source_specs"]] == [
        "vendor",
        "vendor_extra_1_shared_skills",
    ]
    assert bridge["config_summary"]["extra_dirs"] == [str(extra_dir.resolve())]


def test_build_openclaw_skill_bridge_report_uses_skill_key_and_entry_state():
    skill = SkillDefinition(
        name="weather-skill",
        description="Weather",
        skill_format="openclaw",
        openclaw_metadata={"skillKey": "weather", "emoji": "⛅"},
        primary_env="WEATHER_TOKEN",
        requires_config=["channels.weather"],
    )

    report = build_openclaw_skill_bridge_report(
        skill,
        {
            "skills": {
                "entries": {
                    "weather": {
                        "enabled": True,
                        "apiKey": "secret",
                        "env": {"WEATHER_TOKEN": "from-config"},
                        "config": {"endpoint": "https://example.com"},
                    }
                }
            },
            "channels": {"weather": {"token": "configured"}},
        },
    )

    assert report["entry_key"] == "weather"
    assert report["entry_present"] is True
    assert report["entry_enabled"] is True
    assert report["entry_api_key_present"] is True
    assert report["entry_env_keys"] == ["WEATHER_TOKEN"]
    assert report["entry_config_keys"] == ["endpoint"]
    assert report["primary_env_bridge"][0]["available_via_api_key"] is True
    assert report["primary_env_bridge"][0]["available_via_entry_env"] is True
    assert report["global_config_bridge"][0] == {"path": "channels.weather", "present": True}


def test_build_openclaw_runtime_env_prefers_entry_env_and_api_key():
    skill = SkillDefinition(
        name="weather",
        description="Weather",
        skill_format="openclaw",
        primary_env="WEATHER_TOKEN",
    )

    env = build_openclaw_runtime_env(
        skill,
        {
            "skills": {
                "entries": {
                    "weather": {
                        "enabled": True,
                        "apiKey": "secret-api-key",
                        "env": {"OTHER_TOKEN": "other", "WEATHER_TOKEN": "preferred"},
                    }
                }
            }
        },
    )

    assert env == {"OTHER_TOKEN": "other", "WEATHER_TOKEN": "preferred"}


def test_build_openclaw_runtime_env_respects_disabled_entry():
    skill = SkillDefinition(
        name="weather",
        description="Weather",
        skill_format="openclaw",
        primary_env="WEATHER_TOKEN",
    )

    env = build_openclaw_runtime_env(
        skill,
        {"skills": {"entries": {"weather": {"enabled": False, "apiKey": "secret"}}}},
    )

    assert env == {}


def test_import_openclaw_channels_for_pybot_imports_supported_and_skips_rest():
    result = import_openclaw_channels_for_pybot(
        {
            "channels": {
                "wechat": {"token": "wechat-token"},
                "webhook": {"enabled": True},
                "discord": {"enabled": True},
            }
        },
        {"existing": {"enabled": True}},
    )

    assert set(result["imported"]) == {"wechat", "webhook"}
    assert result["channels"]["existing"]["enabled"] is True
    assert result["channels"]["wechat"]["kind"] == "wechat"
    assert result["channels"]["webhook"]["kind"] == "webhook"
    assert result["skipped"] == [{"name": "discord", "reason": "unsupported_by_pybot"}]


# ── Section 4: Memory and Tool Integration with Mock LLM ─────────────

class TestMemoryEngineWithMock:
    def test_journal_writes_records(self, tmp_path):
        from core.systems.memory import MemoryEngine, Modality

        journal_response = (
            "- [偏好] 用户偏好 Python 类型注解\n"
            "- [决策] 建议拆分函数\n"
            "- [事实] 用户接受了建议"
        )
        caller = mock_llm_caller(response=journal_response)
        eng = MemoryEngine(tmp_path, journal_caller=caller)
        try:
            messages = [
                {"role": "user", "content": "帮我重构这个函数"},
                {"role": "assistant", "content": "好的，我建议拆分为两个函数"},
                {"role": "user", "content": "可以"},
                {"role": "assistant", "content": "已完成重构"},
                {"role": "user", "content": "谢谢"},
            ]
            eng._pipeline._journal_sync(messages, None)
            journals = eng.store.list(modality=Modality.JOURNAL.value)
            assert journals
            assert "偏好" in journals[0].content
        finally:
            eng.close()

    def test_distill_promotes_facts(self, tmp_path):
        from core.systems.memory import MemoryEngine, Modality

        eng = MemoryEngine(
            tmp_path,
            distill_caller=mock_llm_caller(response=(
                "[MEMORY]\n"
                "- [偏好|★★★] 用户偏好 Python 类型注解\n"
                "- [事实|★★] 用户倾向函数式风格\n"
            )),
        )
        try:
            eng.ingest(
                Modality.JOURNAL,
                "- [偏好] 用户喜欢类型注解",
                metadata={"date_str": "2025-01-01"},
            )
            eng._pipeline._distill_sync(force=True)
            facts = eng.store.list(modality=Modality.FACT.value)
            assert any("类型注解" in f.content for f in facts)
        finally:
            eng.close()

    def test_export_memory_md_includes_facts(self, tmp_path):
        from core.systems.memory import MemoryEngine, Modality

        eng = MemoryEngine(tmp_path)
        try:
            eng.ingest(
                Modality.FACT,
                "Python 类型注解",
                importance=0.9,
                metadata={"soft_tags": {"偏好": 1.0}},
            )
            md = eng.export_memory_md()
            assert "类型注解" in md
            assert "[MEMORY]" in md
        finally:
            eng.close()

    def test_today_journal_empty_when_none(self, tmp_path):
        from core.systems.memory import MemoryEngine

        eng = MemoryEngine(tmp_path)
        try:
            assert eng.get_today_journal() == ""
        finally:
            eng.close()


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
