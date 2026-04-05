"""Helpers for workflow delegated-approval pause state."""

from __future__ import annotations

from typing import Any

from core.systems.governance.execution_protocol import (
    WaitingApprovalPayload,
    normalize_pending_approval_refs,
)


def extract_waiting_approval_ids(payload: dict[str, Any] | None) -> list[str]:
    """Return all approval ids carried by a waiting workflow payload."""
    return list(WaitingApprovalPayload.from_payload(payload).all_approval_ids)


def primary_waiting_approval_id(payload: dict[str, Any] | None) -> str:
    """Return the primary approval id for a delegated pause payload."""
    return WaitingApprovalPayload.from_payload(payload).primary_approval_id


def normalize_pending_approvals(value: Any) -> list[dict[str, Any]]:
    """Return normalized pending-approval entries from a pause-state payload."""
    return [item.to_dict() for item in normalize_pending_approval_refs(value)]


def apply_waiting_approvals(
    payload: dict[str, Any],
    pending_approvals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach normalized pending approvals to a waiting payload."""
    updated = dict(payload)
    normalized = normalize_pending_approval_refs(pending_approvals)
    if not normalized:
        updated.pop("approval_ids", None)
        updated.pop("pending_approvals", None)
        return updated

    approval_ids = [item.approval_id for item in normalized]
    updated["approval_id"] = approval_ids[0]
    updated["approval_ids"] = approval_ids
    updated["pending_approvals"] = [item.to_dict() for item in normalized]
    return updated
