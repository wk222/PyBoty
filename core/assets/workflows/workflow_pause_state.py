"""Helpers for workflow delegated-approval pause state."""

from __future__ import annotations

from typing import Any


def extract_waiting_approval_ids(payload: dict[str, Any] | None) -> list[str]:
    """Return all approval ids carried by a waiting workflow payload."""
    if not isinstance(payload, dict):
        return []

    approval_ids: list[str] = []
    raw_ids = payload.get("approval_ids", [])
    if isinstance(raw_ids, list):
        for item in raw_ids:
            value = str(item).strip()
            if value and value not in approval_ids:
                approval_ids.append(value)

    raw_pending = payload.get("pending_approvals", [])
    if isinstance(raw_pending, list):
        for item in raw_pending:
            if not isinstance(item, dict):
                continue
            value = str(item.get("approval_id", "")).strip()
            if value and value not in approval_ids:
                approval_ids.append(value)

    primary = str(payload.get("approval_id", "")).strip()
    if primary and primary not in approval_ids:
        approval_ids.insert(0, primary)

    return approval_ids


def primary_waiting_approval_id(payload: dict[str, Any] | None) -> str:
    """Return the primary approval id for a delegated pause payload."""
    if not isinstance(payload, dict):
        return ""
    primary = str(payload.get("approval_id", "")).strip()
    if primary:
        return primary
    approval_ids = extract_waiting_approval_ids(payload)
    return approval_ids[0] if approval_ids else ""


def normalize_pending_approvals(value: Any) -> list[dict[str, Any]]:
    """Return normalized pending-approval entries from a pause-state payload."""
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        approval_id = str(item.get("approval_id", "")).strip()
        if not approval_id or approval_id in seen:
            continue
        pending = dict(item)
        pending["approval_id"] = approval_id
        agent_name = str(pending.get("agent_name", "")).strip()
        if agent_name:
            pending["agent_name"] = agent_name
        pending.setdefault("task", "")
        pending.setdefault("context", "")
        normalized.append(pending)
        seen.add(approval_id)
    return normalized


def apply_waiting_approvals(
    payload: dict[str, Any],
    pending_approvals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach normalized pending approvals to a waiting payload."""
    updated = dict(payload)
    normalized = normalize_pending_approvals(pending_approvals)
    if not normalized:
        updated.pop("approval_ids", None)
        updated.pop("pending_approvals", None)
        return updated

    approval_ids = [item["approval_id"] for item in normalized]
    updated["approval_id"] = approval_ids[0]
    updated["approval_ids"] = approval_ids
    updated["pending_approvals"] = normalized
    return updated
