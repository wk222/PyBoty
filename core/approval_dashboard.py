"""Approval Governance Dashboard — query, filtering, grouping, and audit trails.

Provides governance-oriented views on top of ``ApprovalQueue``:
- Policy-aware filtering (by agent, policy tag, risk level, age)
- Grouping/reporting views (by kind, scope, status, policy tag)
- Approval-trend statistics over time windows
- Full audit trails showing who approved what and why
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .approval_queue import ApprovalQueue, ApprovalRequest


@dataclass
class AuditEntry:
    """A single row in the audit trail."""

    approval_id: str
    kind: str
    scope: str
    summary: str
    status: str
    resolved_by: str
    resolution_note: str
    created_at: float
    resolved_at: float | None
    latency_seconds: float | None
    labels: tuple[str, ...]
    policy_tags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "kind": self.kind,
            "scope": self.scope,
            "summary": self.summary,
            "status": self.status,
            "resolved_by": self.resolved_by,
            "resolution_note": self.resolution_note,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "latency_seconds": self.latency_seconds,
            "labels": list(self.labels),
            "policy_tags": list(self.policy_tags),
        }


@dataclass
class TrendBucket:
    """Counts for a single time window."""

    window_start: float
    window_end: float
    approved: int = 0
    rejected: int = 0
    pending: int = 0
    avg_latency_seconds: float | None = None


@dataclass
class DashboardFilter:
    """Query filter for dashboard views."""

    kind: str | None = None
    scope: str | None = None
    status: str | None = None
    resolved_by: str | None = None
    label: str | None = None
    policy_tag: str | None = None
    min_age_seconds: float | None = None
    max_age_seconds: float | None = None
    limit: int = 100

    def matches(self, request: ApprovalRequest) -> bool:
        if self.kind and request.kind != self.kind:
            return False
        if self.scope and request.scope != self.scope:
            return False
        if self.status and request.status != self.status:
            return False
        if self.resolved_by and request.resolved_by != self.resolved_by:
            return False
        if self.label and self.label not in request.labels:
            return False
        if self.policy_tag and self.policy_tag not in request.policy_tags:
            return False
        now = time.time()
        age = now - request.created_at
        if self.min_age_seconds is not None and age < self.min_age_seconds:
            return False
        if self.max_age_seconds is not None and age > self.max_age_seconds:
            return False
        return True


class ApprovalDashboard:
    """Governance-oriented query layer on top of ApprovalQueue."""

    def __init__(self, queue: ApprovalQueue):
        self._queue = queue

    def query(self, filt: DashboardFilter | None = None) -> list[dict[str, Any]]:
        """Return filtered approval requests as dicts."""
        filt = filt or DashboardFilter()
        results: list[dict[str, Any]] = []
        with self._queue._lock:
            for request in self._queue._requests.values():
                if filt.matches(request):
                    results.append(request.to_dict())
                    if len(results) >= filt.limit:
                        break
        return sorted(results, key=lambda x: x.get("created_at", 0), reverse=True)

    def group_by(
        self,
        field_name: str,
        filt: DashboardFilter | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Group filtered requests by a field (kind, scope, status, resolved_by)."""
        items = self.query(filt)
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            key = str(item.get(field_name, "unknown"))
            groups[key].append(item)
        return dict(groups)

    def summary_stats(self, filt: DashboardFilter | None = None) -> dict[str, Any]:
        """Compute summary statistics for filtered requests."""
        items = self.query(filt)
        total = len(items)
        by_status: dict[str, int] = defaultdict(int)
        latencies: list[float] = []
        by_kind: dict[str, int] = defaultdict(int)
        by_policy_tag: dict[str, int] = defaultdict(int)

        for item in items:
            by_status[item.get("status", "unknown")] += 1
            by_kind[item.get("kind", "unknown")] += 1
            for tag in item.get("policy_tags", []):
                by_policy_tag[tag] += 1
            created = item.get("created_at")
            resolved = item.get("resolved_at")
            if isinstance(created, (int, float)) and isinstance(resolved, (int, float)):
                latencies.append(resolved - created)

        return {
            "total": total,
            "by_status": dict(by_status),
            "by_kind": dict(by_kind),
            "by_policy_tag": dict(by_policy_tag),
            "avg_latency_seconds": sum(latencies) / len(latencies) if latencies else None,
            "min_latency_seconds": min(latencies) if latencies else None,
            "max_latency_seconds": max(latencies) if latencies else None,
        }

    def audit_trail(
        self,
        filt: DashboardFilter | None = None,
    ) -> list[AuditEntry]:
        """Build a full audit trail of resolved requests."""
        filt = filt or DashboardFilter()
        if filt.status is None:
            filt.status = None  # include all resolved statuses
        entries: list[AuditEntry] = []
        with self._queue._lock:
            for request in self._queue._requests.values():
                if request.status == "pending":
                    continue
                if not filt.matches(request):
                    continue
                latency = None
                if request.resolved_at is not None:
                    latency = request.resolved_at - request.created_at
                entries.append(
                    AuditEntry(
                        approval_id=request.approval_id,
                        kind=request.kind,
                        scope=request.scope,
                        summary=request.summary,
                        status=request.status,
                        resolved_by=request.resolved_by,
                        resolution_note=request.resolution_note,
                        created_at=request.created_at,
                        resolved_at=request.resolved_at,
                        latency_seconds=latency,
                        labels=request.labels,
                        policy_tags=request.policy_tags,
                    )
                )
                if len(entries) >= filt.limit:
                    break
        return sorted(entries, key=lambda e: e.resolved_at or e.created_at, reverse=True)

    def approval_trends(
        self,
        *,
        window_seconds: float = 3600,
        num_windows: int = 24,
        filt: DashboardFilter | None = None,
    ) -> list[TrendBucket]:
        """Compute approval trends over fixed time windows.

        Returns ``num_windows`` buckets of ``window_seconds`` width,
        ending at the current time.
        """
        now = time.time()
        buckets: list[TrendBucket] = []
        for i in range(num_windows):
            end = now - i * window_seconds
            start = end - window_seconds
            buckets.append(TrendBucket(window_start=start, window_end=end))

        filt = filt or DashboardFilter()
        with self._queue._lock:
            for request in self._queue._requests.values():
                if not filt.matches(request):
                    continue
                ts = request.resolved_at or request.created_at
                for bucket in buckets:
                    if bucket.window_start <= ts < bucket.window_end:
                        if request.status == "approved":
                            bucket.approved += 1
                        elif request.status == "rejected":
                            bucket.rejected += 1
                        elif request.status == "pending":
                            bucket.pending += 1
                        break

        for bucket in buckets:
            latencies: list[float] = []
            with self._queue._lock:
                for request in self._queue._requests.values():
                    if request.resolved_at is None:
                        continue
                    if not (bucket.window_start <= request.resolved_at < bucket.window_end):
                        continue
                    latencies.append(request.resolved_at - request.created_at)
            bucket.avg_latency_seconds = sum(latencies) / len(latencies) if latencies else None

        buckets.reverse()
        return buckets

    def stale_pending(self, *, max_age_seconds: float = 3600) -> list[dict[str, Any]]:
        """Find pending requests older than ``max_age_seconds``."""
        return self.query(
            DashboardFilter(
                status="pending",
                min_age_seconds=max_age_seconds,
            )
        )

    def agent_activity(self, scope: str) -> dict[str, Any]:
        """Summarize approval activity for a specific agent/scope."""
        return self.summary_stats(DashboardFilter(scope=scope))
