"""Recording and state update logic for PyBot sessions."""

from __future__ import annotations

import time
from typing import Any

from core.systems.session.session_memory_policy import (
    SESSION_MEMORY_TYPE,
    typed_memory_entry_payload,
    validate_session_memory,
)
from core.systems.session.session_record import (
    SessionRecord,
    _append_unique,
    _preview_text,
    _stable_text_hash,
)


class SessionRecorderMixin:
    """Mixin for SessionRuntime to handle recording events and updating session state."""

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
            self._sync_runtime_view_from_record_unlocked(record)
            record.updated_at = time.time()
            self._note_usage_unlocked(normalized_session_key, "session_ensure")
            if previous_mode and previous_mode != record.primary_mode:
                self._invalidate_runtime_view_unlocked(
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
            return self._session_snapshot_unlocked(record)

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
            self._invalidate_runtime_view_unlocked(
                record.session_key,
                reason="conversation_bound",
                scopes=["system_context", "compiled_artifacts"],
            )
            self._persist_unlocked()
            return self._session_snapshot_unlocked(record)

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
            self._invalidate_runtime_view_unlocked(
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
            return self._session_snapshot_unlocked(record)

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
            self._invalidate_runtime_view_unlocked(
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
            return self._session_snapshot_unlocked(record)

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
            self._invalidate_runtime_view_unlocked(
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
            return self._session_snapshot_unlocked(record)

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
            current_view = self._build_record_runtime_view_unlocked(record)
            workspace = dict(current_view.workspace)
            recent = list(workspace.get("recent_views", []))
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
            workspace["recent_views"] = recent[-24:]
            workspace["recent_paths"] = [
                str(item.get("path", "")).strip()
                for item in workspace["recent_views"]
                if isinstance(item, dict) and str(item.get("path", "")).strip()
            ][-24:]
            workspace["last_updated_at"] = resolved_timestamp
            self._merge_record_runtime_view_unlocked(record, workspace=workspace)
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
            self._invalidate_runtime_view_unlocked(
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
            return self._session_snapshot_unlocked(record)

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
            self._invalidate_runtime_view_unlocked(
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
            return self._session_snapshot_unlocked(record)

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
            self._invalidate_runtime_view_unlocked(
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
            return self._session_snapshot_unlocked(record)

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
            normalized_layer = self._canonical_runtime_layer_name(layer)
            current_view = self._build_record_runtime_view_unlocked(record)
            hooks = dict(current_view.hooks)
            notes = list(hooks.get("notes", []))
            notes.append(decision.note)
            hooks["notes"] = notes[-24:]
            layer_state = dict(getattr(current_view, normalized_layer))
            layer_state["last_note"] = decision.note
            if decision.durable:
                entries = layer_state.setdefault("entries", [])
                entry = typed_memory_entry_payload(decision, layer=normalized_layer)
                entry["recorded_at"] = time.time()
                entries.append(entry)
                if len(entries) > 24:
                    layer_state["entries"] = entries[-24:]
            layer_state["last_note_type"] = decision.memory_type
            if normalized_layer == "session":
                self._merge_record_runtime_view_unlocked(record, hooks=hooks, session=layer_state)
            else:
                self._merge_record_runtime_view_unlocked(record, hooks=hooks, workspace=layer_state)
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
            self._invalidate_runtime_view_unlocked(
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
            return self._session_snapshot_unlocked(record)

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
            normalized_layer = self._canonical_runtime_layer_name(layer)
            current_view = self._build_record_runtime_view_unlocked(record)
            layer_state = dict(getattr(current_view, normalized_layer))
            layer_state["summary"] = record.working_summary
            if normalized_layer == "session":
                self._merge_record_runtime_view_unlocked(record, session=layer_state)
            else:
                self._merge_record_runtime_view_unlocked(record, workspace=layer_state)
            record.updated_at = time.time()
            self._apply_budget_unlocked(record, reason="summary")
            self._invalidate_runtime_view_unlocked(
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
            return self._session_snapshot_unlocked(record)
