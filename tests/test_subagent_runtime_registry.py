from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import AIMessage

from core.systems.governance.agent_control import AgentControlPolicy
from core.assets.agents.agent_storage import AgentDefinition
from core.systems.governance.approval_queue import ApprovalQueue
from core.assets.agents.subagent_registry import SubagentRegistry
from core.assets.agents.subagent_runtime import SubAgentRuntime
from core.systems.governance.subagent_sandbox import SubagentSandbox


@dataclass
class _CheckpointBundle:
    checkpointer: object | None = None
    backend: str = "sqlite"
    path: object | None = None

    def close(self) -> None:
        return None


class _Graph:
    def __init__(self, result):
        self._result = result

    def invoke(self, *_args, **_kwargs):
        return self._result


def _sandbox() -> SubagentSandbox:
    return SubagentSandbox(
        mode="restricted",
        visibility="isolated",
        workspace_dir=".",
        allows_writes=True,
        allows_code_execution=False,
    )


def _runtime(*, graph_result, registry: SubagentRegistry) -> SubAgentRuntime:
    return SubAgentRuntime(
        graph=_Graph(graph_result),
        definition=AgentDefinition(
            name="helper",
            role="helper",
            description="General helper",
            system_prompt="Help.",
        ),
        tool_names=["search_notes"],
        control_policy=AgentControlPolicy(),
        sandbox=_sandbox(),
        checkpoint_bundle=_CheckpointBundle(),
        approval_queue=ApprovalQueue(),
        registry=registry,
    )


def test_subagent_runtime_marks_completed_runs_in_registry():
    registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=60)
    runtime = _runtime(graph_result={"messages": [AIMessage(content="done")]}, registry=registry)

    result = runtime.invoke(
        task="help",
        thread_id="thread-1",
        parent_agent_name="root",
        parent_depth=0,
    )

    assert result["status"] == "completed"
    assert registry.get_active(agent_name="helper", thread_id="thread-1") is None
    assert registry.get_latest(agent_name="helper", thread_id="thread-1").status == "completed"


def test_subagent_runtime_waiting_approval_can_be_aborted_without_resume(monkeypatch):
    registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=60)
    runtime = _runtime(graph_result={"messages": []}, registry=registry)

    class _PendingApproval:
        approval_id = "approval-1"

    monkeypatch.setattr(runtime, "_register_tool_approval", lambda *_args, **_kwargs: _PendingApproval())

    result = runtime.invoke(
        task="help",
        thread_id="thread-1",
        parent_agent_name="root",
        parent_depth=0,
    )

    assert result["status"] == "waiting_approval"
    assert registry.get_active(agent_name="helper", thread_id="thread-1").status == "waiting_approval"

    aborted = runtime.abort(thread_id="thread-1", reason="operator stop")
    resumed = runtime.resume_approval(
        approval_id="approval-1",
        thread_id="thread-1",
        approved=True,
        note="resume",
    )

    assert aborted["status"] == "aborted"
    assert resumed["status"] == "aborted"
