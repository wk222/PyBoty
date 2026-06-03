"""Long-running task APIs.

Codex-Cloud-style "task panel" data source:

* ``GET  /api/tasks``                       — flat list of all live tasks
* ``GET  /api/tasks/{task_id}``             — single task snapshot
* ``GET  /api/tasks/{task_id}/events``      — long-lived SSE stream of
  ``task.spawned`` / ``heartbeat`` / ``step`` / ``completed`` / ``failed`` /
  ``cancelled`` for one task
* ``GET  /api/tasks/events``                — same SSE stream but for **all**
  tasks (used by the dashboard list view)
* ``POST /api/tasks/{task_id}/cancel``      — cooperative cancel
* ``POST /api/tasks/{task_id}/pause``       — runner-dependent
* ``POST /api/tasks/{task_id}/resume``      — runner-dependent

The router is read-mostly and stateless; it does not own any storage and
relies entirely on :data:`core.systems.tasks.task_registry.task_registry`
for state and on :class:`~core.systems.runtime.event_bus.EventBus` for
push notifications.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from core.systems.runtime.event_bus import Event, EventType, event_bus
from core.systems.tasks import TaskKind, TaskStatus, task_registry
from core.systems.tasks.task_events import is_task_event

router = APIRouter(tags=["tasks"])

_HEARTBEAT_INTERVAL = 15.0  # seconds between SSE keepalive pings
_MAX_QUEUE = 256


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


def _coerce_kind(kind: str | None) -> TaskKind | None:
    if not kind:
        return None
    try:
        return TaskKind(kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"unknown kind {kind!r}") from exc


def _coerce_status(status: str | None) -> TaskStatus | None:
    if not status:
        return None
    try:
        return TaskStatus(status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"unknown status {status!r}") from exc


@router.get("/api/tasks")
async def list_tasks(
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    thread_id: str | None = Query(default=None),
) -> dict[str, Any]:
    snaps = task_registry.list(
        kind=_coerce_kind(kind),
        status=_coerce_status(status),
        thread_id=thread_id,
    )
    return {
        "count": len(snaps),
        "tasks": [s.to_dict() for s in snaps],
    }


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    task = task_registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task.snapshot().to_dict()


# ---------------------------------------------------------------------------
# Control endpoints
# ---------------------------------------------------------------------------


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict[str, Any]:
    if task_registry.get(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    ok = task_registry.cancel(task_id)
    return {"ok": ok}


@router.post("/api/tasks/{task_id}/pause")
async def pause_task(task_id: str) -> dict[str, Any]:
    if task_registry.get(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    ok = task_registry.pause(task_id)
    return {"ok": ok}


@router.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str) -> dict[str, Any]:
    if task_registry.get(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    ok = task_registry.resume(task_id)
    return {"ok": ok}


# ---------------------------------------------------------------------------
# SSE streams
# ---------------------------------------------------------------------------


def _format_sse(name: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {name}\ndata: {payload}\n\n".encode("utf-8")


def _format_keepalive() -> bytes:
    return b": ping\n\n"


async def _task_event_stream(task_id: str | None):
    """Async generator pumping a queue fed by an EventBus subscriber.

    The subscriber callback is sync (EventBus emits on the originating
    thread); we forward through ``asyncio.Queue`` so the FastAPI
    StreamingResponse can drain it without blocking.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)

    def _on_event(event: Event) -> None:
        if not is_task_event(event, task_id=task_id):
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:
            # event loop already gone — drop silently
            pass

    event_bus.subscribe(EventType.SCHEDULE_RUN, _on_event)

    try:
        # Bootstrap: send current snapshot(s) so the client doesn't have to
        # wait for the first heartbeat to draw something on screen.
        if task_id is not None:
            handle = task_registry.get(task_id)
            if handle is None:
                yield _format_sse(
                    "error",
                    {"error": "task not found", "task_id": task_id},
                )
                return
            yield _format_sse(
                "snapshot",
                {"snapshot": handle.snapshot().to_dict()},
            )
        else:
            yield _format_sse(
                "snapshot",
                {"tasks": [s.to_dict() for s in task_registry.list()]},
            )

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                yield _format_keepalive()
                continue
            payload = dict(event.payload or {})
            payload.setdefault("ts", time.time())
            yield _format_sse(payload.get("task_event", "task.event"), payload)
    finally:
        event_bus.unsubscribe(EventType.SCHEDULE_RUN, _on_event)


@router.get("/api/tasks/events")
async def stream_all_task_events() -> StreamingResponse:
    return StreamingResponse(
        _task_event_stream(task_id=None),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/tasks/{task_id}/events")
async def stream_task_events(task_id: str) -> StreamingResponse:
    if task_registry.get(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    return StreamingResponse(
        _task_event_stream(task_id=task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
