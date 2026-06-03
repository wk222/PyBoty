from __future__ import annotations

from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.governance.delegated_approval_runtime import DelegatedApprovalResolutionRuntime


def test_delegated_approval_resolution_runtime_normalizes_pending_and_rejected_payloads():
    queue = ApprovalQueue()
    pending = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="pending approval",
        prompt="allow?",
    )
    rejected = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="rejected approval",
        prompt="allow?",
    )
    queue.resolve(rejected.approval_id, approved=False, note="blocked")

    runtime = DelegatedApprovalResolutionRuntime(approval_queue=queue)

    pending_payload = runtime.resolve_payload(pending.approval_id, agent_name="helper", task="write report")
    rejected_payload = runtime.resolve_payload(rejected.approval_id, agent_name="helper", task="write report")

    assert pending_payload["status"] == "waiting_approval"
    assert pending_payload["agent_name"] == "helper"
    assert pending_payload["task"] == "write report"
    assert rejected_payload["status"] == "rejected"
    assert rejected_payload["response"] == "blocked"


def test_delegated_approval_resolution_runtime_returns_error_for_missing_request():
    runtime = DelegatedApprovalResolutionRuntime(approval_queue=ApprovalQueue())

    payload = runtime.resolve_payload("missing", agent_name="helper")

    assert payload["status"] == "error"
    assert payload["success"] is False
    assert payload["agent_name"] == "helper"
