"""Shared compaction boundary helpers for session and conversation compaction."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CompactionBoundary:
    boundary_id: str
    source: str
    reason: str
    created_at: float
    source_event_range: dict[str, Any]
    notebook_summary: str
    retained_recent_window: dict[str, Any]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "source": self.source,
            "reason": self.reason,
            "created_at": self.created_at,
            "source_event_range": dict(self.source_event_range),
            "notebook_summary": self.notebook_summary,
            "retained_recent_window": dict(self.retained_recent_window),
            "summary": self.summary,
        }


def create_compaction_boundary(
    *,
    source: str,
    reason: str,
    summary: str,
    notebook_summary: str = "",
    source_event_range: dict[str, Any] | None = None,
    retained_recent_window: dict[str, Any] | None = None,
    created_at: float | None = None,
) -> CompactionBoundary:
    timestamp = float(created_at) if created_at is not None else time.time()
    return CompactionBoundary(
        boundary_id=f"cb-{uuid.uuid4().hex[:10]}",
        source=str(source).strip() or "session_runtime",
        reason=str(reason).strip() or "compaction",
        created_at=timestamp,
        source_event_range=dict(source_event_range or {}),
        notebook_summary=str(notebook_summary).strip(),
        retained_recent_window=dict(retained_recent_window or {}),
        summary=str(summary).strip(),
    )
