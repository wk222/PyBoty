from __future__ import annotations

from pathlib import Path

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

from core.systems.middleware.agent_middleware_factory import (
    build_root_langchain_middleware,
    build_subagent_langchain_middleware,
    build_subagent_runtime_prompt_sections,
)
from core.systems.middleware.agent_prompt_middleware import PromptSectionMiddleware
from core.assets.agents import AgentCapabilityProfile, AgentMiddlewareProfile
from core.systems.bus.capability_tree import build_capability_tree_resume_projection
from core.systems.runtime.prompts import build_static_system_prompt, get_root_mode_label, normalize_root_mode
from core.assets.agents.subagent_governance import build_subagent_governance_snapshot
from core.systems.governance.subagent_sandbox import SubagentSandbox
from core.systems.governance import AgentControlPolicy


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


def test_prompt_section_middleware_appends_to_existing_system_message():
    middleware = PromptSectionMiddleware(name="TestPromptSection", prompt_builder=lambda: "最新记忆")
    captured: dict[str, ModelRequest] = {}

    def handler(request: ModelRequest) -> ModelResponse:
        captured["request"] = request
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_make_request(SystemMessage(content="基础提示")), handler)

    assert captured["request"].system_message is not None
    assert captured["request"].system_message.text == "基础提示\n\n最新记忆"


def test_prompt_section_middleware_skips_empty_sections():
    middleware = PromptSectionMiddleware(name="TestPromptSection", prompt_builder=lambda: "   ")
    original_request = _make_request(SystemMessage(content="基础提示"))
    captured: dict[str, ModelRequest] = {}

    def handler(request: ModelRequest) -> ModelResponse:
        captured["request"] = request
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(original_request, handler)

    assert captured["request"].system_message is not None
    assert captured["request"].system_message.text == "基础提示"


def test_static_root_prompt_describes_persistent_admin_identity():
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


def test_static_root_prompt_keeps_assistant_identity_by_default():
    prompt = build_static_system_prompt()

    assert "PyBot 的通用协作助手" in prompt
    assert "通用协作助手" in prompt
    assert "APP 编排运行时: 禁用" in prompt
    assert "你不是一次性聊天助手" not in prompt
    assert "不要默认把自己当成 应用矩阵或长期自治执行体" in prompt


def test_static_root_prompt_supports_app_matrix_identity():
    prompt = build_static_system_prompt(root_mode="app_matrix")

    assert "PyBot 的 应用矩阵" in prompt
    assert "应用矩阵" in prompt
    assert "中央调度智能体" in prompt
    assert "中央调度脑" in prompt
    assert "APP 拓扑规划: 启用" in prompt
    assert "你主要负责应用级协作编排" in prompt


def test_root_mode_aliases_and_labels_support_admin_mode():
    assert normalize_root_mode("admin") == "admin"
    assert normalize_root_mode("全局管理员模式") == "admin"
    assert normalize_root_mode("app_matrix") == "app_matrix"
    assert normalize_root_mode("应用矩阵模式") == "app_matrix"
    assert get_root_mode_label("admin") == "全局管理员智能体"
    assert get_root_mode_label("app_matrix") == "应用矩阵智能体"
    assert get_root_mode_label("assistant") == "人类助手模式"


def test_root_middleware_factory_injects_runtime_context():
    class DummyWorkspace:
        def build_system_context(self) -> str:
            return "WORKSPACE"

    class DummyMemory:
        def get_context_prompt(self) -> str:
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


def test_root_middleware_factory_merges_todos_into_session_runtime_view():
    class DummyWorkspace:
        def build_system_context(self) -> str:
            return "WORKSPACE"

    class DummyMemory:
        def get_context_prompt(self) -> str:
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


def test_root_middleware_factory_includes_recent_actions_from_runtime_middleware():
    class DummyWorkspace:
        def build_system_context(self) -> str:
            return "WORKSPACE"

    class DummyMemory:
        def get_context_prompt(self) -> str:
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


def test_root_middleware_factory_includes_capability_tree_from_artifacts():
    class DummyWorkspace:
        def build_system_context(self) -> str:
            return "WORKSPACE"

    class DummyMemory:
        def get_context_prompt(self) -> str:
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


def test_subagent_runtime_prompt_sections_describe_boundaries():
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


def test_subagent_middleware_factory_respects_profile_sections():
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


def test_subagent_governance_snapshot_surfaces_inheritance():
    snapshot = build_subagent_governance_snapshot(
        base_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        capability_profile=AgentCapabilityProfile.from_value("builder"),
        middleware_profile=AgentMiddlewareProfile.from_value("builder"),
    )

    assert snapshot["root_policy"]["mode"] == "balanced"
    assert snapshot["effective_policy"]["allow_tool_mutation"] is True
    assert "tool_control" in snapshot["middleware_stack"]
    assert snapshot["inheritance"]["delegation_continues_with_effective_policy"] is True


def test_static_root_prompt_includes_tree_routing_guide():
    prompt = build_static_system_prompt()

    assert "Tree Routing" in prompt
    assert "Start with the trunk and the Single-Agent Runtime first." in prompt
    assert "prefer `build_app_iteratively`" in prompt
    assert "Use Skills for strategy and SOP guidance" in prompt


def test_capability_tree_resume_projection_includes_branch_route_hints():
    projection = build_capability_tree_resume_projection(
        {
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


def test_workflow_and_delegation_tool_descriptions_include_routing_guidance():
    from core.assets.agents.agent_creator import DelegateToAgentTool
    from core.assets.workflows.workflow_tools import RunWorkflowTool

    run_workflow = RunWorkflowTool(engine=object())
    delegate = DelegateToAgentTool()

    assert "Single-Agent + trunk" in run_workflow.description
    assert "workflow collaboration" in run_workflow.description
    assert "Single-Agent + trunk" in delegate.description
    assert "app runtime / workflow runtime" in delegate.description
