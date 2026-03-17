"""Tests for core.yaml_config."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.yaml_config import (
    auto_discover_yaml,
    interpolate_placeholders,
    load_agents_yaml,
    load_tasks_yaml,
)

yaml = pytest.importorskip("yaml")


class TestInterpolatePlaceholders:
    def test_basic(self):
        assert interpolate_placeholders("Hello {name}!", {"name": "World"}) == "Hello World!"

    def test_missing_key_unchanged(self):
        assert interpolate_placeholders("{a} and {b}", {"a": "X"}) == "X and {b}"

    def test_no_placeholders(self):
        assert interpolate_placeholders("plain text", {"x": "y"}) == "plain text"

    def test_multiple_same_key(self):
        assert interpolate_placeholders("{x}-{x}", {"x": "1"}) == "1-1"

    def test_numeric_value(self):
        assert interpolate_placeholders("count: {n}", {"n": 42}) == "count: 42"

    def test_empty_variables(self):
        assert interpolate_placeholders("{x}", {}) == "{x}"


class TestLoadAgentsYaml:
    def _write(self, tmpdir, content):
        p = Path(tmpdir) / "agents.yaml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_basic_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
data_analyst:
  role: 数据分析师
  description: 擅长数据清洗和可视化
  system_prompt: |
    你是数据分析师
  capabilities: [数据分析, Python]
  model: gpt-4
  temperature: 0.5
""",
            )
            agents = load_agents_yaml(p)
            assert len(agents) == 1
            a = agents[0]
            assert a["name"] == "data_analyst"
            assert a["role"] == "数据分析师"
            assert a["model"] == "gpt-4"
            assert a["temperature"] == 0.5
            assert "数据分析" in a["capabilities"]

    def test_multiple_agents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
agent_a:
  role: A
  description: First
agent_b:
  role: B
  description: Second
""",
            )
            agents = load_agents_yaml(p)
            assert len(agents) == 2

    def test_with_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
coder:
  role: coder
  description: writes code
  profile: builder
""",
            )
            agents = load_agents_yaml(p)
            assert agents[0]["capability_profile"] == {"preset": "builder"}

    def test_interpolation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
analyst:
  role: "{domain}分析师"
  description: 分析{domain}数据
  system_prompt: 你专注{domain}
""",
            )
            agents = load_agents_yaml(p, variables={"domain": "金融"})
            assert agents[0]["role"] == "金融分析师"
            assert "金融" in agents[0]["system_prompt"]

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_agents_yaml("/nonexistent/agents.yaml")

    def test_invalid_yaml_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(tmpdir, "- item1\n- item2\n")
            with pytest.raises(ValueError, match="mapping"):
                load_agents_yaml(p)

    def test_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
minimal:
  role: test
""",
            )
            agents = load_agents_yaml(p)
            a = agents[0]
            assert a["description"] == ""
            assert a["system_prompt"] == ""
            assert a["model"] == "gemini-3-flash-preview"
            assert a["temperature"] == 0.7


class TestLoadTasksYaml:
    def _write(self, tmpdir, content):
        p = Path(tmpdir) / "tasks.yaml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_basic_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
analyze_data:
  description: 分析销售数据
  agent: data_analyst
  expected_output: 分析报告
  context: [fetch_data]
""",
            )
            tasks = load_tasks_yaml(p)
            assert len(tasks) == 1
            t = tasks[0]
            assert t["name"] == "analyze_data"
            assert t["agent"] == "data_analyst"
            assert t["context"] == ["fetch_data"]

    def test_interpolation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
analyze:
  description: 分析 {topic} 的数据
  agent: analyst
""",
            )
            tasks = load_tasks_yaml(p, variables={"topic": "电商"})
            assert "电商" in tasks[0]["description"]

    def test_extra_fields_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
task1:
  description: test
  agent: bot
  priority: high
  timeout: 30
""",
            )
            tasks = load_tasks_yaml(p)
            assert tasks[0]["priority"] == "high"
            assert tasks[0]["timeout"] == 30

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_tasks_yaml("/nonexistent/tasks.yaml")

    def test_invalid_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(tmpdir, "just a string")
            with pytest.raises(ValueError, match="mapping"):
                load_tasks_yaml(p)


class TestAutoDiscoverYaml:
    def test_both_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agents.yaml").write_text("a: {}", encoding="utf-8")
            (Path(tmpdir) / "tasks.yaml").write_text("t: {}", encoding="utf-8")
            result = auto_discover_yaml(tmpdir)
            assert result["agents"] is not None
            assert result["tasks"] is not None

    def test_none_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = auto_discover_yaml(tmpdir)
            assert result["agents"] is None
            assert result["tasks"] is None

    def test_yml_extension(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agents.yml").write_text("a: {}", encoding="utf-8")
            result = auto_discover_yaml(tmpdir)
            assert result["agents"] is not None

    def test_yaml_takes_priority_over_yml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agents.yaml").write_text("yaml: {}", encoding="utf-8")
            (Path(tmpdir) / "agents.yml").write_text("yml: {}", encoding="utf-8")
            result = auto_discover_yaml(tmpdir)
            assert result["agents"].name == "agents.yaml"
