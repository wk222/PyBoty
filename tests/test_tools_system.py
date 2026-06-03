"""Unified tests for core Tool systems and middleware (Eighth Round).

Consolidated and merged from 14 individual tool test files:
* test_tool_arg_repair.py
* test_tool_cache.py
* test_tool_call_runtime.py
* test_tool_control_runtime.py
* test_tool_creator.py
* test_tool_delegation_runtime.py
* test_tool_dynamic_inventory.py
* test_tool_eviction_middleware.py
* test_tool_middleware.py
* test_tool_middleware_factory.py
* test_tool_model_runtime.py
* test_tool_policy_pipeline.py
* test_tool_result_normalize.py
* test_tool_schema_quality.py
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain.tools import BaseTool
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

# Core tool-related imports
from core.assets.agents import AgentDefinition, AgentStorage
from core.systems.agents.agent_creator import (
    AgentCreatorTool,
    AskAgentTool,
    DelegateToAgentTool,
    ListAgentsTool,
    RemoveAgentTool,
)
from core.assets.skills.skill_marketplace import (
    CreateSkillTool,
    InstallSkillTool,
    PackageSkillTool,
    SearchSkillsTool,
    UninstallSkillTool,
)
from core.assets.tools import (
    DelegatedToolApprovalRuntime,
    DynamicToolInventory,
    DynamicToolMiddleware,
    TemplateToolCreator,
    ToolCache,
    ToolCallRuntime,
    ToolCreationError,
    ToolCreatorTool,
    ToolModelHookRuntime,
    ToolStorage,
    build_tool_definition,
    cached_tool_call,
    create_dynamic_tool,
    persist_validated_tool_definition,
    tool_delegation_runtime,
)
from core.assets.tools.clarification_tool import (
    AnalyzeRequirementTool,
    AskClarificationTool,
)
from core.assets.tools.tool_chain import RunChainTool, ToolStatsTool
from core.assets.tools.tool_creator import (
    ListTemplatesTool,
    RemoveToolTool,
)
from core.assets.tools.tool_middleware_factory import (
    build_tool_middleware_components,
    create_decorator_middleware,
    create_tool_middleware,
)
from core.assets.tools.tool_result_normalize import (
    canonicalize_dynamic_tool_content_string,
    normalize_for_app_tool_proxy,
    peel_json_wrapped_strings,
)
from core.assets.workflows.workflow_tools import (
    GenerateWorkflowTool,
    ListWorkflowsTool,
    ResumeWorkflowTool,
    RunWorkflowTool,
    TriggerWorkflowTool,
)
from core.systems.apps.app_creator import get_app_creator_tools
from core.systems.apps.app_verifier import get_app_verifier_tools
from core.systems.capability.capability_bus import CapBusTool
from core.systems.eval.eval_framework import EvalResponseTool, RunTestsTool
from core.systems.execution.execution_loop import (
    ExecCodeTool,
    IterativeFixTool,
    ScanProjectTool,
)
from core.systems.governance import (
    AgentControlPolicy,
    ApprovalQueue,
    PathPolicyStage,
    RateLimitStage,
    ToolPolicyContext,
    ToolRiskLevel,
    build_default_tool_policy_pipeline,
)
from core.systems.governance.tool_approval_runtime import (
    build_tool_approval_resume_command,
    create_tool_approval_request,
    extract_tool_approval_interrupts,
)
from core.systems.governance.tool_control_runtime import ToolControlRuntime
from core.systems.runtime.hooks_runtime import HooksRuntime, HookPhase
from core.systems.context.projected_runtime_view import build_projected_runtime_view
from core.systems.runtime.trusted_settings import build_trusted_settings_bundle
from core.systems.middleware.tool_arg_repair import (
    _coerce_value,
    _pick_best_type,
    _repair_code_content,
    _repair_js_regex,
    _repair_over_escaped_newlines,
    repair_tool_args,
)
from core.systems.middleware.tool_eviction_middleware import (
    LCToolEvictionMiddleware,
)


# ---------------------------------------------------------------------------
# 1. Tool Argument Repair Tests (formerly test_tool_arg_repair.py)
# ---------------------------------------------------------------------------

class DummyInput(BaseModel):
    name: str = Field(description="A name")
    count: int = Field(default=0, description="A count")
    tags: list[str] = Field(default_factory=list, description="Tag list")
    enabled: bool = Field(default=True, description="Toggle")
    score: float = Field(default=0.0, description="A score")
    metadata: dict = Field(default_factory=dict, description="Extra metadata")


class DummyTool(BaseTool):
    name: str = "dummy_tool"
    description: str = "A test tool"
    args_schema: type[BaseModel] = DummyInput

    def _run(self, **kwargs):
        return "ok"


class FileInput(BaseModel):
    app_name: str = Field(description="App id")
    file_path: str = Field(description="File path")
    content: str = Field(description="File content")


class UpdateAppFileTool(BaseTool):
    name: str = "update_app_file"
    description: str = "Update app file"
    args_schema: type[BaseModel] = FileInput

    def _run(self, **kwargs):
        return "ok"


TOOLS: list[BaseTool] = [DummyTool(), UpdateAppFileTool()]


class TestCoerceValue:
    def test_list_to_string(self):
        result = _coerce_value([1, 2, 3], "string")
        assert result == "[1, 2, 3]"

    def test_dict_to_string(self):
        result = _coerce_value({"a": 1}, "string")
        assert result == json.dumps({"a": 1}, ensure_ascii=False)

    def test_int_to_string(self):
        result = _coerce_value(42, "string")
        assert result == "42"

    def test_string_stays_string(self):
        val = "hello"
        result = _coerce_value(val, "string")
        assert result is val

    def test_str_to_int(self):
        assert _coerce_value("10", "integer") == 10

    def test_float_to_int(self):
        assert _coerce_value(3.0, "integer") == 3

    def test_invalid_str_to_int_stays(self):
        val = "abc"
        assert _coerce_value(val, "integer") is val

    def test_str_to_float(self):
        assert _coerce_value("3.14", "number") == pytest.approx(3.14)

    def test_str_true_to_bool(self):
        assert _coerce_value("true", "boolean") is True
        assert _coerce_value("YES", "boolean") is True
        assert _coerce_value("1", "boolean") is True

    def test_str_false_to_bool(self):
        assert _coerce_value("false", "boolean") is False
        assert _coerce_value("NO", "boolean") is False
        assert _coerce_value("0", "boolean") is False

    def test_bool_stays_bool(self):
        assert _coerce_value(True, "boolean") is True

    def test_json_str_to_array(self):
        assert _coerce_value('[1, 2]', "array") == [1, 2]

    def test_non_array_str_wraps(self):
        assert _coerce_value("hello", "array") == ["hello"]

    def test_list_stays_list(self):
        val = [1, 2]
        assert _coerce_value(val, "array") is val

    def test_scalar_wraps_to_array(self):
        assert _coerce_value(42, "array") == [42]

    def test_json_str_to_object(self):
        assert _coerce_value('{"a": 1}', "object") == {"a": 1}

    def test_dict_stays_dict(self):
        val = {"a": 1}
        assert _coerce_value(val, "object") is val

    def test_invalid_json_str_stays_for_object(self):
        val = "not-json"
        assert _coerce_value(val, "object") is val

    def test_unknown_type_returns_unchanged(self):
        val = "test"
        assert _coerce_value(val, "null") is val

    def test_bool_not_treated_as_int(self):
        assert _coerce_value(True, "integer") is True


class TestPickBestType:
    def test_list_prefers_string(self):
        assert _pick_best_type([{"type": "array"}, {"type": "string"}], [1, 2]) == "string"

    def test_dict_prefers_string(self):
        assert _pick_best_type([{"type": "object"}, {"type": "string"}], {"a": 1}) == "string"

    def test_str_prefers_array(self):
        assert _pick_best_type([{"type": "string"}, {"type": "array"}], "[1,2]") == "array"

    def test_defaults_to_first(self):
        assert _pick_best_type([{"type": "integer"}, {"type": "string"}], 42) == "integer"

    def test_empty_returns_none(self):
        assert _pick_best_type([], "x") is None


class TestRepairToolArgs:
    def test_list_to_str_repair(self):
        args = {"name": ["a", "b"], "count": 0}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result["name"] == '["a", "b"]'

    def test_str_to_int_repair(self):
        args = {"name": "test", "count": "5"}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result["count"] == 5

    def test_str_to_bool_repair(self):
        args = {"name": "test", "enabled": "false"}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result["enabled"] is False

    def test_str_to_list_repair(self):
        args = {"name": "test", "tags": '["a", "b"]'}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result["tags"] == ["a", "b"]

    def test_str_to_dict_repair(self):
        args = {"name": "test", "metadata": '{"key": "val"}'}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result["metadata"] == {"key": "val"}

    def test_no_repair_needed_returns_same(self):
        args = {"name": "test", "count": 3}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result is args

    def test_unknown_tool_returns_unchanged(self):
        args = {"name": [1, 2]}
        result = repair_tool_args("nonexistent_tool", args, TOOLS)
        assert result is args

    def test_missing_param_ignored(self):
        args = {"name": "test"}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result is args


# ── _repair_js_regex ──


class TestRepairJsRegex:
    def test_fixes_broken_regex_newline(self):
        broken = "var x = text.replace(/\n/g, '<br>');"
        fixed = _repair_js_regex(broken)
        assert "/\\n/g" in fixed
        assert "\n" not in fixed.split("/g")[0].split("replace(")[1]

    def test_fixes_broken_string_newline(self):
        broken = "var s = 'hello\nworld';"
        fixed = _repair_js_regex(broken)
        assert "\\n" in fixed
        assert fixed.count("\n") < broken.count("\n")

    def test_preserves_correct_code(self):
        correct = "var x = text.replace(/\\n/g, '<br>');\nvar y = 1;"
        assert _repair_js_regex(correct) == correct

    def test_handles_empty_string(self):
        assert _repair_js_regex("") == ""


# ── _repair_over_escaped_newlines ──


class TestRepairOverEscapedNewlines:
    def test_converts_bare_escaped_newline(self):
        code = "var a = 1;\\nvar b = 2;"
        result = _repair_over_escaped_newlines(code)
        assert result == "var a = 1;\nvar b = 2;"

    def test_preserves_escaped_in_string(self):
        code = "var s = 'hello\\nworld';"
        result = _repair_over_escaped_newlines(code)
        assert result == code

    def test_preserves_escaped_in_template(self):
        code = "var s = `hello\\nworld`;"
        result = _repair_over_escaped_newlines(code)
        assert result == code

    def test_no_change_when_no_escaped_n(self):
        code = "var a = 1;\nvar b = 2;"
        assert _repair_over_escaped_newlines(code) is code


# ── _repair_code_content ──


class TestRepairCodeContent:
    def test_repairs_js_file_content(self):
        args = {
            "app_name": "test",
            "file_path": "static/app.js",
            "content": "text.replace(/\n/g, '')",
        }
        result = _repair_code_content("update_app_file", args)
        assert result is not None
        assert "/\\n/g" in result["content"]

    def test_skips_non_js_file(self):
        args = {
            "app_name": "test",
            "file_path": "index.html",
            "content": "<div>\n</div>",
        }
        result = _repair_code_content("update_app_file", args)
        assert result is None

    def test_skips_unknown_tool(self):
        args = {"file_path": "app.js", "content": "/\n/g"}
        result = _repair_code_content("some_other_tool", args)
        assert result is None

    def test_skips_non_string_content(self):
        args = {"app_name": "x", "file_path": "app.js", "content": 123}
        result = _repair_code_content("update_app_file", args)
        assert result is None


# ---------------------------------------------------------------------------
# 2. Tool Cache Tests (formerly test_tool_cache.py)
# ---------------------------------------------------------------------------

class TestToolCacheClass:
    def setup_method(self):
        self.cache = ToolCache(default_ttl=10.0)

    def test_miss_then_hit(self):
        hit, val = self.cache.get("tool_a", "hash1")
        assert not hit
        self.cache.set("tool_a", "hash1", "result_1")
        hit, val = self.cache.get("tool_a", "hash1")
        assert hit
        assert val == "result_1"

    def test_different_tools_different_keys(self):
        self.cache.set("tool_a", "h1", "r1")
        self.cache.set("tool_b", "h1", "r2")
        _, v1 = self.cache.get("tool_a", "h1")
        _, v2 = self.cache.get("tool_b", "h1")
        assert v1 == "r1"
        assert v2 == "r2"

    def test_ttl_expiry(self):
        self.cache.set("tool_a", "h1", "old", ttl=0.01)
        time.sleep(0.05)
        hit, _ = self.cache.get("tool_a", "h1")
        assert not hit

    def test_no_ttl(self):
        self.cache.set("tool_a", "h1", "persistent", ttl=None)
        hit, val = self.cache.get("tool_a", "h1")
        assert hit
        assert val == "persistent"

    def test_invalidate_all(self):
        self.cache.set("t1", "h1", "a")
        self.cache.set("t2", "h2", "b")
        removed = self.cache.invalidate()
        assert removed == 2
        assert self.cache.stats()["size"] == 0

    def test_invalidate_specific_tool(self):
        self.cache.set("t1", "h1", "a")
        self.cache.set("t1", "h2", "b")
        self.cache.set("t2", "h3", "c")
        removed = self.cache.invalidate("t1")
        assert removed == 2
        assert self.cache.stats()["size"] == 1

    def test_stats(self):
        self.cache.set("t", "h", "v")
        self.cache.get("t", "h")  # hit
        self.cache.get("t", "missing")  # miss
        s = self.cache.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["size"] == 1
        assert s["hit_rate"] == 0.5

    def test_stats_empty(self):
        s = self.cache.stats()
        assert s["hit_rate"] == 0.0

    def test_evict_expired(self):
        self.cache.set("t", "h1", "a", ttl=0.01)
        self.cache.set("t", "h2", "b", ttl=None)
        time.sleep(0.05)
        evicted = self.cache._evict_expired()
        assert evicted == 1
        assert self.cache.stats()["size"] == 1


class TestHashArgs:
    def test_deterministic(self):
        h1 = ToolCache.hash_args({"a": 1, "b": "hello"})
        h2 = ToolCache.hash_args({"b": "hello", "a": 1})
        assert h1 == h2

    def test_different_args_different_hash(self):
        h1 = ToolCache.hash_args({"x": 1})
        h2 = ToolCache.hash_args({"x": 2})
        assert h1 != h2


class TestCachedToolCallClass:
    def test_caches_result(self):
        cache = ToolCache()
        call_count = 0

        def my_tool(x=0):
            nonlocal call_count
            call_count += 1
            return x * 2

        r1 = cached_tool_call(cache, "my_tool", {"x": 5}, my_tool)
        r2 = cached_tool_call(cache, "my_tool", {"x": 5}, my_tool)
        assert r1 == 10
        assert r2 == 10
        assert call_count == 1

    def test_not_cacheable(self):
        cache = ToolCache()
        call_count = 0

        def my_tool(x=0):
            nonlocal call_count
            call_count += 1
            return x

        cached_tool_call(cache, "t", {"x": 1}, my_tool, cacheable=False)
        cached_tool_call(cache, "t", {"x": 1}, my_tool, cacheable=False)
        assert call_count == 2

    def test_different_args_not_cached(self):
        cache = ToolCache()
        call_count = 0

        def my_tool(x=0):
            nonlocal call_count
            call_count += 1
            return x

        cached_tool_call(cache, "t", {"x": 1}, my_tool)
        cached_tool_call(cache, "t", {"x": 2}, my_tool)
        assert call_count == 2


class TestConcurrency:
    def test_concurrent_reads_writes(self):
        cache = ToolCache(default_ttl=60.0)
        errors = []

        def writer():
            try:
                for i in range(50):
                    cache.set("t", f"h{i}", f"v{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    cache.get("t", f"h{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ---------------------------------------------------------------------------
# 3. Tool Call Runtime Tests (formerly test_tool_call_runtime.py)
# ---------------------------------------------------------------------------

class TestToolCallRuntimeClass:
    def test_tool_call_runtime_executes_low_risk_tool_call(self):
        from core.systems.middleware.tool_middleware_observability import (
            ToolMiddlewareObservability,
        )

        inventory = DynamicToolInventory()
        control_runtime = ToolControlRuntime(
            control_policy=AgentControlPolicy.from_config({"mode": "open"}),
            approval_scope="root:test",
            observability=ToolMiddlewareObservability(
                max_recent_calls=8,
                stuck_loop_threshold=5,
                stuck_loop_kill_threshold=8,
            ),
        )
        delegated_runtime = DelegatedToolApprovalRuntime(
            approval_queue=ApprovalQueue(),
            approval_scope="root:test",
        )
        runtime = ToolCallRuntime(
            inventory=inventory,
            control_runtime=control_runtime,
            delegated_runtime=delegated_runtime,
        )
        request = SimpleNamespace(
            tool_call={
                "name": "lookup",
                "args": {"q": "release"},
                "id": "call_1",
            }
        )

        result = runtime.run_tool_call(
            request,
            lambda tool_request: ToolMessage(
                content=f"ok:{tool_request.tool_call['args']['q']}",
                tool_call_id="call_1",
                status="success",
            ),
        )

        assert isinstance(result, ToolMessage)
        assert result.content == "ok:release"
        assert control_runtime.get_usage_stats()["lookup"] == 1


# ---------------------------------------------------------------------------
# 4. Tool Control Runtime Tests (formerly test_tool_control_runtime.py)
# ---------------------------------------------------------------------------

class TestToolControlRuntimeClass:
    def test_tool_control_runtime_blocks_disallowed_dynamic_tools(self):
        runtime = ToolControlRuntime(
            control_policy=AgentControlPolicy.from_config({"mode": "strict"}),
            approval_scope="root:test",
        )

        result = runtime.enforce_tool_call(
            tool_name="custom_lookup",
            tool_args={"city": "Shanghai"},
            tool_call_id="call_1",
            is_dynamic=True,
        )

        assert result is not None
        payload = json.loads(result.content)
        assert payload["error"].startswith("CONTROL_POLICY_BLOCKED:")
        assert payload["tool_name"] == "custom_lookup"

    def test_tool_control_runtime_reject_decision_returns_error_tool_message(self):
        runtime = ToolControlRuntime(
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            approval_scope="root:test",
        )
        control_decision = runtime.control_policy.evaluate_tool_call("create_agent", is_dynamic=False)

        processed_tool_call, tool_message = runtime.apply_approval_decision(
            tool_call={
                "name": "create_agent",
                "args": {"agent_name": "helper"},
                "id": "call_1",
            },
            decision={"type": "reject", "message": "not allowed"},
            control_decision=control_decision,
        )

        assert processed_tool_call is None
        assert tool_message is not None
        assert tool_message.status == "error"
        assert tool_message.content == "not allowed"

    def test_tool_control_runtime_validates_resume_decision_count(self):
        with pytest.raises(ValueError, match="decision count mismatch"):
            ToolControlRuntime.extract_resume_decisions(
                {"decisions": [{"type": "approve"}]},
                expected_count=2,
            )


# ---------------------------------------------------------------------------
# 5. Tool Creator Tests (formerly test_tool_creator.py)
# ---------------------------------------------------------------------------

class TestToolCreatorClass:
    def test_tool_creator_persists_and_executes_tool(self, temp_paths):
        storage = ToolStorage(str(temp_paths.global_tools_dir))
        creator = ToolCreatorTool(storage=storage)

        result = json.loads(
            creator._run(
                tool_name="adder",
                description="Add two integers",
                parameters='[{"name":"a","type":"int","description":"left"},{"name":"b","type":"int","description":"right"}]',
                code="result = a + b",
                dependencies=[],
                usage_guide="用于简单加法",
            )
        )

        assert result["success"] is True
        definition = storage.get_tool("adder")
        assert definition is not None

        dynamic_tool = create_dynamic_tool(definition, project_paths=temp_paths)
        execution = json.loads(dynamic_tool._run(a=2, b=3))

        assert execution["success"] is True
        assert execution["result"] == 5

    def test_template_tool_creator_targets_agent_storage(self, temp_paths):
        storage = ToolStorage(str(temp_paths.global_tools_dir))
        agent_storage = AgentStorage(str(temp_paths.agents_dir))
        agent_storage.add_agent(
            AgentDefinition(
                name="helper",
                role="support",
                description="Shared helper agent",
                system_prompt="You help with utility tasks.",
            )
        )
        creator = TemplateToolCreator(storage=storage, agent_storage=agent_storage)

        result = json.loads(creator._run("calculator", custom_name="agent_calc", target_agent="helper"))

        assert result["success"] is True
        assert storage.get_tool("agent_calc") is None

        agent_tools = ToolStorage(str(temp_paths.agents_dir / "helper" / "tools"))
        assert agent_tools.get_tool("agent_calc") is not None

    def test_failed_tool_update_restores_previous_definition(self, temp_paths):
        storage = ToolStorage(str(temp_paths.global_tools_dir))
        original = build_tool_definition(
            tool_name="demo_tool",
            description="Original",
            parameters=[],
            code="result = 'ok'",
            dependencies=[],
            usage_guide="original",
        )
        storage.add_tool("demo_tool", original)

        updated = build_tool_definition(
            tool_name="demo_tool",
            description="Broken",
            parameters=[],
            code="result = 'broken'",
            dependencies=[],
            usage_guide="broken",
        )

        def broken_validator(_: dict[str, object]) -> None:
            raise RuntimeError("boom")

        with pytest.raises(ToolCreationError):
            persist_validated_tool_definition(storage, updated, validator=broken_validator)

        restored = storage.get_tool("demo_tool")
        assert restored is not None
        assert restored["description"] == "Original"
        assert restored["code"] == "result = 'ok'"


# ---------------------------------------------------------------------------
# 6. Tool Delegation Runtime Tests (formerly test_tool_delegation_runtime.py)
# ---------------------------------------------------------------------------

class TestToolDelegationRuntimeClass:
    def test_delegated_tool_approval_runtime_returns_resolved_payload(self, monkeypatch):
        queue = ApprovalQueue()
        request = queue.create_request(
            kind="tool_call",
            scope="subagent:helper",
            summary="helper approval",
            prompt="allow helper?",
            callback=lambda approved, note: {
                "status": "completed",
                "success": approved,
                "response": "helper done" if approved else note or "rejected",
                "state_update": {"next_step": "report"},
            },
        )
        queue.resolve(request.approval_id, approved=True, note="ok")

        runtime = DelegatedToolApprovalRuntime(
            approval_queue=queue,
            approval_scope="root:test",
        )
        monkeypatch.setattr(tool_delegation_runtime, "interrupt", lambda payload: {"approval_id": payload["approval_id"]})

        result = runtime.handle_tool_result(
            tool_name="delegate_to_agent",
            tool_call_id="call_1",
            result=ToolMessage(
                content=json.dumps(
                    {
                        "status": "waiting_approval",
                        "approval_id": request.approval_id,
                        "success": False,
                    },
                    ensure_ascii=False,
                ),
                tool_call_id="call_1",
                status="success",
            ),
        )

        assert result is not None
        payload = json.loads(result.content)
        assert payload["response"] == "helper done"
        assert payload["state_update"] == {"next_step": "report"}
        assert result.status == "success"


# ---------------------------------------------------------------------------
# 7. Tool Dynamic Inventory Tests (formerly test_tool_dynamic_inventory.py)
# ---------------------------------------------------------------------------

@tool("alpha_tool")
def alpha_tool() -> str:
    """Return alpha."""
    return "alpha"


@tool("beta_tool")
def beta_tool() -> str:
    """Return beta."""
    return "beta"


class FakeRequest:
    def __init__(self, tools):
        self.tools = list(tools)

    def override(self, *, tools):
        return FakeRequest(tools)


class TestToolDynamicInventoryClass:
    def test_dynamic_tool_inventory_deduplicates_base_tools_during_injection(self):
        inventory = DynamicToolInventory()
        inventory.set_base_tools([alpha_tool, beta_tool])

        request, added_count = inventory.inject_tools(FakeRequest([alpha_tool]))

        assert added_count == 1
        assert [tool.name for tool in request.tools] == ["alpha_tool", "beta_tool"]

    def test_dynamic_tool_inventory_tracks_known_dynamic_tool_names(self):
        inventory = DynamicToolInventory()

        inventory.set_known_dynamic_tools(["custom_lookup", " secondary_tool ", "", "custom_lookup"])

        assert inventory.is_dynamic_tool("custom_lookup") is True
        assert inventory.is_dynamic_tool("secondary_tool") is True
        assert inventory.is_dynamic_tool("missing_tool") is False

    def test_dynamic_tool_inventory_records_successful_tool_creation_notice(self):
        inventory = DynamicToolInventory()
        result = ToolMessage(
            content=json.dumps({"success": True, "tool_name": "weather_lookup"}, ensure_ascii=False),
            tool_call_id="call_1",
            status="success",
        )

        inventory.note_tool_mutation(tool_name="create_custom_tool", result=result)

        assert inventory.last_created_tool == "weather_lookup"
        assert inventory.pop_mutation_notice() == "weather_lookup"
        assert inventory.pop_mutation_notice() is None


# ---------------------------------------------------------------------------
# 8. Tool Eviction Middleware Tests (formerly test_tool_eviction_middleware.py)
# ---------------------------------------------------------------------------

class TestEviction:
    def test_short_output_passes_through(self, tmp_path):
        mw = LCToolEvictionMiddleware(eviction_dir=str(tmp_path))
        request = type("R", (), {"name": "some_tool"})()
        msg = ToolMessage(content="short output", tool_call_id="tc1")
        result = mw._maybe_evict(request, msg)
        assert result.content == "short output"

    def test_large_output_evicted(self, tmp_path):
        mw = LCToolEvictionMiddleware(eviction_dir=str(tmp_path), max_output_chars=100)
        request = type("R", (), {"name": "some_tool"})()
        big_content = "x" * 500
        msg = ToolMessage(content=big_content, tool_call_id="tc1")
        result = mw._maybe_evict(request, msg)
        assert "truncated" in result.content
        assert result.tool_call_id == "tc1"
        evicted_files = list(tmp_path.iterdir())
        assert len(evicted_files) == 1
        assert evicted_files[0].read_text(encoding="utf-8") == big_content

    def test_excluded_tool_not_evicted(self, tmp_path):
        mw = LCToolEvictionMiddleware(eviction_dir=str(tmp_path), max_output_chars=10)
        request = type("R", (), {"name": "read_file"})()
        msg = ToolMessage(content="x" * 100, tool_call_id="tc1")
        result = mw._maybe_evict(request, msg)
        assert result.content == "x" * 100

    def test_command_passes_through(self, tmp_path):
        from langgraph.types import Command

        mw = LCToolEvictionMiddleware(eviction_dir=str(tmp_path))
        request = type("R", (), {"name": "tool"})()
        cmd = Command(update={"messages": []})
        result = mw._maybe_evict(request, cmd)
        assert isinstance(result, Command)

    def test_name_property(self, tmp_path):
        mw = LCToolEvictionMiddleware(eviction_dir=str(tmp_path))
        assert mw.name == "LCToolEvictionMiddleware"


# ---------------------------------------------------------------------------
# 9. Tool Middleware Tests (formerly test_tool_middleware.py)
# ---------------------------------------------------------------------------

class ToolAwareFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any):
        return self


@tool("create_agent")
def create_agent_tool(agent_name: str) -> str:
    """Create an agent."""
    return f"created:{agent_name}"


@tool("exec_code")
def exec_code_tool(code: str, language: str = "python", timeout: int = 15, cwd: str = "") -> str:
    """Execute code."""
    return f"executed:{code}"


def _build_graph(*, queue: ApprovalQueue, responses: list[AIMessage]):
    middleware = DynamicToolMiddleware(
        control_policy=AgentControlPolicy.from_config({
            "mode": "balanced",
            "approval_required_tools": ["create_agent", "exec_code"]
        }),
        approval_queue=queue,
        approval_scope="root:test",
    )
    from langchain.agents import create_agent as create_langchain_agent
    graph = create_langchain_agent(
        model=ToolAwareFakeModel(responses=responses),
        tools=[create_agent_tool, exec_code_tool],
        middleware=[middleware],
        checkpointer=MemorySaver(),
    )
    return graph, middleware


def _register_interrupt(queue: ApprovalQueue, graph, response: dict[str, Any], config: dict[str, Any]):
    interrupts = extract_tool_approval_interrupts(response, scope="root:test")
    assert len(interrupts) == 1
    approval = interrupts[0]
    return create_tool_approval_request(
        approval_queue=queue,
        approval=approval,
        thread_id="thread-1",
        target="root_agent",
        callback=lambda approved, note: graph.invoke(
            build_tool_approval_resume_command(approval, approved=approved, note=note),
            config=config,
        ),
    )


class TestToolMiddlewareClass:
    def test_high_risk_tool_call_pauses_and_resumes_without_retry(self):
        queue = ApprovalQueue()
        graph, _ = _build_graph(
            queue=queue,
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_agent",
                            "args": {"agent_name": "helper"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="创建完成"),
            ],
        )
        config = {"configurable": {"thread_id": "thread-1"}}

        response = graph.invoke({"messages": [{"role": "user", "content": "创建一个 helper"}]}, config=config)

        assert "__interrupt__" in response
        request = _register_interrupt(queue, graph, response, config)
        resolved = queue.resolve(request.approval_id, approved=True, note="允许")

        assert resolved["success"] is True
        result = resolved["result"]
        assert result["messages"][-1].content == "创建完成"
        tool_messages = [message for message in result["messages"] if getattr(message, "type", "") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0].content == "created:helper"

    def test_rejected_tool_approval_resumes_with_error_feedback(self):
        queue = ApprovalQueue()
        graph, _ = _build_graph(
            queue=queue,
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_agent",
                            "args": {"agent_name": "malicious"},
                            "id": "call_2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="好的，我明白了"),
            ],
        )
        config = {"configurable": {"thread_id": "thread-1"}}

        response = graph.invoke({"messages": [{"role": "user", "content": "创建一个恶意 agent"}]}, config=config)

        assert "__interrupt__" in response
        request = _register_interrupt(queue, graph, response, config)
        resolved = queue.resolve(request.approval_id, approved=False, note="禁止创建恶意 agent")

        assert resolved["success"] is True
        result = resolved["result"]
        tool_messages = [message for message in result["messages"] if getattr(message, "type", "") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0].status == "error"
        assert "禁止创建恶意 agent" in str(tool_messages[0].content)

    def test_host_execution_security_chain_revalidates_hash(self):
        queue = ApprovalQueue()
        graph, middleware = _build_graph(
            queue=queue,
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "exec_code",
                            "args": {"code": "print('hello')", "language": "python"},
                            "id": "call_3",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="执行完成"),
            ],
        )
        config = {"configurable": {"thread_id": "thread-1"}}

        response = graph.invoke({"messages": [{"role": "user", "content": "运行代码"}]}, config=config)

        assert "__interrupt__" in response
        request = _register_interrupt(queue, graph, response, config)
        
        resolved = queue.resolve(request.approval_id, approved=True, note="允许")
        
        assert resolved["success"] is True
        result = resolved["result"]
        tool_messages = [message for message in result["messages"] if getattr(message, "type", "") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0].content == "executed:print('hello')"

    def test_host_execution_security_chain_blocks_tampered_args(self):
        queue = ApprovalQueue()
        graph, middleware = _build_graph(
            queue=queue,
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "exec_code",
                            "args": {"code": "print('hello')", "language": "python"},
                            "id": "call_4",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="执行完成"),
            ],
        )
        config = {"configurable": {"thread_id": "thread-1"}}

        response = graph.invoke({"messages": [{"role": "user", "content": "恶意运行"}]}, config=config)

        assert "__interrupt__" in response
        
        # 获取 interrupt 并注册
        interrupts = extract_tool_approval_interrupts(response, scope="root:test")
        approval = interrupts[0]
        
        # 模拟篡改参数 (在实际场景中，这可能是因为某种绕过机制或状态不一致)
        # 我们通过修改图的状态来模拟
        state = graph.get_state(config)
        messages = state.values["messages"]
        last_message = messages[-1]
        last_message.tool_calls[0]["args"]["code"] = "import os; os.system('rm -rf /')"
        graph.update_state(config, {"messages": [last_message]})
        
        # 构造 resume command (使用原始的 approval，所以 plan_hash 是 print('hello') 的)
        resume_cmd = build_tool_approval_resume_command(approval, approved=True, note="允许")
        
        # 恢复执行
        result = graph.invoke(resume_cmd, config=config)
        
        tool_messages = [message for message in result["messages"] if getattr(message, "type", "") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0].status == "error"
        assert "审批后的执行内容已变化" in str(tool_messages[0].content)
        assert "CONTROL_POLICY_BLOCKED" in str(tool_messages[0].content)

    def test_session_rule_ask_prompts_even_for_low_risk_tool(self):
        prompts: list[tuple[str, str]] = []
        middleware = DynamicToolMiddleware(
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            ask_user_fn=lambda tool_name, args: prompts.append((tool_name, args)) and False,
        )
        middleware.permission_policy.add_rule("read_file", "ask", reason="manual confirmation")

        result = middleware._check_governance_approval(
            "read_file",
            {"path": "app.py"},
            tool_call_id="call_read_1",
        )

        assert prompts == [("read_file", "{'path': 'app.py'}")]
        assert result is not None
        assert result.status == "error"
        assert "read_file" in str(result.content)

    def test_permission_control_plane_mutators_expose_snapshot(self):
        middleware = DynamicToolMiddleware(
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        )

        mode_snapshot = middleware.set_permission_mode("plan")
        rule_snapshot = middleware.add_permission_rule(
            "read_file",
            "ask",
            reason="manual review",
            source="session",
        )
        final_snapshot = middleware.get_control_snapshot()

        assert mode_snapshot["mode"] == "plan"
        assert rule_snapshot["rules"]["read_file"]["verdict"] == "ask"
        assert final_snapshot["permission"]["mode"] == "plan"
        assert final_snapshot["permission"]["rule_count"] == 1
        assert final_snapshot["permission"]["summary"] == "mode=plan, 1 active rule"

        removed_snapshot = middleware.remove_permission_rule("read_file")
        cleared_snapshot = middleware.clear_permission_rules()

        assert removed_snapshot["rule_count"] == 0
        assert cleared_snapshot["rule_count"] == 0

    def test_permission_hook_receives_canonical_runtime_view(self):
        seen: list[dict[str, Any]] = []
        hooks = HooksRuntime()
        hooks.register(
            HookPhase.PERMISSION_DECISION,
            "capture_view",
            lambda payload: seen.append(dict(payload.get("projected_runtime_view", {}))) or {},
        )
        middleware = DynamicToolMiddleware(
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            hooks_runtime=hooks,
            runtime_view_provider=lambda: build_projected_runtime_view(
                thread_id="thread-hooks",
                root_mode="assistant",
                session={"session_notebook_summary": "resume from canonical notes"},
                route={"recommended": {"slot": "workspace_view", "top_level": "workspace_view"}},
                isolation={"delegation_ready": False},
            ).to_payload(),
        )

        result = middleware._check_governance_approval("read_file", {"path": "app.py"}, tool_call_id="call-read")

        assert result is None
        assert seen
        assert seen[-1]["session"]["session_notebook_summary"] == "resume from canonical notes"
        assert seen[-1]["route"]["recommended"]["slot"] == "workspace_view"
        assert seen[-1]["isolation"]["delegation_ready"] is False

    def test_permission_mutation_syncs_trusted_settings_projection(self):
        trusted_settings = build_trusted_settings_bundle(
            session_values={
                "permission": {
                    "mode": "default",
                    "rules": {},
                }
            }
        )
        middleware = DynamicToolMiddleware(
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            trusted_settings=trusted_settings,
        )

        middleware.set_permission_mode("plan")
        middleware.add_permission_rule("read_file", "ask", reason="manual confirmation")

        settings_projection = middleware.get_settings_projection()
        assert settings_projection["permission_mode"] == "plan"
        snapshot = middleware.get_control_snapshot()
        assert snapshot["settings"]["permission_mode"] == "plan"

        bundle = middleware.get_trusted_settings()
        assert bundle is not None
        session_layer = bundle.get_layer("session")
        assert session_layer is not None
        assert session_layer.values["permission"]["mode"] == "plan"
        assert session_layer.values["permission"]["rules"]["read_file"]["verdict"] == "ask"

        middleware.clear_permission_rules()
        cleared_bundle = middleware.get_trusted_settings()
        assert cleared_bundle is not None
        assert cleared_bundle.get_layer("session").values["permission"].get("rules", {}) == {}


# ---------------------------------------------------------------------------
# 10. Tool Middleware Factory Tests (formerly test_tool_middleware_factory.py)
# ---------------------------------------------------------------------------

class TestToolMiddlewareFactoryClass:
    def test_tool_middleware_factory_builds_runtime_components(self, tmp_path):
        storage = ToolStorage(str(tmp_path / "tools"))
        components = build_tool_middleware_components(
            tool_storage=storage,
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            approval_queue=ApprovalQueue(),
            approval_scope="root:test",
        )

        assert components.control_policy.mode == "balanced"
        assert components.inventory.tool_storage is storage
        assert components.tool_call_runtime is not None
        assert components.model_runtime is not None

    def test_tool_middleware_factory_creates_middleware_and_decorator_hooks(self, tmp_path):
        storage = ToolStorage(str(tmp_path / "tools"))

        middleware = create_tool_middleware(
            tool_storage=storage,
            control_policy=AgentControlPolicy.from_config({"mode": "open"}),
            approval_queue=ApprovalQueue(),
            approval_scope="root:test",
        )
        decorators = create_decorator_middleware(storage)

        assert isinstance(middleware, DynamicToolMiddleware)
        assert len(decorators) == 2


# ---------------------------------------------------------------------------
# 11. Tool Model Hook Runtime Tests (formerly test_tool_model_runtime.py)
# ---------------------------------------------------------------------------

class _ControlRuntimeStub:
    def __init__(self, approval_update=None):
        self.approval_update = approval_update
        self.calls: list[dict[str, object]] = []

    def interrupt_for_pending_approvals(self, **kwargs):
        self.calls.append(kwargs)
        return self.approval_update


class TestToolModelHookRuntimeClass:
    def test_tool_model_hook_runtime_falls_back_when_refresh_fails(self):
        class Inventory:
            def __init__(self):
                self.fallback_called = False

            def pop_mutation_notice(self):
                return "builder_tool"

            def list_dynamic_tools(self):
                return [SimpleNamespace(name="builder_tool")]

            def refresh(self, dynamic_tools):
                raise RuntimeError("boom")

            def fallback_to_base_tools(self):
                self.fallback_called = True

        inventory = Inventory()
        runtime = ToolModelHookRuntime(inventory=inventory, control_runtime=_ControlRuntimeStub())

        runtime.before_model()

        assert inventory.fallback_called is True

    def test_tool_model_hook_runtime_injects_tools_via_inventory(self):
        class Inventory:
            def inject_tools(self, request):
                request.tools.append("dynamic_lookup")
                return request, 1

        request = SimpleNamespace(tools=[])
        runtime = ToolModelHookRuntime(inventory=Inventory(), control_runtime=_ControlRuntimeStub())

        updated_request = runtime.inject_tools(request)

        assert updated_request.tools == ["dynamic_lookup"]

    def test_tool_model_hook_runtime_passes_dynamic_tool_names_to_control_runtime(self):
        class Inventory:
            def get_dynamic_tool_names(self):
                return {"dynamic_lookup"}

        control_runtime = _ControlRuntimeStub(approval_update={"messages": ["pause"]})
        runtime = ToolModelHookRuntime(inventory=Inventory(), control_runtime=control_runtime)
        last_message = SimpleNamespace(tool_calls=[{"name": "create_agent", "args": {"agent_name": "helper"}, "id": "1"}])

        result = runtime.after_model({"messages": [last_message]})

        assert result == {"messages": ["pause"]}
        assert control_runtime.calls[0]["last_message"] is last_message
        assert control_runtime.calls[0]["dynamic_tool_names"] == {"dynamic_lookup"}

    def test_tool_model_hook_runtime_returns_none_without_messages(self):
        runtime = ToolModelHookRuntime(inventory=object(), control_runtime=_ControlRuntimeStub())

        assert runtime.after_model({"messages": []}) is None

    def test_tool_model_hook_runtime_wrap_model_call_uses_injected_request(self):
        class Inventory:
            def inject_tools(self, request):
                request.tools.append("dynamic_lookup")
                return request, 1

        runtime = ToolModelHookRuntime(inventory=Inventory(), control_runtime=_ControlRuntimeStub())
        request = SimpleNamespace(tools=[])

        updated = runtime.wrap_model_call(request, lambda incoming: incoming)

        assert updated.tools == ["dynamic_lookup"]

    def test_tool_model_hook_runtime_wrap_model_call_async_survives_injection_failure(self):
        class Inventory:
            def inject_tools(self, request):
                raise RuntimeError("boom")

        runtime = ToolModelHookRuntime(inventory=Inventory(), control_runtime=_ControlRuntimeStub())
        request = SimpleNamespace(tools=[])

        async def invoke():
            return await runtime.wrap_model_call_async(request, lambda incoming: asyncio.sleep(0, result=incoming))

        updated = asyncio.run(invoke())

        assert updated is request


# ---------------------------------------------------------------------------
# 12. Tool Policy Pipeline Tests (formerly test_tool_policy_pipeline.py)
# ---------------------------------------------------------------------------

def _context(
    *,
    tool_name: str = "write_file",
    tool_args: dict | None = None,
    recent_calls: int = 0,
    policy: AgentControlPolicy | None = None,
) -> ToolPolicyContext:
    return ToolPolicyContext(
        tool_name=tool_name,
        tool_args=tool_args or {},
        tool_call_id="call_1",
        is_dynamic=False,
        approval_scope="root:test",
        control_policy=policy or AgentControlPolicy.from_config({"mode": "balanced"}),
        recent_calls=recent_calls,
    )


class TestToolPolicyPipelineClass:
    def test_path_policy_blocks_parent_traversal(self):
        stage = PathPolicyStage(allowed_roots=("C:/workspace",))

        decision = stage.evaluate(_context(tool_args={"path": "../secrets.txt"}))

        assert decision is not None
        assert decision.allowed is False
        assert decision.risk_level == ToolRiskLevel.CRITICAL
        assert "path-traversal" in decision.control_tags

    def test_path_policy_blocks_absolute_path_outside_allowed_roots(self, tmp_path):
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        outside = tmp_path / "outside.txt"

        stage = PathPolicyStage(allowed_roots=(str(allowed),))
        decision = stage.evaluate(_context(tool_args={"path": str(outside)}))

        assert decision is not None
        assert decision.allowed is False
        assert "path-outside-root" in decision.control_tags

    def test_rate_limit_stage_blocks_after_threshold(self):
        stage = RateLimitStage(max_calls_per_tool=2)

        decision = stage.evaluate(_context(recent_calls=2))

        assert decision is not None
        assert decision.allowed is False
        assert decision.risk_level == ToolRiskLevel.HIGH
        assert "rate-limit" in decision.control_tags

    def test_default_pipeline_preserves_approval_required_risk(self):
        policy = AgentControlPolicy.from_config({"mode": "balanced"})
        pipeline = build_default_tool_policy_pipeline(
            control_policy=policy,
            allowed_roots=(),
            max_calls_per_tool=policy.max_recent_tool_calls,
        )

        decision = pipeline.evaluate(
            _context(
                tool_name="create_agent",
                tool_args={"agent_name": "helper"},
                policy=policy,
            )
        )

        assert decision.allowed is True
        assert decision.requires_approval is True
        assert decision.risk_level == ToolRiskLevel.CRITICAL
        assert "agent-mutation" in decision.control_tags

    def test_default_pipeline_blocks_absolute_path_when_roots_provided(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "system.txt"
        pipeline = build_default_tool_policy_pipeline(
            control_policy=AgentControlPolicy.from_config({"mode": "open"}),
            allowed_roots=[str(workspace)],
            max_calls_per_tool=10,
        )

        decision = pipeline.evaluate(
            _context(
                tool_name="write_file",
                tool_args={"path": str(outside)},
                policy=AgentControlPolicy.from_config({"mode": "open"}),
            )
        )

        assert decision.allowed is False
        assert decision.risk_level == ToolRiskLevel.CRITICAL
        assert "path-policy" in decision.control_tags


# ---------------------------------------------------------------------------
# 13. Tool Result Normalize Tests (formerly test_tool_result_normalize.py)
# ---------------------------------------------------------------------------

class TestToolResultNormalizeClass:
    def test_peel_json_wrapped_strings_nested(self):
        inner = {"items": [1, 2]}
        s = json.dumps(json.dumps(json.dumps(inner)))
        out = peel_json_wrapped_strings(s, max_layers=8)
        assert out == inner

    def test_peel_json_wrapped_strings_respects_max_layers(self):
        inner = {"a": 1}
        s = json.dumps(json.dumps(inner))
        out_full = peel_json_wrapped_strings(s, max_layers=8)
        assert out_full == inner
        out_one = peel_json_wrapped_strings(s, max_layers=1)
        assert isinstance(out_one, str)
        assert json.loads(out_one) == inner

    def test_normalize_for_app_tool_proxy_unwrap_success(self):
        payload = {"success": True, "result": [1, 2, 3]}
        assert normalize_for_app_tool_proxy(payload) == [1, 2, 3]

    def test_normalize_for_app_tool_proxy_keep_error_envelope(self):
        payload = {"success": False, "error": "boom"}
        assert normalize_for_app_tool_proxy(payload) == payload

    def test_canonicalize_dynamic_tool_content_string_double_json(self):
        inner = {"a": 1}
        doubled = json.dumps(json.dumps(inner))
        out = canonicalize_dynamic_tool_content_string(doubled)
        assert json.loads(out) == inner

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            42,
            {"x": 1},
            ["a"],
        ],
    )
    def test_normalize_for_app_tool_proxy_passthrough_non_str(self, raw):
        assert normalize_for_app_tool_proxy(raw) is raw


# ---------------------------------------------------------------------------
# 14. Tool Schema Quality Tests (formerly test_tool_schema_quality.py)
# ---------------------------------------------------------------------------

class _FakeEngine:
    def list_workflows(self): return []
    def parse_workflow(self, *a, **kw): return None


def _collect_builtin_tools() -> list[BaseTool]:
    tools: list[BaseTool] = []
    tools.extend(get_app_creator_tools())
    tools.extend(get_app_verifier_tools())
    tools.extend([AskClarificationTool(), AnalyzeRequirementTool()])
    tools.extend([ExecCodeTool(), ScanProjectTool(), IterativeFixTool()])
    tools.extend([ListTemplatesTool(), RemoveToolTool(), ToolCreatorTool()])

    engine = _FakeEngine()
    tools.extend([
        RunWorkflowTool(engine=engine),
        ResumeWorkflowTool(engine=engine),
        ListWorkflowsTool(engine=engine),
        GenerateWorkflowTool(engine=engine),
        TriggerWorkflowTool(engine=engine),
    ])

    tools.extend([
        AgentCreatorTool(),
        DelegateToAgentTool(),
        AskAgentTool(),
        ListAgentsTool(),
        RemoveAgentTool(),
    ])

    tools.extend([EvalResponseTool(), RunTestsTool()])
    tools.extend([RunChainTool(), ToolStatsTool()])
    tools.extend([
        PackageSkillTool(),
        InstallSkillTool(),
        UninstallSkillTool(),
        SearchSkillsTool(),
        CreateSkillTool(),
    ])
    tools.append(CapBusTool())
    return tools


ALL_TOOLS = _collect_builtin_tools()


def _get_schema(t: BaseTool) -> dict | None:
    schema_cls = getattr(t, "args_schema", None)
    if schema_cls is None:
        return None
    if hasattr(schema_cls, "model_json_schema"):
        return schema_cls.model_json_schema()
    if hasattr(schema_cls, "schema"):
        return schema_cls.schema()
    return None


class TestToolSchemaQualityClass:
    @pytest.mark.parametrize("t", ALL_TOOLS, ids=lambda t: t.name)
    def test_tool_has_schema(self, t: BaseTool):
        assert getattr(t, "args_schema", None) is not None, (
            f"Tool '{t.name}' has no args_schema — LLM cannot generate proper arguments"
        )

    @pytest.mark.parametrize("t", ALL_TOOLS, ids=lambda t: t.name)
    def test_tool_has_description(self, t: BaseTool):
        assert t.description and len(t.description.strip()) > 10, (
            f"Tool '{t.name}' has missing or trivially short description"
        )

    @pytest.mark.parametrize("t", ALL_TOOLS, ids=lambda t: t.name)
    def test_all_params_have_type(self, t: BaseTool):
        schema = _get_schema(t)
        if schema is None:
            pytest.skip(f"Tool '{t.name}' has no parseable schema")

        properties = schema.get("properties", {})
        for param_name, param_def in properties.items():
            has_type = "type" in param_def or "anyOf" in param_def or "$ref" in param_def or "allOf" in param_def
            assert has_type, (
                f"Tool '{t.name}', param '{param_name}' has no type definition. "
                f"Schema: {param_def}"
            )

    @pytest.mark.parametrize("t", ALL_TOOLS, ids=lambda t: t.name)
    def test_all_params_have_description(self, t: BaseTool):
        schema = _get_schema(t)
        if schema is None:
            pytest.skip(f"Tool '{t.name}' has no parseable schema")

        properties = schema.get("properties", {})
        missing = []
        for param_name, param_def in properties.items():
            if "description" not in param_def or not param_def["description"].strip():
                missing.append(param_name)

        assert not missing, (
            f"Tool '{t.name}' has parameters without descriptions: {missing}. "
            f"LLM relies on descriptions to understand parameter usage."
        )

    @pytest.mark.parametrize("t", ALL_TOOLS, ids=lambda t: t.name)
    def test_required_params_listed(self, t: BaseTool):
        schema = _get_schema(t)
        if schema is None:
            pytest.skip(f"Tool '{t.name}' has no parseable schema")

        properties = schema.get("properties", {})
        if not properties:
            return

        required = schema.get("required", [])
        for req_field in required:
            assert req_field in properties, (
                f"Tool '{t.name}': required field '{req_field}' is not in properties"
            )
