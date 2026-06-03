"""Helpers for normalizing delegated subagent feedback."""

from __future__ import annotations

import json
from typing import Any


def normalize_delegation_payload(
    payload: dict[str, Any] | str | None,
    *,
    agent_name: str = "",
    task: str = "",
) -> dict[str, Any]:
    """Coerce a delegated result into a consistent orchestration payload."""
    parsed = _parse_payload(payload)

    status = str(parsed.get("status", "completed")).strip() or "completed"
    response = str(parsed.get("response", "")).strip()
    success_value = parsed.get("success")
    success = bool(success_value) if isinstance(success_value, bool) else status == "completed"

    state_update = parsed.get("state_update", {})
    if not isinstance(state_update, dict):
        state_update = {}

    state_keys = parsed.get("state_keys", [])
    if not isinstance(state_keys, list) or not all(isinstance(item, str) for item in state_keys) or not state_keys:
        state_keys = sorted(state_update.keys())

    tool_names = parsed.get("tool_names", [])
    assigned_tools = parsed.get("assigned_tools", [])
    if not isinstance(tool_names, list):
        tool_names = []
    if not isinstance(assigned_tools, list):
        assigned_tools = []

    normalized = {
        **parsed,
        "status": status,
        "success": success,
        "response": response,
        "agent_name": str(parsed.get("agent_name", "")).strip() or agent_name,
        "role": str(parsed.get("role", "")).strip(),
        "task": str(parsed.get("task", "")).strip() or task,
        "approval_id": _normalize_optional_text(parsed.get("approval_id")),
        "thread_id": _normalize_optional_text(parsed.get("thread_id")),
        "state_update": state_update,
        "state_keys": state_keys,
        "has_state_update": bool(state_update),
        "tool_names": [str(item) for item in tool_names if str(item).strip()],
        "assigned_tools": [str(item) for item in assigned_tools if str(item).strip()],
        "tool_inventory": parsed.get("tool_inventory", {}) if isinstance(parsed.get("tool_inventory"), dict) else {},
        "sandbox": parsed.get("sandbox", {}) if isinstance(parsed.get("sandbox"), dict) else {},
    }
    return normalized


def delegation_response_text(payload: dict[str, Any] | str | None) -> str:
    """Return the human-readable response text from a delegated payload."""
    normalized = normalize_delegation_payload(payload)
    return str(normalized.get("response", "")).strip()


def _parse_payload(payload: dict[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {"response": payload, "raw_text": payload}
        return dict(parsed) if isinstance(parsed, dict) else {"response": payload, "raw_text": payload}
    return {}


def _normalize_optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
