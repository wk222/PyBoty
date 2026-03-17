from __future__ import annotations

import json

from core.delegation_payload import delegation_response_text, normalize_delegation_payload


def test_normalize_delegation_payload_preserves_structured_state():
    payload = normalize_delegation_payload(
        {
            "status": "completed",
            "success": True,
            "response": "done",
            "state_update": {"next_step": "ship"},
            "tool_names": ["search_notes"],
        },
        agent_name="helper",
        task="finish task",
    )

    assert payload["agent_name"] == "helper"
    assert payload["task"] == "finish task"
    assert payload["state_update"] == {"next_step": "ship"}
    assert payload["state_keys"] == ["next_step"]
    assert payload["has_state_update"] is True


def test_normalize_delegation_payload_parses_json_strings():
    payload = normalize_delegation_payload(
        json.dumps(
            {
                "status": "waiting_approval",
                "success": False,
                "response": "paused",
                "approval_id": "appr_123",
            },
            ensure_ascii=False,
        ),
        agent_name="helper",
    )

    assert payload["status"] == "waiting_approval"
    assert payload["approval_id"] == "appr_123"
    assert delegation_response_text(payload) == "paused"
