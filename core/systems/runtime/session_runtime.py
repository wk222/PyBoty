"""Persistent session spine for chat, gateway, and mode-aware runtime state."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.systems.runtime.session_artifacts import compile_session_artifacts
from core.systems.runtime.session_compaction import create_compaction_boundary
from core.systems.runtime.session_kernel import SessionKernel
from core.systems.runtime.session_memory_policy import (
    SESSION_MEMORY_TYPE,
    typed_memory_entry_payload,
    validate_session_memory,
)


def _default_memory_layers() -> dict[str, Any]:
    return {
        "workspace": {},
        "session": {},
        "agent": {},
        "admin": {},
    }


def _default_compaction_state() -> dict[str, Any]:
    return {
        "compacted_notes": 0,
        "compacted_timeline_events": 0,
        "compacted_tool_events": 0,
        "tool_notebook_entries": 0,
        "compacted_file_views": 0,
        "file_view_notebook_entries": 0,
        "microcompacted_previews": 0,
        "microcompacted_metadata": 0,
        "notebook_entries": 0,
        "resume_scrubbed_events": 0,
        "boundaries": [],
        "history": [],
        "last_compacted_at": None,
        "last_reason": "",
    }


def _preview_text(content: str, *, limit: int = 160) -> str:
    normalized = " ".join(str(content).split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"


def _append_unique(items: list[str], value: str) -> None:
    normalized = str(value).strip()
    if normalized and normalized not in items:
        items.append(normalized)


def _stable_text_hash(*parts: Any) -> str:
    payload = "||".join(str(part) for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


_FILE_VIEW_TOOL_NAMES = {
    "read_file",
    "read_app_file",
    "read_app_file_tool",
}


def _extract_file_view_from_tool_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    normalized_name = str(tool_name).strip().lower()
    args = payload.get("args", {}) if isinstance(payload.get("args"), dict) else {}
    candidate_path = ""
    for key in ("path", "file_path", "relative_path", "target_path"):
        value = str(args.get(key, "")).strip()
        if value:
            candidate_path = value
            break
    if normalized_name not in _FILE_VIEW_TOOL_NAMES and not candidate_path:
        return None
    if not candidate_path:
        return None

    raw_offset = args.get("offset", args.get("start", 0))
    raw_limit = args.get("limit", args.get("length", args.get("max_chars", 0)))
    try:
        offset = max(0, int(raw_offset or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = max(0, int(raw_limit or 0))
    except (TypeError, ValueError):
        limit = 0
    preview = str(payload.get("preview", "") or payload.get("output_preview", "") or payload.get("content", "")).strip()
    is_partial_view = bool(offset or limit)
    return {
        "path": candidate_path,
        "offset": offset,
        "limit": limit,
        "is_partial_view": is_partial_view,
        "tool_name": normalized_name or str(tool_name).strip(),
        "source": str(payload.get("source", "")).strip(),
        "preview": preview,
    }


@dataclass
class SessionRecord:
    session_key: str
    thread_id: str
    primary_mode: str = "assistant"
    mode_history: list[str] = field(default_factory=list)
    source_history: list[str] = field(default_factory=list)
    title: str = ""
    status: str = "active"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_message_at: float | None = None
    message_count: int = 0
    last_message_preview: str = ""
    last_user_message: str = ""
    last_assistant_message: str = ""
    working_summary: str = ""
    context_notes: list[str] = field(default_factory=list)
    memory_layers: dict[str, Any] = field(default_factory=_default_memory_layers)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    compaction_state: dict[str, Any] = field(default_factory=_default_compaction_state)
    gateway: dict[str, Any] = field(default_factory=dict)
    latest_run: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "thread_id": self.thread_id,
            "primary_mode": self.primary_mode,
            "mode_history": list(self.mode_history),
            "source_history": list(self.source_history),
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_message_at": self.last_message_at,
            "message_count": self.message_count,
            "last_message_preview": self.last_message_preview,
            "last_user_message": self.last_user_message,
            "last_assistant_message": self.last_assistant_message,
            "working_summary": self.working_summary,
            "context_notes": list(self.context_notes),
            "memory_layers": self.memory_layers,
            "timeline": list(self.timeline),
            "compaction_state": self.compaction_state,
            "gateway": self.gateway,
            "latest_run": self.latest_run,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SessionRecord:
        return cls(
            session_key=str(payload.get("session_key", "")).strip(),
            thread_id=str(payload.get("thread_id", "")).strip(),
            primary_mode=str(payload.get("primary_mode", "assistant")).strip() or "assistant",
            mode_history=[str(item) for item in payload.get("mode_history", []) if str(item).strip()],
            source_history=[str(item) for item in payload.get("source_history", []) if str(item).strip()],
            title=str(payload.get("title", "")),
            status=str(payload.get("status", "active")).strip() or "active",
            created_at=float(payload.get("created_at", time.time())),
            updated_at=float(payload.get("updated_at", time.time())),
            last_message_at=float(payload["last_message_at"]) if payload.get("last_message_at") is not None else None,
            message_count=int(payload.get("message_count", 0) or 0),
            last_message_preview=str(payload.get("last_message_preview", "")),
            last_user_message=str(payload.get("last_user_message", "")),
            last_assistant_message=str(payload.get("last_assistant_message", "")),
            working_summary=str(payload.get("working_summary", "")),
            context_notes=[str(item) for item in payload.get("context_notes", []) if str(item).strip()],
            memory_layers={
                **_default_memory_layers(),
                **(payload.get("memory_layers", {}) if isinstance(payload.get("memory_layers"), dict) else {}),
            },
            timeline=[dict(item) for item in payload.get("timeline", []) if isinstance(item, dict)],
            compaction_state={
                **_default_compaction_state(),
                **(payload.get("compaction_state", {}) if isinstance(payload.get("compaction_state"), dict) else {}),
            },
            gateway=payload.get("gateway", {}) if isinstance(payload.get("gateway"), dict) else {},
            latest_run=payload.get("latest_run") if isinstance(payload.get("latest_run"), dict) else None,
            metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {},
        )


class SessionRuntime:
    """Persisted session registry spanning chat threads and gateway sessions."""

    def __init__(
        self,
        storage_path: str | Path,
        *,
        event_log_path: str | Path | None = None,
        max_timeline_events: int = 40,
        max_context_notes: int = 12,
        max_note_chars: int = 1200,
        max_timeline_chars: int = 6000,
        event_bus: Any | None = None,
    ):
        self._storage_path = Path(storage_path).resolve()
        self._event_log_path = (
            Path(event_log_path).resolve()
            if event_log_path is not None
            else self._storage_path.with_name(f"{self._storage_path.stem}.events.jsonl")
        )
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionRecord] = {}
        self._max_timeline_events = max(1, int(max_timeline_events))
        self._max_context_notes = max(1, int(max_context_notes))
        self._max_note_chars = max(16, int(max_note_chars))
        self._max_timeline_chars = max(128, int(max_timeline_chars))
        self._event_bus = None
        self._attached_bus_ids: set[int] = set()
        self._is_replaying = False
        self._kernels: dict[str, SessionKernel] = {}
        self._artifact_cache: dict[str, dict[str, Any]] = {}
        self._load_unlocked()
        scrubbed_sessions = self._scrub_resume_state_unlocked()
        if scrubbed_sessions:
            self._persist_unlocked()
            for session_key in scrubbed_sessions:
                record = self._sessions.get(session_key)
                if record is None:
                    continue
                self._append_event_unlocked(
                    record,
                    op="resume_scrubbed",
                    payload={"session_key": session_key, "thread_id": record.thread_id},
                )
        if event_bus is not None:
            self.attach_event_bus(event_bus)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [record.to_dict() for record in self._sessions.values()]
        return sorted(
            items,
            key=lambda item: item.get("updated_at") or item.get("last_message_at") or item.get("created_at", 0),
            reverse=True,
        )

    def get_session(self, session_key: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            return record.to_dict() if record is not None else None

    def get_timeline(
        self,
        session_key: str,
        *,
        kind: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            if record is None:
                return []
            items = list(record.timeline)
        normalized_kind = str(kind).strip()
        if normalized_kind:
            items = [item for item in items if str(item.get("kind", "")).strip() == normalized_kind]
        return items[-max(1, int(limit)) :]

    def get_event_log(
        self,
        session_key: str,
        *,
        op: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        normalized_session_key = str(session_key).strip()
        normalized_op = str(op).strip()
        if not normalized_session_key:
            return []
        with self._lock:
            items = self._read_event_log_unlocked(session_key=normalized_session_key, op=normalized_op, limit=limit)
        return items

    def get_file_views(self, session_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            if record is None:
                return []
            workspace_layer = record.memory_layers.get("workspace", {})
            file_views = workspace_layer.get("file_views", {})
            recent = list(file_views.get("recent", [])) if isinstance(file_views, dict) else []
        return recent[-max(1, int(limit)) :]

    def get_sidechains(self, session_key: str) -> list[dict[str, Any]]:
        with self._lock:
            normalized = str(session_key).strip()
            if normalized not in self._sessions:
                return []
            kernel = self._kernel_unlocked(normalized)
            return [item.to_dict() for item in kernel.sidechains.values()]

    def get_kernel_snapshot(self, session_key: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            if record is None:
                return None
            return self._kernel_unlocked(record.session_key).snapshot()

    def get_compiled_artifacts(
        self,
        session_key: str,
        *,
        invalidate: bool = False,
        reason: str = "",
    ) -> dict[str, Any] | None:
        normalized = str(session_key).strip()
        with self._lock:
            record = self._sessions.get(normalized)
            if record is None:
                return None
            if invalidate:
                self._invalidate_artifacts_unlocked(
                    normalized,
                    reason=reason or "manual",
                    scopes=["compiled_artifacts"],
                )
            cached = self._artifact_cache.get(normalized)
            kernel = self._kernel_unlocked(normalized)
            if cached is not None and int(cached.get("artifact_version", -1)) == kernel.artifact_version:
                return dict(cached)
            compiled = compile_session_artifacts(record, kernel)
            kernel.file_view_projection = dict(compiled.get("file_view_projection", {}))
            kernel.mutable_artifacts["compiled_checksums"] = {
                "system_context": _stable_text_hash(
                    json.dumps(compiled.get("system_context", {}), ensure_ascii=False, default=str)
                ),
                "user_context": _stable_text_hash(
                    json.dumps(compiled.get("user_context", {}), ensure_ascii=False, default=str)
                ),
                "session_notebook_projection": _stable_text_hash(
                    json.dumps(compiled.get("session_notebook_projection", {}), ensure_ascii=False, default=str)
                ),
                "tool_projection": _stable_text_hash(
                    json.dumps(compiled.get("tool_projection", {}), ensure_ascii=False, default=str)
                ),
                "file_view_projection": _stable_text_hash(
                    json.dumps(compiled.get("file_view_projection", {}), ensure_ascii=False, default=str)
                ),
            }
            kernel.last_compiled_at = time.time()
            self._artifact_cache[normalized] = dict(compiled)
            return compiled

    def invalidate_artifacts(
        self,
        session_key: str,
        *,
        reason: str,
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized = str(session_key).strip()
        with self._lock:
            if normalized not in self._sessions:
                raise KeyError(f"unknown session: {session_key}")
            self._invalidate_artifacts_unlocked(normalized, reason=reason, scopes=scopes or [])
            kernel = self._kernel_unlocked(normalized)
            return {
                "session_key": normalized,
                "artifact_version": kernel.artifact_version,
                "invalidations": list(kernel.invalidations),
            }

    def rebuild_checkpoint(self) -> dict[str, Any]:
        with self._lock:
            self._persist_unlocked()
            return {
                "session_count": len(self._sessions),
                "storage_path": str(self._storage_path),
                "event_log_path": str(self._event_log_path),
            }

    def set_prompt_injection(self, session_key: str, *, prompt_injection: str) -> dict[str, Any]:
        normalized = str(session_key).strip()
        with self._lock:
            record = self._sessions.get(normalized)
            if record is None:
                raise KeyError(f"unknown session: {session_key}")
            metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
            metadata["prompt_injection"] = str(prompt_injection).strip()
            record.metadata = metadata
            record.updated_at = time.time()
            self._invalidate_artifacts_unlocked(
                normalized,
                reason="prompt_injection_updated",
                scopes=["prompt_injection", "system_context", "compiled_artifacts"],
            )
            self._kernel_unlocked(normalized).mutable_artifacts["prompt_injection"] = metadata["prompt_injection"]
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="prompt_injection_updated",
                payload={"prompt_injection": metadata["prompt_injection"]},
                timestamp=record.updated_at,
            )
            return record.to_dict()

    def _kernel_unlocked(self, session_key: str) -> SessionKernel:
        normalized = str(session_key).strip()
        kernel = self._kernels.get(normalized)
        if kernel is not None:
            return kernel
        kernel = SessionKernel(session_key=normalized)
        record = self._sessions.get(normalized)
        if record is not None:
            workspace_layer = (
                record.memory_layers.get("workspace", {}) if isinstance(record.memory_layers, dict) else {}
            )
            file_views = workspace_layer.get("file_views", {}) if isinstance(workspace_layer, dict) else {}
            kernel.file_view_projection = {
                "recent_paths": [
                    str(item.get("path", "")).strip()
                    for item in list(file_views.get("recent", []))[-8:]
                    if isinstance(item, dict) and str(item.get("path", "")).strip()
                ],
                "notebook_summary": str(file_views.get("notebook", {}).get("summary", "")).strip()
                if isinstance(file_views.get("notebook", {}), dict)
                else "",
            }
            prompt_injection = (
                str(record.metadata.get("prompt_injection", "")).strip() if isinstance(record.metadata, dict) else ""
            )
            if prompt_injection:
                kernel.mutable_artifacts["prompt_injection"] = prompt_injection
        self._kernels[normalized] = kernel
        return kernel

    def _invalidate_artifacts_unlocked(
        self,
        session_key: str,
        *,
        reason: str,
        scopes: list[str] | None = None,
    ) -> None:
        normalized = str(session_key).strip()
        kernel = self._kernel_unlocked(normalized)
        kernel.invalidate(reason=reason, scopes=scopes or [])
        self._artifact_cache.pop(normalized, None)

    def _note_usage_unlocked(self, session_key: str, counter_name: str, amount: int = 1) -> None:
        self._kernel_unlocked(session_key).note_usage(counter_name, amount)

    def _upsert_sidechain_unlocked(
        self,
        session_key: str,
        *,
        purpose: str,
        summary: str = "",
        status: str = "completed",
        metadata: dict[str, Any] | None = None,
        sidechain_id: str = "",
        timestamp: float | None = None,
        replaying: bool = False,
    ) -> dict[str, Any]:
        kernel = self._kernel_unlocked(session_key)
        sidechain = kernel.upsert_sidechain(
            purpose=purpose,
            summary=summary,
            status=status,
            metadata=metadata,
            sidechain_id=sidechain_id,
        )
        payload = sidechain.to_dict()
        if not replaying and session_key in self._sessions:
            self._append_event_unlocked(
                self._sessions[session_key],
                op="sidechain_upserted",
                payload=payload,
                timestamp=timestamp,
            )
        return payload

    def get_overview(self, session_key: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            if record is None:
                return None
            timeline = list(record.timeline)
            gateway = dict(record.gateway)
            latest_run = dict(record.latest_run) if isinstance(record.latest_run, dict) else None
            compaction_state = dict(record.compaction_state)
        counts_by_kind: dict[str, int] = {}
        latest_by_kind: dict[str, dict[str, Any]] = {}
        for item in timeline:
            kind = str(item.get("kind", "")).strip() or "event"
            counts_by_kind[kind] = counts_by_kind.get(kind, 0) + 1
            latest_by_kind[kind] = item
        return {
            "session_key": session_key,
            "counts": {
                "timeline_events": len(timeline),
                "notes": len(record.context_notes) if isinstance(record, SessionRecord) else 0,
                "messages": record.message_count if isinstance(record, SessionRecord) else 0,
                "by_kind": counts_by_kind,
            },
            "latest_by_kind": latest_by_kind,
            "latest_run": latest_run,
            "gateway": gateway,
            "compaction": compaction_state,
            "working_summary": record.working_summary if isinstance(record, SessionRecord) else "",
        }

    def get_session_for_thread(self, thread_id: str) -> dict[str, Any] | None:
        normalized = str(thread_id).strip()
        with self._lock:
            for record in self._sessions.values():
                if record.thread_id == normalized:
                    return record.to_dict()
        return None

    def delete_session(self, session_key: str) -> bool:
        with self._lock:
            removed = self._sessions.pop(str(session_key).strip(), None)
            if removed is None:
                return False
            self._persist_unlocked()
            return True

    def delete_session_for_thread(self, thread_id: str) -> bool:
        session_key = self.session_key_for_thread(thread_id)
        if session_key is None:
            return False
        return self.delete_session(session_key)

    def session_key_for_thread(self, thread_id: str) -> str | None:
        session = self.get_session_for_thread(thread_id)
        return str(session["session_key"]) if session is not None else None

    def ensure_session(
        self,
        *,
        session_key: str,
        thread_id: str,
        root_mode: str = "assistant",
        source: str = "chat",
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_session_key = str(session_key).strip()
        normalized_thread_id = str(thread_id).strip()
        normalized_mode = str(root_mode).strip() or "assistant"
        normalized_source = str(source).strip() or "chat"
        if not normalized_session_key or not normalized_thread_id:
            raise ValueError("session_key and thread_id are required")

        with self._lock:
            record = self._sessions.get(normalized_session_key)
            previous_mode = record.primary_mode if record is not None else ""
            if record is None:
                record = SessionRecord(
                    session_key=normalized_session_key,
                    thread_id=normalized_thread_id,
                    primary_mode=normalized_mode,
                    title=str(title).strip(),
                )
                self._sessions[normalized_session_key] = record
            record.thread_id = normalized_thread_id
            if not record.primary_mode or (
                record.primary_mode == "assistant" and normalized_mode and normalized_mode != "assistant"
            ):
                record.primary_mode = normalized_mode
            _append_unique(record.mode_history, normalized_mode)
            _append_unique(record.source_history, normalized_source)
            if title and not record.title:
                record.title = str(title).strip()
            if metadata:
                record.metadata = {**record.metadata, **dict(metadata)}
            record.updated_at = time.time()
            self._note_usage_unlocked(normalized_session_key, "session_ensure")
            if previous_mode and previous_mode != record.primary_mode:
                self._invalidate_artifacts_unlocked(
                    normalized_session_key,
                    reason="mode_changed",
                    scopes=["mode", "system_context", "compiled_artifacts"],
                )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="session_ensured",
                payload={
                    "source": normalized_source,
                    "root_mode": normalized_mode,
                    "title": record.title,
                },
            )
            return record.to_dict()

    def ensure_thread_session(
        self,
        thread_id: str,
        *,
        root_mode: str = "assistant",
        source: str = "chat",
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_key = self.session_key_for_thread(thread_id) or str(thread_id).strip()
        return self.ensure_session(
            session_key=session_key,
            thread_id=thread_id,
            root_mode=root_mode,
            source=source,
            title=title,
            metadata=metadata,
        )

    def bind_conversation(
        self,
        *,
        thread_id: str,
        title: str = "",
        message_count: int | None = None,
        last_message_at: float | None = None,
        root_mode: str = "assistant",
        source: str = "chat",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = self.ensure_thread_session(
            thread_id,
            root_mode=root_mode,
            source=source,
            title=title,
            metadata=metadata,
        )
        with self._lock:
            record = self._sessions[session["session_key"]]
            if title:
                record.title = str(title).strip()
            if message_count is not None:
                record.message_count = int(message_count)
            if last_message_at is not None:
                record.last_message_at = float(last_message_at)
            record.updated_at = time.time()
            self._invalidate_artifacts_unlocked(
                record.session_key,
                reason="conversation_bound",
                scopes=["system_context", "compiled_artifacts"],
            )
            self._persist_unlocked()
            return record.to_dict()

    def add_timeline_event(
        self,
        *,
        thread_id: str,
        kind: str,
        title: str,
        status: str = "",
        source: str = "",
        preview: str = "",
        run_id: str = "",
        metadata: dict[str, Any] | None = None,
        root_mode: str = "assistant",
        session_key: str | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        resolved_timestamp = float(timestamp) if timestamp is not None else time.time()
        if session_key:
            session = self.ensure_session(
                session_key=session_key,
                thread_id=thread_id,
                root_mode=root_mode,
                source=source or f"timeline:{kind}",
            )
        else:
            session = self.ensure_thread_session(
                thread_id,
                root_mode=root_mode,
                source=source or f"timeline:{kind}",
            )
        with self._lock:
            record = self._sessions[session["session_key"]]
            record.timeline.append(
                {
                    "timestamp": resolved_timestamp,
                    "kind": str(kind).strip(),
                    "title": str(title).strip(),
                    "status": str(status).strip(),
                    "source": str(source).strip(),
                    "preview": _preview_text(preview, limit=240) if preview else "",
                    "run_id": str(run_id).strip(),
                    "metadata": dict(metadata or {}),
                }
            )
            record.updated_at = resolved_timestamp
            self._apply_budget_unlocked(record, reason="timeline")
            self._note_usage_unlocked(record.session_key, "timeline_events")
            self._invalidate_artifacts_unlocked(
                record.session_key,
                reason="timeline_event",
                scopes=["timeline", "compiled_artifacts"],
            )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="timeline_event",
                payload={
                    "kind": str(kind).strip(),
                    "title": str(title).strip(),
                    "status": str(status).strip(),
                    "source": str(source).strip(),
                    "preview": _preview_text(preview, limit=240) if preview else "",
                    "run_id": str(run_id).strip(),
                    "metadata": dict(metadata or {}),
                },
                timestamp=resolved_timestamp,
            )
            return record.to_dict()

    def record_workflow_run(
        self,
        *,
        thread_id: str,
        workflow_id: str,
        workflow_name: str,
        run_id: str,
        status: str,
        source: str = "",
        preview: str = "",
        root_mode: str = "assistant",
        session_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        resolved_timestamp = float(timestamp) if timestamp is not None else time.time()
        if session_key:
            session = self.ensure_session(
                session_key=session_key,
                thread_id=thread_id,
                root_mode=root_mode,
                source=source or "workflow",
            )
        else:
            session = self.ensure_thread_session(
                thread_id,
                root_mode=root_mode,
                source=source or "workflow",
            )
        with self._lock:
            record = self._sessions[session["session_key"]]
            self._upsert_run_event_unlocked(
                record,
                kind="workflow_run",
                run_id=run_id,
                title=workflow_name or workflow_id or "workflow",
                status=status,
                source=source,
                preview=preview,
                metadata={
                    "workflow_id": str(workflow_id).strip(),
                    "workflow_name": str(workflow_name).strip(),
                    **dict(metadata or {}),
                },
                timestamp=resolved_timestamp,
            )
            record.updated_at = resolved_timestamp
            self._note_usage_unlocked(record.session_key, "workflow_runs")
            self._invalidate_artifacts_unlocked(
                record.session_key,
                reason="workflow_run_recorded",
                scopes=["timeline", "compiled_artifacts"],
            )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="workflow_run_recorded",
                payload={
                    "workflow_id": str(workflow_id).strip(),
                    "workflow_name": str(workflow_name).strip(),
                    "run_id": str(run_id).strip(),
                    "status": str(status).strip(),
                    "source": str(source).strip(),
                    "preview": _preview_text(preview, limit=240) if preview else "",
                    "metadata": dict(metadata or {}),
                },
                timestamp=resolved_timestamp,
            )
            return record.to_dict()

    def compact_session(self, session_key: str, *, reason: str = "manual") -> dict[str, Any]:
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            if record is None:
                raise KeyError(f"unknown session: {session_key}")
            self._apply_budget_unlocked(record, reason=reason, force=True)
            self._note_usage_unlocked(record.session_key, "manual_compactions")
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="session_compacted",
                payload={"reason": str(reason).strip() or "manual"},
            )
            return record.to_dict()

    def record_external_compaction(
        self,
        *,
        thread_id: str,
        summary: str,
        source: str = "middleware.summarization",
        reason: str = "conversation_compaction",
        message_count: int = 0,
        recent_window: int = 0,
        offload_path: str = "",
        session_key: str | None = None,
        root_mode: str = "assistant",
    ) -> dict[str, Any]:
        resolved_session = (
            self.ensure_session(
                session_key=session_key,
                thread_id=thread_id,
                root_mode=root_mode,
                source=source,
            )
            if session_key
            else self.ensure_thread_session(thread_id, root_mode=root_mode, source=source)
        )
        with self._lock:
            record = self._sessions[resolved_session["session_key"]]
            self._record_external_compaction_unlocked(
                record,
                summary=str(summary).strip(),
                source=str(source).strip() or "middleware.summarization",
                reason=str(reason).strip() or "conversation_compaction",
                message_count=message_count,
                recent_window=recent_window,
                offload_path=offload_path,
                timestamp=time.time(),
                replaying=False,
            )
            self._persist_unlocked()
            return record.to_dict()

    def record_message(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        root_mode: str = "assistant",
        source: str = "chat",
        session_key: str | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        resolved_timestamp = float(timestamp) if timestamp is not None else time.time()
        if session_key:
            session = self.ensure_session(
                session_key=session_key,
                thread_id=thread_id,
                root_mode=root_mode,
                source=source,
                title=_preview_text(content, limit=48) if role == "user" else "",
            )
        else:
            session = self.ensure_thread_session(
                thread_id,
                root_mode=root_mode,
                source=source,
                title=_preview_text(content, limit=48) if role == "user" else "",
            )
        with self._lock:
            record = self._sessions[session["session_key"]]
            preview = _preview_text(content)
            if role == "user":
                record.last_user_message = content
                if not record.title:
                    record.title = _preview_text(content, limit=48)
            elif role == "assistant":
                record.last_assistant_message = content
            record.message_count += 1
            record.last_message_preview = preview
            record.last_message_at = resolved_timestamp
            record.updated_at = resolved_timestamp
            self._note_usage_unlocked(record.session_key, "messages")
            self._invalidate_artifacts_unlocked(
                record.session_key,
                reason="message_recorded",
                scopes=["user_context", "compiled_artifacts"],
            )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="message_recorded",
                payload={
                    "role": str(role).strip(),
                    "source": str(source).strip(),
                    "preview": preview,
                    "content": content,
                },
                timestamp=resolved_timestamp,
            )
            return record.to_dict()

    def record_file_view(
        self,
        *,
        thread_id: str,
        path: str,
        root_mode: str = "assistant",
        source: str = "tool.read_file",
        session_key: str | None = None,
        tool_name: str = "read_file",
        preview: str = "",
        visible_content: str = "",
        raw_content: str = "",
        offset: int = 0,
        limit: int = 0,
        is_partial_view: bool | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        normalized_path = str(path).strip()
        if not normalized_path:
            raise ValueError("path is required")
        resolved_timestamp = float(timestamp) if timestamp is not None else time.time()
        partial = bool(is_partial_view) if is_partial_view is not None else bool(offset or limit)
        session = (
            self.ensure_session(
                session_key=session_key,
                thread_id=thread_id,
                root_mode=root_mode,
                source=source,
            )
            if session_key
            else self.ensure_thread_session(thread_id, root_mode=root_mode, source=source)
        )
        with self._lock:
            record = self._sessions[session["session_key"]]
            workspace_layer = record.memory_layers.setdefault("workspace", {})
            file_views = workspace_layer.setdefault(
                "file_views",
                {"recent": [], "notebook": {"summary": "", "entries": [], "last_updated_at": None}},
            )
            recent = list(file_views.get("recent", []))
            visible_excerpt = str(visible_content or preview).strip()
            raw_excerpt = str(raw_content).strip()
            entry = {
                "timestamp": resolved_timestamp,
                "path": normalized_path,
                "offset": max(0, int(offset or 0)),
                "limit": max(0, int(limit or 0)),
                "is_partial_view": partial,
                "view_kind": "partial" if partial else "full",
                "tool_name": str(tool_name).strip() or "read_file",
                "source": str(source).strip() or "tool.read_file",
                "preview": _preview_text(visible_excerpt, limit=180) if visible_excerpt else "",
                "visible_chars": len(visible_excerpt),
                "content_hash": _stable_text_hash(raw_excerpt or visible_excerpt),
                "view_hash": _stable_text_hash(
                    normalized_path,
                    max(0, int(offset or 0)),
                    max(0, int(limit or 0)),
                    partial,
                    visible_excerpt,
                ),
            }
            recent.append(entry)
            file_views["recent"] = recent[-24:]
            file_views["last_updated_at"] = resolved_timestamp
            self._kernel_unlocked(record.session_key).file_view_projection = {
                "recent_paths": [
                    str(item.get("path", "")).strip()
                    for item in file_views["recent"]
                    if isinstance(item, dict) and str(item.get("path", "")).strip()
                ][-8:],
                "notebook_summary": str(file_views.get("notebook", {}).get("summary", "")).strip()
                if isinstance(file_views.get("notebook", {}), dict)
                else "",
            }
            self._upsert_run_event_unlocked(
                record,
                kind="file_view",
                run_id="",
                title=normalized_path,
                status="captured",
                source=str(source).strip() or "tool.read_file",
                preview=entry["preview"],
                metadata={
                    "path": normalized_path,
                    "offset": entry["offset"],
                    "limit": entry["limit"],
                    "is_partial_view": partial,
                    "tool_name": entry["tool_name"],
                    "view_kind": entry["view_kind"],
                    "visible_chars": entry["visible_chars"],
                    "content_hash": entry["content_hash"],
                    "view_hash": entry["view_hash"],
                },
                timestamp=resolved_timestamp,
            )
            record.updated_at = resolved_timestamp
            self._note_usage_unlocked(record.session_key, "file_views")
            self._invalidate_artifacts_unlocked(
                record.session_key,
                reason="file_view_recorded",
                scopes=["file_views", "compiled_artifacts"],
            )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="file_view_recorded",
                payload=entry,
                timestamp=resolved_timestamp,
            )
            return record.to_dict()

    def bind_gateway_session(
        self,
        *,
        session_key: str,
        thread_id: str,
        mode: str,
        source: str,
        user: str = "",
        device_id: str = "",
        client_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_session(
            session_key=session_key,
            thread_id=thread_id,
            root_mode=mode,
            source=source,
            metadata=metadata,
        )
        with self._lock:
            record = self._sessions[str(session_key).strip()]
            gateway = dict(record.gateway)
            gateway["user"] = user or gateway.get("user", "")
            device_ids = list(gateway.get("device_ids", []))
            client_ids = list(gateway.get("client_ids", []))
            sources = list(gateway.get("sources", []))
            _append_unique(device_ids, device_id)
            _append_unique(client_ids, client_id)
            _append_unique(sources, source)
            gateway["device_ids"] = device_ids
            gateway["client_ids"] = client_ids
            gateway["sources"] = sources
            if metadata:
                gateway["metadata"] = {**gateway.get("metadata", {}), **dict(metadata)}
            record.gateway = gateway
            record.updated_at = time.time()
            self._note_usage_unlocked(record.session_key, "gateway_bindings")
            self._invalidate_artifacts_unlocked(
                record.session_key,
                reason="gateway_bound",
                scopes=["system_context", "gateway", "compiled_artifacts"],
            )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="gateway_bound",
                payload={
                    "mode": str(mode).strip(),
                    "source": str(source).strip(),
                    "user": str(user).strip(),
                    "device_id": str(device_id).strip(),
                    "client_id": str(client_id).strip(),
                },
            )
            return record.to_dict()

    def record_run(
        self,
        *,
        session_key: str,
        thread_id: str,
        run_id: str,
        mode: str,
        status: str,
        source: str,
        requested_model: str = "",
        display_input: str = "",
        output_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_session(
            session_key=session_key,
            thread_id=thread_id,
            root_mode=mode,
            source=source,
        )
        with self._lock:
            record = self._sessions[str(session_key).strip()]
            preview_source = output_text or display_input
            if not preview_source and isinstance(metadata, dict):
                preview_source = str(metadata.get("error", ""))
            record.latest_run = {
                "run_id": str(run_id).strip(),
                "mode": str(mode).strip() or record.primary_mode,
                "status": str(status).strip() or "in_progress",
                "source": str(source).strip(),
                "requested_model": str(requested_model).strip(),
                "display_input": display_input,
                "output_text": output_text,
                "updated_at": time.time(),
                "metadata": dict(metadata or {}),
            }
            updated_at = float(record.latest_run["updated_at"])
            self._upsert_run_event_unlocked(
                record,
                kind="gateway_run",
                run_id=str(run_id).strip(),
                title=_preview_text(display_input, limit=72) or str(requested_model).strip() or "gateway run",
                status=str(status).strip() or "in_progress",
                source=str(source).strip(),
                preview=_preview_text(preview_source, limit=240),
                metadata={
                    "requested_model": str(requested_model).strip(),
                    "source": str(source).strip(),
                    **dict(metadata or {}),
                },
                timestamp=updated_at,
            )
            record.updated_at = updated_at
            self._note_usage_unlocked(record.session_key, "gateway_runs")
            self._invalidate_artifacts_unlocked(
                record.session_key,
                reason="gateway_run_recorded",
                scopes=["timeline", "gateway_run", "compiled_artifacts"],
            )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="gateway_run_recorded",
                payload={
                    "run_id": str(run_id).strip(),
                    "mode": str(mode).strip(),
                    "status": str(status).strip(),
                    "source": str(source).strip(),
                    "requested_model": str(requested_model).strip(),
                    "display_input": display_input,
                    "output_text": output_text,
                    "metadata": dict(metadata or {}),
                },
                timestamp=updated_at,
            )
            return record.to_dict()

    def remember(
        self,
        session_key: str,
        *,
        note: str,
        layer: str = "session",
        memory_type: str = SESSION_MEMORY_TYPE,
        durable: bool = False,
        occurred_on: str = "",
        verified: bool = False,
    ) -> dict[str, Any]:
        decision = validate_session_memory(
            note=note,
            memory_type=memory_type,
            durable=durable,
            occurred_on=occurred_on,
            verified=verified,
        )
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            if record is None:
                raise KeyError(f"unknown session: {session_key}")
            normalized_layer = str(layer).strip() or "session"
            record.context_notes.append(decision.note)
            layer_state = record.memory_layers.setdefault(normalized_layer, {})
            layer_state["last_note"] = decision.note
            if decision.durable:
                entries = layer_state.setdefault("entries", [])
                entry = typed_memory_entry_payload(decision, layer=normalized_layer)
                entry["recorded_at"] = time.time()
                entries.append(entry)
                if len(entries) > 24:
                    layer_state["entries"] = entries[-24:]
            layer_state["last_note_type"] = decision.memory_type
            record.updated_at = time.time()
            if decision.durable:
                self._upsert_sidechain_unlocked(
                    record.session_key,
                    purpose="memory_extraction",
                    summary=f"{decision.memory_type} memory captured: {_preview_text(decision.note, limit=96)}",
                    metadata={
                        "memory_type": decision.memory_type,
                        "layer": normalized_layer,
                        "verified": decision.verified,
                    },
                    timestamp=record.updated_at,
                )
            self._apply_budget_unlocked(record, reason="notes")
            self._note_usage_unlocked(record.session_key, "memory_writes")
            self._invalidate_artifacts_unlocked(
                record.session_key,
                reason="memory_note_recorded",
                scopes=["memory", "user_context", "compiled_artifacts"],
            )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="memory_note_recorded",
                payload={
                    "note": decision.note,
                    "layer": normalized_layer,
                    "memory_type": decision.memory_type,
                    "durable": decision.durable,
                    "verified": decision.verified,
                    "occurred_on": decision.occurred_on,
                    "warnings": list(decision.warnings),
                },
            )
            return record.to_dict()

    def update_summary(
        self,
        session_key: str,
        *,
        summary: str,
        layer: str = "session",
    ) -> dict[str, Any]:
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            if record is None:
                raise KeyError(f"unknown session: {session_key}")
            record.working_summary = str(summary).strip()
            record.memory_layers.setdefault(layer, {})
            record.memory_layers[layer]["summary"] = record.working_summary
            record.updated_at = time.time()
            self._apply_budget_unlocked(record, reason="summary")
            self._invalidate_artifacts_unlocked(
                record.session_key,
                reason="summary_updated",
                scopes=["system_context", "session_notebook", "compiled_artifacts"],
            )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="summary_updated",
                payload={
                    "layer": str(layer).strip() or "session",
                    "summary": record.working_summary,
                },
            )
            return record.to_dict()

    def attach_event_bus(self, event_bus: Any) -> None:
        bus_id = id(event_bus)
        if bus_id in self._attached_bus_ids:
            return
        self._attached_bus_ids.add(bus_id)
        self._event_bus = event_bus
        try:
            from core.systems.runtime.event_bus import EventType

            for event_type in (
                EventType.TOOL_CALL,
                EventType.TOOL_RESULT,
                EventType.SUBAGENT_SPAWNED,
                EventType.SUBAGENT_COMPLETED,
                EventType.SUBAGENT_FAILED,
                EventType.SUBAGENT_TIMEOUT,
                EventType.SCHEDULE_RUN,
            ):
                event_bus.subscribe(event_type, self._handle_runtime_event, priority=-100)
        except Exception:
            self._attached_bus_ids.discard(bus_id)
            raise

    def sync_subagent_registry(self, registry: Any) -> None:
        records = registry.list_all() if registry is not None and hasattr(registry, "list_all") else []
        for item in records:
            payload = item.to_dict() if hasattr(item, "to_dict") else item
            if not isinstance(payload, dict):
                continue
            thread_id = str(payload.get("thread_id", "")).strip()
            if not thread_id:
                continue
            self.add_timeline_event(
                thread_id=thread_id,
                kind="delegated_subagent",
                title=str(payload.get("agent_name", "subagent")),
                status=str(payload.get("status", "")),
                source="subagent_registry.sync",
                preview=str(payload.get("last_response", "") or payload.get("error", "")),
                run_id=str(payload.get("run_id", "")),
                metadata=payload,
            )

    def sync_persistent_tasks(self, runtime: Any, *, root_mode: str = "admin") -> None:
        tasks = runtime.list_tasks() if runtime is not None and hasattr(runtime, "list_tasks") else []
        thread_id = str(getattr(getattr(runtime, "host_agent", None), "thread_id", "")).strip()
        if not thread_id:
            return
        for task in tasks:
            payload = task.to_dict() if hasattr(task, "to_dict") else task
            if not isinstance(payload, dict):
                continue
            current_step = payload.get("steps", [])
            preview = ""
            if isinstance(current_step, list):
                for step in current_step:
                    if isinstance(step, dict) and step.get("status") in {"pending", "running", "paused"}:
                        preview = str(step.get("description", ""))
                        break
            self.add_timeline_event(
                thread_id=thread_id,
                kind="durable_task",
                title=str(payload.get("name", "task")),
                status=str(payload.get("status", "")),
                source="admin_runtime.sync",
                preview=preview,
                run_id=str(payload.get("task_id", "")),
                metadata=payload,
                root_mode=root_mode,
            )

    def sync_gateway_runtime(self, gateway_runtime: Any) -> None:
        sessions = gateway_runtime.sessions.list() if gateway_runtime is not None else []
        runs = gateway_runtime.runs.list() if gateway_runtime is not None else []
        for item in sessions:
            if not isinstance(item, dict):
                continue
            session_key = str(item.get("session_key", "")).strip()
            if not session_key:
                continue
            mode = str(item.get("mode", "assistant")).strip() or "assistant"
            thread_id = str(item.get("thread_id", "")).strip()
            if not thread_id and mode:
                thread_id = f"gateway-{mode}-{session_key}"
            if not thread_id:
                continue
            device_ids = [str(value).strip() for value in item.get("device_ids", []) if str(value).strip()]
            client_ids = [str(value).strip() for value in item.get("client_ids", []) if str(value).strip()]
            self.bind_gateway_session(
                session_key=session_key,
                thread_id=thread_id,
                mode=mode,
                source=str(item.get("last_source", "gateway")).strip() or "gateway",
                user=str(item.get("user", "")).strip(),
                device_id=device_ids[0] if device_ids else "",
                client_id=client_ids[0] if client_ids else "",
                metadata=item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {},
            )
        for item in runs:
            if not isinstance(item, dict):
                continue
            self.record_run(
                session_key=str(item.get("session_key", "")).strip(),
                thread_id=str(item.get("thread_id", "")).strip(),
                run_id=str(item.get("run_id", "")).strip() or str(item.get("response_id", "")).strip(),
                mode=str(item.get("mode", "assistant")).strip() or "assistant",
                status=str(item.get("status", "in_progress")).strip() or "in_progress",
                source=str(item.get("source", "gateway")).strip() or "gateway",
                requested_model=str(item.get("requested_model", "")).strip(),
                display_input=str(item.get("display_input", "")),
                output_text=str(item.get("output_text", "")),
                metadata=item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {},
            )

    def sync_workflow_runtime(self, workflow_engine: Any) -> None:
        execution_runtime = getattr(workflow_engine, "execution_runtime", None)
        run_history = getattr(execution_runtime, "run_history", []) if execution_runtime is not None else []
        for item in run_history:
            payload = item.to_dict() if hasattr(item, "to_dict") else item
            if not isinstance(payload, dict):
                continue
            thread_id = str(payload.get("thread_id", "")).strip()
            if not thread_id:
                continue
            self.record_workflow_run(
                thread_id=thread_id,
                session_key=str(payload.get("session_key", "")).strip() or None,
                workflow_id=str(payload.get("workflow_id", "")).strip(),
                workflow_name=str(payload.get("workflow_name", "")).strip(),
                run_id=str(payload.get("run_id", "")).strip(),
                status=str(payload.get("status", "")).strip() or "completed",
                source=str(payload.get("source", "workflow")).strip() or "workflow",
                preview=str(payload.get("error", "") or payload.get("status", "")),
                root_mode=str(payload.get("root_mode", "assistant")).strip() or "assistant",
                metadata={
                    "completed_nodes": payload.get("completed_nodes", 0),
                    "total_nodes": payload.get("total_nodes", 0),
                    "error": payload.get("error"),
                },
                timestamp=float(payload.get("created_at", time.time()) or time.time()),
            )

    def sync_conversations(self, conversation_store: Any) -> None:
        items = conversation_store.list_conversations() if conversation_store is not None else []
        for item in items:
            if not isinstance(item, dict):
                continue
            thread_id = str(item.get("thread_id", "")).strip()
            if not thread_id:
                continue
            self.bind_conversation(
                thread_id=thread_id,
                title=str(item.get("title", "")),
                message_count=int(item.get("message_count", 0) or 0),
                last_message_at=float(item["last_message_at"]) if item.get("last_message_at") is not None else None,
                root_mode="assistant",
                source="conversation_store",
            )

    def _handle_runtime_event(self, event: Any) -> None:
        payload = event.payload if isinstance(getattr(event, "payload", None), dict) else {}
        thread_id = str(getattr(event, "session_id", "") or payload.get("thread_id", "")).strip()
        if not thread_id:
            return

        event_type = str(getattr(getattr(event, "type", None), "value", getattr(event, "type", ""))).strip()
        source = str(getattr(event, "source", "")).strip()
        timestamp = float(getattr(event, "timestamp", time.time()) or time.time())

        if event_type in {"tool_call", "tool_result"}:
            self.add_timeline_event(
                thread_id=thread_id,
                kind="tool_run",
                title=str(payload.get("tool_name", "tool")),
                status=str(payload.get("status", "")) or ("completed" if event_type == "tool_result" else "started"),
                source=source or event_type,
                preview=str(payload.get("preview", "") or payload.get("error", "")),
                run_id=str(payload.get("run_id", "") or payload.get("tool_call_id", "")),
                metadata=payload,
                root_mode=str(payload.get("root_mode", "assistant")),
                timestamp=timestamp,
            )
            if event_type == "tool_result":
                file_view = _extract_file_view_from_tool_payload(str(payload.get("tool_name", "")), payload)
                if file_view is not None:
                    self.record_file_view(
                        thread_id=thread_id,
                        session_key=str(payload.get("session_key", "")).strip() or None,
                        path=str(file_view["path"]),
                        root_mode=str(payload.get("root_mode", "assistant")),
                        source=source or "tool_result",
                        tool_name=str(file_view["tool_name"]),
                        preview=str(file_view["preview"]),
                        offset=int(file_view["offset"]),
                        limit=int(file_view["limit"]),
                        is_partial_view=bool(file_view["is_partial_view"]),
                        timestamp=timestamp,
                    )
            return

        if event_type.startswith("subagent."):
            self.add_timeline_event(
                thread_id=thread_id,
                kind="delegated_subagent",
                title=str(payload.get("agent_name", "subagent")),
                status=str(payload.get("status", event_type.split(".")[-1])),
                source=source or event_type,
                preview=str(payload.get("last_response", "") or payload.get("error", "")),
                run_id=str(payload.get("run_id", "")),
                metadata=payload,
                root_mode=str(payload.get("root_mode", "assistant")),
                timestamp=timestamp,
            )
            return

        if event_type == "schedule_run":
            self.add_timeline_event(
                thread_id=thread_id,
                kind=str(payload.get("run_kind", "durable_task")),
                title=str(payload.get("task_name", payload.get("name", "task"))),
                status=str(payload.get("status", payload.get("task_status", ""))),
                source=source or event_type,
                preview=str(payload.get("preview", "") or payload.get("step_description", "")),
                run_id=str(payload.get("task_id", payload.get("run_id", ""))),
                metadata=payload,
                root_mode=str(payload.get("root_mode", "admin")),
                timestamp=timestamp,
            )

    def _upsert_run_event_unlocked(
        self,
        record: SessionRecord,
        *,
        kind: str,
        run_id: str,
        title: str,
        status: str,
        source: str,
        preview: str,
        metadata: dict[str, Any] | None,
        timestamp: float,
    ) -> None:
        normalized_run_id = str(run_id).strip()
        event_payload = {
            "timestamp": float(timestamp),
            "kind": str(kind).strip(),
            "title": str(title).strip(),
            "status": str(status).strip(),
            "source": str(source).strip(),
            "preview": _preview_text(preview, limit=240) if preview else "",
            "run_id": normalized_run_id,
            "metadata": dict(metadata or {}),
        }
        if not normalized_run_id:
            record.timeline.append(event_payload)
            self._apply_budget_unlocked(record, reason="timeline")
            return

        for index, existing in enumerate(record.timeline):
            if (
                str(existing.get("kind", "")).strip() == event_payload["kind"]
                and str(existing.get("run_id", "")).strip() == normalized_run_id
            ):
                record.timeline.pop(index)
                break
        record.timeline.append(event_payload)
        self._apply_budget_unlocked(record, reason="timeline")

    def _append_event_unlocked(
        self,
        record: SessionRecord,
        *,
        op: str,
        payload: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> None:
        if self._is_replaying:
            return
        event_payload = {
            "timestamp": float(timestamp) if timestamp is not None else time.time(),
            "session_key": record.session_key,
            "thread_id": record.thread_id,
            "op": str(op).strip(),
            "payload": dict(payload or {}),
        }
        try:
            self._event_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._event_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event_payload, ensure_ascii=False, default=str))
                handle.write("\n")
        except Exception:
            pass

    def _read_event_log_unlocked(
        self,
        *,
        session_key: str,
        op: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not self._event_log_path.exists():
            return []
        try:
            lines = self._event_log_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        normalized_op = str(op).strip()
        items: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("session_key", "")).strip() != session_key:
                continue
            if normalized_op and str(payload.get("op", "")).strip() != normalized_op:
                continue
            items.append(payload)
            if len(items) >= max(1, int(limit)):
                break
        items.reverse()
        return items

    def _scrub_resume_state_unlocked(self) -> list[str]:
        scrubbed_sessions: list[str] = []
        transient_kinds = {"tool_run", "delegated_subagent", "durable_task", "gateway_run", "workflow_run"}
        now = time.time()
        for session_key, record in self._sessions.items():
            scrubbed_count = 0
            if isinstance(record.latest_run, dict):
                latest_status = str(record.latest_run.get("status", "")).strip()
                scrubbed_status = self._scrub_status_for_resume("gateway_run", latest_status)
                if scrubbed_status != latest_status:
                    record.latest_run["status"] = scrubbed_status
                    metadata = record.latest_run.setdefault("metadata", {})
                    metadata["resume_scrubbed"] = True
                    metadata["resume_scrubbed_from"] = latest_status
                    scrubbed_count += 1
            for item in record.timeline:
                kind = str(item.get("kind", "")).strip()
                if kind not in transient_kinds:
                    continue
                current_status = str(item.get("status", "")).strip()
                scrubbed_status = self._scrub_status_for_resume(kind, current_status)
                if scrubbed_status == current_status:
                    continue
                item["status"] = scrubbed_status
                metadata = dict(item.get("metadata", {})) if isinstance(item.get("metadata"), dict) else {}
                metadata["resume_scrubbed"] = True
                metadata["resume_scrubbed_from"] = current_status
                metadata["resume_scrubbed_at"] = now
                item["metadata"] = metadata
                if not str(item.get("preview", "")).strip():
                    item["preview"] = f"Recovered {kind} after interrupted runtime"
                scrubbed_count += 1
            if scrubbed_count:
                record.updated_at = now
                state = record.compaction_state
                state["resume_scrubbed_events"] = int(state.get("resume_scrubbed_events", 0)) + scrubbed_count
                scrubbed_sessions.append(session_key)
        return scrubbed_sessions

    @staticmethod
    def _scrub_status_for_resume(kind: str, status: str) -> str:
        normalized_kind = str(kind).strip()
        normalized_status = str(status).strip()
        if not normalized_kind or not normalized_status:
            return normalized_status
        final_statuses = {
            "completed",
            "failed",
            "error",
            "cancelled",
            "rejected",
            "approved",
            "resolved",
            "timeout",
            "timed_out",
        }
        paused_statuses = {"paused", "waiting_approval", "pending", "needs_approval"}
        transient_statuses = {"started", "running", "in_progress", "streaming"}
        if normalized_status in final_statuses:
            return normalized_status
        if normalized_status in paused_statuses:
            return "paused"
        if normalized_status in transient_statuses:
            return "interrupted"
        return normalized_status

    def _apply_budget_unlocked(self, record: SessionRecord, *, reason: str, force: bool = False) -> None:
        tool_transcript = self._compact_tool_transcript_unlocked(record, force=force)
        file_views = self._compact_file_views_unlocked(record, force=force)
        micro = self._microcompact_timeline_unlocked(record)
        notebook = self._compact_notes_into_notebook_unlocked(record, force=force)
        trimmed_notes, trimmed_events = self._trim_budget_unlocked(record, force=force)

        if (
            not tool_transcript["events"]
            and not file_views["views"]
            and not micro["previews"]
            and not micro["metadata"]
            and not notebook["entries"]
            and not trimmed_notes
            and not trimmed_events
        ):
            return

        summary = self._build_layered_compaction_summary(
            compacted_tool_events=tool_transcript["events"],
            tool_notebook_entries=tool_transcript["entries"],
            tool_notebook_summary=tool_transcript["summary"],
            compacted_file_views=file_views["views"],
            file_view_notebook_entries=file_views["entries"],
            file_view_notebook_summary=file_views["summary"],
            micro_preview_count=micro["previews"],
            micro_metadata_count=micro["metadata"],
            notebook_entry_count=notebook["entries"],
            notebook_summary=notebook["summary"],
            trimmed_notes=trimmed_notes,
            trimmed_events=trimmed_events,
        )
        boundary = create_compaction_boundary(
            source="session_runtime",
            reason=reason,
            summary=summary,
            notebook_summary=notebook["summary"],
            source_event_range={
                "compacted_tool_events": tool_transcript["events"],
                "compacted_file_views": file_views["views"],
                "trimmed_timeline_events": len(trimmed_events),
                "trimmed_notes": len(trimmed_notes),
                "earliest_trimmed_event_ts": (
                    min(float(item.get("timestamp", 0) or 0) for item in trimmed_events) if trimmed_events else None
                ),
                "latest_trimmed_event_ts": (
                    max(float(item.get("timestamp", 0) or 0) for item in trimmed_events) if trimmed_events else None
                ),
            },
            retained_recent_window={
                "timeline_events": len(record.timeline),
                "context_notes": len(record.context_notes),
                "file_views": len(record.memory_layers.get("workspace", {}).get("file_views", {}).get("recent", [])),
            },
        )
        state = record.compaction_state
        state["compacted_tool_events"] = int(state.get("compacted_tool_events", 0)) + tool_transcript["events"]
        state["tool_notebook_entries"] = int(state.get("tool_notebook_entries", 0)) + tool_transcript["entries"]
        state["compacted_file_views"] = int(state.get("compacted_file_views", 0)) + file_views["views"]
        state["file_view_notebook_entries"] = int(state.get("file_view_notebook_entries", 0)) + file_views["entries"]
        state["compacted_notes"] = int(state.get("compacted_notes", 0)) + len(trimmed_notes)
        state["compacted_timeline_events"] = int(state.get("compacted_timeline_events", 0)) + len(trimmed_events)
        state["microcompacted_previews"] = int(state.get("microcompacted_previews", 0)) + micro["previews"]
        state["microcompacted_metadata"] = int(state.get("microcompacted_metadata", 0)) + micro["metadata"]
        state["notebook_entries"] = int(state.get("notebook_entries", 0)) + notebook["entries"]
        state["last_compacted_at"] = time.time()
        state["last_reason"] = reason
        history = state.setdefault("history", [])
        history.append(
            {
                "timestamp": state["last_compacted_at"],
                "reason": reason,
                "compacted_tool_events": tool_transcript["events"],
                "tool_notebook_entries": tool_transcript["entries"],
                "compacted_file_views": file_views["views"],
                "file_view_notebook_entries": file_views["entries"],
                "microcompacted_previews": micro["previews"],
                "microcompacted_metadata": micro["metadata"],
                "notebook_entries": notebook["entries"],
                "trimmed_notes": len(trimmed_notes),
                "trimmed_timeline_events": len(trimmed_events),
                "summary": summary,
            }
        )
        if len(history) > 12:
            state["history"] = history[-12:]
        self._append_compaction_boundary_unlocked(record, boundary)

        session_layer = record.memory_layers.setdefault("session", {})
        session_layer["compaction"] = {
            "summary": summary,
            "last_reason": reason,
            "compacted_tool_events": state["compacted_tool_events"],
            "tool_notebook_entries": state["tool_notebook_entries"],
            "compacted_file_views": state["compacted_file_views"],
            "file_view_notebook_entries": state["file_view_notebook_entries"],
            "compacted_notes": state["compacted_notes"],
            "compacted_timeline_events": state["compacted_timeline_events"],
            "microcompacted_previews": state["microcompacted_previews"],
            "microcompacted_metadata": state["microcompacted_metadata"],
            "notebook_entries": state["notebook_entries"],
            "last_compacted_at": state["last_compacted_at"],
            "boundary_id": boundary.boundary_id,
        }
        if not self._is_replaying:
            self._upsert_sidechain_unlocked(
                record.session_key,
                purpose="context_compaction",
                summary=summary or f"Compaction boundary {boundary.boundary_id}",
                metadata={
                    "boundary_id": boundary.boundary_id,
                    "reason": reason,
                    "compacted_tool_events": tool_transcript["events"],
                    "compacted_file_views": file_views["views"],
                    "trimmed_timeline_events": len(trimmed_events),
                    "trimmed_notes": len(trimmed_notes),
                },
                timestamp=state["last_compacted_at"],
            )
        if summary:
            if not record.working_summary:
                record.working_summary = summary
            elif summary not in record.working_summary:
                record.working_summary = f"{record.working_summary}\n{summary}"[-2400:]
        self._invalidate_artifacts_unlocked(
            record.session_key,
            reason=f"compaction:{reason}",
            scopes=["compaction", "session_notebook", "tool_projection", "file_views", "compiled_artifacts"],
        )

        if self._event_bus is not None:
            try:
                from core.systems.runtime.event_bus import Event, EventType

                self._event_bus.emit(
                    Event(
                        type=EventType.CONTEXT_COMPACT,
                        source="SessionRuntime",
                        session_id=record.thread_id,
                        payload={
                            "thread_id": record.thread_id,
                            "session_key": record.session_key,
                            "reason": reason,
                            "compacted_tool_events": tool_transcript["events"],
                            "tool_notebook_entries": tool_transcript["entries"],
                            "compacted_file_views": file_views["views"],
                            "file_view_notebook_entries": file_views["entries"],
                            "microcompacted_previews": micro["previews"],
                            "microcompacted_metadata": micro["metadata"],
                            "notebook_entries": notebook["entries"],
                            "trimmed_notes": len(trimmed_notes),
                            "trimmed_timeline_events": len(trimmed_events),
                            "summary": summary,
                        },
                    )
                )
            except Exception:
                pass
        self._append_event_unlocked(
            record,
            op="context_compacted",
            payload={
                "boundary_id": boundary.boundary_id,
                "reason": reason,
                "compacted_tool_events": tool_transcript["events"],
                "tool_notebook_entries": tool_transcript["entries"],
                "compacted_file_views": file_views["views"],
                "file_view_notebook_entries": file_views["entries"],
                "microcompacted_previews": micro["previews"],
                "microcompacted_metadata": micro["metadata"],
                "notebook_entries": notebook["entries"],
                "trimmed_notes": len(trimmed_notes),
                "trimmed_timeline_events": len(trimmed_events),
                "summary": summary,
            },
            timestamp=state["last_compacted_at"],
        )

    def _append_compaction_boundary_unlocked(self, record: SessionRecord, boundary: Any) -> None:
        state = record.compaction_state
        boundaries = state.setdefault("boundaries", [])
        boundaries.append(boundary.to_dict() if hasattr(boundary, "to_dict") else dict(boundary))
        if len(boundaries) > 16:
            state["boundaries"] = boundaries[-16:]

    def _record_external_compaction_unlocked(
        self,
        record: SessionRecord,
        *,
        summary: str,
        source: str,
        reason: str,
        message_count: int,
        recent_window: int,
        offload_path: str,
        timestamp: float,
        replaying: bool,
    ) -> None:
        if not summary:
            return
        record.updated_at = timestamp
        if not record.working_summary:
            record.working_summary = summary
        elif summary not in record.working_summary:
            record.working_summary = f"{record.working_summary}\n{summary}"[-2400:]
        boundary = create_compaction_boundary(
            source=source,
            reason=reason,
            summary=summary,
            source_event_range={"message_count": int(message_count or 0)},
            retained_recent_window={
                "recent_window_messages": int(recent_window or 0),
                "timeline_events": len(record.timeline),
                "context_notes": len(record.context_notes),
            },
        )
        self._append_compaction_boundary_unlocked(record, boundary)
        record.timeline.append(
            {
                "timestamp": timestamp,
                "kind": "conversation_compaction",
                "title": "Conversation compacted",
                "status": "completed",
                "source": source,
                "preview": _preview_text(summary, limit=240),
                "run_id": "",
                "metadata": {
                    "boundary_id": boundary.boundary_id,
                    "reason": reason,
                    "message_count": int(message_count or 0),
                    "recent_window": int(recent_window or 0),
                    "offload_path": offload_path,
                },
            }
        )
        self._apply_budget_unlocked(record, reason=reason)
        if not replaying:
            self._upsert_sidechain_unlocked(
                record.session_key,
                purpose="conversation_compaction",
                summary=summary,
                metadata={
                    "boundary_id": boundary.boundary_id,
                    "reason": reason,
                    "message_count": int(message_count or 0),
                    "recent_window": int(recent_window or 0),
                    "offload_path": offload_path,
                },
                timestamp=timestamp,
            )
        if not replaying:
            self._append_event_unlocked(
                record,
                op="external_compaction_recorded",
                payload={
                    "summary": summary,
                    "source": source,
                    "reason": reason,
                    "message_count": int(message_count or 0),
                    "recent_window": int(recent_window or 0),
                    "offload_path": offload_path,
                },
                timestamp=timestamp,
            )

    def _microcompact_timeline_unlocked(self, record: SessionRecord) -> dict[str, int]:
        preview_count = 0
        metadata_count = 0
        for item in record.timeline:
            preview = str(item.get("preview", ""))
            compacted_preview = _preview_text(preview, limit=120) if preview else ""
            if compacted_preview and compacted_preview != preview:
                item["preview"] = compacted_preview
                preview_count += 1
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                compacted_metadata = self._compact_json_value(metadata, limit=180, max_items=8)
                if compacted_metadata != metadata:
                    item["metadata"] = compacted_metadata
                    metadata_count += 1
        return {"previews": preview_count, "metadata": metadata_count}

    def _compact_tool_transcript_unlocked(
        self,
        record: SessionRecord,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        tool_indexes = [
            index for index, item in enumerate(record.timeline) if str(item.get("kind", "")).strip() == "tool_run"
        ]
        keep_recent = min(8, max(3, self._max_timeline_events // 3))
        eligible_indexes = tool_indexes[:-keep_recent] if len(tool_indexes) > keep_recent else []
        if force and not eligible_indexes and tool_indexes:
            eligible_indexes = tool_indexes[:1]
        if not eligible_indexes:
            return {"entries": 0, "events": 0, "summary": ""}

        summarized: list[str] = []
        compacted_events = 0
        for index in eligible_indexes:
            item = record.timeline[index]
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            preview = str(item.get("preview", "")).strip()
            metadata_size = len(json.dumps(metadata, ensure_ascii=False, default=str)) if metadata else 0
            if not force and metadata_size < 240 and len(preview) < 120:
                continue
            tool_name = str(item.get("title", metadata.get("tool_name", "tool"))).strip() or "tool"
            status = str(item.get("status", "")).strip() or "completed"
            args = metadata.get("args", {}) if isinstance(metadata.get("args"), dict) else {}
            path_hint = ""
            for key in ("path", "file_path", "relative_path", "target_path"):
                candidate = str(args.get(key, "")).strip()
                if candidate:
                    path_hint = candidate
                    break
            summary = f"{tool_name}[{status}]"
            if path_hint:
                summary = f"{summary} {path_hint}"
            if preview:
                summary = f"{summary} -> {_preview_text(preview, limit=90)}"
            summarized.append(summary)
            item["preview"] = _preview_text(preview, limit=90) if preview else ""
            item["metadata"] = {
                "tool_name": tool_name,
                "status": status,
                "compacted": True,
                "had_args": bool(args),
                "path_hint": path_hint,
            }
            compacted_events += 1

        if not compacted_events:
            return {"entries": 0, "events": 0, "summary": ""}

        notebook_summary = "; ".join(summarized[-4:])
        session_layer = record.memory_layers.setdefault("session", {})
        notebook = session_layer.setdefault(
            "tool_transcript",
            {"summary": "", "entries": [], "last_updated_at": None},
        )
        entries = list(notebook.get("entries", []))
        entries.append(
            {
                "timestamp": time.time(),
                "kind": "tool_batch",
                "count": compacted_events,
                "samples": summarized[-6:],
                "summary": notebook_summary,
            }
        )
        notebook["entries"] = entries[-16:]
        notebook_parts = [str(notebook.get("summary", "")).strip(), notebook_summary]
        notebook["summary"] = "\n".join(part for part in notebook_parts if part)[-3200:]
        notebook["last_updated_at"] = time.time()
        if not self._is_replaying:
            self._upsert_sidechain_unlocked(
                record.session_key,
                purpose="tool_result_distillation",
                summary=notebook_summary,
                metadata={"compacted_events": compacted_events, "samples": summarized[-4:]},
            )
        return {"entries": 1, "events": compacted_events, "summary": notebook_summary}

    def _compact_file_views_unlocked(
        self,
        record: SessionRecord,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        workspace_layer = record.memory_layers.setdefault("workspace", {})
        file_views = workspace_layer.setdefault(
            "file_views",
            {
                "recent": [],
                "notebook": {"summary": "", "entries": [], "last_updated_at": None},
                "last_updated_at": None,
            },
        )
        recent = list(file_views.get("recent", []))
        keep_recent = 6
        if len(recent) <= keep_recent and not force:
            return {"entries": 0, "views": 0, "summary": ""}

        compacted = recent[:-keep_recent] if len(recent) > keep_recent else []
        if force and not compacted and recent:
            compacted = recent[:1]
        if not compacted:
            return {"entries": 0, "views": 0, "summary": ""}

        file_views["recent"] = recent[len(compacted) :]
        summarized = []
        for item in compacted:
            path = str(item.get("path", "")).strip()
            offset = int(item.get("offset", 0) or 0)
            limit = int(item.get("limit", 0) or 0)
            partial = bool(item.get("is_partial_view"))
            path_label = Path(path).name if path else "(unknown)"
            view_shape = f"@{offset}" if offset else ""
            if limit:
                view_shape = f"{view_shape}+{limit}" if view_shape else f"+{limit}"
            partial_label = "partial" if partial else "full"
            summarized.append(f"{path_label} {partial_label}{view_shape}")

        notebook_summary = "; ".join(summarized[-5:])
        notebook = file_views.setdefault(
            "notebook",
            {"summary": "", "entries": [], "last_updated_at": None},
        )
        entries = list(notebook.get("entries", []))
        entries.append(
            {
                "timestamp": time.time(),
                "kind": "file_view_batch",
                "count": len(compacted),
                "samples": compacted[-6:],
                "summary": notebook_summary,
            }
        )
        notebook["entries"] = entries[-16:]
        notebook_parts = [str(notebook.get("summary", "")).strip(), notebook_summary]
        notebook["summary"] = "\n".join(part for part in notebook_parts if part)[-2400:]
        notebook["last_updated_at"] = time.time()
        file_views["last_updated_at"] = time.time()
        if not self._is_replaying:
            self._upsert_sidechain_unlocked(
                record.session_key,
                purpose="file_view_distillation",
                summary=notebook_summary,
                metadata={"compacted_views": len(compacted), "samples": summarized[-4:]},
            )
        return {"entries": 1, "views": len(compacted), "summary": notebook_summary}

    def _compact_notes_into_notebook_unlocked(self, record: SessionRecord, *, force: bool = False) -> dict[str, Any]:
        keep_notes = min(self._max_context_notes, 4)
        keep_notes = max(1, keep_notes)
        note_chars = sum(len(item) for item in record.context_notes)
        should_compact = force or len(record.context_notes) > keep_notes or note_chars > self._max_note_chars
        if not should_compact or not record.context_notes:
            return {"entries": 0, "summary": ""}

        compacted_notes: list[str] = []
        while len(record.context_notes) > keep_notes:
            compacted_notes.append(record.context_notes.pop(0))
        note_chars = sum(len(item) for item in record.context_notes)
        while note_chars > self._max_note_chars and record.context_notes:
            removed = record.context_notes.pop(0)
            compacted_notes.append(removed)
            note_chars -= len(removed)
        if force and not compacted_notes and record.context_notes:
            compacted_notes.append(record.context_notes.pop(0))

        if not compacted_notes:
            return {"entries": 0, "summary": ""}

        notebook_summary = "; ".join(_preview_text(note, limit=80) for note in compacted_notes[-4:])
        session_layer = record.memory_layers.setdefault("session", {})
        notebook = session_layer.setdefault("notebook", {"summary": "", "entries": [], "last_updated_at": None})
        entries = list(notebook.get("entries", []))
        entries.append(
            {
                "timestamp": time.time(),
                "kind": "session_note_batch",
                "count": len(compacted_notes),
                "notes": compacted_notes[-4:],
                "summary": notebook_summary,
            }
        )
        notebook["entries"] = entries[-12:]
        notebook["summary"] = "\n".join(
            part for part in [str(notebook.get("summary", "")).strip(), notebook_summary] if part
        )[-2400:]
        notebook["last_updated_at"] = time.time()
        return {"entries": 1, "summary": notebook_summary}

    def _trim_budget_unlocked(
        self,
        record: SessionRecord,
        *,
        force: bool = False,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        trimmed_notes: list[str] = []
        trimmed_events: list[dict[str, Any]] = []
        while len(record.context_notes) > self._max_context_notes:
            trimmed_notes.append(record.context_notes.pop(0))
        while len(record.timeline) > self._max_timeline_events:
            trimmed_events.append(record.timeline.pop(0))

        note_chars = sum(len(item) for item in record.context_notes)
        while note_chars > self._max_note_chars and record.context_notes:
            removed = record.context_notes.pop(0)
            trimmed_notes.append(removed)
            note_chars -= len(removed)

        timeline_chars = self._timeline_chars(record.timeline)
        while timeline_chars > self._max_timeline_chars and record.timeline:
            removed = record.timeline.pop(0)
            trimmed_events.append(removed)
            timeline_chars = self._timeline_chars(record.timeline)

        if force and not trimmed_notes and record.context_notes:
            trimmed_notes.append(record.context_notes.pop(0))
        if force and not trimmed_events and record.timeline:
            trimmed_events.append(record.timeline.pop(0))
        return trimmed_notes, trimmed_events

    @staticmethod
    def _compact_json_value(value: Any, *, limit: int = 180, max_items: int = 8) -> Any:
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        if isinstance(value, str):
            return _preview_text(value, limit=limit)
        if isinstance(value, list):
            items = [
                SessionRuntime._compact_json_value(item, limit=max(60, limit // 2), max_items=max_items)
                for item in value[:max_items]
            ]
            if len(value) > max_items:
                items.append(f"...({len(value) - max_items} more items)")
            return items
        if isinstance(value, dict):
            compacted = {
                str(key): SessionRuntime._compact_json_value(
                    item,
                    limit=max(60, limit // 2),
                    max_items=max_items,
                )
                for key, item in list(value.items())[:max_items]
            }
            if len(value) > max_items:
                compacted["__truncated__"] = f"{len(value) - max_items} more keys"
            return compacted
        return _preview_text(str(value), limit=limit)

    @staticmethod
    def _build_layered_compaction_summary(
        *,
        compacted_tool_events: int,
        tool_notebook_entries: int,
        tool_notebook_summary: str,
        compacted_file_views: int,
        file_view_notebook_entries: int,
        file_view_notebook_summary: str,
        micro_preview_count: int,
        micro_metadata_count: int,
        notebook_entry_count: int,
        notebook_summary: str,
        trimmed_notes: list[str],
        trimmed_events: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        if compacted_tool_events or tool_notebook_entries:
            parts.append(
                "Tool transcript compacted "
                f"events={compacted_tool_events}, notebook_entries={tool_notebook_entries}: "
                f"{_preview_text(tool_notebook_summary, limit=120)}"
            )
        if compacted_file_views or file_view_notebook_entries:
            parts.append(
                "File views compacted "
                f"views={compacted_file_views}, notebook_entries={file_view_notebook_entries}: "
                f"{_preview_text(file_view_notebook_summary, limit=120)}"
            )
        if micro_preview_count or micro_metadata_count:
            parts.append(f"Microcompacted timeline previews={micro_preview_count}, metadata={micro_metadata_count}")
        if notebook_entry_count:
            parts.append(
                "Session notebook absorbed "
                f"{notebook_entry_count} note batches: "
                f"{_preview_text(notebook_summary, limit=120)}"
            )
        trimmed_summary = SessionRuntime._build_compaction_summary(trimmed_notes, trimmed_events)
        if trimmed_summary:
            parts.append(trimmed_summary)
        return " | ".join(part for part in parts if part)

    @staticmethod
    def _timeline_chars(items: list[dict[str, Any]]) -> int:
        return sum(len(json.dumps(item, ensure_ascii=False, default=str)) for item in items)

    @staticmethod
    def _build_compaction_summary(trimmed_notes: list[str], trimmed_events: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        if trimmed_notes:
            note_preview = "; ".join(_preview_text(note, limit=80) for note in trimmed_notes[-3:])
            parts.append(f"Compacted {len(trimmed_notes)} notes: {note_preview}")
        if trimmed_events:
            event_preview = "; ".join(
                _preview_text(
                    (
                        f"{item.get('kind', 'event')}:{item.get('title', '')}:"
                        f"{item.get('status', '')}:{item.get('preview', '')}"
                    ),
                    limit=90,
                )
                for item in trimmed_events[-3:]
            )
            parts.append(f"Compacted {len(trimmed_events)} timeline events: {event_preview}")
        return " | ".join(part for part in parts if part)

    def _load_unlocked(self) -> None:
        self._is_replaying = True
        try:
            self._sessions = self._replay_event_log_unlocked()
        finally:
            self._is_replaying = False

    def _replay_event_log_unlocked(self) -> dict[str, SessionRecord]:
        if not self._event_log_path.exists():
            return {}
        replayed: dict[str, SessionRecord] = {}
        try:
            with self._event_log_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(payload, dict):
                        self._apply_logged_event_unlocked(replayed, payload)
        except Exception:
            return {}
        return replayed

    def _apply_logged_event_unlocked(
        self,
        sessions: dict[str, SessionRecord],
        event: dict[str, Any],
    ) -> None:
        session_key = str(event.get("session_key", "")).strip()
        thread_id = str(event.get("thread_id", "")).strip() or session_key
        op = str(event.get("op", "")).strip()
        payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
        timestamp = float(event.get("timestamp", time.time()) or time.time())
        if not session_key or not op:
            return

        if op == "session_checkpoint_imported":
            session_payload = payload.get("session", {}) if isinstance(payload.get("session"), dict) else {}
            if session_payload:
                record = SessionRecord.from_dict(session_payload)
                sessions[record.session_key] = record
            return

        record = sessions.get(session_key)
        if record is None:
            root_mode = str(payload.get("root_mode", "assistant")).strip() or "assistant"
            record = SessionRecord(
                session_key=session_key,
                thread_id=thread_id,
                primary_mode=root_mode,
            )
            sessions[session_key] = record

        if op == "session_ensured":
            root_mode = str(payload.get("root_mode", record.primary_mode)).strip() or record.primary_mode
            source = str(payload.get("source", "")).strip()
            title = str(payload.get("title", "")).strip()
            record.thread_id = thread_id
            record.primary_mode = root_mode
            _append_unique(record.mode_history, root_mode)
            _append_unique(record.source_history, source)
            if title:
                record.title = title
            record.updated_at = timestamp
            return

        if op == "message_recorded":
            role = str(payload.get("role", "")).strip()
            content = str(payload.get("content", "") or payload.get("preview", ""))
            preview = str(payload.get("preview", "")).strip() or _preview_text(content)
            source = str(payload.get("source", "")).strip()
            if role == "user":
                record.last_user_message = content
                if not record.title:
                    record.title = _preview_text(content, limit=48)
            elif role == "assistant":
                record.last_assistant_message = content
            record.message_count += 1
            record.last_message_preview = preview
            record.last_message_at = timestamp
            record.updated_at = timestamp
            _append_unique(record.source_history, source)
            return

        if op == "prompt_injection_updated":
            metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
            metadata["prompt_injection"] = str(payload.get("prompt_injection", "")).strip()
            record.metadata = metadata
            kernel = self._kernel_unlocked(record.session_key)
            kernel.mutable_artifacts["prompt_injection"] = metadata["prompt_injection"]
            record.updated_at = timestamp
            return

        if op == "file_view_recorded":
            workspace_layer = record.memory_layers.setdefault("workspace", {})
            file_views = workspace_layer.setdefault(
                "file_views",
                {"recent": [], "notebook": {"summary": "", "entries": [], "last_updated_at": None}},
            )
            recent = list(file_views.get("recent", []))
            entry = {
                "timestamp": timestamp,
                "path": str(payload.get("path", "")).strip(),
                "offset": int(payload.get("offset", 0) or 0),
                "limit": int(payload.get("limit", 0) or 0),
                "is_partial_view": bool(payload.get("is_partial_view")),
                "view_kind": str(
                    payload.get("view_kind", "partial" if payload.get("is_partial_view") else "full")
                ).strip()
                or "full",
                "tool_name": str(payload.get("tool_name", "read_file")).strip() or "read_file",
                "source": str(payload.get("source", "")).strip(),
                "preview": _preview_text(str(payload.get("preview", "")).strip(), limit=180),
                "visible_chars": int(payload.get("visible_chars", 0) or 0),
                "content_hash": str(payload.get("content_hash", "")).strip(),
                "view_hash": str(payload.get("view_hash", "")).strip(),
            }
            if entry["path"]:
                recent.append(entry)
                file_views["recent"] = recent[-24:]
                file_views["last_updated_at"] = timestamp
                self._kernel_unlocked(record.session_key).file_view_projection = {
                    "recent_paths": [
                        str(item.get("path", "")).strip()
                        for item in file_views["recent"]
                        if isinstance(item, dict) and str(item.get("path", "")).strip()
                    ][-8:],
                    "notebook_summary": str(file_views.get("notebook", {}).get("summary", "")).strip()
                    if isinstance(file_views.get("notebook", {}), dict)
                    else "",
                }
                self._upsert_run_event_unlocked(
                    record,
                    kind="file_view",
                    run_id="",
                    title=entry["path"],
                    status="captured",
                    source=entry["source"] or "tool.read_file",
                    preview=entry["preview"],
                    metadata={
                        "path": entry["path"],
                        "offset": entry["offset"],
                        "limit": entry["limit"],
                        "is_partial_view": entry["is_partial_view"],
                        "tool_name": entry["tool_name"],
                        "view_kind": entry["view_kind"],
                        "visible_chars": entry["visible_chars"],
                        "content_hash": entry["content_hash"],
                        "view_hash": entry["view_hash"],
                    },
                    timestamp=timestamp,
                )
                record.updated_at = timestamp
            return

        if op == "memory_note_recorded":
            note = str(payload.get("note", "")).strip()
            layer = str(payload.get("layer", "session")).strip() or "session"
            memory_type = str(payload.get("memory_type", SESSION_MEMORY_TYPE)).strip() or SESSION_MEMORY_TYPE
            if note:
                record.context_notes.append(note)
            layer_state = record.memory_layers.setdefault(layer, {})
            if note:
                layer_state["last_note"] = note
            layer_state["last_note_type"] = memory_type
            if bool(payload.get("durable")) and note:
                entries = layer_state.setdefault("entries", [])
                entries.append(
                    {
                        "memory_type": memory_type,
                        "durable": True,
                        "note": note,
                        "occurred_on": str(payload.get("occurred_on", "")).strip(),
                        "verified": bool(payload.get("verified")),
                        "warnings": list(payload.get("warnings", []))
                        if isinstance(payload.get("warnings"), list)
                        else [],
                        "layer": layer,
                        "source": "session_runtime",
                        "recorded_at": timestamp,
                    }
                )
                if len(entries) > 24:
                    layer_state["entries"] = entries[-24:]
            record.updated_at = timestamp
            self._apply_budget_unlocked(record, reason="notes")
            return

        if op == "summary_updated":
            layer = str(payload.get("layer", "session")).strip() or "session"
            summary = str(payload.get("summary", "")).strip()
            record.working_summary = summary
            layer_state = record.memory_layers.setdefault(layer, {})
            layer_state["summary"] = summary
            record.updated_at = timestamp
            self._apply_budget_unlocked(record, reason="summary")
            return

        if op == "session_compacted":
            self._apply_budget_unlocked(
                record,
                reason=str(payload.get("reason", "manual")).strip() or "manual",
                force=True,
            )
            record.updated_at = timestamp
            return

        if op == "gateway_bound":
            gateway = dict(record.gateway)
            source = str(payload.get("source", "")).strip()
            gateway["user"] = str(payload.get("user", "")).strip() or gateway.get("user", "")
            device_ids = list(gateway.get("device_ids", []))
            client_ids = list(gateway.get("client_ids", []))
            sources = list(gateway.get("sources", []))
            _append_unique(device_ids, str(payload.get("device_id", "")).strip())
            _append_unique(client_ids, str(payload.get("client_id", "")).strip())
            _append_unique(sources, source)
            gateway["device_ids"] = device_ids
            gateway["client_ids"] = client_ids
            gateway["sources"] = sources
            record.gateway = gateway
            _append_unique(record.source_history, source)
            record.updated_at = timestamp
            return

        if op == "gateway_run_recorded":
            run_id = str(payload.get("run_id", "")).strip()
            mode = str(payload.get("mode", record.primary_mode)).strip() or record.primary_mode
            status = str(payload.get("status", "in_progress")).strip() or "in_progress"
            source = str(payload.get("source", "gateway")).strip() or "gateway"
            requested_model = str(payload.get("requested_model", "")).strip()
            display_input = str(payload.get("display_input", ""))
            output_text = str(payload.get("output_text", ""))
            record.latest_run = {
                "run_id": run_id,
                "mode": mode,
                "status": status,
                "source": source,
                "requested_model": requested_model,
                "display_input": display_input,
                "output_text": output_text,
                "updated_at": timestamp,
                "metadata": (
                    dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else dict(payload)
                ),
            }
            self._upsert_run_event_unlocked(
                record,
                kind="gateway_run",
                run_id=run_id,
                title=_preview_text(display_input, limit=72) or requested_model or "gateway run",
                status=status,
                source=source,
                preview=_preview_text(output_text or display_input, limit=240),
                metadata=(
                    dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else dict(payload)
                ),
                timestamp=timestamp,
            )
            record.updated_at = timestamp
            return

        if op == "workflow_run_recorded":
            self._upsert_run_event_unlocked(
                record,
                kind="workflow_run",
                run_id=str(payload.get("run_id", "")).strip(),
                title=str(payload.get("workflow_name", "")).strip() or str(payload.get("workflow_id", "")).strip(),
                status=str(payload.get("status", "completed")).strip() or "completed",
                source=str(payload.get("source", "workflow")).strip() or "workflow",
                preview=str(payload.get("preview", "")),
                metadata=payload,
                timestamp=timestamp,
            )
            record.updated_at = timestamp
            return

        if op == "timeline_event":
            record.timeline.append(
                {
                    "timestamp": timestamp,
                    "kind": str(payload.get("kind", "")).strip(),
                    "title": str(payload.get("title", "")).strip(),
                    "status": str(payload.get("status", "")).strip(),
                    "source": str(payload.get("source", "")).strip(),
                    "preview": _preview_text(str(payload.get("preview", "")), limit=240),
                    "run_id": str(payload.get("run_id", "")).strip(),
                    "metadata": dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else {},
                }
            )
            record.updated_at = timestamp
            self._apply_budget_unlocked(record, reason="timeline")
            return

        if op == "external_compaction_recorded":
            self._record_external_compaction_unlocked(
                record,
                summary=str(payload.get("summary", "")).strip(),
                source=str(payload.get("source", "middleware.summarization")).strip() or "middleware.summarization",
                reason=str(payload.get("reason", "conversation_compaction")).strip() or "conversation_compaction",
                message_count=int(payload.get("message_count", 0) or 0),
                recent_window=int(payload.get("recent_window", 0) or 0),
                offload_path=str(payload.get("offload_path", "")).strip(),
                timestamp=timestamp,
                replaying=True,
            )
            return

        if op == "sidechain_upserted":
            self._upsert_sidechain_unlocked(
                record.session_key,
                purpose=str(payload.get("purpose", "")).strip() or "background",
                summary=str(payload.get("summary", "")).strip(),
                status=str(payload.get("status", "completed")).strip() or "completed",
                metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {},
                sidechain_id=str(payload.get("sidechain_id", "")).strip(),
                timestamp=timestamp,
                replaying=True,
            )

    def _persist_unlocked(self) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "sessions": {key: record.to_dict() for key, record in self._sessions.items()},
            }
            temp_path = self._storage_path.with_name(f"{self._storage_path.name}.tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self._storage_path)
        except Exception:
            return
