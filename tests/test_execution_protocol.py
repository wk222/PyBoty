from __future__ import annotations

from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.governance.execution_protocol import (
    ApprovalResolutionContext,
    WaitingApprovalPayload,
    WorkflowPauseMetadata,
    attach_workflow_pause_metadata,
    normalize_pending_approval_refs,
)


def test_waiting_approval_payload_normalizes_primary_and_pending_ids():
    payload = WaitingApprovalPayload.from_payload(
        {
            "status": "waiting_approval",
            "approval_id": "appr_primary",
            "approval_ids": ["appr_secondary", "appr_primary"],
            "pending_approvals": [
                {"approval_id": "appr_secondary", "agent_name": "reviewer", "task": "review"},
                {"approval_id": "appr_third", "agent_name": "security", "task": "scan"},
            ],
            "workflow_pause_kind": "delegated_subagent",
            "workflow_pause_mode": "consensus",
        }
    )

    assert payload.primary_approval_id == "appr_primary"
    assert payload.all_approval_ids == ("appr_primary", "appr_secondary", "appr_third")
    normalized = payload.to_payload({"status": "waiting_approval"})
    assert normalized["approval_ids"] == ["appr_primary", "appr_secondary", "appr_third"]
    assert normalized["pending_approvals"][1]["agent_name"] == "security"


def test_workflow_pause_metadata_attaches_resume_context_to_all_pending_requests():
    queue = ApprovalQueue()
    first = queue.create_request(kind="tool_call", scope="subagent:planner", summary="planner", prompt="allow 1?")
    second = queue.create_request(kind="tool_call", scope="subagent:reviewer", summary="reviewer", prompt="allow 2?")
    metadata = WorkflowPauseMetadata.from_waiting_payload(
        workflow_id="wf_consensus",
        workflow_name="consensus",
        node_id="consensus_node",
        node_label="Consensus",
        resume_token="resume-123",
        payload={
            "workflow_pause_kind": "delegated_subagent",
            "workflow_pause_mode": "consensus",
        },
    )

    prompt = attach_workflow_pause_metadata(
        approval_queue=queue,
        approval_ids=[first.approval_id, second.approval_id],
        primary_approval_id=first.approval_id,
        pause_metadata=metadata,
        default_prompt="fallback",
    )

    assert prompt == "allow 1?"
    assert queue.get_request(first.approval_id).metadata["workflow_resume_token"] == "resume-123"
    assert queue.get_request(second.approval_id).metadata["workflow_pause_mode"] == "consensus"


def test_approval_resolution_context_routes_subagent_tool_calls_and_resume_targets():
    queue = ApprovalQueue()
    request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="helper",
        prompt="allow helper?",
        metadata={
            "target": "subagent:helper",
            "thread_id": "delegate-thread",
            "workflow_id": "wf_delegate",
            "workflow_resume_token": "resume-xyz",
            "workflow_pause_kind": "delegated_subagent",
        },
    )
    context = ApprovalResolutionContext.from_request(request)

    assert context.routes_to_system_agent is True
    assert context.workflow_pause.should_resume_delegated_subagent is True


def test_normalize_pending_approval_refs_preserves_extra_payload_fields():
    refs = normalize_pending_approval_refs(
        [
            {
                "approval_id": "appr_1",
                "agent_name": "reviewer",
                "task": "review",
                "payload": {"status": "waiting_approval"},
                "custom": "value",
            }
        ]
    )

    assert refs[0].to_dict()["payload"]["status"] == "waiting_approval"
    assert refs[0].to_dict()["custom"] == "value"
