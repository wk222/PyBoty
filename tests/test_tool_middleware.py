from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent as create_langchain_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from core.systems.governance import AgentControlPolicy, ApprovalQueue
from core.systems.governance.tool_approval_runtime import (
    build_tool_approval_resume_command,
    create_tool_approval_request,
    extract_tool_approval_interrupts,
)
from core.assets.tools.tool_middleware import DynamicToolMiddleware
from core.systems.runtime.hooks_runtime import HookPhase, HooksRuntime
from core.systems.runtime.projected_runtime_view import build_projected_runtime_view
from core.systems.runtime.trusted_settings import build_trusted_settings_bundle


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


def test_high_risk_tool_call_pauses_and_resumes_without_retry():
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


def test_rejected_tool_approval_resumes_with_error_feedback():
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


def test_host_execution_security_chain_revalidates_hash():
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
    
    # 模拟审批通过
    resolved = queue.resolve(request.approval_id, approved=True, note="允许")
    
    assert resolved["success"] is True
    result = resolved["result"]
    tool_messages = [message for message in result["messages"] if getattr(message, "type", "") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "executed:print('hello')"


def test_host_execution_security_chain_blocks_tampered_args():
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

    response = graph.invoke({"messages": [{"role": "user", "content": "运行代码"}]}, config=config)

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


def test_session_rule_ask_prompts_even_for_low_risk_tool():
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


def test_permission_control_plane_mutators_expose_snapshot():
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


def test_permission_hook_receives_canonical_runtime_view():
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


def test_permission_mutation_syncs_trusted_settings_projection():
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
