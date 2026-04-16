"""Global publish-subscribe event bus with optional SQLite persistence.

All modules (tools, approval, memory, MCP, cost, workflow) emit events
through a single bus. Handlers can be sync or async, are priority-ordered,
and are isolated — one handler blowing up won't affect the rest.

When a db_path is provided, events are also persisted to SQLite so that
event history survives process restarts.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EventType(str, enum.Enum):
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    SUBAGENT_SPAWNED = "subagent.spawned"
    SUBAGENT_COMPLETED = "subagent.completed"
    SUBAGENT_FAILED = "subagent.failed"
    SUBAGENT_TIMEOUT = "subagent.timeout"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESOLVED = "approval_resolved"
    MEMORY_WRITE = "memory_write"
    MEMORY_SEARCH = "memory_search"
    MEMORY_FORGET = "memory_forget"
    MCP_CONNECT = "mcp_connect"
    MCP_DISCONNECT = "mcp_disconnect"
    COST_RECORD = "cost_record"
    WORKFLOW_STEP = "workflow_step"
    WORKFLOW_START = "workflow_start"
    WORKFLOW_END = "workflow_end"
    GUARDRAIL_PASS = "guardrail_pass"
    GUARDRAIL_FAIL = "guardrail_fail"
    GUARDRAIL_RETRY = "guardrail_retry"
    MODEL_USAGE = "model_usage"
    MODEL_FAILOVER = "model_failover"
    CONTEXT_COMPACT = "context_compact"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SCHEDULE_RUN = "schedule_run"
    WEBHOOK_RECEIVED = "webhook_received"
    ERROR = "error"
    TELEMETRY_REPORT_GENERATED = "telemetry_report_generated"
    CAPABILITY_DISCOVERED = "capability_discovered"
    CAPABILITY_PUBLISHED = "capability_published"
    CAPABILITY_INSTALLED = "capability_installed"
    CAPABILITY_GRANTED = "capability_granted"
    CAPABILITY_CONSUMED = "capability_consumed"
    CAPABILITY_GAP_DETECTED = "capability_gap_detected"
    CAPABILITY_GAP_PROMOTED = "capability_gap_promoted"
    CAPABILITY_DRAFT_CREATED = "capability_draft_created"
    CAPABILITY_DRAFT_VALIDATED = "capability_draft_validated"
    CAPABILITY_ROLLOUT_STARTED = "capability_rollout_started"
    CAPABILITY_ROLLOUT_EVALUATED = "capability_rollout_evaluated"
    CAPABILITY_GAP_RESOLVED = "capability_gap_resolved"
    APP_RUNTIME_ERROR = "app_runtime_error"
    CANVAS_CHANGED = "canvas_changed"


@dataclass
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    session_id: str | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class _Subscription:
    handler: Callable
    priority: int
    is_async: bool


_DEFAULT_HISTORY_LIMIT = 500


class EventBus:
    """Thread-safe publish-subscribe event bus with priority ordering.

    Args:
        history_limit: Max in-memory history entries.
        db_path: If set, persist events to SQLite for crash recovery.
        persist_limit: Max events kept in the SQLite database.
    """

    def __init__(
        self,
        history_limit: int = _DEFAULT_HISTORY_LIMIT,
        db_path: str | Path | None = None,
        persist_limit: int = 5000,
    ):
        self._lock = threading.Lock()
        self._subs: dict[EventType, list[_Subscription]] = {}
        self._history: list[Event] = []
        self._history_limit = history_limit
        self._db_path = Path(db_path) if db_path else None
        self._persist_limit = persist_limit
        self._db_conn: sqlite3.Connection | None = None
        
        self._persist_queue: queue.Queue[Event | None] = queue.Queue()
        self._persist_thread: threading.Thread | None = None
        
        if self._db_path:
            self._start_background_worker()
            self._load_history_from_db_initial()

    def _start_background_worker(self) -> None:
        self._persist_thread = threading.Thread(
            target=self._background_worker,
            name="EventBusPersistence",
            daemon=True,
        )
        self._persist_thread.start()

    def _background_worker(self) -> None:
        """Dedicated thread for SQLite operations to avoid blocking main execution."""
        if not self._db_path:
            return
            
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._db_conn.execute("PRAGMA journal_mode=WAL")
            self._db_conn.execute("PRAGMA synchronous=NORMAL")
            self._db_conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    payload TEXT DEFAULT '{}',
                    source TEXT DEFAULT '',
                    session_id TEXT,
                    timestamp REAL NOT NULL
                )
            """)
            self._db_conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
            self._db_conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
            self._db_conn.commit()
        except Exception:
            logger.exception("EventBus: failed to initialize persistence worker")
            return

        while True:
            event = self._persist_queue.get()
            if event is None:
                break
                
            try:
                self._db_conn.execute(
                    "INSERT INTO events (type, payload, source, session_id, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (
                        event.type.value,
                        json.dumps(event.payload, default=str),
                        event.source,
                        event.session_id,
                        event.timestamp,
                    ),
                )
                self._db_conn.commit()
                self._prune_db_worker()
            except Exception:
                logger.exception("EventBus: failed to persist event %s", event.type)
            finally:
                self._persist_queue.task_done()

    def _prune_db_worker(self) -> None:
        if self._db_conn is None:
            return
        try:
            count = self._db_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            if count > self._persist_limit * 1.2:
                cutoff = count - self._persist_limit
                self._db_conn.execute(
                    "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id LIMIT ?)",
                    (cutoff,),
                )
                self._db_conn.commit()
        except Exception:
            logger.exception("EventBus: failed to prune event DB")

    def _load_history_from_db_initial(self) -> None:
        """Synchronous initial load from DB (called only during __init__)."""
        if not self._db_path or not self._db_path.exists():
            return
            
        try:
            # Create a temporary connection just for initial load
            conn = sqlite3.connect(str(self._db_path))
            rows = conn.execute(
                "SELECT type, payload, source, session_id, timestamp FROM events ORDER BY id DESC LIMIT ?",
                (self._history_limit,),
            ).fetchall()
            conn.close()
            
            recovered = []
            for row in reversed(rows):
                try:
                    etype = EventType(row[0])
                except ValueError:
                    continue
                recovered.append(
                    Event(
                        type=etype,
                        payload=json.loads(row[1]) if row[1] else {},
                        source=row[2] or "",
                        session_id=row[3],
                        timestamp=row[4],
                    )
                )
            with self._lock:
                self._history = recovered
            logger.info("EventBus: recovered %d events from persistent store", len(recovered))
        except Exception:
            logger.exception("EventBus: failed to load initial history from DB")

    def subscribe(
        self,
        event_type: EventType,
        handler: Callable,
        *,
        priority: int = 0,
    ) -> None:
        is_async = asyncio.iscoroutinefunction(handler)
        sub = _Subscription(handler=handler, priority=priority, is_async=is_async)
        with self._lock:
            subs = self._subs.setdefault(event_type, [])
            subs.append(sub)
            subs.sort(key=lambda s: -s.priority)

    def unsubscribe(self, event_type: EventType, handler: Callable) -> bool:
        with self._lock:
            subs = self._subs.get(event_type, [])
            before = len(subs)
            self._subs[event_type] = [s for s in subs if s.handler is not handler]
            return len(self._subs[event_type]) < before

    def emit(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subs.get(event.type, []))
            self._history.append(event)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit :]

        if self._db_path:
            self._persist_queue.put(event)

        for sub in subs:
            try:
                if sub.is_async:
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None
                    if loop and loop.is_running():
                        loop.create_task(sub.handler(event))
                    else:
                        asyncio.run(sub.handler(event))
                else:
                    sub.handler(event)
            except Exception:
                logger.exception("EventBus handler %r failed for %s", sub.handler, event.type)

    async def emit_async(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subs.get(event.type, []))
            self._history.append(event)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit :]

        if self._db_path:
            self._persist_queue.put(event)

        for sub in subs:
            try:
                if sub.is_async:
                    await sub.handler(event)
                else:
                    sub.handler(event)
            except Exception:
                logger.exception("EventBus handler %r failed for %s", sub.handler, event.type)

    def history(
        self,
        event_type: EventType | None = None,
        *,
        limit: int = 100,
    ) -> list[Event]:
        with self._lock:
            events = self._history
            if event_type is not None:
                events = [e for e in events if e.type == event_type]
            return events[-limit:]

    def persistent_history(
        self,
        event_type: EventType | None = None,
        *,
        limit: int = 100,
        since: float | None = None,
        session_id: str | None = None,
    ) -> list[Event]:
        """Query persisted events (richer than in-memory history)."""
        if not self._db_path or not self._db_path.exists():
            return self.history(event_type, limit=limit)

        try:
            # Use a short-lived connection for queries to avoid blocking the background worker
            # or having to wait for its lock.
            conn = sqlite3.connect(str(self._db_path))
            query = "SELECT type, payload, source, session_id, timestamp FROM events"
            params: list[Any] = []
            conditions = []

            if event_type is not None:
                conditions.append("type = ?")
                params.append(event_type.value)
            if since is not None:
                conditions.append("timestamp >= ?")
                params.append(since)
            if session_id is not None:
                conditions.append("session_id = ?")
                params.append(session_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            conn.close()
            
            result = []
            for row in reversed(rows):
                try:
                    etype = EventType(row[0])
                except ValueError:
                    continue
                result.append(
                    Event(
                        type=etype,
                        payload=json.loads(row[1]) if row[1] else {},
                        source=row[2] or "",
                        session_id=row[3],
                        timestamp=row[4],
                    )
                )
            return result
        except Exception:
            logger.exception("EventBus: failed to query persistent history")
            return self.history(event_type, limit=limit)

    def close(self) -> None:
        """Shutdown the persistence worker."""
        if self._persist_thread:
            self._persist_queue.put(None)
            self._persist_thread.join(timeout=2.0)
            if self._db_conn:
                self._db_conn.close()

    def clear(self) -> None:
        with self._lock:
            self._subs.clear()
            self._history.clear()



    def subscriber_count(self, event_type: EventType | None = None) -> int:
        with self._lock:
            if event_type is not None:
                return len(self._subs.get(event_type, []))
            return sum(len(s) for s in self._subs.values())


def _default_event_db_path() -> str | None:
    """Try to locate workspace/data/events.db relative to the project."""
    candidates = [
        Path("workspace/data/events.db"),
        Path(__file__).parent.parent / "workspace" / "data" / "events.db",
    ]
    for p in candidates:
        if p.parent.exists() or p.parent.parent.exists():
            return str(p)
    return None


event_bus = EventBus(db_path=_default_event_db_path())
