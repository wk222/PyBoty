"""Core data model for PyBot sessions."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from core.systems.runtime.projected_runtime_view import extract_projected_runtime_view


def _preview_text(content: str, *, limit: int = 160) -> str:
    normalized = " ".join(str(content).split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 3)]}..."


def _append_unique(items: list[str], value: str) -> None:
    normalized = str(value).strip()
    if normalized and normalized not in items:
        items.append(normalized)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _stable_text_hash(*parts: Any) -> str:
    payload = "||".join(str(part) for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_runtime_view_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(payload or {})
    view = extract_projected_runtime_view(data)
    if view is None:
        return {}
    return view.to_payload()


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
    timeline: list[dict[str, Any]] = field(default_factory=list)
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
            "timeline": list(self.timeline),
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
            timeline=[dict(item) for item in payload.get("timeline", []) if isinstance(item, dict)],
            gateway=payload.get("gateway", {}) if isinstance(payload.get("gateway"), dict) else {},
            latest_run=payload.get("latest_run") if isinstance(payload.get("latest_run"), dict) else None,
            metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {},
        )

    def verify_invariants(self) -> None:
        """Validate critical guarantees for session state and history."""
        last_ts = -1.0
        compaction_count = 0
        
        for index, event in enumerate(self.timeline):
            ts = float(event.get("timestamp", 0.0))
            if ts < last_ts:
                raise ValueError(f"Event ordering invariant violated at index {index}: timestamp {ts} < {last_ts}")
            last_ts = ts
            
            if event.get("kind") == "compaction":
                compaction_count += 1
                
        if self.timeline:
            last_event_ts = float(self.timeline[-1].get("timestamp", 0.0))
            if self.updated_at < last_event_ts:
                raise ValueError(f"Replay safety invariant violated: updated_at {self.updated_at} < last_event_ts {last_event_ts}")
                
        compiled_artifacts = self.metadata.get("compiled_artifacts", {})
        if isinstance(compiled_artifacts, dict):
            hygiene = compiled_artifacts.get("context_hygiene", {})
            if isinstance(hygiene, dict):
                snip_count = int(hygiene.get("history_snip_count", 0) or 0)
                if compaction_count > 0 and snip_count == 0:
                    raise ValueError("Compaction boundary invariant violated: compaction events exist but snip_count is 0")

