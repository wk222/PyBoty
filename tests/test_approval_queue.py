from __future__ import annotations

from core.systems.governance import ApprovalQueue


def test_approval_queue_persists_resolved_history(tmp_path):
    storage_path = tmp_path / "approvals.json"
    queue = ApprovalQueue(storage_path=storage_path)
    request = queue.create_request(
        kind="tool_call",
        scope="root:test",
        summary="dangerous action",
        prompt="allow?",
        metadata={"thread_id": "thread-1"},
    )

    resolved = queue.resolve(
        request.approval_id,
        approved=True,
        note="approved for rollout",
        resolved_by="ops",
    )

    assert resolved["success"] is True
    assert storage_path.exists()

    reloaded = ApprovalQueue(storage_path=storage_path)
    history = reloaded.list_history()

    assert history[0]["approval_id"] == request.approval_id
    assert history[0]["resolved_by"] == "ops"
    assert history[0]["resolution_note"] == "approved for rollout"


def test_approval_queue_persists_metadata_updates(tmp_path):
    storage_path = tmp_path / "approvals.json"
    queue = ApprovalQueue(storage_path=storage_path)
    request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="delegated approval",
        prompt="allow?",
    )

    queue.update_request_metadata(
        request.approval_id,
        parent_thread_id="session-1",
        parent_target="root_agent",
    )

    reloaded = ApprovalQueue(storage_path=storage_path)
    restored = reloaded.get_request(request.approval_id)

    assert restored is not None
    assert restored.metadata["parent_thread_id"] == "session-1"
    assert restored.metadata["parent_target"] == "root_agent"


def test_approval_queue_reports_when_no_live_callback_is_available():
    queue = ApprovalQueue()
    request = queue.create_request(
        kind="tool_call",
        scope="root:test",
        summary="offline approval",
        prompt="allow?",
    )

    resolved = queue.resolve(request.approval_id, approved=False, note="rejected", resolved_by="reviewer")

    assert resolved["success"] is True
    assert resolved["approval"]["resolved_by"] == "reviewer"
    assert resolved["result"]["status"] == "recorded"


def test_approval_queue_tracks_labels_policy_tags_and_resolution_labels():
    queue = ApprovalQueue()
    first = queue.create_request(
        kind="tool_call",
        scope="root:test",
        summary="dangerous action",
        prompt="allow?",
        labels=["tool-call", "root"],
        policy_tags=["risk:high", "delegation"],
    )
    queue.create_request(
        kind="workflow_node",
        scope="workflow:test",
        summary="workflow gate",
        prompt="continue?",
        labels=["workflow-node"],
        policy_tags=["workflow-approval"],
    )

    snapshot = queue.get_snapshot()
    resolved = queue.resolve(
        first.approval_id,
        approved=True,
        note="approved",
        resolved_by="ops",
        resolution_labels=["expedite"],
    )

    assert snapshot["pending_labels"]["tool-call"] == 1
    assert snapshot["pending_policy_tags"]["risk:high"] == 1
    assert resolved["approval"]["labels"] == ["tool-call", "root"]
    assert resolved["approval"]["policy_tags"] == ["risk:high", "delegation"]
    assert resolved["approval"]["resolution_labels"] == ["expedite"]
