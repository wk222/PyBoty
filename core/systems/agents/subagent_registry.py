"""In-memory control plane for active subagent runs."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from core.systems.runtime.event_bus import Event, EventType, event_bus

ACTIVE_SUBAGENT_STATUSES = frozenset({"running", "waiting_approval"})
TERMINAL_SUBAGENT_STATUSES = frozenset({"completed", "failed", "timed_out", "aborted"})


class SubagentRegistryError(RuntimeError):
    """Base registry error."""


class SubagentDepthLimitError(SubagentRegistryError):
    """Raised when a spawn would exceed the configured nesting depth."""


class SubagentConcurrencyLimitError(SubagentRegistryError):
    """Raised when a spawn would exceed the configured active-subagent limit."""


@dataclass
class SubagentRunRecord:
    run_id: str
    agent_name: str
    thread_id: str
    team_key: str
    status: str
    depth: int
    created_at: float
    updated_at: float
    timeout_seconds: float
    owner_session_key: str = ""
    owner_thread_id: str = ""
    parent_agent_name: str | None = None
    parent_run_id: str | None = None
    parent_thread_id: str | None = None
    approval_id: str | None = None
    error: str | None = None
    error_context: str | None = None
    last_response: str | None = None
    steering_instructions: list[str] = field(default_factory=list)
    context_notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["age_seconds"] = max(0.0, time.time() - self.created_at)
        payload["idle_seconds"] = max(0.0, time.time() - self.updated_at)
        return payload


class SubagentRegistry:
    """Track active/pending subagent runs and their lifecycle transitions."""

    def __init__(
        self,
        *,
        max_depth: int = 3,
        max_concurrent: int = 5,
        default_timeout_seconds: float = 300.0,
    ):
        self.max_depth = max(1, int(max_depth))
        self.max_concurrent = max(1, int(max_concurrent))
        self.default_timeout_seconds = max(1.0, float(default_timeout_seconds))
        self._lock = threading.Lock()
        self._records: dict[str, SubagentRunRecord] = {}
        self._active_index: dict[tuple[str, str], str] = {}
        self._team_notes: dict[str, list[dict[str, Any]]] = {}
        self._completion_events: dict[str, threading.Event] = {}

    def wait(self, run_id: str, timeout: float | None = None) -> bool:
        """Wait for a specific run to reach a terminal status. Returns True if finished."""
        event = None
        with self._lock:
            record = self._records.get(run_id)
            if not record:
                return False
            if record.status in TERMINAL_SUBAGENT_STATUSES:
                return True
            
            event = self._completion_events.get(run_id)
            if not event:
                event = threading.Event()
                self._completion_events[run_id] = event
                
        return event.wait(timeout=timeout)


    def configure(
        self,
        *,
        max_depth: int | None = None,
        max_concurrent: int | None = None,
        default_timeout_seconds: float | None = None,
    ) -> None:
        with self._lock:
            if max_depth is not None:
                self.max_depth = max(1, int(max_depth))
            if max_concurrent is not None:
                self.max_concurrent = max(1, int(max_concurrent))
            if default_timeout_seconds is not None:
                self.default_timeout_seconds = max(1.0, float(default_timeout_seconds))

    def spawn(
        self,
        *,
        agent_name: str,
        thread_id: str,
        parent_agent_name: str | None = None,
        parent_run_id: str | None = None,
        parent_thread_id: str | None = None,
        parent_depth: int = 0,
        team_key: str = "",
        owner_session_key: str = "",
        owner_thread_id: str = "",
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubagentRunRecord:
        now = time.time()
        with self._lock:
            self._cleanup_stale_locked(now)
            active_count = sum(1 for record in self._records.values() if record.status in ACTIVE_SUBAGENT_STATUSES)
            if active_count >= self.max_concurrent:
                raise SubagentConcurrencyLimitError(f"活跃子智能体已达到上限（{self.max_concurrent}）")

            depth = max(0, int(parent_depth)) + 1
            if depth > self.max_depth:
                raise SubagentDepthLimitError(f"子智能体委派深度超出上限（当前 {depth}，最大 {self.max_depth}）")

            run_id = f"subagent-{agent_name}-{uuid.uuid4().hex[:12]}"
            record = SubagentRunRecord(
                run_id=run_id,
                agent_name=agent_name,
                thread_id=thread_id,
                team_key=str(team_key).strip() or str(owner_session_key).strip() or str(owner_thread_id).strip() or thread_id,
                status="running",
                depth=depth,
                created_at=now,
                updated_at=now,
                timeout_seconds=max(1.0, float(timeout_seconds or self.default_timeout_seconds)),
                owner_session_key=str(owner_session_key).strip(),
                owner_thread_id=str(owner_thread_id).strip(),
                parent_agent_name=parent_agent_name,
                parent_run_id=parent_run_id,
                parent_thread_id=parent_thread_id,
                metadata=dict(metadata or {}),
            )
            self._records[run_id] = record
            self._active_index[(agent_name, thread_id)] = run_id

        event_bus.emit(
            Event(
                type=EventType.SUBAGENT_SPAWNED,
                payload=record.to_dict(),
                source="subagent_registry",
                session_id=thread_id,
            )
        )
        return record

    def get(self, run_id: str) -> SubagentRunRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def get_active(self, *, agent_name: str, thread_id: str) -> SubagentRunRecord | None:
        with self._lock:
            run_id = self._active_index.get((agent_name, thread_id))
            if not run_id:
                return None
            return self._records.get(run_id)

    def get_latest(self, *, agent_name: str, thread_id: str) -> SubagentRunRecord | None:
        with self._lock:
            matches = [
                record
                for record in self._records.values()
                if record.agent_name == agent_name and record.thread_id == thread_id
            ]
        if not matches:
            return None
        return max(matches, key=lambda record: record.updated_at)

    def list_all(self) -> list[SubagentRunRecord]:
        with self._lock:
            return list(self._records.values())

    def list_active(self) -> list[SubagentRunRecord]:
        with self._lock:
            return [record for record in self._records.values() if record.status in ACTIVE_SUBAGENT_STATUSES]

    def add_team_note(
        self,
        *,
        team_key: str,
        agent_name: str,
        note: str,
    ) -> dict[str, Any]:
        normalized_team = str(team_key).strip()
        if not normalized_team:
            raise ValueError("Team key is required to add a team note.")
            
        normalized_note = str(note).strip()
        if not normalized_note:
            return {}
            
        entry = {
            "note_id": f"team-note-{uuid.uuid4().hex[:8]}",
            "run_id": "",
            "agent_name": agent_name,
            "note": normalized_note,
            "status": "shared",
            "timestamp": time.time(),
        }
        
        with self._lock:
            if normalized_team not in self._team_notes:
                self._team_notes[normalized_team] = []
            self._team_notes[normalized_team].append(entry)
            
        return entry

    def build_team_memory_projection(
        self,
        *,
        team_key: str = "",
        owner_session_key: str = "",
        owner_thread_id: str = "",
        limit: int = 8,
    ) -> dict[str, Any]:
        normalized_team = str(team_key).strip() or str(owner_session_key).strip() or str(owner_thread_id).strip()
        if not normalized_team:
            return {}
        capped_limit = max(1, int(limit or 0))
        with self._lock:
            records = [
                record
                for record in self._records.values()
                if record.team_key == normalized_team
                or (owner_session_key and record.owner_session_key == owner_session_key)
                or (owner_thread_id and record.owner_thread_id == owner_thread_id)
            ]
            shared_notes = list(self._team_notes.get(normalized_team, []))
            
        if not records and not shared_notes:
            return {}

        ordered = sorted(records, key=lambda item: item.updated_at)
        participant_agents = sorted(
            {record.agent_name for record in ordered if str(record.agent_name).strip()} | 
            {note.get("agent_name", "") for note in shared_notes if note.get("agent_name")}
        )
        recent_runs = [
            {
                "run_id": record.run_id,
                "agent_name": record.agent_name,
                "thread_id": record.thread_id,
                "status": record.status,
                "depth": record.depth,
                "parent_run_id": record.parent_run_id or "",
                "updated_at": record.updated_at,
                "owner_session_key": record.owner_session_key,
                "owner_thread_id": record.owner_thread_id,
            }
            for record in ordered[-capped_limit:]
        ]
        recent_notes: list[dict[str, Any]] = []
        for record in ordered:
            if not record.context_notes:
                continue
            for note_index, note in enumerate(record.context_notes):
                normalized_note = str(note).strip()
                if not normalized_note:
                    continue
                recent_notes.append(
                    {
                        "note_id": f"{record.run_id}:{note_index}",
                        "run_id": record.run_id,
                        "agent_name": record.agent_name,
                        "note": normalized_note,
                        "status": record.status,
                        "timestamp": record.updated_at,
                    }
                )
        recent_notes.extend(shared_notes)
        recent_notes.sort(key=lambda x: x.get("timestamp", 0))
        note_count = len(recent_notes)
        recent_notes = recent_notes[-capped_limit:]
        
        active_run_count = sum(1 for record in ordered if record.status in ACTIVE_SUBAGENT_STATUSES)
        run_status_counts: dict[str, int] = {}
        for record in ordered:
            run_status_counts[record.status] = run_status_counts.get(record.status, 0) + 1
            
        summary = (
            f"team memory for {normalized_team}: {active_run_count} active run"
            f"{'s' if active_run_count != 1 else ''}, {len(recent_notes)} recent notes"
        )
        return {
            "summary": summary,
            "team_key": normalized_team,
            "owner_session_key": str(owner_session_key).strip() or (ordered[-1].owner_session_key if ordered else ""),
            "owner_thread_id": str(owner_thread_id).strip() or (ordered[-1].owner_thread_id if ordered else ""),
            "participant_agents": participant_agents,
            "recent_runs": recent_runs,
            "recent_notes": recent_notes,
            "note_count": note_count,
            "active_run_count": active_run_count,
            "shared_memory_ready": True,
            "run_status_counts": run_status_counts,
            "last_note": recent_notes[-1]["note"] if recent_notes else "",
            "last_updated_at": ordered[-1].updated_at if ordered else (shared_notes[-1]["timestamp"] if shared_notes else 0),
        }

    def mark_waiting_approval(
        self,
        *,
        agent_name: str,
        thread_id: str,
        approval_id: str,
    ) -> SubagentRunRecord | None:
        with self._lock:
            record = self._lookup_active_locked(agent_name, thread_id)
            if record is None:
                return None
            record.status = "waiting_approval"
            record.approval_id = approval_id
            record.updated_at = time.time()
            return record

    def record_steer(
        self,
        *,
        agent_name: str,
        thread_id: str,
        instructions: str,
    ) -> SubagentRunRecord | None:
        instruction = str(instructions).strip()
        if not instruction:
            return self.get_active(agent_name=agent_name, thread_id=thread_id)
        with self._lock:
            record = self._lookup_active_locked(agent_name, thread_id)
            if record is None:
                return None
            record.steering_instructions.append(instruction)
            record.updated_at = time.time()
            return record

    def consume_steering(
        self,
        *,
        agent_name: str,
        thread_id: str,
    ) -> list[str]:
        with self._lock:
            record = self._lookup_active_locked(agent_name, thread_id)
            if record is None or not record.steering_instructions:
                return []
            items = list(record.steering_instructions)
            record.steering_instructions.clear()
            record.updated_at = time.time()
            return items

    def abort(
        self,
        *,
        agent_name: str,
        thread_id: str,
        reason: str = "",
    ) -> SubagentRunRecord | None:
        return self._transition_active(
            agent_name=agent_name,
            thread_id=thread_id,
            status="aborted",
            error=reason or "aborted",
            event_type=EventType.SUBAGENT_FAILED,
        )

    def complete(
        self,
        *,
        agent_name: str,
        thread_id: str,
        response: str = "",
        context_notes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubagentRunRecord | None:
        return self._transition_active(
            agent_name=agent_name,
            thread_id=thread_id,
            status="completed",
            response=response,
            context_notes=context_notes,
            metadata=metadata,
            event_type=EventType.SUBAGENT_COMPLETED,
        )

    def fail(
        self,
        *,
        agent_name: str,
        thread_id: str,
        error: str,
        error_context: str | None = None,
        context_notes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubagentRunRecord | None:
        return self._transition_active(
            agent_name=agent_name,
            thread_id=thread_id,
            status="failed",
            error=error,
            error_context=error_context,
            context_notes=context_notes,
            metadata=metadata,
            event_type=EventType.SUBAGENT_FAILED,
        )

    def cleanup_stale(self, *, now: float | None = None) -> list[SubagentRunRecord]:
        stale: list[SubagentRunRecord] = []
        now_ts = now or time.time()
        with self._lock:
            stale.extend(self._cleanup_stale_locked(now_ts))
        for record in stale:
            event_bus.emit(
                Event(
                    type=EventType.SUBAGENT_TIMEOUT,
                    payload=record.to_dict(),
                    source="subagent_registry",
                    session_id=record.thread_id,
                )
            )
        return stale

    def _cleanup_stale_locked(self, now: float) -> list[SubagentRunRecord]:
        stale_records: list[SubagentRunRecord] = []
        for record in self._records.values():
            if record.status not in ACTIVE_SUBAGENT_STATUSES:
                continue
            if now - record.updated_at <= record.timeout_seconds:
                continue
            record.status = "timed_out"
            record.error = f"timed out after {int(record.timeout_seconds)}s"
            record.updated_at = now
            self._active_index.pop((record.agent_name, record.thread_id), None)
            stale_records.append(record)
        return stale_records

    def _transition_active(
        self,
        *,
        agent_name: str,
        thread_id: str,
        status: str,
        event_type: EventType,
        response: str = "",
        error: str = "",
        error_context: str | None = None,
        context_notes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubagentRunRecord | None:
        with self._lock:
            record = self._lookup_active_locked(agent_name, thread_id)
            if record is None:
                return None
            record.status = status
            record.updated_at = time.time()
            if response:
                record.last_response = response
            if error:
                record.error = error
            if error_context:
                record.error_context = error_context
            if context_notes:
                record.context_notes.extend(context_notes)
            if metadata:
                record.metadata.update(metadata)
            self._active_index.pop((agent_name, thread_id), None)
            
            # Signal completion
            completion_event = self._completion_events.pop(record.run_id, None)
            if completion_event:
                completion_event.set()

        event_bus.emit(
            Event(
                type=event_type,
                payload=record.to_dict(),
                source="subagent_registry",
                session_id=thread_id,
            )
        )
        return record

    def _lookup_active_locked(self, agent_name: str, thread_id: str) -> SubagentRunRecord | None:
        run_id = self._active_index.get((agent_name, thread_id))
        if not run_id:
            return None
        return self._records.get(run_id)


_global_registry = SubagentRegistry()


def get_global_subagent_registry() -> SubagentRegistry:
    return _global_registry


def reset_global_subagent_registry() -> SubagentRegistry:
    global _global_registry
    _global_registry = SubagentRegistry()
    return _global_registry


__all__ = [
    "ACTIVE_SUBAGENT_STATUSES",
    "SubagentConcurrencyLimitError",
    "SubagentDepthLimitError",
    "SubagentRegistry",
    "SubagentRegistryError",
    "SubagentRunRecord",
    "TERMINAL_SUBAGENT_STATUSES",
    "get_global_subagent_registry",
    "reset_global_subagent_registry",
]
