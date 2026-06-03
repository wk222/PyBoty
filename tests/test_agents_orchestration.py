from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from agent import PyBot
from core.assets.agents import (
    AgentCapabilityProfile,
    AgentMiddlewareProfile,
    AgentDefinition,
    AgentStorage,
    build_agent_tool_inventory,
)
from core.assets.agents.role_policy import (
    AgentRole,
    AgentRolePolicy,
    ROLE_POLICIES,
    apply_role_defaults,
    get_policy,
)
from core.assets.agents.storage import AgentModelConfig
from core.assets.agents.tool_inventory import build_effective_profiles
from core.assets.tools import ToolStorage
from core.assets.workflows.task_definition import TaskDefinition
from core.assets.workflows.workflow_tools import RunWorkflowTool
from core.modes.factories import (
    build_subagent_langchain_middleware,
    build_subagent_runtime_prompt_sections,
    build_subagent_runtime_prompt_sections as build_subagent_runtime_prompt_sections_alias,
)
from core.systems.agents import (
    AgentToolSyncError,
    create_agent_record,
    create_sub_agent_instance,
    delegate_agent_task,
    invoke_persisted_agent,
    resume_persisted_agent_approval,
    sync_agent_tool,
)
from core.systems.agents.agent_as_tool import AgentTool, TeamTool, create_agent_tool, create_team_tool
from core.systems.agents.agent_creator import AskAgentTool, DelegateToAgentTool, get_agent_creator_tools
from core.assets.agents.delegation_payload import delegation_response_text, normalize_delegation_payload
from core.systems.agents.persistent_agent_runner import (
    PersistentAgentRunner,
    PersistentTask,
    PersistentTaskStatus,
    PersistentTaskStep,
)
from core.systems.agents.society_of_mind import MindAgent, SocietyConfig, SocietyOfMind
from core.systems.agents.speaker_selection import (
    ChatMessage,
    LLMSelector,
    Participant,
    RandomSelector,
    RoundRobinSelector,
    RuleBasedSelector,
)
from core.systems.agents.subagent_governance import (
    build_delegation_chain,
    build_subagent_governance_snapshot,
    format_delegation_tree,
)
from core.systems.agents.subagent_sandbox import SubagentSandbox, list_sandbox_adapters
from core.systems.agents.team_orchestrator import HierarchicalTeam, SequentialTeam, TeamResult
from core.systems.capability.capability_tree import build_capability_tree_resume_projection
from core.systems.context.prompts import build_static_system_prompt, get_root_mode_label, normalize_root_mode
from core.systems.governance import AgentControlPolicy, ApprovalQueue
from core.systems.middleware.agent_middleware_factory import build_root_langchain_middleware
from core.systems.middleware.agent_prompt_middleware import PromptSectionMiddleware
from core.systems.runtime import ProjectPaths
from core.systems.runtime.event_bus import Event, EventType, event_bus

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime


# ---------------------------------------------------------------------------
# Setup and Mock Helpers
# ---------------------------------------------------------------------------

def _mock_llm_factory(response="LLM response"):
    def factory(model="", temperature=0.7):
        llm = MagicMock()
        result = MagicMock()
        result.content = response
        llm.invoke.return_value = result
        return llm
    return factory


def _mind_agents():
    return [
        MindAgent("analyst", role="数据分析师", system_prompt="你擅长数据分析"),
        MindAgent("critic", role="批评者", system_prompt="你擅长找问题"),
    ]


def _make_request(system_message: SystemMessage | None = None) -> ModelRequest:
    return ModelRequest(
        model=GenericFakeChatModel(messages=iter([AIMessage(content="ok")])),
        system_message=system_message,
        messages=[],
        tool_choice=None,
        tools=[],
        response_format=None,
        state=AgentState(messages=[]),
        runtime=Runtime(),
        model_settings={},
    )


def _make_agent_orchestrator_def(name: str, role: str, goal: str = "") -> AgentDefinition:
    return AgentDefinition(
        name=name,
        role=role,
        description=f"Agent: {name}",
        system_prompt=f"You are {name}.",
        goal=goal,
    )


# ---------------------------------------------------------------------------
# 1. Agent Role Policy Tests (formerly test_agent_role_policy.py)
# ---------------------------------------------------------------------------

class TestAgentRole:
    def test_all_variants_exist(self):
        assert AgentRole.COORDINATOR
        assert AgentRole.WORKER
        assert AgentRole.VERIFIER
        assert AgentRole.FORK_CHILD

    def test_str_values(self):
        assert AgentRole.COORDINATOR.value == "coordinator"
        assert AgentRole.WORKER.value == "worker"
        assert AgentRole.VERIFIER.value == "verifier"
        assert AgentRole.FORK_CHILD.value == "fork_child"

    def test_from_str_valid(self):
        assert AgentRole.from_str("coordinator") == AgentRole.COORDINATOR
        assert AgentRole.from_str("WORKER") == AgentRole.WORKER
        assert AgentRole.from_str("verifier") == AgentRole.VERIFIER

    def test_from_str_unknown_falls_back_to_worker(self):
        assert AgentRole.from_str("unknown_role") == AgentRole.WORKER
        assert AgentRole.from_str("") == AgentRole.WORKER

    def test_from_str_case_insensitive(self):
        assert AgentRole.from_str("FORK_CHILD") == AgentRole.FORK_CHILD


class TestRolePoliciesRegistry:
    def test_all_roles_have_policies(self):
        for role in AgentRole:
            assert role in ROLE_POLICIES, f"Missing policy for {role}"

    def test_policy_role_matches_key(self):
        for role, policy in ROLE_POLICIES.items():
            assert policy.role == role

    def test_to_dict_round_trips_role(self):
        for role, policy in ROLE_POLICIES.items():
            d = policy.to_dict()
            assert d["role"] == role.value


class TestCoordinatorPolicy:
    def setup_method(self):
        self.policy = ROLE_POLICIES[AgentRole.COORDINATOR]

    def test_autonomous(self):
        assert self.policy.autonomy_level == "autonomous"

    def test_can_create_tools(self):
        assert self.policy.allow_tool_creation is True

    def test_can_delegate(self):
        assert self.policy.allow_delegation is True

    def test_not_read_only(self):
        assert self.policy.read_only_tools_only is False

    def test_no_structured_output_required(self):
        assert self.policy.structured_output_required is False


class TestWorkerPolicy:
    def setup_method(self):
        self.policy = ROLE_POLICIES[AgentRole.WORKER]

    def test_reactive(self):
        assert self.policy.autonomy_level == "reactive"

    def test_cannot_create_tools(self):
        assert self.policy.allow_tool_creation is False

    def test_cannot_delegate(self):
        assert self.policy.allow_delegation is False

    def test_capped_tool_calls(self):
        assert self.policy.max_tool_calls_per_turn is not None
        assert self.policy.max_tool_calls_per_turn > 0


class TestVerifierPolicy:
    def setup_method(self):
        self.policy = ROLE_POLICIES[AgentRole.VERIFIER]

    def test_read_only(self):
        assert self.policy.read_only_tools_only is True

    def test_structured_output_required(self):
        assert self.policy.structured_output_required is True

    def test_cannot_delegate(self):
        assert self.policy.allow_delegation is False


class TestForkChildPolicy:
    def setup_method(self):
        self.policy = ROLE_POLICIES[AgentRole.FORK_CHILD]

    def test_inherits_parent_context(self):
        assert self.policy.inherits_parent_context is True

    def test_cannot_delegate(self):
        assert self.policy.allow_delegation is False

    def test_capped_tool_calls(self):
        assert self.policy.max_tool_calls_per_turn is not None


class TestGetPolicy:
    def test_get_by_enum(self):
        p = get_policy(AgentRole.COORDINATOR)
        assert p.role == AgentRole.COORDINATOR

    def test_get_by_string(self):
        p = get_policy("worker")
        assert p.role == AgentRole.WORKER

    def test_unknown_string_returns_worker(self):
        p = get_policy("nonexistent")
        assert p.role == AgentRole.WORKER


class TestApplyRoleDefaults:
    def test_role_defaults_applied_when_profile_empty(self):
        cap, mid = apply_role_defaults("coordinator", capability_profile={}, middleware_profile={})
        assert cap["allow_tool_creation"] is True
        assert cap["allow_delegation"] is True
        assert cap["autonomy_level"] == "autonomous"
        assert mid["approval_threshold"] == "critical"

    def test_caller_overrides_role_defaults(self):
        cap, mid = apply_role_defaults(
            "coordinator",
            capability_profile={"allow_tool_creation": False},
            middleware_profile={"approval_threshold": "low"},
        )
        assert cap["allow_tool_creation"] is False
        assert mid["approval_threshold"] == "low"

    def test_worker_defaults(self):
        cap, mid = apply_role_defaults("worker", capability_profile={}, middleware_profile={})
        assert cap["allow_delegation"] is False
        assert mid["structured_output_required"] is False

    def test_verifier_defaults(self):
        cap, mid = apply_role_defaults("verifier", capability_profile={}, middleware_profile={})
        assert cap["read_only_tools_only"] is True
        assert mid["structured_output_required"] is True

    def test_fork_child_inherits_parent(self):
        cap, _ = apply_role_defaults("fork_child", capability_profile={}, middleware_profile={})
        assert cap["inherits_parent_context"] is True

    def test_unknown_role_uses_worker(self):
        cap, mid = apply_role_defaults("mystery_role", capability_profile={}, middleware_profile={})
        assert cap["allow_delegation"] is False


class TestAgentDefinitionTeamRole:
    def _make_agent(self, team_role: str = "worker") -> AgentDefinition:
        return AgentDefinition(
            name="test_agent",
            role="data analyst",
            description="an agent",
            system_prompt="you are helpful",
            team_role=team_role,
        )

    def test_default_team_role_is_worker(self):
        agent = AgentDefinition(
            name="a", role="r", description="d", system_prompt="s"
        )
        assert agent.team_role == "worker"

    def test_team_role_stored_in_to_dict(self):
        agent = self._make_agent("coordinator")
        d = agent.to_dict()
        assert d["team_role"] == "coordinator"

    def test_team_role_roundtrips_through_from_dict(self):
        agent = self._make_agent("verifier")
        d = agent.to_dict()
        restored = AgentDefinition.from_dict(d)
        assert restored.team_role == "verifier"

    def test_from_dict_missing_team_role_defaults_to_worker(self):
        d = {
            "name": "a", "role": "r", "description": "d",
            "system_prompt": "s",
        }
        restored = AgentDefinition.from_dict(d)
        assert restored.team_role == "worker"


class TestBuildEffectiveProfiles:
    def test_coordinator_effective_profiles(self):
        agent = AgentDefinition(
            name="coord", role="planner", description="d",
            system_prompt="s", team_role="coordinator"
        )
        cap, mid = build_effective_profiles(agent)
        assert cap["allow_tool_creation"] is True
        assert cap["allow_delegation"] is True

    def test_agent_overrides_win(self):
        agent = AgentDefinition(
            name="w", role="r", description="d", system_prompt="s",
            team_role="coordinator",
            capability_profile={"allow_tool_creation": False},
            middleware_profile={"approval_threshold": "low"},
        )
        cap, mid = build_effective_profiles(agent)
        assert cap["allow_tool_creation"] is False
        assert mid["approval_threshold"] == "low"

    def test_verifier_profiles(self):
        agent = AgentDefinition(
            name="v", role="checker", description="d",
            system_prompt="s", team_role="verifier"
        )
        cap, mid = build_effective_profiles(agent)
        assert cap["read_only_tools_only"] is True
        assert mid["structured_output_required"] is True


# ---------------------------------------------------------------------------
# 2. Agent Prompt Middleware Tests (formerly test_agent_prompt_middleware.py)
# ---------------------------------------------------------------------------

class TestAgentPromptMiddlewareClass:
    def test_prompt_section_middleware_appends_to_existing_system_message(self):
        middleware = PromptSectionMiddleware(name="TestPromptSection", prompt_builder=lambda: "最新记忆")
        captured: dict[str, ModelRequest] = {}

        def handler(request: ModelRequest) -> ModelResponse:
            captured["request"] = request
            return ModelResponse(result=[AIMessage(content="ok")])

        middleware.wrap_model_call(_make_request(SystemMessage(content="基础提示")), handler)

        assert captured["request"].system_message is not None
        assert captured["request"].system_message.text == "基础提示\n\n最新记忆"

    def test_prompt_section_middleware_skips_empty_sections(self):
        middleware = PromptSectionMiddleware(name="TestPromptSection", prompt_builder=lambda: "   ")
        original_request = _make_request(SystemMessage(content="基础提示"))
        captured: dict[str, ModelRequest] = {}

        def handler(request: ModelRequest) -> ModelResponse:
            captured["request"] = request
            return ModelResponse(result=[AIMessage(content="ok")])

        middleware.wrap_model_call(original_request, handler)

        assert captured["request"].system_message is not None
        assert captured["request"].system_message.text == "基础提示"

    def test_static_root_prompt_describes_persistent_admin_identity(self):
        prompt = build_static_system_prompt(root_mode="admin")

        assert "PyBoty" not in prompt
        assert "PyBot 的长期运行总控智能体" in prompt
        assert "长期运行总控智能体" in prompt
        assert "模式能力开关" in prompt
        assert "长期任务循环: 启用" in prompt
        assert "优先把重复劳动转成工具、技能或工作流" in prompt
        assert "可审计、可暂停、可恢复" in prompt
        assert "五个产品概念" in prompt
        assert "不要再把支撑系统讲成新的平台层" in prompt

    def test_static_root_prompt_keeps_assistant_identity_by_default(self):
        prompt = build_static_system_prompt()

        assert "PyBot 的通用协作助手" in prompt
        assert "通用协作助手" in prompt
        assert "APP 编排运行时: 禁用" in prompt
        assert "你不是一次性聊天助手" not in prompt
        assert "不要默认把自己当成 应用矩阵或长期自治执行体" in prompt

    def test_static_root_prompt_supports_app_matrix_identity(self):
        prompt = build_static_system_prompt(root_mode="app_matrix")

        assert "PyBot 的 应用矩阵" in prompt
        assert "应用矩阵" in prompt
        assert "中央调度智能体" in prompt
        assert "中央调度脑" in prompt
        assert "APP 拓扑规划: 启用" in prompt
        assert "你主要负责应用级协作编排" in prompt

    def test_root_mode_aliases_and_labels_support_admin_mode(self):
        assert normalize_root_mode("admin") == "admin"
        assert normalize_root_mode("全局管理员模式") == "admin"
        assert normalize_root_mode("app_matrix") == "app_matrix"
        assert normalize_root_mode("应用矩阵模式") == "app_matrix"
        assert get_root_mode_label("admin") == "全局管理员智能体"
        assert get_root_mode_label("app_matrix") == "应用矩阵智能体"
        assert get_root_mode_label("assistant") == "人类助手模式"

    def test_root_middleware_factory_injects_runtime_context(self):
        class DummyWorkspace:
            def build_system_context(self) -> str:
                return "WORKSPACE"

        class DummyMemory:
            def get_context_prompt(self, canvas=None, query=None) -> str:
                return "MEMORY"

        class DummySkills:
            def get_active_prompt_extensions(self, progressive: bool = True) -> str:
                assert progressive is True
                return "SKILLS"

        class DummyBus:
            pass

        class DummyRuntime:
            def __init__(self):
                self.workspace = DummyWorkspace()
                self.memory = DummyMemory()
                self.skill_registry = DummySkills()
                self.middleware = object()
                self.capability_bus = DummyBus()

        runtime = DummyRuntime()
        middlewares = build_root_langchain_middleware(runtime=runtime)
        captured: dict[str, ModelRequest] = {}

        def handler(request: ModelRequest) -> ModelResponse:
            captured["request"] = request
            return ModelResponse(result=[AIMessage(content="ok")])

        from core.systems.middleware.loop_guard_middleware import LoopGuardMiddleware
        from core.systems.runtime.patch_tool_calls import PatchToolCallsMiddleware
        from core.systems.middleware.todo_middleware import TodoListMiddleware

        assert isinstance(middlewares[0], LoopGuardMiddleware)
        assert isinstance(middlewares[1], TodoListMiddleware)
        assert isinstance(middlewares[-1], PatchToolCallsMiddleware)

        prompt_mw = middlewares[2]
        prompt_mw.wrap_model_call(_make_request(SystemMessage(content="基础提示")), handler)

        assert captured["request"].system_message is not None
        text = captured["request"].system_message.text
        assert "WORKSPACE" in text
        assert "MEMORY" in text
        assert "SKILLS" in text

    def test_root_middleware_factory_merges_todos_into_session_runtime_view(self):
        class DummyWorkspace:
            def build_system_context(self) -> str:
                return "WORKSPACE"

        class DummyMemory:
            def get_context_prompt(self, canvas=None, query=None) -> str:
                return "MEMORY"

        class DummySkills:
            def get_active_prompt_extensions(self, progressive: bool = True) -> str:
                return "SKILLS"

        class DummyBus:
            pass

        class DummyRuntime:
            def __init__(self):
                self.workspace = DummyWorkspace()
                self.memory = DummyMemory()
                self.skill_registry = DummySkills()
                self.middleware = object()
                self.capability_bus = DummyBus()

        runtime = DummyRuntime()
        middlewares = build_root_langchain_middleware(
            runtime=runtime,
            runtime_view_provider=lambda: {
                "projected_runtime_view": {
                    "tasks": {
                        "activities": [
                            {
                                "activity_id": "run-1",
                                "kind": "tool_run",
                                "title": "read_file",
                                "status": "completed",
                                "source": "tool_result",
                            }
                        ]
                    }
                }
            },
        )
        todo_mw = middlewares[1]
        prompt_mw = middlewares[2]
        todo_mw._state.upsert(
            [
                {"id": "t1", "content": "wire resume bundle", "status": "in_progress"},
                {"id": "t2", "content": "stabilize context chain", "status": "pending"},
            ]
        )
        captured: dict[str, ModelRequest] = {}

        def handler(request: ModelRequest) -> ModelResponse:
            captured["request"] = request
            return ModelResponse(result=[AIMessage(content="ok")])

        prompt_mw.wrap_model_call(_make_request(SystemMessage(content="基础提示")), handler)

        assert captured["request"].system_message is not None
        text = captured["request"].system_message.text
        assert "## Task Runtime" in text
        assert "t1: wire resume bundle" in text
        assert "t2: stabilize context chain" in text
        assert "## Recent Activity" in text
        assert "read_file" in text

    def test_root_middleware_factory_includes_recent_actions_from_runtime_middleware(self):
        class DummyWorkspace:
            def build_system_context(self) -> str:
                return "WORKSPACE"

        class DummyMemory:
            def get_context_prompt(self, canvas=None, query=None) -> str:
                return "MEMORY"

        class DummySkills:
            def get_active_prompt_extensions(self, progressive: bool = True) -> str:
                return "SKILLS"

        class DummyBus:
            pass

        class DummyToolMiddleware:
            def get_control_snapshot(self):
                return {
                    "observability": {
                        "recent_events": [
                            {
                                "tool_name": "read_file",
                                "tool_call_id": "run-1",
                                "allowed": True,
                                "requires_approval": False,
                                "args_preview": "{\"path\": \"app.py\"}",
                                "timestamp": 1.0,
                            },
                            {
                                "tool_name": "write_file",
                                "tool_call_id": "run-2",
                                "allowed": False,
                                "requires_approval": False,
                                "args_preview": "{\"path\": \"app.py\"}",
                                "timestamp": 2.0,
                            },
                        ]
                    }
                }

        class DummyRuntime:
            def __init__(self):
                self.workspace = DummyWorkspace()
                self.memory = DummyMemory()
                self.skill_registry = DummySkills()
                self.middleware = DummyToolMiddleware()
                self.capability_bus = DummyBus()

        runtime = DummyRuntime()
        middlewares = build_root_langchain_middleware(runtime=runtime)
        prompt_mw = middlewares[2]
        captured: dict[str, ModelRequest] = {}

        def handler(request: ModelRequest) -> ModelResponse:
            captured["request"] = request
            return ModelResponse(result=[AIMessage(content="ok")])

        prompt_mw.wrap_model_call(_make_request(SystemMessage(content="基础提示")), handler)

        assert captured["request"].system_message is not None
        text = captured["request"].system_message.text
        assert "## Recent Activity" in text
        assert "read_file" in text
        assert "write_file" in text
        assert "blocked" in text

    def test_root_middleware_factory_includes_capability_tree_from_artifacts(self):
        class DummyWorkspace:
            def build_system_context(self) -> str:
                return "WORKSPACE"

        class DummyMemory:
            def get_context_prompt(self, canvas=None, query=None) -> str:
                return "MEMORY"

        class DummySkills:
            def get_active_prompt_extensions(self, progressive: bool = True) -> str:
                return "SKILLS"

        class DummyBus:
            pass

        class DummyRuntime:
            def __init__(self):
                self.workspace = DummyWorkspace()
                self.memory = DummyMemory()
                self.skill_registry = DummySkills()
                self.middleware = object()
                self.capability_bus = DummyBus()

        runtime = DummyRuntime()
        middlewares = build_root_langchain_middleware(
            runtime=runtime,
            runtime_view_provider=lambda: {
                "projected_runtime_view": {
                    "capability": {
                        "trunk_summary": "Tool Runtime / Governance -> Workspace View -> Context Hygiene",
                        "execution_summary": "Single-Agent Runtime builds on the trunk; Skills stay as a strategy overlay.",
                        "primary_branches": [
                            {
                                "label": "Workflow / Apps / Automation",
                                "depends_on": ["Single-Agent Runtime", "Permission / Recovery"],
                                "children": ["App Asset Runtime", "App Modes"],
                                "capabilities": ["create_app", "build_app_iteratively"],
                            }
                        ],
                        "route_hints": [
                            {
                                "topic": "create_app",
                                "hint": "Prefer build_app_iteratively first, then create_app -> update_app_file -> verify_app -> test_app_api.",
                            }
                        ],
                    }
                }
            },
        )
        prompt_mw = middlewares[2]
        captured: dict[str, ModelRequest] = {}

        def handler(request: ModelRequest) -> ModelResponse:
            captured["request"] = request
            return ModelResponse(result=[AIMessage(content="ok")])

        prompt_mw.wrap_model_call(_make_request(SystemMessage(content="基础提示")), handler)

        assert captured["request"].system_message is not None
        text = captured["request"].system_message.text
        assert "Capability Tree" in text
        assert "Workflow / Apps / Automation" in text
        assert "route:create_app" in text

    def test_subagent_runtime_prompt_sections_describe_boundaries(self):
        profile = AgentCapabilityProfile.from_value({"preset": "specialist", "allow_code_execution": False})
        sandbox = SubagentSandbox(
            mode="read_only",
            visibility="project",
            workspace_dir=Path("/tmp/subagent"),
            allows_writes=False,
            allows_code_execution=False,
        )

        prompt = build_subagent_runtime_prompt_sections(
            agent_name="helper",
            role="researcher",
            sandbox=sandbox,
            capability_profile=profile,
        )

        assert "helper" in prompt
        assert "researcher" in prompt
        assert "read_only" in prompt
        assert "代码执行: 禁止" in prompt
        assert "智能体委派" in prompt

    def test_subagent_middleware_factory_respects_profile_sections(self):
        profile = AgentCapabilityProfile.from_value("manager")
        middleware_profile = AgentMiddlewareProfile.from_value("coordinator")
        effective_policy = AgentControlPolicy.from_config({"mode": "balanced"})
        sandbox = SubagentSandbox(
            mode="restricted",
            visibility="isolated",
            workspace_dir=Path("/tmp/coordinator"),
            allows_writes=True,
            allows_code_execution=False,
        )
        definition = type("Definition", (), {"name": "coordinator", "role": "manager"})()
        tool_middleware = object()

        stack = build_subagent_langchain_middleware(
            definition=definition,
            sandbox=sandbox,
            capability_profile=profile,
            middleware_profile=middleware_profile,
            effective_policy=effective_policy,
            tool_middleware=tool_middleware,
        )

        names = [getattr(item, "name", "tool") for item in stack]
        assert any("SubagentPromptContextMiddleware:coordinator" == name for name in names)
        assert any("SubagentDelegationContextMiddleware:coordinator" == name for name in names)
        assert any("SubagentPolicyContextMiddleware:coordinator" == name for name in names)
        assert tool_middleware in stack
        from core.systems.runtime.patch_tool_calls import PatchToolCallsMiddleware

        assert isinstance(stack[-1], PatchToolCallsMiddleware)

    def test_subagent_governance_snapshot_surfaces_inheritance(self):
        snapshot = build_subagent_governance_snapshot(
            base_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            capability_profile=AgentCapabilityProfile.from_value("builder"),
            middleware_profile=AgentMiddlewareProfile.from_value("builder"),
        )

        assert snapshot["root_policy"]["mode"] == "balanced"
        assert snapshot["effective_policy"]["allow_tool_mutation"] is True
        assert "tool_control" in snapshot["middleware_stack"]
        assert snapshot["inheritance"]["delegation_continues_with_effective_policy"] is True

    def test_static_root_prompt_includes_tree_routing_guide(self):
        prompt = build_static_system_prompt()

        assert "Tree Routing" in prompt
        assert "Start with the trunk and the Single-Agent Runtime first." in prompt
        assert "prefer `build_app_iteratively`" in prompt
        assert "Use Skills for strategy and SOP guidance" in prompt

    def test_capability_tree_resume_projection_includes_branch_route_hints(self):
        projection = build_capability_tree_resume_projection(
            {
                "id": "root",
                "trunk": [
                    {"id": "tool_runtime_governance", "label": "Tool Runtime / Governance"},
                    {"id": "workspace_view", "label": "Workspace View"},
                ],
                "execution_surfaces": [
                    {"id": "single_agent_runtime", "label": "Single-Agent Runtime"},
                    {"id": "skill_strategy", "label": "Skill Strategy Overlay"},
                ],
                "primary_branches": [
                    {
                        "id": "workflow_apps",
                        "label": "Workflow / Apps / Automation",
                        "depends_on": ["single_agent_runtime"],
                        "children": [{"id": "workflow_runtime", "label": "Workflow Runtime"}],
                        "capabilities": ["run_workflow"],
                        "capability_count": 1,
                    }
                ],
                "secondary_branches": [{"id": "hooks_runtime", "label": "Hooks Runtime"}],
            }
        )

        topics = [item["topic"] for item in projection["route_hints"]]
        assert "workflow" in topics
        assert "web" in topics
        assert "knowledge_rag" in topics
        assert "multi_agent" in topics

    def test_workflow_and_delegation_tool_descriptions_include_routing_guidance(self):
        from core.systems.agents.agent_creator import DelegateToAgentTool
        from core.assets.workflows.workflow_tools import RunWorkflowTool

        run_workflow = RunWorkflowTool(engine=object())
        delegate = DelegateToAgentTool()

        assert "Single-Agent + trunk" in run_workflow.description
        assert "workflow collaboration" in run_workflow.description
        assert "Single-Agent + trunk" in delegate.description
        assert "app runtime / workflow runtime" in delegate.description


# ---------------------------------------------------------------------------
# 3. Agent Services Tests (formerly test_agent_services.py)
# ---------------------------------------------------------------------------

class TestAgentServicesClass:
    def test_create_agent_record_parses_capabilities(self, tmp_path):
        storage = AgentStorage(str(tmp_path / "agents"))

        result = create_agent_record(
            agent_storage=storage,
            agent_name="data_analyst",
            role="analyst",
            description="Analyzes data",
            system_prompt="You analyze data.",
            capabilities='["python", "analysis"]',
            capability_profile='{"preset":"builder"}',
            model="gpt-4o-mini",
            temperature=0.2,
        )

        assert result["success"] is True
        saved = storage.get_agent("data_analyst")
        assert saved is not None
        assert saved.capabilities == ["python", "analysis"]
        assert saved.capability_profile["allow_local_tool_creation"] is True

    def test_delegate_agent_task_returns_structured_payload(self, tmp_path, monkeypatch):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="helper",
                role="helper",
                description="General helper",
                system_prompt="Help with tasks.",
                tools=["search_notes"],
            )
        )

        class FakeRuntime:
            def invoke(self, **kwargs):
                return {
                    "response": "done:hello",
                    "agent_name": "helper",
                    "role": "helper",
                    "success": True,
                    "state_update": {"plan": "ok"},
                    "tool_names": ["search_notes"],
                    "thread_id": kwargs["thread_id"],
                }

        monkeypatch.setattr("core.systems.agents.agent_services.create_sub_agent_instance", lambda **kwargs: FakeRuntime())

        result = delegate_agent_task(
            agent_storage=storage,
            llm_factory=lambda **kwargs: object(),
            agent_name="helper",
            task="hello",
            context="ctx",
        )

        assert result["success"] is True
        assert result["response"] == "done:hello"
        assert result["state_update"] == {"plan": "ok"}
        assert result["has_state_update"] is True
        assert result["state_keys"] == ["plan"]
        assert result["tool_names"] == ["search_notes"]
        assert result["thread_id"].startswith("delegate_helper_")

    def test_invoke_persisted_agent_respects_control_policy(self, tmp_path):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="blocked",
                role="blocked",
                description="Blocked helper",
                system_prompt="Do not run.",
            )
        )
        control_policy = AgentControlPolicy.from_config({"mode": "strict"})

        with pytest.raises(ValueError):
            invoke_persisted_agent(
                agent_storage=storage,
                llm_factory=lambda **kwargs: object(),
                control_policy=control_policy,
                agent_name="blocked",
                task="hello",
            )

    def test_subagent_runtime_applies_capability_profile_and_global_tools(self, tmp_path, monkeypatch):
        agent_storage = AgentStorage(str(tmp_path / "agents"))
        created = create_agent_record(
            agent_storage=agent_storage,
            agent_name="builder",
            role="builder",
            description="Builds tools",
            system_prompt="You build tools.",
            capability_profile=json.dumps({"preset": "builder"}),
        )

        assert created["success"] is True
        agent_def = agent_storage.get_agent("builder")
        assert agent_def is not None
        agent_def.tools = ["shared_tool"]
        agent_storage.update_agent("builder", {"tools": ["shared_tool"]})

        class DummyGraph:
            def invoke(self, state, config=None):
                return {"messages": []}

        monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

        local_storage = ToolStorage(str(tmp_path / "agents" / "builder" / "tools"))
        global_storage = ToolStorage(str(tmp_path / "global_tools"))
        global_storage.add_tool(
            "shared_tool",
            {
                "name": "shared_tool",
                "description": "Shared helper",
                "parameters": [],
                "code": "result = 'ok'",
                "dependencies": [],
                "usage_guide": "shared",
            },
        )

        runtime = create_sub_agent_instance(
            agent_def=agent_def,
            tool_storage=local_storage,
            global_tool_storage=global_storage,
            agent_storage=agent_storage,
            llm_factory=lambda **kwargs: object(),
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            project_paths=ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace"),
        )

        assert "create_custom_tool" in runtime.tool_names
        assert "shared_tool" in runtime.tool_names
        assert runtime.checkpoint_bundle.backend == "sqlite"
        assert runtime.checkpoint_bundle.path is not None and runtime.checkpoint_bundle.path.exists()

    def test_specialist_profile_does_not_receive_privileged_tools(self, tmp_path, monkeypatch):
        class DummyGraph:
            def invoke(self, state, config=None):
                return {"messages": []}

        monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

        agent_def = AgentDefinition(
            name="specialist",
            role="specialist",
            description="Focused specialist",
            system_prompt="You are focused.",
            capability_profile=AgentCapabilityProfile.from_value("specialist").to_dict(),
        )
        runtime = create_sub_agent_instance(
            agent_def=agent_def,
            tool_storage=None,
            agent_storage=None,
            llm_factory=lambda **kwargs: object(),
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        )

        assert "create_custom_tool" not in runtime.tool_names
        assert "delegate_to_agent" not in runtime.tool_names

    def test_subagent_profile_blocks_code_execution_tools(self, tmp_path, monkeypatch):
        class DummyGraph:
            def invoke(self, state, config=None):
                return {"messages": []}

        monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

        global_storage = ToolStorage(str(tmp_path / "global_tools"))
        global_storage.add_tool(
            "exec_code",
            {
                "name": "exec_code",
                "description": "Execute code",
                "parameters": [],
                "code": "result = 'ok'",
                "dependencies": [],
                "usage_guide": "exec",
            },
        )

        agent_def = AgentDefinition(
            name="restricted_builder",
            role="builder",
            description="Builder with restricted sandbox",
            system_prompt="Build carefully.",
            tools=["exec_code"],
            capability_profile=AgentCapabilityProfile.from_value(
                {"preset": "builder", "allow_code_execution": False}
            ).to_dict(),
        )

        runtime = create_sub_agent_instance(
            agent_def=agent_def,
            tool_storage=ToolStorage(str(tmp_path / "agents" / "restricted_builder" / "tools")),
            global_tool_storage=global_storage,
            agent_storage=None,
            llm_factory=lambda **kwargs: object(),
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            project_paths=ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace"),
        )

        assert "exec_code" not in runtime.tool_names
        assert "exec_code" in runtime.control_policy.blocked_tools

    def test_agent_tool_inventory_separates_assigned_local_and_missing_tools(self, tmp_path):
        agent_def = AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help with tasks.",
            tools=["shared_tool", "missing_tool"],
        )
        global_storage = ToolStorage(str(tmp_path / "global_tools"))
        global_storage.add_tool(
            "shared_tool",
            {
                "name": "shared_tool",
                "description": "Shared helper",
                "parameters": [],
                "code": "result = 'ok'",
                "dependencies": [],
                "usage_guide": "shared",
            },
        )
        local_storage = ToolStorage(str(tmp_path / "agents" / "helper" / "tools"))
        local_storage.add_tool(
            "local_helper",
            {
                "name": "local_helper",
                "description": "Local helper",
                "parameters": [],
                "code": "result = 'ok'",
                "dependencies": [],
                "usage_guide": "local",
            },
        )

        inventory = build_agent_tool_inventory(
            agent_def=agent_def,
            global_tool_storage=global_storage,
            local_tool_storage=local_storage,
        )

        assert inventory["assigned_global_tool_names"] == ["shared_tool"]
        assert inventory["local_tool_names"] == ["local_helper"]
        assert inventory["missing_assigned_tools"][0]["name"] == "missing_tool"
        assert inventory["assigned_global_tools"][0]["sync_status"] == "global_only"
        assert inventory["local_tools"][0]["sync_status"] == "local_only"

    def test_builder_profile_gets_isolated_execution_tools(self, tmp_path, monkeypatch):
        class DummyGraph:
            def invoke(self, state, config=None):
                return {"messages": []}

        monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

        paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace")
        agent_def = AgentDefinition(
            name="builder",
            role="builder",
            description="Builder",
            system_prompt="Build things.",
            capability_profile=AgentCapabilityProfile.from_value("builder").to_dict(),
        )

        runtime = create_sub_agent_instance(
            agent_def=agent_def,
            tool_storage=None,
            global_tool_storage=None,
            agent_storage=None,
            llm_factory=lambda **kwargs: object(),
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            project_paths=paths,
        )

        assert "exec_code" in runtime.tool_names
        assert "scan_project" in runtime.tool_names
        assert "iterative_test" not in runtime.tool_names
        assert runtime.sandbox.adapter == "isolated"
        assert runtime.sandbox.visibility == "isolated"
        assert runtime.sandbox.workspace_dir != paths.workspace_dir

    def test_researcher_profile_gets_read_only_workspace_sandbox(self, tmp_path, monkeypatch):
        class DummyGraph:
            def invoke(self, state, config=None):
                return {"messages": []}

        monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

        paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace")
        agent_def = AgentDefinition(
            name="researcher",
            role="researcher",
            description="Researcher",
            system_prompt="Research carefully.",
            capability_profile=AgentCapabilityProfile.from_value("researcher").to_dict(),
        )

        runtime = create_sub_agent_instance(
            agent_def=agent_def,
            tool_storage=None,
            global_tool_storage=None,
            agent_storage=None,
            llm_factory=lambda **kwargs: object(),
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            project_paths=paths,
        )

        assert "scan_project" in runtime.tool_names
        assert "exec_code" not in runtime.tool_names
        assert runtime.sandbox.adapter == "workspace"
        assert runtime.sandbox.visibility == "project"
        assert runtime.sandbox.allows_writes is False
        assert runtime.sandbox.workspace_dir == paths.workspace_dir

    def test_coordinator_profile_uses_shared_tools_adapter(self, tmp_path, monkeypatch):
        class DummyGraph:
            def invoke(self, state, config=None):
                return {"messages": []}

        monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

        paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace")
        agent_def = AgentDefinition(
            name="coordinator",
            role="coordinator",
            description="Coordinator",
            system_prompt="Coordinate work.",
            capability_profile=AgentCapabilityProfile.from_value("coordinator").to_dict(),
        )

        runtime = create_sub_agent_instance(
            agent_def=agent_def,
            tool_storage=None,
            global_tool_storage=None,
            agent_storage=None,
            llm_factory=lambda **kwargs: object(),
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            project_paths=paths,
        )

        assert "exec_code" not in runtime.tool_names
        assert runtime.sandbox.adapter == "shared_tools"
        assert runtime.sandbox.visibility == "shared_tools"
        assert runtime.sandbox.workspace_dir == paths.tools_workspace_dir / "shared"

    def test_lead_profile_gets_workspace_write_execution_tools(self, tmp_path, monkeypatch):
        class DummyGraph:
            def invoke(self, state, config=None):
                return {"messages": []}

        monkeypatch.setattr("langchain.agents.create_agent", lambda *args, **kwargs: DummyGraph())

        paths = ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace")
        agent_def = AgentDefinition(
            name="lead",
            role="lead",
            description="Lead",
            system_prompt="Lead work.",
            capability_profile=AgentCapabilityProfile.from_value("lead").to_dict(),
        )

        runtime = create_sub_agent_instance(
            agent_def=agent_def,
            tool_storage=None,
            global_tool_storage=None,
            agent_storage=None,
            llm_factory=lambda **kwargs: object(),
            control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
            project_paths=paths,
        )

        assert "exec_code" in runtime.tool_names
        assert "scan_project" in runtime.tool_names
        assert "iterative_test" in runtime.tool_names
        assert runtime.sandbox.adapter == "workspace"
        assert runtime.sandbox.visibility == "project"
        assert runtime.sandbox.workspace_dir == paths.workspace_dir

    def test_delegate_agent_task_preserves_waiting_approval_payload(self, tmp_path, monkeypatch):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="helper",
                role="helper",
                description="General helper",
                system_prompt="Help with tasks.",
            )
        )

        class FakeRuntime:
            def invoke(self, **kwargs):
                return {
                    "status": "waiting_approval",
                    "response": "waiting",
                    "agent_name": "helper",
                    "role": "helper",
                    "success": False,
                    "state_update": {},
                    "tool_names": ["exec_code"],
                    "thread_id": kwargs["thread_id"],
                    "approval_id": "appr_sub_1",
                    "sandbox": {"mode": "restricted", "visibility": "isolated"},
                }

        monkeypatch.setattr("core.systems.agents.agent_services.create_sub_agent_instance", lambda **kwargs: FakeRuntime())

        result = delegate_agent_task(
            agent_storage=storage,
            llm_factory=lambda **kwargs: object(),
            agent_name="helper",
            task="hello",
            context="ctx",
        )

        assert result["status"] == "waiting_approval"
        assert result["approval_id"] == "appr_sub_1"
        assert result["sandbox"]["mode"] == "restricted"

    def test_invoke_persisted_agent_includes_structured_tool_inventory(self, tmp_path, monkeypatch):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="helper",
                role="helper",
                description="General helper",
                system_prompt="Help with tasks.",
                tools=["shared_tool"],
            )
        )

        global_storage = ToolStorage(str(tmp_path / "global_tools"))
        global_storage.add_tool(
            "shared_tool",
            {
                "name": "shared_tool",
                "description": "Shared helper",
                "parameters": [],
                "code": "result = 'ok'",
                "dependencies": [],
                "usage_guide": "shared",
            },
        )
        local_storage = ToolStorage(str(tmp_path / "agents" / "helper" / "tools"))
        local_storage.add_tool(
            "local_helper",
            {
                "name": "local_helper",
                "description": "Local helper",
                "parameters": [],
                "code": "result = 'ok'",
                "dependencies": [],
                "usage_guide": "local",
            },
        )

        class FakeRuntime:
            def invoke(self, **kwargs):
                return {
                    "response": "done:hello",
                    "agent_name": "helper",
                    "role": "helper",
                    "success": True,
                    "state_update": {},
                    "tool_names": ["shared_tool", "local_helper"],
                    "thread_id": kwargs["thread_id"],
                    "sandbox": {"mode": "isolated"},
                }

        monkeypatch.setattr("core.systems.agents.agent_services.create_sub_agent_instance", lambda **kwargs: FakeRuntime())

        result = invoke_persisted_agent(
            agent_storage=storage,
            llm_factory=lambda **kwargs: object(),
            agent_name="helper",
            task="hello",
            global_tool_storage=global_storage,
        )

        assert result["tool_inventory"]["assigned_global_tool_names"] == ["shared_tool"]
        assert result["tool_inventory"]["local_tool_names"] == ["local_helper"]

    def test_sync_agent_tool_to_global_copies_local_tool_to_global_storage(self, tmp_path):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="helper",
                role="helper",
                description="General helper",
                system_prompt="Help with tasks.",
            )
        )
        local_storage = ToolStorage(str(storage.tools_dir_for("helper")))
        local_storage.add_tool(
            "local_helper",
            {
                "name": "local_helper",
                "description": "Local helper",
                "parameters": [],
                "code": "result = 'ok'",
                "dependencies": [],
                "usage_guide": "local",
            },
        )
        global_storage = ToolStorage(str(tmp_path / "global_tools"))

        result = sync_agent_tool(
            agent_storage=storage,
            global_tool_storage=global_storage,
            agent_name="helper",
            tool_name="local_helper",
            direction="to_global",
        )

        assert result["action"] == "promoted_to_global"
        assert global_storage.get_tool("local_helper") is not None
        assert result["tool_inventory"]["local_tools"][0]["sync_status"] == "in_sync"

    def test_sync_agent_tool_to_global_requires_overwrite_for_conflicts(self, tmp_path):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="helper",
                role="helper",
                description="General helper",
                system_prompt="Help with tasks.",
            )
        )
        local_storage = ToolStorage(str(storage.tools_dir_for("helper")))
        local_storage.add_tool(
            "shared_tool",
            {
                "name": "shared_tool",
                "description": "Local helper",
                "parameters": [],
                "code": "result = 'local'",
                "dependencies": [],
                "usage_guide": "local",
            },
        )
        global_storage = ToolStorage(str(tmp_path / "global_tools"))
        global_storage.add_tool(
            "shared_tool",
            {
                "name": "shared_tool",
                "description": "Global helper",
                "parameters": [],
                "code": "result = 'global'",
                "dependencies": [],
                "usage_guide": "global",
            },
        )

        with pytest.raises(AgentToolSyncError):
            sync_agent_tool(
                agent_storage=storage,
                global_tool_storage=global_storage,
                agent_name="helper",
                tool_name="shared_tool",
                direction="to_global",
            )

        result = sync_agent_tool(
            agent_storage=storage,
            global_tool_storage=global_storage,
            agent_name="helper",
            tool_name="shared_tool",
            direction="to_global",
            overwrite=True,
        )

        assert result["action"] == "overwrote_global"
        assert global_storage.get_tool("shared_tool")["code"] == "result = 'local'"

    def test_sync_agent_tool_from_global_copies_tool_into_local_storage(self, tmp_path):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="helper",
                role="helper",
                description="General helper",
                system_prompt="Help with tasks.",
                tools=["shared_tool"],
            )
        )
        global_storage = ToolStorage(str(tmp_path / "global_tools"))
        global_storage.add_tool(
            "shared_tool",
            {
                "name": "shared_tool",
                "description": "Global helper",
                "parameters": [],
                "code": "result = 'global'",
                "dependencies": [],
                "usage_guide": "global",
            },
        )

        result = sync_agent_tool(
            agent_storage=storage,
            global_tool_storage=global_storage,
            agent_name="helper",
            tool_name="shared_tool",
            direction="from_global",
        )

        local_storage = ToolStorage(str(storage.tools_dir_for("helper")))
        assert result["action"] == "pulled_to_local"
        assert local_storage.get_tool("shared_tool") is not None
        assert result["tool_inventory"]["assigned_global_tools"][0]["sync_status"] == "in_sync"
        assert result["tool_inventory"]["local_tools"][0]["sync_status"] == "in_sync"

    def test_sync_agent_tool_from_global_requires_overwrite_for_conflicts(self, tmp_path):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="helper",
                role="helper",
                description="General helper",
                system_prompt="Help with tasks.",
                tools=["shared_tool"],
            )
        )
        local_storage = ToolStorage(str(storage.tools_dir_for("helper")))
        local_storage.add_tool(
            "shared_tool",
            {
                "name": "shared_tool",
                "description": "Local helper",
                "parameters": [],
                "code": "result = 'local'",
                "dependencies": [],
                "usage_guide": "local",
            },
        )
        global_storage = ToolStorage(str(tmp_path / "global_tools"))
        global_storage.add_tool(
            "shared_tool",
            {
                "name": "shared_tool",
                "description": "Global helper",
                "parameters": [],
                "code": "result = 'global'",
                "dependencies": [],
                "usage_guide": "global",
            },
        )

        with pytest.raises(AgentToolSyncError):
            sync_agent_tool(
                agent_storage=storage,
                global_tool_storage=global_storage,
                agent_name="helper",
                tool_name="shared_tool",
                direction="from_global",
            )

        result = sync_agent_tool(
            agent_storage=storage,
            global_tool_storage=global_storage,
            agent_name="helper",
            tool_name="shared_tool",
            direction="from_global",
            overwrite=True,
        )

        assert result["action"] == "overwrote_local"
        refreshed_local = ToolStorage(str(storage.tools_dir_for("helper")))
        assert refreshed_local.get_tool("shared_tool")["code"] == "result = 'global'"

    def test_pybot_resolve_approval_replaces_result_with_parent_resume(self):
        queue = ApprovalQueue()
        request = queue.create_request(
            kind="tool_call",
            scope="subagent:helper",
            summary="helper approval",
            prompt="allow?",
            callback=lambda approved, note: {"status": "completed", "response": "subagent done"},
        )

        queue.update_request_metadata(
            request.approval_id,
            target="subagent:helper",
            thread_id="delegate-helper-thread",
            parent_thread_id="session-1",
            parent_target="root_agent",
        )

        bot = PyBot.__new__(PyBot)
        bot.approval_queue = queue
        bot.thread_id = "session-1"
        bot._resume_delegated_tool_approval = lambda **kwargs: {"status": "completed", "response": "parent resumed"}
        bot._rebuild_runtime_result_if_needed = PyBot._rebuild_runtime_result_if_needed.__get__(bot, PyBot)
        bot._resume_parent_orchestration_if_needed = PyBot._resume_parent_orchestration_if_needed.__get__(bot, PyBot)
        bot._is_recorded_resolution = PyBot._is_recorded_resolution
        bot._invoke_config = lambda **kwargs: {"configurable": {"thread_id": "session-1"}, "recursion_limit": 100}

        result = PyBot.resolve_approval(
            bot,
            request.approval_id,
            approved=True,
            note="ok",
            approver="lead",
        )

        assert result["success"] is True
        assert result["approval"]["resolved_by"] == "lead"
        assert result["subagent_result"]["response"] == "subagent done"
        assert result["result"]["response"] == "parent resumed"

    def test_pybot_resolve_approval_replays_root_tool_interrupt_from_metadata(self):
        queue = ApprovalQueue()
        request = queue.create_request(
            kind="tool_call",
            scope="root:session-1",
            summary="root tool approval",
            prompt="allow root?",
            metadata={
                "target": "root_agent",
                "thread_id": "session-1",
                "interrupt_id": "interrupt-1",
                "action_requests": [{"name": "create_agent", "args": {"agent_name": "helper"}, "tool_call_id": "call_1"}],
                "review_configs": [{"action_name": "create_agent", "allowed_decisions": ["approve", "reject"]}],
                "approval_scope": "root:session-1",
            },
        )

        bot = PyBot.__new__(PyBot)
        bot.approval_queue = queue
        bot.thread_id = "session-1"
        bot.middleware = type("Middleware", (), {"approval_scope": "root:session-1"})()
        bot._resume_tool_approval = lambda **kwargs: {"status": "completed", "response": "root resumed"}
        bot._resume_root_tool_approval_from_request = PyBot._resume_root_tool_approval_from_request.__get__(bot, PyBot)
        bot._rebuild_runtime_result_if_needed = PyBot._rebuild_runtime_result_if_needed.__get__(bot, PyBot)
        bot._resume_parent_orchestration_if_needed = PyBot._resume_parent_orchestration_if_needed.__get__(bot, PyBot)
        bot._is_recorded_resolution = PyBot._is_recorded_resolution
        bot._invoke_config = lambda **kwargs: {"configurable": {"thread_id": "session-1"}, "recursion_limit": 100}

        result = PyBot.resolve_approval(
            bot,
            request.approval_id,
            approved=True,
            note="ok",
            approver="lead",
        )

        assert result["success"] is True
        assert result["result"]["response"] == "root resumed"
        assert queue.get_request(request.approval_id).resolution_result["response"] == "root resumed"

    def test_resume_persisted_agent_approval_updates_queue_result(self, tmp_path, monkeypatch):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="helper",
                role="helper",
                description="General helper",
                system_prompt="Help with tasks.",
            )
        )
        queue = ApprovalQueue()
        request = queue.create_request(
            kind="tool_call",
            scope="subagent:helper",
            summary="helper approval",
            prompt="allow helper?",
            metadata={"target": "subagent:helper", "thread_id": "delegate-helper-thread"},
        )

        class FakeRuntime:
            def resume_approval(self, **kwargs):
                return {"status": "completed", "response": "helper resumed", "thread_id": kwargs["thread_id"]}

        monkeypatch.setattr("core.systems.agents.agent_services.create_sub_agent_instance", lambda **kwargs: FakeRuntime())

        result = resume_persisted_agent_approval(
            agent_storage=storage,
            llm_factory=lambda **kwargs: object(),
            approval_queue=queue,
            approval_id=request.approval_id,
            agent_name="helper",
            thread_id="delegate-helper-thread",
            approved=True,
            note="ok",
        )

        assert result["response"] == "helper resumed"
        assert queue.get_request(request.approval_id).resolution_result["response"] == "helper resumed"


# ---------------------------------------------------------------------------
# 4. Agent Storage Tests (formerly test_agent_storage.py)
# ---------------------------------------------------------------------------

class TestAgentStorageClass:
    def test_agent_storage_persists_updates(self, tmp_path: Path):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="helper",
                role="helper",
                description="General helper",
                system_prompt="Help users.",
            )
        )
        storage.add_tool_to_agent("helper", "search_notes")
        storage.toggle_agent("helper", False)

        reloaded = AgentStorage(str(tmp_path / "agents"))
        saved = reloaded.get_agent("helper")

        assert saved is not None
        assert saved.enabled is False
        assert saved.tools == ["search_notes"]

    def test_agent_storage_remove_agent_deletes_directory(self, tmp_path: Path):
        storage = AgentStorage(str(tmp_path / "agents"))
        storage.add_agent(
            AgentDefinition(
                name="helper",
                role="helper",
                description="General helper",
                system_prompt="Help users.",
            )
        )

        agent_dir = tmp_path / "agents" / "helper"
        assert agent_dir.exists()

        removed = storage.remove_agent("helper")

        assert removed is True
        assert not agent_dir.exists()


# ---------------------------------------------------------------------------
# 5. Persistent Agent Runner Tests (formerly test_persistent_agent_runner.py)
# ---------------------------------------------------------------------------

class TestPersistentTask:
    def test_create_and_serialize(self):
        task = PersistentTask(
            task_id="t1",
            name="research",
            description="Do research",
            agent_name="researcher",
        )
        task.add_step("Gather data")
        task.add_step("Analyze data")
        d = task.to_dict()
        assert d["task_id"] == "t1"
        assert len(d["steps"]) == 2
        assert d["progress"] == 0.0

    def test_from_dict_roundtrip(self):
        task = PersistentTask(
            task_id="t2",
            name="build",
            description="Build app",
            agent_name="builder",
            max_steps=10,
        )
        task.add_step("Design")
        task.add_step("Code")
        d = task.to_dict()
        restored = PersistentTask.from_dict(d)
        assert restored.task_id == "t2"
        assert len(restored.steps) == 2
        assert restored.max_steps == 10

    def test_progress_tracking(self):
        task = PersistentTask(task_id="t3", name="x", description="x", agent_name="a")
        task.add_step("s1")
        task.add_step("s2")
        assert task.progress == 0.0
        task.steps[0].status = "completed"
        assert task.progress == 0.5
        task.steps[1].status = "completed"
        assert task.progress == 1.0

    def test_current_step(self):
        task = PersistentTask(task_id="t4", name="x", description="x", agent_name="a")
        task.add_step("s1")
        task.add_step("s2")
        assert task.current_step.step_id == "step_1"
        task.steps[0].status = "completed"
        assert task.current_step.step_id == "step_2"
        task.steps[1].status = "completed"
        assert task.current_step is None


class TestPersistentAgentRunner:
    def _make_runner(self, tmpdir: str) -> PersistentAgentRunner:
        return PersistentAgentRunner(tmpdir)

    def test_create_and_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="test_task",
                description="test",
                agent_name="agent1",
                steps=["Step 1", "Step 2"],
            )
            assert task.task_id
            assert len(runner.list_tasks()) == 1

    def test_execute_step(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="exec_test",
                description="test",
                agent_name="agent1",
                steps=["Step 1", "Step 2"],
            )

            def step_fn(step: PersistentTaskStep, ctx: dict) -> dict:
                return {"result": f"done_{step.step_id}"}

            step = runner.execute_step(task.task_id, step_fn)
            assert step.status == "completed"
            assert step.output_data["result"] == "done_step_1"

    def test_run_all_steps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="run_all",
                description="test",
                agent_name="agent1",
                steps=["A", "B", "C"],
            )
            call_count = 0

            def step_fn(step: PersistentTaskStep, ctx: dict) -> dict:
                nonlocal call_count
                call_count += 1
                return {"step": step.step_id}

            result = runner.run_all_steps(task.task_id, step_fn)
            assert result.status == PersistentTaskStatus.COMPLETED
            assert call_count == 3
            assert result.progress == 1.0

    def test_step_failure_stops(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="fail_test",
                description="test",
                agent_name="agent1",
                steps=["OK", "FAIL", "SKIP"],
            )

            def step_fn(step: PersistentTaskStep, ctx: dict) -> dict:
                if step.description == "FAIL":
                    raise RuntimeError("boom")
                return {"ok": True}

            result = runner.run_all_steps(task.task_id, step_fn)
            assert result.status == PersistentTaskStatus.FAILED
            assert result.steps[0].status == "completed"
            assert result.steps[1].status == "failed"
            assert result.steps[2].status == "pending"

    def test_persistence_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner1 = self._make_runner(tmpdir)
            task = runner1.create_task(
                name="persist_test",
                description="test",
                agent_name="agent1",
                steps=["Step 1"],
            )
            tid = task.task_id

            runner2 = self._make_runner(tmpdir)
            loaded = runner2.get_task(tid)
            assert loaded is not None
            assert loaded.name == "persist_test"
            assert len(loaded.steps) == 1

    def test_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="pause_test",
                description="test",
                agent_name="agent1",
                steps=["A", "B"],
            )
            runner.execute_step(task.task_id, lambda s, c: {"ok": True})
            assert task.status == PersistentTaskStatus.RUNNING

            assert runner.pause_task(task.task_id)
            assert task.status == PersistentTaskStatus.PAUSED

            assert runner.resume_task(task.task_id)
            assert task.status == PersistentTaskStatus.RUNNING

    def test_cancel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="cancel_test",
                description="test",
                agent_name="agent1",
                steps=["A"],
            )
            assert runner.cancel_task(task.task_id)
            assert task.status == PersistentTaskStatus.CANCELLED

    def test_get_resumable_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            t1 = runner.create_task(name="a", description="a", agent_name="a", steps=["x", "y"])
            runner.create_task(name="b", description="b", agent_name="b", steps=["x"])
            runner.execute_step(t1.task_id, lambda s, c: {"ok": True})
            runner.pause_task(t1.task_id)

            resumable = runner.get_resumable_tasks()
            assert len(resumable) == 1
            assert resumable[0].task_id == t1.task_id

    def test_context_accumulation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="ctx_test",
                description="test",
                agent_name="agent1",
                steps=["Gather", "Process"],
                context={"initial": True},
            )

            def step_fn(step: PersistentTaskStep, ctx: dict) -> dict:
                if step.description == "Gather":
                    return {"data": [1, 2, 3]}
                return {"processed": sum(ctx.get("data", []))}

            runner.run_all_steps(task.task_id, step_fn)
            assert task.context["processed"] == 6
            assert task.context["initial"] is True

    def test_execute_step_supports_separate_context_update(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="memory_test",
                description="test",
                agent_name="agent1",
                steps=["Summarize"],
                context={"existing": True},
            )

            def step_fn(step: PersistentTaskStep, ctx: dict) -> dict:
                assert ctx["existing"] is True
                return {
                    "__step_output__": {"raw": "x" * 1000},
                    "__context_update__": {"summary": "short note"},
                }

            step = runner.execute_step(task.task_id, step_fn)
            assert step is not None
            assert step.status == "completed"
            assert step.output_data == {"raw": "x" * 1000}
            assert task.context["summary"] == "short note"
            assert task.final_output == {"raw": "x" * 1000}

    def test_merge_context_updates_task_in_place(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="merge_ctx",
                description="test",
                agent_name="agent1",
                steps=["A"],
                context={"a": 1},
            )

            assert runner.merge_context(task.task_id, {"b": 2, "c": 3}) is True
            assert task.context == {"a": 1, "b": 2, "c": 3}

    def test_replace_pending_steps_reopens_completed_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._make_runner(tmpdir)
            task = runner.create_task(
                name="replan_task",
                description="test",
                agent_name="agent1",
                steps=["Initial"],
            )

            runner.execute_step(task.task_id, lambda s, c: {"done": True})
            assert task.status == PersistentTaskStatus.COMPLETED

            assert runner.replace_pending_steps(task.task_id, ["New step", "Wrap up"]) is True
            assert task.status == PersistentTaskStatus.RUNNING
            assert task.final_output is None
            assert [step.description for step in task.steps] == ["Initial", "New step", "Wrap up"]
            assert task.steps[0].status == "completed"
            assert task.steps[1].status == "pending"


# ---------------------------------------------------------------------------
# 6. Society Of Mind Tests (formerly test_society_of_mind.py)
# ---------------------------------------------------------------------------

class TestSocietyOfMind:
    def test_basic_run(self):
        soc = SocietyOfMind("team1", _mind_agents(), _mock_llm_factory("Final answer"))
        result = soc.run("Analyze the data")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_custom_config(self):
        config = SocietyConfig(max_rounds=2, synthesizer_model="gpt-4")
        soc = SocietyOfMind("team2", _mind_agents(), _mock_llm_factory("OK"), config=config)
        result = soc.run("Task")
        assert result == "OK"

    def test_conversation_log(self):
        soc = SocietyOfMind("team3", _mind_agents(), _mock_llm_factory("Summary"), config=SocietyConfig(max_rounds=2))
        log = soc.get_conversation_log("Do something")
        assert "task" in log
        assert "conversation" in log
        assert "final_answer" in log
        assert log["final_answer"] == "Summary"
        assert log["agent_count"] == 2
        assert log["message_count"] >= 1

    def test_event_emission(self):
        events = []

        def handler(event):
            events.append(event)

        event_bus.subscribe(EventType.AGENT_START, handler)
        event_bus.subscribe(EventType.AGENT_END, handler)

        soc = SocietyOfMind("ev_team", _mind_agents(), _mock_llm_factory(), config=SocietyConfig(max_rounds=1))
        soc.run("test")

        event_bus.unsubscribe(EventType.AGENT_START, handler)
        event_bus.unsubscribe(EventType.AGENT_END, handler)

        start_events = [e for e in events if e.type == EventType.AGENT_START]
        end_events = [e for e in events if e.type == EventType.AGENT_END]
        assert len(start_events) >= 1
        assert len(end_events) >= 1
        assert start_events[0].payload["mode"] == "society_of_mind"

    def test_with_context(self):
        soc = SocietyOfMind("ctx_team", _mind_agents(), _mock_llm_factory("result"), config=SocietyConfig(max_rounds=1))
        result = soc.run("task", context="important background")
        assert result == "result"

    def test_llm_failure(self):
        def bad_factory(model="", temperature=0.7):
            raise RuntimeError("LLM down")

        agents = _mind_agents()
        soc = SocietyOfMind("fail_team", agents, bad_factory, config=SocietyConfig(max_rounds=1))
        result = soc.run("task")
        assert isinstance(result, str)

    def test_single_agent(self):
        agents = [MindAgent("solo", role="expert")]
        soc = SocietyOfMind("solo_team", agents, _mock_llm_factory("answer"), config=SocietyConfig(max_rounds=1))
        result = soc.run("question")
        assert result == "answer"

    def test_custom_selector(self):
        soc = SocietyOfMind(
            "selector_team",
            _mind_agents(),
            _mock_llm_factory("done"),
            selector=RoundRobinSelector(),
            config=SocietyConfig(max_rounds=3),
        )
        result = soc.run("task")
        assert result == "done"

    def test_discussion_includes_task_each_round(self):
        call_log = []

        def logging_factory(model="", temperature=0.7):
            llm = MagicMock()
            result = MagicMock()
            result.content = "response"

            def invoke_fn(prompt):
                call_log.append(prompt)
                return result

            llm.invoke = invoke_fn
            return llm

        config = SocietyConfig(max_rounds=2, include_task_in_each_round=True)
        soc = SocietyOfMind("log_team", _mind_agents(), logging_factory, config=config)
        soc.run("Specific task XYZ")
        agent_calls = [c for c in call_log if "Specific task XYZ" in c]
        assert len(agent_calls) >= 2


# ---------------------------------------------------------------------------
# 7. Speaker Selection Tests (formerly test_speaker_selection.py)
# ---------------------------------------------------------------------------

def _participants():
    return [
        Participant("Alice", role="researcher", description="Expert in data analysis"),
        Participant("Bob", role="developer", description="Backend specialist"),
        Participant("Carol", role="designer", description="UI/UX expert"),
    ]


def _history():
    return [
        ChatMessage("Alice", "I found some interesting patterns in the data."),
        ChatMessage("Bob", "Let me write an API for that."),
    ]


class TestRoundRobinSelector:
    def test_cycles_through(self):
        s = RoundRobinSelector()
        p = _participants()
        names = [s.select(p, []) for _ in range(6)]
        assert names == ["Alice", "Bob", "Carol", "Alice", "Bob", "Carol"]

    def test_empty_raises(self):
        s = RoundRobinSelector()
        with pytest.raises(ValueError):
            s.select([], [])


class TestRandomSelector:
    def test_selects_from_participants(self):
        s = RandomSelector()
        p = _participants()
        for _ in range(20):
            name = s.select(p, [])
            assert name in {"Alice", "Bob", "Carol"}

    def test_no_repeat(self):
        s = RandomSelector(allow_repeat=False)
        p = _participants()
        hist = [ChatMessage("Bob", "just said something")]
        for _ in range(20):
            name = s.select(p, hist)
            assert name != "Bob"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            RandomSelector().select([], [])


class TestRuleBasedSelector:
    def test_keyword_match(self):
        s = RuleBasedSelector({"data": "Alice", "api": "Bob", "design": "Carol"})
        p = _participants()
        hist = [ChatMessage("x", "We need to update the API endpoint")]
        assert s.select(p, hist) == "Bob"

    def test_no_match_returns_default(self):
        s = RuleBasedSelector({"data": "Alice"}, default="Carol")
        p = _participants()
        hist = [ChatMessage("x", "nothing relevant")]
        assert s.select(p, hist) == "Carol"

    def test_no_match_no_default(self):
        s = RuleBasedSelector({"data": "Alice"})
        p = _participants()
        hist = [ChatMessage("x", "nothing")]
        assert s.select(p, hist) == "Alice"

    def test_empty_history(self):
        s = RuleBasedSelector({"data": "Alice"}, default="Bob")
        p = _participants()
        assert s.select(p, []) == "Bob"

    def test_case_insensitive(self):
        s = RuleBasedSelector({"DATA": "Alice"})
        p = _participants()
        hist = [ChatMessage("x", "need data analysis")]
        assert s.select(p, hist) == "Alice"


class TestLLMSelector:
    def _mock_llm(self, response: str):
        llm = MagicMock()
        result = MagicMock()
        result.content = response
        llm.invoke.return_value = result
        return llm

    def test_valid_selection(self):
        llm = self._mock_llm("Alice")
        s = LLMSelector(llm)
        p = _participants()
        assert s.select(p, _history()) == "Alice"

    def test_case_insensitive_parse(self):
        llm = self._mock_llm("bob")
        s = LLMSelector(llm, allow_repeat=True)
        p = _participants()
        assert s.select(p, _history()) == "Bob"

    def test_name_in_sentence(self):
        llm = self._mock_llm("I think Carol should speak next")
        s = LLMSelector(llm, allow_repeat=True)
        p = _participants()
        assert s.select(p, _history()) == "Carol"

    def test_fallback_on_invalid(self):
        llm = self._mock_llm("UnknownAgent")
        s = LLMSelector(llm, max_attempts=1)
        p = _participants()
        name = s.select(p, _history())
        assert name in {"Alice", "Bob", "Carol"}

    def test_fallback_on_exception(self):
        llm = MagicMock()
        llm.invoke.side_effect = Exception("LLM down")
        s = LLMSelector(llm, max_attempts=1)
        p = _participants()
        name = s.select(p, _history())
        assert name in {"Alice", "Bob", "Carol"}

    def test_single_participant(self):
        llm = self._mock_llm("whatever")
        s = LLMSelector(llm)
        p = [Participant("Solo")]
        assert s.select(p, []) == "Solo"

    def test_no_repeat(self):
        llm = self._mock_llm("Bob")
        s = LLMSelector(llm, allow_repeat=False, max_attempts=1)
        p = _participants()
        hist = [ChatMessage("Bob", "I just spoke")]
        name = s.select(p, hist)
        assert name != "Bob"

    def test_callable_llm(self):
        s = LLMSelector(lambda prompt: "Carol", allow_repeat=True)
        p = _participants()
        assert s.select(p, _history()) == "Carol"

    def test_empty_raises(self):
        llm = self._mock_llm("x")
        with pytest.raises(ValueError):
            LLMSelector(llm).select([], [])

    def test_prompt_building(self):
        llm = self._mock_llm("Alice")
        s = LLMSelector(llm, history_window=2)
        p = _participants()
        s.select(p, _history())
        call_args = llm.invoke.call_args[0][0]
        assert "Alice" in call_args
        assert "Bob" in call_args
        assert "researcher" in call_args


# ---------------------------------------------------------------------------
# 8. Team Orchestrator Tests (formerly test_team_orchestrator.py)
# ---------------------------------------------------------------------------

class TestSequentialTeam:
    def test_basic_sequential(self):
        agents = {
            "researcher": _make_agent_orchestrator_def("researcher", "Researches topics"),
            "writer": _make_agent_orchestrator_def("writer", "Writes content"),
        }
        tasks = [
            TaskDefinition(name="research", description="Research AI", expected_output="findings", agent_name="researcher"),
            TaskDefinition(name="write", description="Write article", expected_output="article", agent_name="writer", context_from=["research"]),
        ]

        def execute_fn(prompt, agent_name=None):
            if agent_name == "researcher":
                return "AI is transforming everything"
            return "Article about AI transformation"

        team = SequentialTeam(agents=agents, tasks=tasks, execute_fn=execute_fn)
        result = team.run()
        assert result.success is True
        assert "research" in result.task_results
        assert "write" in result.task_results
        assert result.final_output == "Article about AI transformation"
        assert len(result.summary) == 2

    def test_failure_stops_pipeline(self):
        agents = {"a": _make_agent_orchestrator_def("a", "Agent A")}
        tasks = [
            TaskDefinition(name="t1", description="fail", expected_output="x"),
            TaskDefinition(name="t2", description="skip", expected_output="y"),
        ]

        def execute_fn(prompt, agent_name=None):
            raise RuntimeError("boom")

        team = SequentialTeam(agents=agents, tasks=tasks, execute_fn=execute_fn)
        result = team.run(stop_on_failure=True)
        assert result.success is False
        assert len(result.task_results) == 0


class TestHierarchicalTeam:
    def test_coordinator_delegates_and_reviews(self):
        agents = {
            "coordinator": _make_agent_orchestrator_def("coordinator", "Coordinator", goal="Coordinate team"),
            "researcher": _make_agent_orchestrator_def("researcher", "Researcher"),
        }
        tasks = [
            TaskDefinition(name="research", description="Research topic", expected_output="findings", agent_name="researcher"),
        ]
        call_log = []

        def execute_fn(prompt, agent_name=None):
            call_log.append(agent_name)
            if agent_name == "coordinator":
                return "researcher"
            return "Research findings here"

        team = HierarchicalTeam(agents=agents, tasks=tasks, execute_fn=execute_fn, coordinator_name="coordinator")
        result = team.run()
        assert result.success is True
        assert "coordinator" in call_log
        assert "researcher" in call_log

    def test_coordinator_retry_on_validation_failure(self):
        class Output(BaseModel):
            data: str = Field(description="data")

        agents = {
            "coordinator": _make_agent_orchestrator_def("coordinator", "Coordinator"),
            "worker": _make_agent_orchestrator_def("worker", "Worker"),
        }
        tasks = [
            TaskDefinition(
                name="task1", description="Do work",
                expected_output="JSON", agent_name="worker",
                output_schema=Output, max_retries=1,
            ),
        ]
        call_count = {"worker": 0, "coordinator": 0}

        def execute_fn(prompt, agent_name=None):
            if agent_name == "coordinator":
                call_count["coordinator"] += 1
                if "assign" in prompt.lower():
                    return "worker"
                return "retry"
            call_count["worker"] += 1
            if call_count["worker"] == 1:
                return "bad output"
            return '{"data": "good"}'

        team = HierarchicalTeam(agents=agents, tasks=tasks, execute_fn=execute_fn, coordinator_name="coordinator")
        result = team.run(max_reviews=1)
        assert result.success is True
        assert call_count["worker"] >= 2


class TestTeamResult:
    def test_final_output(self):
        result = TeamResult(
            task_results={"t1": "first", "t2": "second"},
            summary=[],
            success=True,
        )
        assert result.final_output == "second"

    def test_empty_final_output(self):
        result = TeamResult(task_results={}, summary=[], success=False)
        assert result.final_output is None


# ---------------------------------------------------------------------------
# 9. Delegation Chain Tests (formerly test_delegation_chain.py)
# ---------------------------------------------------------------------------

class TestDelegationChain:
    def test_empty_agents(self):
        chain = build_delegation_chain([])
        assert len(chain) == 1
        assert chain[0]["name"] == "root"

    def test_single_level(self):
        agents = [
            {
                "name": "worker",
                "role": "builder",
                "parent": None,
                "capability_profile": AgentCapabilityProfile.from_value("builder"),
            },
        ]
        chain = build_delegation_chain(agents)
        assert len(chain) == 2
        assert chain[1]["name"] == "worker"
        assert chain[1]["level"] == 1

    def test_multi_level(self):
        agents = [
            {
                "name": "manager",
                "role": "coordinator",
                "parent": None,
                "capability_profile": AgentCapabilityProfile.from_value("manager"),
            },
            {
                "name": "worker",
                "role": "builder",
                "parent": "manager",
                "capability_profile": AgentCapabilityProfile.from_value("builder"),
            },
        ]
        chain = build_delegation_chain(agents)
        assert len(chain) == 3
        assert chain[2]["level"] == 2
        assert chain[2]["name"] == "worker"

    def test_restrictions_accumulate(self):
        restricted_profile = AgentCapabilityProfile.from_value("builder")
        restricted_profile = AgentCapabilityProfile(
            **{
                **restricted_profile.to_dict(),
                "allow_code_execution": False,
                "allow_workflow_management": False,
            }
        )
        agents = [
            {
                "name": "restricted",
                "role": "limited",
                "parent": None,
                "capability_profile": restricted_profile,
            },
        ]
        chain = build_delegation_chain(agents)
        assert len(chain[1]["new_restrictions"]) > 0

    def test_format_delegation_tree_ascii(self):
        agents = [
            {
                "name": "mgr",
                "role": "coordinator",
                "parent": None,
                "capability_profile": AgentCapabilityProfile.from_value("manager"),
            },
        ]
        chain = build_delegation_chain(agents)
        tree = format_delegation_tree(chain)
        assert "root" in tree
        assert "mgr" in tree
        assert "|--" in tree


class TestDockerSandboxAdapter:
    def test_docker_in_adapter_list(self):
        adapters = list_sandbox_adapters()
        names = [a["name"] for a in adapters]
        assert "docker" in names
        docker_adapter = next(a for a in adapters if a["name"] == "docker")
        assert docker_adapter["backend"] == "docker"


# ---------------------------------------------------------------------------
# 10. Delegation Payload Tests (formerly test_delegation_payload.py)
# ---------------------------------------------------------------------------

class TestDelegationPayloadClass:
    def test_normalize_delegation_payload_preserves_structured_state(self):
        payload = normalize_delegation_payload(
            {
                "status": "completed",
                "success": True,
                "response": "done",
                "state_update": {"next_step": "ship"},
                "tool_names": ["search_notes"],
            },
            agent_name="helper",
            task="finish task",
        )

        assert payload["agent_name"] == "helper"
        assert payload["task"] == "finish task"
        assert payload["state_update"] == {"next_step": "ship"}
        assert payload["state_keys"] == ["next_step"]
        assert payload["has_state_update"] is True

    def test_normalize_delegation_payload_parses_json_strings(self):
        payload = normalize_delegation_payload(
            json.dumps(
                {
                    "status": "waiting_approval",
                    "success": False,
                    "response": "paused",
                    "approval_id": "appr_123",
                },
                ensure_ascii=False,
            ),
            agent_name="helper",
        )

        assert payload["status"] == "waiting_approval"
        assert payload["approval_id"] == "appr_123"
        assert delegation_response_text(payload) == "paused"


# ---------------------------------------------------------------------------
# 11. Ask/Delegate Tools Tests (formerly test_delegation_tools.py)
# ---------------------------------------------------------------------------

def _make_ask_agent_def(name="test_agent"):
    return AgentDefinition(
        name=name,
        role="tester",
        description="A test agent",
        system_prompt="You are a test agent.",
        capabilities=["testing"],
        model_config_data=AgentModelConfig(model_id="gpt-4", temperature=0.5),
    )


def _make_storage_with_agent(name="test_agent"):
    storage = AgentStorage()
    storage.add_agent(_make_ask_agent_def(name))
    return storage


class TestAskAgentTool:
    def test_ask_existing_agent(self):
        storage = _make_storage_with_agent()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="42 is the answer")
        llm_factory = MagicMock(return_value=mock_llm)

        tool = AskAgentTool(
            agent_storage=storage,
            llm_factory=llm_factory,
        )
        result = json.loads(tool._run(agent_name="test_agent", question="What is 42?"))
        assert result["success"]
        assert result["answer"] == "42 is the answer"
        llm_factory.assert_called_once_with(model="gpt-4", temperature=0.5)

    def test_ask_with_context(self):
        storage = _make_storage_with_agent()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="Got it")
        llm_factory = MagicMock(return_value=mock_llm)

        tool = AskAgentTool(agent_storage=storage, llm_factory=llm_factory)
        result = json.loads(
            tool._run(
                agent_name="test_agent",
                question="What now?",
                context="Previous analysis showed X",
            )
        )
        assert result["success"]
        prompt_used = mock_llm.invoke.call_args[0][0]
        assert "Previous analysis showed X" in prompt_used

    def test_ask_nonexistent_agent(self):
        storage = _make_storage_with_agent("real_agent")
        tool = AskAgentTool(agent_storage=storage, llm_factory=MagicMock())
        result = json.loads(tool._run(agent_name="ghost", question="Hello?"))
        assert not result["success"]
        assert "不存在" in result["error"]
        assert "real_agent" in result["available_agents"]

    def test_ask_no_storage(self):
        tool = AskAgentTool(agent_storage=None)
        result = json.loads(tool._run(agent_name="x", question="y"))
        assert not result["success"]

    def test_ask_no_llm_factory(self):
        storage = _make_storage_with_agent()
        tool = AskAgentTool(agent_storage=storage, llm_factory=None)
        result = json.loads(tool._run(agent_name="test_agent", question="y"))
        assert not result["success"]

    def test_ask_llm_error(self):
        storage = _make_storage_with_agent()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("LLM boom")
        llm_factory = MagicMock(return_value=mock_llm)

        tool = AskAgentTool(agent_storage=storage, llm_factory=llm_factory)
        result = json.loads(tool._run(agent_name="test_agent", question="?"))
        assert not result["success"]
        assert "boom" in result["error"]


class TestAskAgentEvents:
    def setup_method(self):
        event_bus.clear()

    def test_ask_emits_events(self):
        events = []
        event_bus.subscribe(EventType.AGENT_START, lambda e: events.append(e))
        event_bus.subscribe(EventType.AGENT_END, lambda e: events.append(e))

        storage = _make_storage_with_agent()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="answer")

        tool = AskAgentTool(
            agent_storage=storage,
            llm_factory=MagicMock(return_value=mock_llm),
        )
        tool._run(agent_name="test_agent", question="?")

        assert len(events) == 2
        assert events[0].type == EventType.AGENT_START
        assert events[0].payload["mode"] == "ask"
        assert events[1].type == EventType.AGENT_END
        assert events[1].payload["success"]

    def test_ask_emits_error_event(self):
        events = []
        event_bus.subscribe(EventType.AGENT_END, lambda e: events.append(e))

        storage = _make_storage_with_agent()
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("fail")

        tool = AskAgentTool(
            agent_storage=storage,
            llm_factory=MagicMock(return_value=mock_llm),
        )
        tool._run(agent_name="test_agent", question="?")

        assert len(events) == 1
        assert not events[0].payload["success"]


class TestDelegateEmitsEvents:
    def setup_method(self):
        event_bus.clear()

    def test_delegate_emits_start_and_end(self):
        events = []
        event_bus.subscribe(EventType.AGENT_START, lambda e: events.append(e))
        event_bus.subscribe(EventType.AGENT_END, lambda e: events.append(e))

        storage = _make_storage_with_agent()
        tool = DelegateToAgentTool(agent_storage=storage)

        with patch("core.systems.agents.agent_creator.delegate_agent_task") as mock_delegate:
            mock_delegate.return_value = {"success": True, "result": "done"}
            tool._run(agent_name="test_agent", task="do something")

        assert len(events) == 2
        assert events[0].payload["mode"] == "delegate"
        assert events[1].payload["mode"] == "delegate"

    def test_delegate_passes_parent_runtime_context(self):
        storage = _make_storage_with_agent()
        tool = DelegateToAgentTool(
            agent_storage=storage,
            runtime_context={
                "current_agent_name": "planner",
                "depth": 2,
                "run_id": "run-1",
                "thread_id": "thread-1",
            },
        )

        with patch("core.systems.agents.agent_creator.delegate_agent_task") as mock_delegate:
            mock_delegate.return_value = {"success": True, "result": "done"}
            tool._run(agent_name="test_agent", task="do something")

        kwargs = mock_delegate.call_args.kwargs
        assert kwargs["parent_agent_name"] == "planner"
        assert kwargs["parent_depth"] == 2
        assert kwargs["parent_run_id"] == "run-1"
        assert kwargs["parent_thread_id"] == "thread-1"


class TestGetAgentCreatorToolsIncludeAsk:
    def test_default_includes_ask(self):
        storage = AgentStorage()
        tools = get_agent_creator_tools(storage)
        names = [t.name for t in tools]
        assert "ask_agent" in names

    def test_exclude_ask(self):
        storage = AgentStorage()
        tools = get_agent_creator_tools(storage, include_ask=False)
        names = [t.name for t in tools]
        assert "ask_agent" not in names


# ---------------------------------------------------------------------------
# 12. Agent as Tool Tests (formerly test_agent_as_tool.py)
# ---------------------------------------------------------------------------

class TestAgentTool:
    def test_basic_call(self):
        tool = AgentTool(
            agent_name="analyst",
            agent_role="data analyst",
            system_prompt="You are a data analyst.",
            llm_factory=_mock_llm_factory("Analysis result: 42"),
        )
        result = tool._run("Analyze this data")
        assert result == "Analysis result: 42"

    def test_tool_name(self):
        tool = AgentTool(agent_name="coder", llm_factory=_mock_llm_factory())
        assert tool.name == "agent_coder"

    def test_with_context(self):
        factory = _mock_llm_factory("OK")
        tool = AgentTool(agent_name="t", system_prompt="sys", llm_factory=factory)
        tool._run("request", context="some background")
        assert tool._run("req", "ctx") == "OK"

    def test_no_llm_factory(self):
        tool = AgentTool(agent_name="t")
        result = tool._run("test")
        data = json.loads(result)
        assert not data["success"]
        assert "llm_factory" in data["error"]

    def test_llm_exception(self):
        def bad_factory(model="", temperature=0.7):
            raise RuntimeError("LLM crashed")

        tool = AgentTool(agent_name="t", llm_factory=bad_factory)
        result = tool._run("test")
        data = json.loads(result)
        assert not data["success"]

    def test_event_emission(self):
        events = []

        def handler(event):
            events.append(event)

        event_bus.subscribe(EventType.AGENT_START, handler)
        event_bus.subscribe(EventType.AGENT_END, handler)

        tool = AgentTool(agent_name="ev_test", llm_factory=_mock_llm_factory())
        tool._run("go")

        event_bus.unsubscribe(EventType.AGENT_START, handler)
        event_bus.unsubscribe(EventType.AGENT_END, handler)

        types = [e.type for e in events]
        assert EventType.AGENT_START in types
        assert EventType.AGENT_END in types

    def test_custom_description(self):
        tool = AgentTool(
            agent_name="x",
            tool_description="Custom tool desc",
            llm_factory=_mock_llm_factory(),
        )
        assert tool.description == "Custom tool desc"


class TestTeamTool:
    def _agents(self):
        return [
            {"name": "Alice", "role": "researcher", "system_prompt": "Research expert"},
            {"name": "Bob", "role": "coder", "system_prompt": "Coding expert"},
        ]

    def test_basic_team(self):
        tool = TeamTool(
            team_name="dream_team",
            agents=self._agents(),
            llm_factory=_mock_llm_factory("Team output"),
            max_rounds=2,
        )
        result = tool._run("Build a feature")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_tool_name(self):
        tool = TeamTool(team_name="alpha", agents=self._agents(), llm_factory=_mock_llm_factory())
        assert tool.name == "team_alpha"

    def test_rounds(self):
        call_count = 0

        def counting_factory(model="", temperature=0.7):
            nonlocal call_count
            call_count += 1
            llm = MagicMock()
            result = MagicMock()
            result.content = f"Response {call_count}"
            llm.invoke.return_value = result
            return llm

        tool = TeamTool(
            team_name="t",
            agents=self._agents(),
            llm_factory=counting_factory,
            max_rounds=3,
        )
        tool._run("task")
        assert call_count == 3

    def test_with_summarizer(self):
        tool = TeamTool(
            team_name="t",
            agents=self._agents(),
            llm_factory=_mock_llm_factory("Summary"),
            max_rounds=2,
            summarizer_prompt="Summarize the conversation",
        )
        result = tool._run("task")
        assert isinstance(result, str)

    def test_no_llm_factory(self):
        tool = TeamTool(team_name="t", agents=self._agents(), max_rounds=1)
        result = tool._run("task")
        assert "Error" in result

    def test_event_emission(self):
        events = []

        def handler(event):
            events.append(event)

        event_bus.subscribe(EventType.AGENT_START, handler)
        event_bus.subscribe(EventType.AGENT_END, handler)

        tool = TeamTool(
            team_name="ev_team",
            agents=self._agents(),
            llm_factory=_mock_llm_factory(),
            max_rounds=1,
        )
        tool._run("go")

        event_bus.unsubscribe(EventType.AGENT_START, handler)
        event_bus.unsubscribe(EventType.AGENT_END, handler)

        assert any(e.type == EventType.AGENT_START for e in events)
        assert any(e.type == EventType.AGENT_END for e in events)

    def test_with_context(self):
        tool = TeamTool(
            team_name="t",
            agents=self._agents(),
            llm_factory=_mock_llm_factory("OK"),
            max_rounds=1,
        )
        result = tool._run("task", context="important context")
        assert isinstance(result, str)


class TestFactoryFunctions:
    def test_create_agent_tool(self):
        tool = create_agent_tool(
            name="helper",
            role="assistant",
            system_prompt="You help with tasks",
            llm_factory=_mock_llm_factory("Done"),
        )
        assert isinstance(tool, AgentTool)
        assert tool.name == "agent_helper"
        assert tool._run("test") == "Done"

    def test_create_team_tool(self):
        agents = [
            {"name": "A", "role": "r1"},
            {"name": "B", "role": "r2"},
        ]
        tool = create_team_tool(
            name="squad",
            agents=agents,
            llm_factory=_mock_llm_factory("Team done"),
            max_rounds=1,
        )
        assert isinstance(tool, TeamTool)
        assert tool.name == "team_squad"
