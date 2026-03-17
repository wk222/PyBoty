from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from core import tool_delegation_runtime
from core.approval_queue import ApprovalQueue
from core.tool_delegation_runtime import DelegatedToolApprovalRuntime


def test_delegated_tool_approval_runtime_returns_resolved_payload(monkeypatch):
    queue = ApprovalQueue()
    request = queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="helper approval",
        prompt="allow helper?",
        callback=lambda approved, note: {
            "status": "completed",
            "success": approved,
            "response": "helper done" if approved else note or "rejected",
            "state_update": {"next_step": "report"},
        },
    )
    queue.resolve(request.approval_id, approved=True, note="ok")

    runtime = DelegatedToolApprovalRuntime(
        approval_queue=queue,
        approval_scope="root:test",
    )
    monkeypatch.setattr(tool_delegation_runtime, "interrupt", lambda payload: {"approval_id": payload["approval_id"]})

    result = runtime.handle_tool_result(
        tool_name="delegate_to_agent",
        tool_call_id="call_1",
        result=ToolMessage(
            content=json.dumps(
                {
                    "status": "waiting_approval",
                    "approval_id": request.approval_id,
                    "success": False,
                },
                ensure_ascii=False,
            ),
            tool_call_id="call_1",
            status="success",
        ),
    )

    assert result is not None
    payload = json.loads(result.content)
    assert payload["response"] == "helper done"
    assert payload["state_update"] == {"next_step": "report"}
    assert result.status == "success"
