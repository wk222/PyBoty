"""Event application and persistence logic for PyBot sessions."""

from __future__ import annotations

import json
import time
from typing import Any

from core.systems.context.projected_runtime_view import (
    extract_projected_runtime_view,
    merge_projected_runtime_views,
)
from core.systems.session.session_memory_policy import SESSION_MEMORY_TYPE
from core.systems.session.session_record import (
    SessionRecord,
    _append_unique,
    _normalize_runtime_view_payload,
    _preview_text,
)


class SessionApplierMixin:
    """Mixin for SessionRuntime to handle event application and persistence."""

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
                current_view = self._build_record_runtime_view_unlocked(record)
                state = dict(current_view.context_hygiene)
                state["resume_scrubbed_events"] = int(state.get("resume_scrubbed_events", 0)) + scrubbed_count
                self._merge_record_runtime_view_unlocked(record, context_hygiene=state)
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

    def _load_unlocked(self) -> None:
        self._is_replaying = True
        try:
            self._sessions = self._replay_event_log_unlocked()
            for record in self._sessions.values():
                self._sync_runtime_view_from_record_unlocked(record)
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
            current_view = self._build_record_runtime_view_unlocked(record)
            system_context = dict(current_view.system_context)
            system_context["thread_id"] = record.thread_id
            system_context["primary_mode"] = record.primary_mode
            system_context["prompt_injection"] = str(payload.get("prompt_injection", "")).strip()
            self._merge_record_runtime_view_unlocked(record, system_context=system_context)
            record.updated_at = timestamp
            return

        if op == "runtime_view_updated":
            runtime_view_payload = _normalize_runtime_view_payload(
                payload.get("runtime_view", {}) if isinstance(payload.get("runtime_view"), dict) else {}
            )
            if runtime_view_payload:
                current_view = self._build_record_runtime_view_unlocked(record)
                incoming_view = extract_projected_runtime_view(runtime_view_payload)
                merged_view = merge_projected_runtime_views(current_view, incoming_view) or incoming_view
                if merged_view is not None:
                    self._set_record_runtime_view_unlocked(record, merged_view)
            record.updated_at = timestamp
            return

        if op == "file_view_recorded":
            current_view = self._build_record_runtime_view_unlocked(record)
            workspace = dict(current_view.workspace)
            recent = list(workspace.get("recent_views", []))
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
                workspace["recent_views"] = recent[-24:]
                workspace["recent_paths"] = [
                    str(item.get("path", "")).strip()
                    for item in workspace["recent_views"]
                    if isinstance(item, dict) and str(item.get("path", "")).strip()
                ][-24:]
                workspace["last_updated_at"] = timestamp
                self._merge_record_runtime_view_unlocked(record, workspace=workspace)
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
            layer = self._canonical_runtime_layer_name(payload.get("layer", "session"))
            memory_type = str(payload.get("memory_type", SESSION_MEMORY_TYPE)).strip() or SESSION_MEMORY_TYPE
            current_view = self._build_record_runtime_view_unlocked(record)
            hooks = dict(current_view.hooks)
            if note:
                notes = list(hooks.get("notes", []))
                notes.append(note)
                hooks["notes"] = notes[-24:]
            layer_state = dict(getattr(current_view, layer))
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
            if layer == "session":
                self._merge_record_runtime_view_unlocked(record, hooks=hooks, session=layer_state)
            else:
                self._merge_record_runtime_view_unlocked(record, hooks=hooks, workspace=layer_state)
            record.updated_at = timestamp
            self._apply_budget_unlocked(record, reason="notes")
            return

        if op == "summary_updated":
            layer = self._canonical_runtime_layer_name(payload.get("layer", "session"))
            summary = str(payload.get("summary", "")).strip()
            record.working_summary = summary
            current_view = self._build_record_runtime_view_unlocked(record)
            layer_state = dict(getattr(current_view, layer))
            layer_state["summary"] = summary
            if layer == "session":
                self._merge_record_runtime_view_unlocked(record, session=layer_state)
            else:
                self._merge_record_runtime_view_unlocked(record, workspace=layer_state)
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
