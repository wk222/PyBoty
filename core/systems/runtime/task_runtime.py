"""Unified task runtime for task state and recent execution activity."""

from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Any


@dataclass
class TaskRecord:
    id: str
    content: str
    status: str = "pending"
    source: str = ""
    updated_at: float = 0.0
    lifecycle: str = "foreground"
    surface: str = "chat"


@dataclass
class TaskActivity:
    activity_id: str
    kind: str
    title: str
    status: str = ""
    source: str = ""
    preview: str = ""
    task_id: str = ""
    timestamp: float = 0.0
    lifecycle: str = "foreground"


class TaskRuntimeService:
    """Track live task state plus recent execution activity on one spine."""

    def __init__(self) -> None:
        self._tasks_by_id: dict[str, TaskRecord] = {}
        self._task_order: list[str] = []
        self._activities_by_id: dict[str, TaskActivity] = {}
        self._activity_order: list[str] = []

    def upsert_tasks(self, items_data: list[dict[str, Any]], *, source: str = "todo_middleware") -> None:
        now = time()
        for raw in items_data:
            if not isinstance(raw, dict):
                continue
            task_id = str(raw.get("id", "")).strip()
            if not task_id:
                continue
            existing = self._tasks_by_id.get(task_id)
            content = str(raw.get("content", existing.content if existing else "")).strip() if existing else str(raw.get("content", "")).strip()
            status = str(raw.get("status", existing.status if existing else "pending")).strip() or "pending"
            record = TaskRecord(
                id=task_id,
                content=content,
                status=status,
                source=str(raw.get("source", source)).strip() or source,
                updated_at=float(raw.get("updated_at", now) or now),
                lifecycle=str(raw.get("lifecycle", existing.lifecycle if existing else "foreground")).strip()
                or "foreground",
                surface=str(raw.get("surface", existing.surface if existing else "chat")).strip() or "chat",
            )
            self._tasks_by_id[task_id] = record
            if task_id not in self._task_order:
                self._task_order.append(task_id)

    def ingest_tool_runs(self, runs: list[dict[str, Any]], *, source: str = "tool_projection") -> None:
        for raw in runs:
            if not isinstance(raw, dict):
                continue
            self.record_activity(
                kind=str(raw.get("kind", "tool_run")).strip() or "tool_run",
                title=str(raw.get("title", "")).strip(),
                status=str(raw.get("status", "")).strip(),
                source=str(raw.get("source", source)).strip() or source,
                activity_id=str(raw.get("run_id", "")).strip(),
                preview=str(raw.get("preview", "")).strip(),
                task_id=str(raw.get("task_id", "")).strip(),
                timestamp=float(raw.get("timestamp", 0) or 0),
                lifecycle=str(raw.get("lifecycle", "foreground")).strip() or "foreground",
            )

    def ingest_permission_events(
        self,
        events: list[dict[str, Any]],
        *,
        source: str = "permission_projection",
    ) -> None:
        for raw in events:
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action", "")).strip()
            if not action:
                continue
            target = str(raw.get("tool_name", "")).strip() or str(raw.get("mode", "")).strip()
            title = f"governance:{action}"
            if target:
                title += f" {target}"
            self.record_activity(
                kind="governance",
                title=title,
                status=str(raw.get("verdict", "")).strip() or str(raw.get("mode", "")).strip(),
                source=source,
                activity_id=str(raw.get("event_id", "")).strip(),
                preview=str(raw.get("reason", "")).strip(),
                timestamp=float(raw.get("timestamp", 0) or 0),
                lifecycle="foreground",
            )

    def record_compaction_boundary(
        self,
        boundary: dict[str, Any],
        *,
        source: str = "compaction",
    ) -> None:
        if not isinstance(boundary, dict) or not boundary:
            return
        reason = str(boundary.get("reason", "")).strip() or "conversation_compaction"
        self.record_activity(
            kind="compaction",
            title=reason,
            status="summarized",
            source=str(boundary.get("source", source)).strip() or source,
            activity_id=str(boundary.get("boundary_id", "")).strip(),
            preview=str(boundary.get("summary", "")).strip(),
            timestamp=float(boundary.get("timestamp", 0) or 0),
            lifecycle="resume",
        )

    def record_activity(
        self,
        *,
        kind: str,
        title: str,
        status: str = "",
        source: str = "",
        activity_id: str = "",
        preview: str = "",
        task_id: str = "",
        timestamp: float = 0.0,
        lifecycle: str = "foreground",
    ) -> None:
        title = str(title).strip()
        if not title:
            return
        resolved_kind = str(kind).strip() or "activity"
        resolved_timestamp = float(timestamp or time())
        resolved_id = activity_id.strip() or f"{resolved_kind}:{title}:{status}:{source}:{resolved_timestamp}"
        activity = TaskActivity(
            activity_id=resolved_id,
            kind=resolved_kind,
            title=title,
            status=str(status).strip(),
            source=str(source).strip(),
            preview=str(preview).strip(),
            task_id=str(task_id).strip(),
            timestamp=resolved_timestamp,
            lifecycle=str(lifecycle).strip() or "foreground",
        )
        self._activities_by_id[resolved_id] = activity
        if resolved_id in self._activity_order:
            self._activity_order.remove(resolved_id)
        self._activity_order.append(resolved_id)

    def build_projection(
        self,
        *,
        task_limit: int = 8,
        activity_limit: int = 8,
    ) -> dict[str, Any] | None:
        tasks = [
            self._tasks_by_id[task_id]
            for task_id in self._task_order[-max(1, int(task_limit)) :]
            if task_id in self._tasks_by_id
        ]
        activities = [
            self._activities_by_id[activity_id]
            for activity_id in self._activity_order[-max(1, int(activity_limit)) :]
            if activity_id in self._activities_by_id
        ]
        if not tasks and not activities:
            return None

        status_counts: dict[str, int] = {}
        for record in self._tasks_by_id.values():
            status_counts[record.status] = status_counts.get(record.status, 0) + 1
        activity_counts: dict[str, int] = {}
        lifecycle_counts: dict[str, int] = {}
        for activity in activities:
            activity_counts[activity.kind] = activity_counts.get(activity.kind, 0) + 1
            lifecycle_counts[activity.lifecycle] = lifecycle_counts.get(activity.lifecycle, 0) + 1
        active_count = status_counts.get("pending", 0) + status_counts.get("in_progress", 0)
        completed_count = status_counts.get("completed", 0)
        summary = (
            f"{active_count} active tasks, {completed_count} completed, {len(activities)} recent activities"
        )

        return {
            "summary": summary,
            "tasks": [
                {
                    "id": record.id,
                    "content": record.content,
                    "status": record.status,
                    "source": record.source,
                    "updated_at": record.updated_at,
                    "lifecycle": record.lifecycle,
                    "surface": record.surface,
                }
                for record in tasks
            ],
            "activities": [
                {
                    "activity_id": activity.activity_id,
                    "kind": activity.kind,
                    "title": activity.title,
                    "status": activity.status,
                    "source": activity.source,
                    "preview": activity.preview,
                    "task_id": activity.task_id,
                    "timestamp": activity.timestamp,
                    "lifecycle": activity.lifecycle,
                }
                for activity in activities
            ],
            "status_counts": status_counts,
            "activity_counts": activity_counts,
            "lifecycle_counts": lifecycle_counts,
        }
