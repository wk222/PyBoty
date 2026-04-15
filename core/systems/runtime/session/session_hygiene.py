"""Context hygiene and compaction logic for PyBot sessions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core.systems.runtime.session.session_record import (
    SessionRecord,
    _preview_text,
)
from core.systems.runtime.session.session_compaction import create_compaction_boundary


class SessionHygieneMixin:
    """Mixin for SessionRuntime to handle budget, compaction, and trimming."""

    def compact_session(self, session_key: str, *, reason: str = "manual") -> dict[str, Any]:
        with self._lock:
            record = self._sessions.get(str(session_key).strip())
            if record is None:
                raise KeyError(f"unknown session: {session_key}")
            self._apply_budget_unlocked(record, reason=reason, force=True)
            self._persist_unlocked()
            return self._session_snapshot_unlocked(record)

    def record_external_compaction(
        self,
        session_key: str | None = None,
        *,
        thread_id: str = "",
        root_mode: str = "assistant",
        summary: str,
        source: str = "middleware.summarization",
        reason: str = "conversation_compaction",
        message_count: int = 0,
        recent_window: int = 0,
        offload_path: str = "",
    ) -> dict[str, Any]:
        normalized_thread = str(thread_id).strip()
        normalized_session = str(session_key or "").strip()
        if normalized_session:
            resolved = self.ensure_session(
                session_key=normalized_session,
                thread_id=normalized_thread or normalized_session,
                root_mode=root_mode,
                source=source,
            )
        elif normalized_thread:
            resolved = self.ensure_thread_session(
                normalized_thread,
                root_mode=root_mode,
                source=source,
            )
        else:
            raise ValueError("thread_id or session_key is required")
        with self._lock:
            record = self._sessions.get(str(resolved["session_key"]).strip())
            if record is None:
                raise KeyError(session_key or thread_id)
            self._record_external_compaction_unlocked(
                record,
                summary=summary,
                source=source,
                reason=reason,
                message_count=message_count,
                recent_window=recent_window,
                offload_path=offload_path,
                timestamp=time.time(),
                replaying=False,
            )
            self._persist_unlocked()
            return self._session_snapshot_unlocked(record)

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
        
        # Automatically suggest garden inclusion if we compacted significant notebook entries
        # or a large number of tool events/files.
        garden_suggested = (
            notebook["entries"] > 0 
            or tool_transcript["entries"] > 0 
            or file_views["entries"] > 0
            or len(trimmed_events) > 10
        )
        
        boundary = create_compaction_boundary(
            source="session_runtime",
            reason=reason,
            summary=summary,
            notebook_summary=notebook["summary"],
            garden_suggested=garden_suggested,
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
                "context_notes": len(self._build_record_runtime_view_unlocked(record).hooks.get("notes", [])),
                "file_views": len(self._build_record_runtime_view_unlocked(record).workspace.get("recent_views", [])),
            },
        )
        current_view = self._build_record_runtime_view_unlocked(record)
        state = dict(current_view.context_hygiene)
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
        boundary_payload = boundary.to_dict() if hasattr(boundary, "to_dict") else dict(boundary)
        boundaries = list(state.get("boundaries", []))
        boundaries.append(boundary_payload)
        state["boundaries"] = boundaries[-16:]
        state["latest_boundary"] = boundary_payload
        state["history_snip_count"] = max(int(state.get("history_snip_count", 0) or 0), len(state["boundaries"]))

        session_layer = dict(current_view.session)
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
        self._merge_record_runtime_view_unlocked(
            record,
            session=session_layer,
            context_hygiene=state,
        )
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
        self._invalidate_runtime_view_unlocked(
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
        current_view = self._build_record_runtime_view_unlocked(record)
        state = dict(current_view.context_hygiene)
        boundaries = list(state.get("boundaries", []))
        payload = boundary.to_dict() if hasattr(boundary, "to_dict") else dict(boundary)
        boundaries.append(payload)
        state["boundaries"] = boundaries[-16:]
        state["latest_boundary"] = payload
        state["history_snip_count"] = max(int(state.get("history_snip_count", 0) or 0), len(state["boundaries"]))
        self._merge_record_runtime_view_unlocked(record, context_hygiene=state)

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
                "context_notes": len(self._build_record_runtime_view_unlocked(record).hooks.get("notes", [])),
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
        current_view = self._build_record_runtime_view_unlocked(record)
        session_layer = dict(current_view.session)
        notebook = dict(session_layer.get("tool_transcript", {}))
        if not notebook:
            notebook = {
                "summary": "",
                "entries": [],
                "last_updated_at": None,
            }
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
        session_layer["tool_transcript"] = notebook
        self._merge_record_runtime_view_unlocked(record, session=session_layer)
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
        current_view = self._build_record_runtime_view_unlocked(record)
        workspace = dict(current_view.workspace)
        recent = list(workspace.get("recent_views", []))
        keep_recent = 6
        if len(recent) <= keep_recent and not force:
            return {"entries": 0, "views": 0, "summary": ""}

        compacted = recent[:-keep_recent] if len(recent) > keep_recent else []
        if force and not compacted and recent:
            compacted = recent[:1]
        if not compacted:
            return {"entries": 0, "views": 0, "summary": ""}

        workspace["recent_views"] = recent[len(compacted) :]
        workspace["recent_paths"] = [
            str(item.get("path", "")).strip()
            for item in workspace["recent_views"]
            if isinstance(item, dict) and str(item.get("path", "")).strip()
        ][-24:]
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
        notebook = dict(workspace.get("file_view_notebook", {}))
        if not notebook:
            notebook = {"summary": "", "entries": [], "last_updated_at": None}
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
        workspace["file_view_notebook"] = notebook
        workspace["notebook_summary"] = notebook["summary"]
        workspace["last_updated_at"] = time.time()
        self._merge_record_runtime_view_unlocked(record, workspace=workspace)
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
        current_view = self._build_record_runtime_view_unlocked(record)
        hooks = dict(current_view.hooks)
        notes = list(hooks.get("notes", []))
        note_chars = sum(len(item) for item in notes)
        should_compact = force or len(notes) > keep_notes or note_chars > self._max_note_chars
        if not should_compact or not notes:
            return {"entries": 0, "summary": ""}

        compacted_notes: list[str] = []
        while len(notes) > keep_notes:
            compacted_notes.append(notes.pop(0))
        note_chars = sum(len(item) for item in notes)
        while note_chars > self._max_note_chars and notes:
            removed = notes.pop(0)
            compacted_notes.append(removed)
            note_chars -= len(removed)
        if force and not compacted_notes and notes:
            compacted_notes.append(notes.pop(0))

        if not compacted_notes:
            return {"entries": 0, "summary": ""}

        notebook_summary = "; ".join(_preview_text(note, limit=80) for note in compacted_notes[-4:])
        session_layer = dict(current_view.session)
        notebook = dict(session_layer.get("notebook", {}))
        if not notebook:
            notebook = {"summary": "", "entries": [], "last_updated_at": None}
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
        session_layer["notebook"] = notebook
        session_layer["session_notebook_summary"] = notebook["summary"]
        hooks["notes"] = notes
        self._merge_record_runtime_view_unlocked(record, session=session_layer, hooks=hooks)
        return {"entries": 1, "summary": notebook_summary}

    def _trim_budget_unlocked(
        self,
        record: SessionRecord,
        *,
        force: bool = False,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        trimmed_notes: list[str] = []
        trimmed_events: list[dict[str, Any]] = []
        current_view = self._build_record_runtime_view_unlocked(record)
        hooks = dict(current_view.hooks)
        notes = list(hooks.get("notes", []))
        while len(notes) > self._max_context_notes:
            trimmed_notes.append(notes.pop(0))
        while len(record.timeline) > self._max_timeline_events:
            trimmed_events.append(record.timeline.pop(0))

        note_chars = sum(len(item) for item in notes)
        while note_chars > self._max_note_chars and notes:
            removed = notes.pop(0)
            trimmed_notes.append(removed)
            note_chars -= len(removed)

        timeline_chars = self._timeline_chars(record.timeline)
        while timeline_chars > self._max_timeline_chars and record.timeline:
            removed = record.timeline.pop(0)
            trimmed_events.append(removed)
            timeline_chars = self._timeline_chars(record.timeline)

        if force and not trimmed_notes and notes:
            trimmed_notes.append(notes.pop(0))
        if force and not trimmed_events and record.timeline:
            trimmed_events.append(record.timeline.pop(0))
        hooks["notes"] = notes
        self._merge_record_runtime_view_unlocked(record, hooks=hooks)
        return trimmed_notes, trimmed_events

    def _compact_json_value(self, value: Any, *, limit: int = 180, max_items: int = 8) -> Any:
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        if isinstance(value, str):
            return _preview_text(value, limit=limit)
        if isinstance(value, list):
            items = [
                self._compact_json_value(item, limit=max(60, limit // 2), max_items=max_items)
                for item in value[:max_items]
            ]
            if len(value) > max_items:
                items.append(f"...({len(value) - max_items} more items)")
            return items
        if isinstance(value, dict):
            compacted = {
                str(key): self._compact_json_value(
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

    def _build_layered_compaction_summary(
        self,
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
        trimmed_summary = self._build_compaction_summary(trimmed_notes, trimmed_events)
        if trimmed_summary:
            parts.append(trimmed_summary)
        return " | ".join(part for part in parts if part)

    @staticmethod
    def _timeline_chars(items: list[dict[str, Any]]) -> int:
        return sum(len(json.dumps(item, ensure_ascii=False, default=str)) for item in items)

    def _build_compaction_summary(self, trimmed_notes: list[str], trimmed_events: list[dict[str, Any]]) -> str:
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
