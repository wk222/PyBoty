"""Snapshot the conversation/memory context that gave birth to a long-running task.

Without this, a recurring task that resumes hours later sees a possibly very
different ``MEMORY.md`` and may "drift" into unrelated work.  The snapshot
captures three things:

* ``conversation_window`` — the last *N* user/assistant messages that framed
  the spawn instruction;
* ``pinned_facts``         — the top-k MemoryRouter results computed at spawn
  time, so resumed runs see the same long-term context regardless of how
  ``MEMORY.md`` later evolves;
* ``memory_md_sha1`` and ``canvas`` — diagnostic fields to detect drift.

The snapshot lives inside ``task.context["__snapshot__"]`` so the
``PersistentTask`` data model needs no changes.  This file lives at L3
(``core/systems/agents``) which is allowed to import the L1 MemoryRouter.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


SNAPSHOT_KEY = "__snapshot__"
DEFAULT_CONVERSATION_WINDOW = 8
DEFAULT_PINNED_FACTS = 16


@dataclass
class TaskContextSnapshot:
    """Serialisable bundle of "what the task knew at spawn time"."""

    conversation_window: list[dict[str, str]] = field(default_factory=list)
    pinned_facts: list[str] = field(default_factory=list)
    memory_md_sha1: str | None = None
    canvas: str | None = None
    captured_at: float = field(default_factory=time.time)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_window": list(self.conversation_window),
            "pinned_facts": list(self.pinned_facts),
            "memory_md_sha1": self.memory_md_sha1,
            "canvas": self.canvas,
            "captured_at": self.captured_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TaskContextSnapshot | None":
        if not isinstance(data, dict):
            return None
        return cls(
            conversation_window=list(data.get("conversation_window", [])),
            pinned_facts=list(data.get("pinned_facts", [])),
            memory_md_sha1=data.get("memory_md_sha1"),
            canvas=data.get("canvas"),
            captured_at=float(data.get("captured_at", 0.0) or 0.0),
            schema_version=int(data.get("schema_version", 1) or 1),
        )


@dataclass
class SnapshotRestoreReport:
    """Telemetry returned from :func:`restore_for_resume`."""

    found: bool
    drift_detected: bool = False
    pinned_facts_used: int = 0
    age_seconds: float = 0.0
    note: str = ""


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture_snapshot(
    *,
    conversation_window: list[dict[str, str]] | None = None,
    memory_md_text: str | None = None,
    memory_router: Any | None = None,
    spawn_query: str | None = None,
    canvas: str | None = None,
    pinned_top_k: int = DEFAULT_PINNED_FACTS,
    window_size: int = DEFAULT_CONVERSATION_WINDOW,
) -> TaskContextSnapshot:
    """Build a :class:`TaskContextSnapshot` from the current session state."""
    window = list(conversation_window or [])[-window_size:]
    sha = _sha1(memory_md_text) if memory_md_text else None

    pinned: list[str] = []
    if memory_router is not None and spawn_query:
        try:
            facts = memory_router.route(query=spawn_query, top_k=pinned_top_k)
            for fact in facts:
                content = getattr(fact, "content", None)
                if not content and isinstance(fact, str):
                    content = fact
                if content:
                    pinned.append(str(content))
        except Exception:
            logger.exception("memory_router.route failed during snapshot capture")

    return TaskContextSnapshot(
        conversation_window=window,
        pinned_facts=pinned,
        memory_md_sha1=sha,
        canvas=canvas,
    )


def attach_snapshot(task: Any, snapshot: TaskContextSnapshot) -> None:
    """Write a snapshot into ``task.context[SNAPSHOT_KEY]`` (in-place)."""
    if not hasattr(task, "context"):
        raise TypeError("attach_snapshot expects an object with a 'context' dict")
    if task.context is None:
        task.context = {}
    task.context[SNAPSHOT_KEY] = snapshot.to_dict()


def read_snapshot(task: Any) -> TaskContextSnapshot | None:
    """Read the snapshot out of ``task.context``, if any."""
    ctx = getattr(task, "context", None) or {}
    return TaskContextSnapshot.from_dict(ctx.get(SNAPSHOT_KEY))


# ---------------------------------------------------------------------------
# Resume / restore
# ---------------------------------------------------------------------------


def restore_for_resume(
    task: Any,
    *,
    current_memory_md_text: str | None = None,
) -> SnapshotRestoreReport:
    """Compute a restore report for the resume codepath.

    The runner is expected to call this when waking a paused/persisted task.
    It does *not* mutate the task; it only tells the caller (a) whether a
    snapshot exists, (b) whether MEMORY.md has drifted, (c) how many pinned
    facts were captured, and (d) how stale the snapshot is.  The caller can
    then decide to re-prompt the LLM with the pinned facts as a fixed
    "background" block.
    """
    snap = read_snapshot(task)
    if snap is None:
        return SnapshotRestoreReport(
            found=False, note="no snapshot stored"
        )

    drift = False
    if snap.memory_md_sha1 and current_memory_md_text is not None:
        drift = _sha1(current_memory_md_text) != snap.memory_md_sha1

    age = max(0.0, time.time() - snap.captured_at) if snap.captured_at else 0.0
    return SnapshotRestoreReport(
        found=True,
        drift_detected=drift,
        pinned_facts_used=len(snap.pinned_facts),
        age_seconds=age,
        note=("memory drifted" if drift else "memory unchanged"),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_CONVERSATION_WINDOW",
    "DEFAULT_PINNED_FACTS",
    "SNAPSHOT_KEY",
    "SnapshotRestoreReport",
    "TaskContextSnapshot",
    "attach_snapshot",
    "capture_snapshot",
    "read_snapshot",
    "restore_for_resume",
]
