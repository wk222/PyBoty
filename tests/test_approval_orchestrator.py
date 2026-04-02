from __future__ import annotations

from core.systems.governance import ApprovalOrchestrator, ApprovalQueue


def test_approval_orchestrator_resumes_workflow_after_subagent_resolution():
    queue = ApprovalQueue()
    request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="helper approval",
        prompt="allow helper?",
        metadata={
            "target": "subagent:helper",
            "thread_id": "delegate-helper-thread",
            "workflow_id": "wf_delegate",
            "workflow_resume_token": "resume-123",
            "workflow_pause_kind": "delegated_subagent",
        },
    )

    class DummyEngine:
        def __init__(self):
            self.calls = []

        def resume_workflow(self, workflow_id, resume_token, approved, *, approval_id="", note="", resolved_by=""):
            self.calls.append((workflow_id, resume_token, approved, approval_id, note, resolved_by))
            return {"status": "completed", "workflow_id": workflow_id, "approval_id": approval_id}

    class DummyAgent:
        def __init__(self):
            self.calls = []
            self.pyflow_engine = DummyEngine()

        def resolve_approval(self, approval_id, *, approved, note="", approver=""):
            self.calls.append((approval_id, approved, note, approver))
            return {
                "success": True,
                "approval": {"approval_id": approval_id, "approved": approved},
                "result": {"status": "completed", "response": "subagent done"},
            }

    system_agent = DummyAgent()
    orchestrator = ApprovalOrchestrator(
        approval_queue=queue,
        get_agent_for_thread=lambda thread_id: system_agent,
        get_system_agent=lambda: system_agent,
    )

    result = orchestrator.resolve(
        request.approval_id,
        approved=True,
        note="ok",
        approver="ops",
    )

    assert result["success"] is True
    assert result["subagent_result"]["response"] == "subagent done"
    assert result["result"]["workflow_id"] == "wf_delegate"
    assert system_agent.calls == [(request.approval_id, True, "ok", "ops")]
    assert system_agent.pyflow_engine.calls == [("wf_delegate", "resume-123", True, request.approval_id, "ok", "ops")]
