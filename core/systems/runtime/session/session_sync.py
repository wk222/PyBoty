"""Synchronization logic for PyBot sessions with external runtimes."""

from __future__ import annotations

import time
from typing import Any


class SessionSyncMixin:
    """Mixin for SessionRuntime to handle synchronization with external runtimes."""

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
                from core.systems.runtime.session.session_record import _extract_file_view_from_tool_payload

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
