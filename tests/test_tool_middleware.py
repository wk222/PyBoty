from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent as create_langchain_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from core.agent_control import AgentControlPolicy
from core.approval_queue import ApprovalQueue
from core.tool_approval_runtime import (
    build_tool_approval_resume_command,
    create_tool_approval_request,
    extract_tool_approval_interrupts,
)
from core.tool_middleware import DynamicToolMiddleware


class ToolAwareFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any):
        return self


@tool("create_agent")
def create_agent_tool(agent_name: str) -> str:
    """Create an agent."""
    return f"created:{agent_name}"


def _build_graph(*, queue: ApprovalQueue, responses: list[AIMessage]):
    middleware = DynamicToolMiddleware(
        control_policy=AgentControlPolicy.from_config({"mode": "balanced"}),
        approval_queue=queue,
        approval_scope="root:test",
    )
    graph = create_langchain_agent(
        model=ToolAwareFakeModel(responses=responses),
        tools=[create_agent_tool],
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
                        "args": {"agent_name": "helper"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="已取消创建"),
        ],
    )
    config = {"configurable": {"thread_id": "thread-1"}}

    response = graph.invoke({"messages": [{"role": "user", "content": "创建一个 helper"}]}, config=config)
    request = _register_interrupt(queue, graph, response, config)
    resolved = queue.resolve(request.approval_id, approved=False, note="暂不允许")

    assert resolved["success"] is True
    result = resolved["result"]
    assert result["messages"][-1].content == "暂不允许"
    tool_messages = [message for message in result["messages"] if getattr(message, "type", "") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].status == "error"
    assert tool_messages[0].content == "暂不允许"


def test_delegated_approval_pauses_parent_until_subagent_resolution():
    queue = ApprovalQueue()
    delegated_request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="subagent approval",
        prompt="allow helper?",
        callback=lambda approved, note: {
            "status": "completed",
            "success": approved,
            "response": "helper done" if approved else note or "rejected",
            "agent_name": "helper",
            "state_update": {"next_step": "summarize"},
        },
    )

    @tool("delegate_to_agent")
    def delegate_to_agent_tool(agent_name: str, task: str) -> str:
        """Delegate to a persisted agent."""
        return json.dumps(
            {
                "status": "waiting_approval",
                "success": False,
                "approval_id": delegated_request.approval_id,
                "response": "helper paused",
                "agent_name": agent_name,
                "task": task,
            },
            ensure_ascii=False,
        )

    middleware = DynamicToolMiddleware(
        control_policy=AgentControlPolicy.from_config({"mode": "open"}),
        approval_queue=queue,
        approval_scope="root:test",
    )
    graph = create_langchain_agent(
        model=ToolAwareFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_agent",
                            "args": {"agent_name": "helper", "task": "solve"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="委派完成"),
            ]
        ),
        tools=[delegate_to_agent_tool],
        middleware=[middleware],
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "thread-1"}}

    interrupted = graph.invoke({"messages": [{"role": "user", "content": "委派 helper"}]}, config=config)
    assert "__interrupt__" in interrupted

    resolved = queue.resolve(delegated_request.approval_id, approved=True, note="ok")
    assert resolved["success"] is True

    resumed = graph.invoke(Command(resume={"approval_id": delegated_request.approval_id}), config=config)
    assert resumed["messages"][-1].content == "委派完成"
    tool_messages = [message for message in resumed["messages"] if getattr(message, "type", "") == "tool"]
    assert len(tool_messages) == 1
    delegated_payload = json.loads(tool_messages[0].content)
    assert delegated_payload["response"] == "helper done"
    assert delegated_payload["state_update"] == {"next_step": "summarize"}
