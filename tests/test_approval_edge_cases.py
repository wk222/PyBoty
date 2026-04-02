"""Approval queue reject/retry/resume edge cases."""

from __future__ import annotations

from core.systems.governance.approval_queue import ApprovalQueue


def test_approval_queue_double_resolve_returns_error():
    queue = ApprovalQueue()
    req = queue.create_request(kind="tool", scope="root", summary="s", prompt="p")
    result1 = queue.resolve(req.approval_id, approved=True)
    assert result1["success"] is True

    result2 = queue.resolve(req.approval_id, approved=False)
    assert result2["success"] is False
    assert "已处理" in result2["error"]


def test_approval_queue_resolve_missing_id_returns_error():
    queue = ApprovalQueue()
    result = queue.resolve("nonexistent_id", approved=True)
    assert result["success"] is False
    assert "不存在" in result["error"]


def test_approval_queue_callback_exception_stores_error():
    def bad_callback(approved, note):
        raise RuntimeError("callback boom")

    queue = ApprovalQueue()
    req = queue.create_request(
        kind="tool",
        scope="root",
        summary="s",
        prompt="p",
        callback=bad_callback,
    )
    result = queue.resolve(req.approval_id, approved=True)
    assert result["success"] is True
    assert result["result"]["success"] is False
    assert "callback boom" in result["result"]["error"]

    stored = queue.get_request(req.approval_id)
    assert stored is not None
    assert stored.status == "approved"
    assert stored.resolution_result["success"] is False


def test_approval_queue_reject_sets_status_and_note():
    queue = ApprovalQueue()
    req = queue.create_request(kind="tool", scope="root", summary="s", prompt="p")
    result = queue.resolve(
        req.approval_id,
        approved=False,
        note="too risky",
        resolved_by="admin",
        resolution_labels=["security"],
    )
    assert result["success"] is True
    stored = queue.get_request(req.approval_id)
    assert stored is not None
    assert stored.status == "rejected"
    assert stored.approved is False
    assert stored.resolution_note == "too risky"
    assert stored.resolved_by == "admin"
    assert "security" in stored.resolution_labels


def test_approval_queue_consume_returns_none_on_second_call():
    queue = ApprovalQueue()
    req = queue.create_request(kind="tool", scope="root", summary="s", prompt="p")
    queue.resolve(req.approval_id, approved=True)
    consumed = queue.consume_approval(
        kind="tool",
        scope="root",
        fingerprint=req.fingerprint or "",
    )
    assert consumed is None


def test_approval_queue_consume_with_matching_fingerprint():
    queue = ApprovalQueue()
    req = queue.create_request(
        kind="tool",
        scope="root",
        summary="s",
        prompt="p",
        fingerprint="fp123",
    )
    queue.resolve(req.approval_id, approved=True)
    consumed = queue.consume_approval(kind="tool", scope="root", fingerprint="fp123")
    assert consumed is not None
    assert consumed.approved is True
    assert consumed.consumed_at is not None

    second = queue.consume_approval(kind="tool", scope="root", fingerprint="fp123")
    assert second is None


def test_approval_queue_dedupe_returns_existing_pending():
    queue = ApprovalQueue()
    req1 = queue.create_request(
        kind="tool",
        scope="root",
        summary="s",
        prompt="p",
        fingerprint="fp_dup",
        dedupe_pending=True,
    )
    req2 = queue.create_request(
        kind="tool",
        scope="root",
        summary="s2",
        prompt="p2",
        fingerprint="fp_dup",
        dedupe_pending=True,
    )
    assert req1.approval_id == req2.approval_id


def test_approval_queue_list_pending_filters_by_kind():
    queue = ApprovalQueue()
    queue.create_request(kind="tool", scope="root", summary="s1", prompt="p")
    queue.create_request(kind="workflow", scope="root", summary="s2", prompt="p")

    tool_pending = queue.list_pending(kind="tool")
    assert len(tool_pending) == 1
    assert tool_pending[0]["kind"] == "tool"

    all_pending = queue.list_pending()
    assert len(all_pending) == 2


def test_approval_queue_set_resolution_result():
    queue = ApprovalQueue()
    req = queue.create_request(kind="tool", scope="root", summary="s", prompt="p")
    queue.set_resolution_result(req.approval_id, {"custom": "data"})

    stored = queue.get_request(req.approval_id)
    assert stored is not None
    assert stored.resolution_result == {"custom": "data"}


def test_approval_queue_set_resolution_result_missing_id():
    queue = ApprovalQueue()
    result = queue.set_resolution_result("missing", {"x": 1})
    assert result is None
