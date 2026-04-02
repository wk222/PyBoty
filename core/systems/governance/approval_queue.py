"""Reusable approval / interrupt queue shared by workflows and agent tool calls.

Supports typed interrupts (inspired by Coze's interrupt system) while
remaining backward-compatible with the existing approval-only flow.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

ApprovalCallback = Callable[[bool, str], Any]


class InterruptKind(str, Enum):
    """Typed interrupt categories extending the basic approval model.

    The ``kind`` field on ``ApprovalRequest`` can be any string for backward
    compatibility, but using these constants enables richer UI and routing.
    """

    TOOL_APPROVAL = "tool_approval"
    USER_QUESTION = "user_question"
    MISSING_PARAMS = "missing_params"
    OAUTH_REQUIRED = "oauth_required"
    WORKFLOW_INPUT = "workflow_input"
    WORKFLOW_CONFIRM = "workflow_confirm"
    SAFETY_REVIEW = "safety_review"
    CUSTOM = "custom"

    @classmethod
    def from_str(cls, value: str) -> InterruptKind:
        try:
            return cls(value)
        except ValueError:
            return cls.CUSTOM


@dataclass
class ResumePayload:
    """Structured data returned when an interrupt is resolved."""

    approved: bool = True
    user_input: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    oauth_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"approved": self.approved}
        if self.user_input:
            d["user_input"] = self.user_input
        if self.params:
            d["params"] = self.params
        if self.oauth_token is not None:
            d["oauth_token"] = self.oauth_token
        return d


@dataclass
class ApprovalRequest:
    approval_id: str
    kind: str
    scope: str
    summary: str
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)
    fingerprint: str | None = None
    status: str = "pending"
    approved: bool | None = None
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    consumed_at: float | None = None
    resolved_by: str = ""
    resolution_note: str = ""
    labels: tuple[str, ...] = ()
    policy_tags: tuple[str, ...] = ()
    resolution_labels: tuple[str, ...] = ()
    resolution_result: Any = None
    resume_payload: ResumePayload | None = None

    @property
    def interrupt_kind(self) -> InterruptKind:
        return InterruptKind.from_str(self.kind)

    @property
    def requires_user_input(self) -> bool:
        return self.interrupt_kind in (
            InterruptKind.USER_QUESTION,
            InterruptKind.MISSING_PARAMS,
            InterruptKind.WORKFLOW_INPUT,
        )

    @property
    def requires_external_action(self) -> bool:
        return self.interrupt_kind == InterruptKind.OAUTH_REQUIRED

    def to_dict(self) -> dict[str, Any]:
        d = {
            "approval_id": self.approval_id,
            "kind": self.kind,
            "interrupt_kind": self.interrupt_kind.value,
            "scope": self.scope,
            "summary": self.summary,
            "prompt": self.prompt,
            "metadata": self.metadata,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "approved": self.approved,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "consumed_at": self.consumed_at,
            "resolved_by": self.resolved_by,
            "resolution_note": self.resolution_note,
            "labels": list(self.labels),
            "policy_tags": list(self.policy_tags),
            "resolution_labels": list(self.resolution_labels),
            "resolution_result": self.resolution_result,
            "requires_user_input": self.requires_user_input,
            "requires_external_action": self.requires_external_action,
        }
        if self.resume_payload is not None:
            d["resume_payload"] = self.resume_payload.to_dict()
        return d

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ApprovalRequest:
        resume_raw = payload.get("resume_payload")
        resume = None
        if isinstance(resume_raw, dict):
            resume = ResumePayload(
                approved=resume_raw.get("approved", True),
                user_input=resume_raw.get("user_input", ""),
                params=resume_raw.get("params", {}),
                oauth_token=resume_raw.get("oauth_token"),
            )
        return cls(
            approval_id=str(payload.get("approval_id", "")),
            kind=str(payload.get("kind", "")),
            scope=str(payload.get("scope", "")),
            summary=str(payload.get("summary", "")),
            prompt=str(payload.get("prompt", "")),
            metadata=dict(payload.get("metadata", {})),
            fingerprint=payload.get("fingerprint"),
            status=str(payload.get("status", "pending")),
            approved=payload.get("approved"),
            created_at=float(payload.get("created_at", time.time())),
            resolved_at=float(payload["resolved_at"]) if payload.get("resolved_at") is not None else None,
            consumed_at=float(payload["consumed_at"]) if payload.get("consumed_at") is not None else None,
            resolved_by=str(payload.get("resolved_by", "")),
            resolution_note=str(payload.get("resolution_note", "")),
            labels=_normalize_names(payload.get("labels")),
            policy_tags=_normalize_names(payload.get("policy_tags")),
            resolution_labels=_normalize_names(payload.get("resolution_labels")),
            resolution_result=payload.get("resolution_result"),
            resume_payload=resume,
        )


class ApprovalQueue:
    """Thread-safe approval storage with optional resolve callbacks."""

    def __init__(self, storage_path: str | Path | None = None):
        self._lock = threading.RLock()
        self._storage_path = Path(storage_path).resolve() if storage_path is not None else None
        self._requests: dict[str, ApprovalRequest] = {}
        self._callbacks: dict[str, ApprovalCallback] = {}
        self._load_unlocked()

    def create_request(
        self,
        *,
        kind: str,
        scope: str,
        summary: str,
        prompt: str,
        metadata: dict[str, Any] | None = None,
        fingerprint: str | None = None,
        callback: ApprovalCallback | None = None,
        dedupe_pending: bool = True,
        labels: list[str] | tuple[str, ...] | None = None,
        policy_tags: list[str] | tuple[str, ...] | None = None,
    ) -> ApprovalRequest:
        with self._lock:
            if dedupe_pending and fingerprint:
                existing = self._find_request(kind=kind, scope=scope, fingerprint=fingerprint, status="pending")
                if existing is not None:
                    if callback is not None:
                        self._callbacks.setdefault(existing.approval_id, callback)
                    return existing

            approval = ApprovalRequest(
                approval_id=f"appr_{uuid.uuid4().hex[:12]}",
                kind=kind,
                scope=scope,
                summary=summary,
                prompt=prompt,
                metadata=dict(metadata or {}),
                fingerprint=fingerprint,
                labels=_normalize_names(labels),
                policy_tags=_normalize_names(policy_tags),
            )
            self._requests[approval.approval_id] = approval
            if callback is not None:
                self._callbacks[approval.approval_id] = callback
            self._persist_unlocked()
            return approval

    def get_request(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            return self._requests.get(approval_id)

    def update_request_metadata(self, approval_id: str, **updates: Any) -> ApprovalRequest | None:
        with self._lock:
            request = self._requests.get(approval_id)
            if request is None:
                return None
            request.metadata.update(updates)
            self._persist_unlocked()
            return request

    def set_resolution_result(self, approval_id: str, result: Any) -> ApprovalRequest | None:
        with self._lock:
            request = self._requests.get(approval_id)
            if request is None:
                return None
            request.resolution_result = result
            self._persist_unlocked()
            return request

    def list_pending(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            pending = [
                request.to_dict()
                for request in self._requests.values()
                if request.status == "pending" and (kind is None or request.kind == kind)
            ]
        return sorted(pending, key=lambda item: item["created_at"], reverse=True)

    def list_recent(self, *, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            requests = [
                request.to_dict() for request in self._requests.values() if kind is None or request.kind == kind
            ]
        return sorted(requests, key=self._request_sort_key, reverse=True)[:limit]

    def list_history(self, *, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            requests = [
                request.to_dict()
                for request in self._requests.values()
                if request.status != "pending" and (kind is None or request.kind == kind)
            ]
        return sorted(requests, key=self._request_sort_key, reverse=True)[:limit]

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
        note: str = "",
        resolved_by: str = "",
        resolution_labels: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            request = self._requests.get(approval_id)
            if request is None:
                return {"success": False, "error": f"审批请求 '{approval_id}' 不存在"}
            if request.status != "pending":
                return {"success": False, "error": f"审批请求 '{approval_id}' 已处理"}

            request.status = "approved" if approved else "rejected"
            request.approved = approved
            request.resolved_at = time.time()
            request.resolved_by = resolved_by.strip()
            request.resolution_note = note
            request.resolution_labels = _normalize_names(resolution_labels)
            callback = self._callbacks.pop(approval_id, None)

        callback_result = None
        if callback is not None:
            try:
                callback_result = callback(approved, note)
            except Exception as exc:
                callback_result = {"success": False, "error": str(exc)}
        else:
            callback_result = {
                "status": "recorded",
                "warning": "审批结果已记录，但没有活动运行时可继续执行。",
            }

        with self._lock:
            request.resolution_result = callback_result
            self._persist_unlocked()
            return {
                "success": True,
                "approval": request.to_dict(),
                "result": callback_result,
            }

    def finalize_request(
        self,
        *,
        kind: str,
        scope: str,
        fingerprint: str,
        approved: bool,
        note: str = "",
        resolved_by: str = "",
        resolution_labels: list[str] | tuple[str, ...] | None = None,
        result: Any = None,
    ) -> ApprovalRequest | None:
        with self._lock:
            request = self._find_request(kind=kind, scope=scope, fingerprint=fingerprint, status="pending")
            if request is None:
                return None
            self._callbacks.pop(request.approval_id, None)
            request.status = "approved" if approved else "rejected"
            request.approved = approved
            request.resolved_at = time.time()
            request.resolved_by = resolved_by.strip()
            request.resolution_note = note
            request.resolution_labels = _normalize_names(resolution_labels)
            request.resolution_result = result
            self._persist_unlocked()
            return request

    def find_resolution(
        self,
        *,
        kind: str,
        scope: str,
        fingerprint: str,
    ) -> ApprovalRequest | None:
        with self._lock:
            return self._find_request(kind=kind, scope=scope, fingerprint=fingerprint, status=None)

    def consume_approval(
        self,
        *,
        kind: str,
        scope: str,
        fingerprint: str,
    ) -> ApprovalRequest | None:
        with self._lock:
            request = self._find_request(kind=kind, scope=scope, fingerprint=fingerprint, status="approved")
            if request is None or request.consumed_at is not None:
                return None
            request.consumed_at = time.time()
            self._persist_unlocked()
            return request

    def consume_request(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            request = self._requests.get(approval_id)
            if request is None or request.consumed_at is not None:
                return None
            request.consumed_at = time.time()
            self._persist_unlocked()
            return request

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests = list(self._requests.values())
            pending = sum(1 for request in requests if request.status == "pending")
            approved = sum(1 for request in requests if request.status == "approved")
            rejected = sum(1 for request in requests if request.status == "rejected")
        return {
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "pending_labels": _count_name_occurrences(
                request.labels for request in requests if request.status == "pending"
            ),
            "pending_policy_tags": _count_name_occurrences(
                request.policy_tags for request in requests if request.status == "pending"
            ),
            "recent": self.list_history(limit=10),
        }

    def _find_request(
        self,
        *,
        kind: str,
        scope: str,
        fingerprint: str,
        status: str | None,
    ) -> ApprovalRequest | None:
        for request in reversed(list(self._requests.values())):
            if request.kind != kind or request.scope != scope or request.fingerprint != fingerprint:
                continue
            if status is not None and request.status != status:
                continue
            return request
        return None

    def _load_unlocked(self) -> None:
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except Exception:
            return

        stored_requests = payload.get("requests", []) if isinstance(payload, dict) else []
        if not isinstance(stored_requests, list):
            return

        for item in stored_requests:
            if not isinstance(item, dict):
                continue
            request = ApprovalRequest.from_dict(item)
            if request.approval_id:
                self._requests[request.approval_id] = request

    def _persist_unlocked(self) -> None:
        if self._storage_path is None:
            return

        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "requests": [request.to_dict() for request in self._requests.values()],
            "saved_at": time.time(),
        }
        tmp_path = self._storage_path.with_name(f"{self._storage_path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._storage_path)

    @staticmethod
    def _request_sort_key(item: dict[str, Any]) -> float:
        resolved_at = item.get("resolved_at")
        created_at = item.get("created_at")
        if isinstance(resolved_at, (int, float)):
            return float(resolved_at)
        if isinstance(created_at, (int, float)):
            return float(created_at)
        return 0.0


def _normalize_names(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        item = value.strip()
        return (item,) if item else ()
    normalized: list[str] = []
    for item in value:
        item_str = str(item).strip()
        if item_str and item_str not in normalized:
            normalized.append(item_str)
    return tuple(normalized)


def _count_name_occurrences(groups: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for group in groups:
        for item in group:
            counts[item] = counts.get(item, 0) + 1
    return dict(sorted(counts.items()))
