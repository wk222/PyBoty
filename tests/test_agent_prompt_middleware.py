from __future__ import annotations

from pathlib import Path

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

from core.agent_capability_profile import AgentCapabilityProfile
from core.agent_control import AgentControlPolicy
from core.agent_middleware_factory import (
    build_root_langchain_middleware,
    build_subagent_langchain_middleware,
    build_subagent_runtime_prompt_sections,
)
from core.agent_middleware_profile import AgentMiddlewareProfile
from core.agent_prompt_middleware import PromptSectionMiddleware
from core.subagent_governance import build_subagent_governance_snapshot
from core.subagent_sandbox import SubagentSandbox


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

    from core.lc_bus_middleware import LCBusMiddleware
    from core.lc_memory_middleware import LCMemoryMiddleware
    from core.patch_tool_calls import PatchToolCallsMiddleware
    from core.summarization_middleware import SummarizationMiddleware
    from core.todo_middleware import TodoListMiddleware

    from core.loop_guard_middleware import LoopGuardMiddleware

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
    from core.patch_tool_calls import PatchToolCallsMiddleware

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
