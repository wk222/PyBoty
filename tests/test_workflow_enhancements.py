"""Tests for workflow engine enhancements:

- Variable system: $input, $last, env.XXX
- Database query node
- File read / write nodes
- Plugin protocol + registry
- notify_and_continue error strategy
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

from core.assets.workflows.workflow_graph_runtime import WorkflowGraphRuntime, _last_completed_output
from core.assets.workflows.workflow_models import (
    FlowNode,
    NodeStatus,
    NodeType,
    OnErrorStrategy,
    WorkflowDef,
)
from core.assets.workflows import (
    run_database_query,
    run_file_read,
    run_file_write,
)
from core.assets.workflows.workflow_plugin import (
    dispatch_plugin,
    get_plugin,
    list_plugins,
    register_node_plugin,
    unregister_node_plugin,
)

# ── Variable System Tests ─────────────────────────────────────────


class TestBuiltinVariables:
    def _make_workflow(self) -> WorkflowDef:
        wf = WorkflowDef(id="test", name="test")
        wf.variables["input"] = {"query": "hello"}
        return wf

    def _graph(self) -> WorkflowGraphRuntime:
        return WorkflowGraphRuntime()

    def test_input_variable(self):
        wf = self._make_workflow()
        result = self._graph().resolve_var("${input}", wf)
        assert result == {"query": "hello"}

    def test_input_in_template(self):
        wf = self._make_workflow()
        result = self._graph().resolve_var("Query is: ${input}", wf)
        assert "hello" in result

    def test_last_variable_empty(self):
        wf = self._make_workflow()
        result = self._graph().resolve_var("${last}", wf)
        assert result is None

    def test_last_variable_with_completed_nodes(self):
        wf = self._make_workflow()
        n1 = FlowNode(id="n1", type=NodeType.EXEC, status=NodeStatus.COMPLETED, output="first", completed_at=1.0)
        n2 = FlowNode(id="n2", type=NodeType.EXEC, status=NodeStatus.COMPLETED, output="second", completed_at=2.0)
        wf.nodes["n1"] = n1
        wf.nodes["n2"] = n2
        result = self._graph().resolve_var("${last}", wf)
        assert result == "second"

    def test_env_variable(self):
        wf = self._make_workflow()
        with patch.dict(os.environ, {"TEST_WF_VAR": "env_value_123"}):
            result = self._graph().resolve_var("${env.TEST_WF_VAR}", wf)
        assert result == "env_value_123"

    def test_env_variable_missing(self):
        wf = self._make_workflow()
        result = self._graph().resolve_var("${env.NONEXISTENT_VAR_XYZ}", wf)
        assert result == ""


class TestLastCompletedOutput:
    def test_empty_workflow(self):
        wf = WorkflowDef(id="t", name="t")
        assert _last_completed_output(wf) is None

    def test_picks_latest(self):
        wf = WorkflowDef(id="t", name="t")
        wf.nodes["a"] = FlowNode(id="a", type=NodeType.EXEC, status=NodeStatus.COMPLETED, output="A", completed_at=10.0)
        wf.nodes["b"] = FlowNode(id="b", type=NodeType.EXEC, status=NodeStatus.COMPLETED, output="B", completed_at=20.0)
        wf.nodes["c"] = FlowNode(id="c", type=NodeType.EXEC, status=NodeStatus.PENDING)
        assert _last_completed_output(wf) == "B"


# ── Database Query Node Tests ─────────────────────────────────────


class TestDatabaseQueryNode:
    def test_sqlite_select(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE items (id INTEGER, name TEXT)")
            conn.execute("INSERT INTO items VALUES (1, 'alpha')")
            conn.execute("INSERT INTO items VALUES (2, 'beta')")
            conn.commit()
            conn.close()

            result = run_database_query({
                "provider": "sqlite",
                "connection_string": db_path,
                "query": "SELECT * FROM items ORDER BY id",
            })
            assert result["row_count"] == 2
            assert result["rows"][0]["name"] == "alpha"
        finally:
            os.unlink(db_path)

    def test_readonly_blocks_insert(self):
        with pytest.raises(ValueError, match="readonly mode blocks INSERT"):
            run_database_query({
                "provider": "sqlite",
                "query": "INSERT INTO t VALUES (1)",
                "readonly": True,
            })

    def test_readonly_allows_select(self):
        result = run_database_query({
            "provider": "sqlite",
            "connection_string": ":memory:",
            "query": "SELECT 1 AS v",
            "readonly": True,
        })
        assert result["rows"][0]["v"] == 1

    def test_empty_query_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            run_database_query({"query": ""})


# ── File Read / Write Node Tests ─────────────────────────────────


class TestFileNodes:
    def test_file_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_file_write({"path": "test.txt", "content": "hello world"}, tmpdir)
            result = run_file_read({"path": "test.txt"}, tmpdir)
            assert result["content"] == "hello world"
            assert result["size"] > 0

    def test_file_append(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_file_write({"path": "log.txt", "content": "line1\n"}, tmpdir)
            run_file_write({"path": "log.txt", "content": "line2\n", "mode": "append"}, tmpdir)
            result = run_file_read({"path": "log.txt"}, tmpdir)
            assert "line1" in result["content"]
            assert "line2" in result["content"]

    def test_file_read_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                run_file_read({"path": "nope.txt"}, tmpdir)

    def test_path_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="路径越界"):
                run_file_read({"path": "../../etc/passwd"}, tmpdir)

    def test_write_creates_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_file_write({"path": "sub/dir/file.txt", "content": "nested"}, tmpdir)
            result = run_file_read({"path": "sub/dir/file.txt"}, tmpdir)
            assert result["content"] == "nested"

    def test_empty_path_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="'path'"):
                run_file_read({"path": ""}, tmpdir)


# ── Plugin Protocol Tests ─────────────────────────────────────────


class _DummyPlugin:
    node_type = "custom_echo"

    def execute(self, config: dict[str, Any], context: dict[str, Any]) -> Any:
        return {"echo": config.get("message", ""), "vars_count": len(context.get("variables", {}))}

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
        }


class TestPluginRegistry:
    def setup_method(self):
        unregister_node_plugin("custom_echo")

    def teardown_method(self):
        unregister_node_plugin("custom_echo")

    def test_register_and_get(self):
        plugin = _DummyPlugin()
        register_node_plugin(plugin)
        assert get_plugin("custom_echo") is plugin

    def test_list_plugins(self):
        register_node_plugin(_DummyPlugin())
        plugins = list_plugins()
        assert "custom_echo" in plugins

    def test_dispatch_plugin(self):
        register_node_plugin(_DummyPlugin())
        result = dispatch_plugin("custom_echo", {"message": "hi"}, {"variables": {"a": 1}})
        assert result["echo"] == "hi"
        assert result["vars_count"] == 1

    def test_dispatch_missing_raises(self):
        with pytest.raises(KeyError, match="custom_echo"):
            dispatch_plugin("custom_echo", {}, {})

    def test_unregister(self):
        register_node_plugin(_DummyPlugin())
        assert unregister_node_plugin("custom_echo") is True
        assert get_plugin("custom_echo") is None

    def test_unregister_missing(self):
        assert unregister_node_plugin("nonexistent") is False


# ── OnErrorStrategy.NOTIFY_AND_CONTINUE Tests ────────────────────


class TestNotifyAndContinue:
    def test_enum_value_exists(self):
        assert OnErrorStrategy.NOTIFY_AND_CONTINUE.value == "notify_and_continue"

    def test_node_type_enums_include_new_types(self):
        assert NodeType.DATABASE_QUERY.value == "database_query"
        assert NodeType.FILE_READ.value == "file_read"
        assert NodeType.FILE_WRITE.value == "file_write"
