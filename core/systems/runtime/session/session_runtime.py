"""Persistent session spine for chat, gateway, and mode-aware runtime state."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from core.systems.runtime.projected_runtime_view import build_projected_runtime_view
from core.systems.runtime.projected_runtime_view import extract_projected_runtime_view
from core.systems.runtime.projected_runtime_view import merge_projected_runtime_views
from core.systems.runtime.session.session_runtime_view import compile_session_runtime_view
from core.systems.runtime.session.session_kernel import SessionKernel
from core.systems.runtime.session.session_record import (
    SessionRecord,
    _as_text,
    _normalize_runtime_view_payload,
    _preview_text,
    _stable_text_hash,
)
from core.systems.runtime.session.session_hygiene import SessionHygieneMixin
from core.systems.runtime.session.session_sync import SessionSyncMixin
from core.systems.runtime.session.session_recorder import SessionRecorderMixin
from core.systems.runtime.session.session_applier import SessionApplierMixin


class SessionRuntime(
    SessionHygieneMixin,
    SessionSyncMixin,
    SessionRecorderMixin,
    SessionApplierMixin,
):
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
            items = [self._session_snapshot_unlocked(record) for record in self._sessions.values()]
        return sorted(
            items,
            key=lambda item: item.get("updated_at") or item.get("last_message_at") or item.get("created_at", 0),
            reverse=True,
        )

    def get_session(self, session_key: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            return self._session_snapshot_unlocked(record) if record is not None else None

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
            kernel = self._kernel_unlocked(record.session_key)
            view = extract_projected_runtime_view(kernel.runtime_view)
            if view is None:
                self._refresh_runtime_view_unlocked(record.session_key)
                view = extract_projected_runtime_view(self._kernel_unlocked(record.session_key).runtime_view)
            recent = list(view.workspace.get("recent_views", [])) if view is not None else []
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

    def switch_mode(self, session_key: str, *, new_mode: str) -> dict[str, Any]:
        """Switch a session's active mode profile and record the transition."""
        _VALID_MODES = {"assistant", "app_matrix", "admin"}
        normalized = str(new_mode).strip().lower()
        if normalized not in _VALID_MODES:
            raise ValueError(f"Unknown mode: {new_mode!r}. Valid modes: {sorted(_VALID_MODES)}")
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            if record is None:
                raise KeyError(session_key)
            previous = record.primary_mode
            if normalized != previous:
                record.primary_mode = normalized
                if normalized not in record.mode_history:
                    record.mode_history.append(normalized)
                record.updated_at = time.time()
                self._append_event_unlocked(
                    record,
                    op="mode_switch",
                    payload={"from": previous, "to": normalized},
                )
            return {"previous_mode": previous, "mode": normalized, "mode_history": list(record.mode_history)}

    def upsert_sidechain(
        self,
        session_key: str,
        *,
        purpose: str,
        summary: str = "",
        status: str = "active",
        metadata: dict[str, Any] | None = None,
        sidechain_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            normalized = str(session_key).strip()
            if normalized not in self._sessions:
                return {}
            return self._upsert_sidechain_unlocked(
                normalized,
                purpose=purpose,
                summary=summary,
                status=status,
                metadata=metadata,
                sidechain_id=sidechain_id,
            )

    def get_compiled_runtime_view(
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
                self._invalidate_runtime_view_unlocked(
                    normalized,
                    reason=reason or "manual",
                    scopes=["compiled_artifacts"],
                )
            cached = self._artifact_cache.get(normalized)
            kernel = self._kernel_unlocked(normalized)
            if cached is not None and int(cached.get("artifact_version", -1)) == kernel.artifact_version:
                return dict(cached)
            compiled = compile_session_runtime_view(record, kernel)
            projected_view = dict(compiled.get("projected_runtime_view", {})) if isinstance(compiled.get("projected_runtime_view"), dict) else {}
            kernel.mutable_views["compiled_checksums"] = {
                "system_context": _stable_text_hash(
                    json.dumps(compiled.get("system_context", {}), ensure_ascii=False, default=str)
                ),
                "user_context": _stable_text_hash(
                    json.dumps(compiled.get("user_context", {}), ensure_ascii=False, default=str)
                ),
                "projected_runtime_view": _stable_text_hash(
                    json.dumps(projected_view, ensure_ascii=False, default=str)
                ),
            }
            kernel.last_compiled_at = time.time()
            self._artifact_cache[normalized] = dict(compiled)
            return compiled

    def invalidate_runtime_view(
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
            self._invalidate_runtime_view_unlocked(normalized, reason=reason, scopes=scopes or [])
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
            prompt_text = str(prompt_injection).strip()
            current_view = self._build_record_runtime_view_unlocked(record)
            system_context = dict(current_view.system_context)
            system_context["thread_id"] = record.thread_id
            system_context["primary_mode"] = record.primary_mode
            system_context["prompt_injection"] = prompt_text
            self._merge_record_runtime_view_unlocked(record, system_context=system_context)
            record.updated_at = time.time()
            self._invalidate_runtime_view_unlocked(
                normalized,
                reason="prompt_injection_updated",
                scopes=["prompt_injection", "system_context", "compiled_artifacts"],
            )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="prompt_injection_updated",
                payload={"prompt_injection": prompt_text},
                timestamp=record.updated_at,
            )
            return self._session_snapshot_unlocked(record)

    def update_runtime_view(
        self,
        *,
        thread_id: str,
        root_mode: str = "assistant",
        session_key: str | None = None,
        source: str = "runtime",
        projected_runtime_view: Any | None = None,
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
        normalized = resolved_session["session_key"]
        incoming_payload = (
            projected_runtime_view.to_payload()
            if hasattr(projected_runtime_view, "to_payload")
            else projected_runtime_view
        )
        runtime_view_payload = _normalize_runtime_view_payload(
            incoming_payload if isinstance(incoming_payload, dict) else {}
        )
        if runtime_view_payload:
            system_context = (
                dict(runtime_view_payload.get("system_context", {}))
                if isinstance(runtime_view_payload.get("system_context"), dict)
                else {}
            )
            if not str(system_context.get("thread_id", "")).strip():
                system_context["thread_id"] = str(thread_id).strip() or "default"
            if not str(system_context.get("primary_mode", "")).strip():
                system_context["primary_mode"] = str(root_mode).strip() or "assistant"
            runtime_view_payload["system_context"] = system_context
        with self._lock:
            record = self._sessions[normalized]
            kernel = self._kernel_unlocked(normalized)
            current_view = self._current_runtime_view_unlocked(record)
            incoming_view = extract_projected_runtime_view(runtime_view_payload)
            merged_view = merge_projected_runtime_views(current_view, incoming_view) or incoming_view or current_view
            next_runtime_view = _normalize_runtime_view_payload(merged_view.to_payload()) if merged_view is not None else {}
            if kernel.runtime_view == next_runtime_view:
                return self._session_snapshot_unlocked(record)
            if merged_view is not None:
                self._set_record_runtime_view_unlocked(record, merged_view)
            record.updated_at = time.time()
            self._invalidate_runtime_view_unlocked(
                normalized,
                reason="runtime_view_updated",
                scopes=["projected_runtime_view", "compiled_artifacts"],
            )
            self._persist_unlocked()
            self._append_event_unlocked(
                record,
                op="runtime_view_updated",
                payload={
                    "source": str(source).strip() or "runtime",
                    "runtime_view": next_runtime_view,
                    "root_mode": root_mode,
                },
                timestamp=record.updated_at,
            )
            return self._session_snapshot_unlocked(record)

    def _kernel_unlocked(self, session_key: str) -> SessionKernel:
        normalized = str(session_key).strip()
        kernel = self._kernels.get(normalized)
        if kernel is not None:
            return kernel
        kernel = SessionKernel(session_key=normalized)
        record = self._sessions.get(normalized)
        if record is not None:
            runtime_view_payload = (
                _normalize_runtime_view_payload(record.metadata.get("runtime_view", {}))
                if isinstance(record.metadata, dict)
                else {}
            )
            if runtime_view_payload:
                kernel.runtime_view = runtime_view_payload
        self._kernels[normalized] = kernel
        return kernel

    @staticmethod
    def _canonical_runtime_layer_name(layer: str) -> str:
        normalized = str(layer).strip().lower()
        return "workspace" if normalized == "workspace" else "session"

    def _current_runtime_view_unlocked(self, record: SessionRecord) -> Any | None:
        kernel = self._kernel_unlocked(record.session_key)
        return extract_projected_runtime_view(kernel.runtime_view)

    def _session_snapshot_unlocked(self, record: SessionRecord) -> dict[str, Any]:
        payload = record.to_dict()
        metadata = dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else {}
        metadata.pop("runtime_view", None)
        payload["metadata"] = metadata
        payload["runtime_view"] = self._build_record_runtime_view_unlocked(record).to_payload()
        return payload

    def _set_record_runtime_view_unlocked(self, record: SessionRecord, view: Any | None) -> dict[str, Any]:
        runtime_view_payload = _normalize_runtime_view_payload(view.to_payload()) if view is not None else {}
        if not runtime_view_payload:
            return {}
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        metadata.pop("prompt_injection", None)
        metadata["runtime_view"] = runtime_view_payload
        record.metadata = metadata
        self._kernel_unlocked(record.session_key).runtime_view = dict(runtime_view_payload)
        return runtime_view_payload

    def _merge_record_runtime_view_unlocked(
        self,
        record: SessionRecord,
        *,
        system_context: dict[str, Any] | None = None,
        session: dict[str, Any] | None = None,
        workspace: dict[str, Any] | None = None,
        tasks: dict[str, Any] | None = None,
        permission: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        capability: dict[str, Any] | None = None,
        context_hygiene: dict[str, Any] | None = None,
        hooks: dict[str, Any] | None = None,
        route: dict[str, Any] | None = None,
        isolation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base_view = self._build_record_runtime_view_unlocked(record)
        merged_view = build_projected_runtime_view(
            thread_id=_as_text(record.thread_id) or _as_text(record.session_key) or "default",
            root_mode=_as_text(record.primary_mode) or "assistant",
            system_context=system_context if system_context is not None else dict(base_view.system_context),
            session=session if session is not None else dict(base_view.session),
            workspace=workspace if workspace is not None else dict(base_view.workspace),
            tasks=tasks if tasks is not None else dict(base_view.tasks),
            permission=permission if permission is not None else dict(base_view.permission),
            settings=settings if settings is not None else dict(base_view.settings),
            capability=capability if capability is not None else dict(base_view.capability),
            context_hygiene=(
                context_hygiene if context_hygiene is not None else dict(base_view.context_hygiene)
            ),
            hooks=hooks if hooks is not None else dict(base_view.hooks),
            route=route if route is not None else dict(base_view.route),
            isolation=isolation if isolation is not None else dict(base_view.isolation),
        )
        return self._set_record_runtime_view_unlocked(record, merged_view)

    @staticmethod
    def _recent_file_views_from_timeline_unlocked(record: SessionRecord, *, limit: int = 24) -> list[dict[str, Any]]:
        recent: list[dict[str, Any]] = []
        for item in record.timeline:
            if _as_text(item.get("kind")) != "file_view":
                continue
            metadata = dict(item.get("metadata", {})) if isinstance(item.get("metadata"), dict) else {}
            path = _as_text(metadata.get("path")) or _as_text(item.get("title"))
            if not path:
                continue
            recent.append(
                {
                    "timestamp": item.get("timestamp", 0),
                    "path": path,
                    "offset": int(metadata.get("offset", 0) or 0),
                    "limit": int(metadata.get("limit", 0) or 0),
                    "is_partial_view": bool(metadata.get("is_partial_view")),
                    "view_kind": _as_text(metadata.get("view_kind")) or "full",
                    "tool_name": _as_text(metadata.get("tool_name")) or "read_file",
                    "source": _as_text(item.get("source")),
                    "preview": _preview_text(_as_text(item.get("preview")), limit=180),
                    "visible_chars": int(metadata.get("visible_chars", 0) or 0),
                    "content_hash": _as_text(metadata.get("content_hash")),
                    "view_hash": _as_text(metadata.get("view_hash")),
                }
            )
        return recent[-max(1, int(limit)) :]

    def _latest_compaction_boundary_from_record_unlocked(
        self,
        record: SessionRecord,
        current_view: Any | None = None,
    ) -> dict[str, Any]:
        view = current_view if current_view is not None else self._current_runtime_view_unlocked(record)
        if view is None:
            return {}
        boundary = (
            dict(view.context_hygiene.get("latest_boundary", {}))
            if isinstance(view.context_hygiene.get("latest_boundary"), dict)
            else {}
        )
        if boundary:
            return boundary
        boundaries = list(view.context_hygiene.get("boundaries", [])) if isinstance(view.context_hygiene, dict) else []
        for raw in reversed(boundaries):
            if isinstance(raw, dict) and raw:
                return dict(raw)
        return {}

    def _session_notebook_summary_from_record_unlocked(
        self,
        record: SessionRecord,
        current_view: Any | None = None,
    ) -> str:
        view = current_view if current_view is not None else self._current_runtime_view_unlocked(record)
        if view is not None:
            notebook = dict(view.session.get("notebook", {})) if isinstance(view.session.get("notebook"), dict) else {}
            summary = _as_text(notebook.get("summary"))
            if summary:
                return summary
            note_lines = [_as_text(item) for item in view.hooks.get("notes", []) if _as_text(item)]
            if note_lines:
                return "\n".join(note_lines)
            summary = _as_text(view.session.get("session_notebook_summary"))
            if summary:
                return summary
        return ""

    def _workspace_projection_from_record_unlocked(
        self,
        record: SessionRecord,
        current_view: Any | None = None,
    ) -> dict[str, Any]:
        current_workspace = dict(current_view.workspace) if current_view is not None else {}
        recent_views = list(current_workspace.get("recent_views", []))
        if not recent_views:
            recent_views = self._recent_file_views_from_timeline_unlocked(record, limit=24)
        projection = dict(current_workspace)
        projection["recent_views"] = recent_views[-24:]
        projection["recent_paths"] = [
            _as_text(item.get("path"))
            for item in recent_views
            if isinstance(item, dict) and _as_text(item.get("path"))
        ][-24:]
        notebook = (
            dict(current_workspace.get("file_view_notebook", {}))
            if isinstance(current_workspace.get("file_view_notebook"), dict)
            else {}
        )
        notebook_summary = _as_text(notebook.get("summary")) or _as_text(current_workspace.get("notebook_summary"))
        projection["file_view_notebook"] = notebook
        projection["last_updated_at"] = current_workspace.get("last_updated_at")
        if notebook_summary:
            projection["notebook_summary"] = notebook_summary
        return projection

    def _build_record_runtime_view_unlocked(self, record: SessionRecord) -> Any:
        current_view = self._current_runtime_view_unlocked(record)
        latest_boundary = self._latest_compaction_boundary_from_record_unlocked(record, current_view=current_view)
        working_summary = _as_text(record.working_summary) or _as_text(
            current_view.system_context.get("working_summary") if current_view is not None else ""
        )
        current_context_hygiene = dict(current_view.context_hygiene) if current_view is not None else {}
        overlay_view = build_projected_runtime_view(
            thread_id=_as_text(record.thread_id) or _as_text(record.session_key) or "default",
            root_mode=_as_text(record.primary_mode) or "assistant",
            system_context={
                **(dict(current_view.system_context) if current_view is not None else {}),
                "thread_id": _as_text(record.thread_id) or _as_text(record.session_key) or "default",
                "primary_mode": _as_text(record.primary_mode) or "assistant",
                "working_summary": working_summary,
                "latest_compaction_boundary": latest_boundary,
                "prompt_injection": _as_text(
                    current_view.system_context.get("prompt_injection") if current_view is not None else ""
                ),
            },
            session={
                **(dict(current_view.session) if current_view is not None else {}),
                "session_notebook_summary": self._session_notebook_summary_from_record_unlocked(
                    record,
                    current_view=current_view,
                ),
                "working_summary": working_summary,
                "compaction_summary": _as_text(latest_boundary.get("summary")),
            },
            workspace=self._workspace_projection_from_record_unlocked(record, current_view=current_view),
            tasks=dict(current_view.tasks) if current_view is not None else {},
            permission=dict(current_view.permission) if current_view is not None else {},
            settings=dict(current_view.settings) if current_view is not None else {},
            capability=dict(current_view.capability) if current_view is not None else {},
            context_hygiene={
                **current_context_hygiene,
                "summary_active": bool(latest_boundary),
                "last_microcompact_count": int(
                    dict(latest_boundary.get("metadata", {})).get("microcompact_count", 0)
                    if isinstance(latest_boundary.get("metadata", {}), dict)
                    else 0
                ),
                "history_snip_count": max(
                    int(current_context_hygiene.get("history_snip_count", 0) or 0),
                    len(current_context_hygiene.get("boundaries", []))
                    if isinstance(current_context_hygiene.get("boundaries"), list)
                    else 0,
                ),
                "latest_boundary": latest_boundary,
            },
            hooks=dict(current_view.hooks) if current_view is not None else {},
            route=dict(current_view.route) if current_view is not None else {},
            isolation=dict(current_view.isolation) if current_view is not None else {},
        )
        return merge_projected_runtime_views(current_view, overlay_view) or overlay_view

    def _sync_runtime_view_from_record_unlocked(self, record: SessionRecord) -> dict[str, Any]:
        view = self._build_record_runtime_view_unlocked(record)
        return self._set_record_runtime_view_unlocked(record, view)

    def _refresh_runtime_view_unlocked(self, session_key: str) -> None:
        normalized = str(session_key).strip()
        record = self._sessions.get(normalized)
        if record is None:
            return
        kernel = self._kernel_unlocked(normalized)
        seeded = self._sync_runtime_view_from_record_unlocked(record)
        compiled = compile_session_runtime_view(record, kernel)
        runtime_view_payload = _normalize_runtime_view_payload(compiled)
        if seeded:
            merged_view = merge_projected_runtime_views(seeded, runtime_view_payload)
            runtime_view_payload = (
                _normalize_runtime_view_payload(merged_view.to_payload()) if merged_view is not None else runtime_view_payload
            )
        if not runtime_view_payload:
            return
        view = extract_projected_runtime_view(runtime_view_payload)
        if view is not None:
            self._set_record_runtime_view_unlocked(record, view)

    def _invalidate_runtime_view_unlocked(
        self,
        session_key: str,
        *,
        reason: str,
        scopes: list[str] | None = None,
    ) -> None:
        normalized = str(session_key).strip()
        kernel = self._kernel_unlocked(normalized)
        kernel.invalidate(reason=reason, scopes=scopes or [])
        self._refresh_runtime_view_unlocked(normalized)
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
            kernel = self._kernel_unlocked(record.session_key)
            view = extract_projected_runtime_view(kernel.runtime_view)
            if view is None:
                self._refresh_runtime_view_unlocked(record.session_key)
                view = extract_projected_runtime_view(self._kernel_unlocked(record.session_key).runtime_view)
        counts_by_kind: dict[str, int] = {}
        latest_by_kind: dict[str, dict[str, Any]] = {}
        for item in timeline:
            kind = str(item.get("kind", "")).strip() or "event"
            counts_by_kind[kind] = counts_by_kind.get(kind, 0) + 1
            latest_by_kind[kind] = item
        compaction_state = dict(view.context_hygiene) if view is not None else {}
        note_count = 0
        working_summary = record.working_summary if isinstance(record, SessionRecord) else ""
        if view is not None:
            note_text = _as_text(view.session.get("session_notebook_summary"))
            note_count = len([line for line in note_text.splitlines() if line.strip()]) if note_text else note_count
            working_summary = _as_text(view.system_context.get("working_summary")) or _as_text(
                view.session.get("working_summary")
            ) or working_summary
        return {
            "session_key": session_key,
            "counts": {
                "timeline_events": len(timeline),
                "notes": note_count,
                "messages": record.message_count if isinstance(record, SessionRecord) else 0,
                "by_kind": counts_by_kind,
            },
            "latest_by_kind": latest_by_kind,
            "latest_run": latest_run,
            "gateway": gateway,
            "compaction": compaction_state,
            "working_summary": working_summary,
        }

    def get_session_for_thread(self, thread_id: str) -> dict[str, Any] | None:
        normalized = str(thread_id).strip()
        with self._lock:
            for record in self._sessions.values():
                if record.thread_id == normalized:
                    return self._session_snapshot_unlocked(record)
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

