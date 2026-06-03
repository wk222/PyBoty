"""Session memory scheduler for rolling notebook extraction."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

_SESSION_NOTES_EXTRACTION_PROMPT = (
    "You are extracting structured session notes from an ongoing conversation.\n"
    "Produce a concise markdown summary covering:\n"
    "1. Current objective\n"
    "2. Key decisions made\n"
    "3. Files touched\n"
    "4. Progress status\n"
    "5. Open questions\n\n"
    "Keep it concise and preserve exact file paths and code identifiers.\n\n"
    "Conversation to summarize:\n{text}"
)

_MAX_NOTES_SIZE = 8000
_MAX_INPUT_CHARS = 12000


@dataclass
class SessionMemoryConfig:
    tool_call_interval: int = 8
    token_growth_interval: int = 30000
    natural_pause_interval: int = 4
    stale_extract_after_seconds: int = 30
    min_messages_for_extraction: int = 6
    storage_dir: str = ""
    thread_id: str = "default"


class SessionMemoryScheduler:
    """Extract rolling session notes on tool growth, token growth, or pauses."""

    def __init__(
        self,
        *,
        summarize_fn: Callable[[str], str] | None = None,
        config: SessionMemoryConfig | None = None,
        workspace_view: Any | None = None,
    ) -> None:
        self._summarize_fn = summarize_fn
        self._config = config or SessionMemoryConfig()
        self._workspace_view = workspace_view
        self._tool_call_count = 0
        self._token_count_at_last_extract = 0
        self._tool_calls_at_last_extract = 0
        self._turn_count = 0
        self._turn_at_last_extract = 0
        self._extraction_count = 0
        self._notes: str | None = None
        self._notes_loaded = False
        self._extract_status = "idle"
        self._extract_started_at = 0.0
        self._last_reason = ""

    @property
    def _notes_path(self) -> str:
        storage_dir = self._config.storage_dir
        if not storage_dir:
            return ""
        return os.path.join(storage_dir, f"{self._config.thread_id}_session_notes.md")

    @property
    def extraction_count(self) -> int:
        return self._extraction_count

    @property
    def tool_call_count(self) -> int:
        return self._tool_call_count

    def get_notes(self) -> str | None:
        if self._notes is not None:
            return self._notes

        if not self._notes_loaded:
            self._notes_loaded = True
            path = self._notes_path
            if path and os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        raw = handle.read().strip()
                    if raw.startswith("<!--"):
                        newline = raw.find("\n")
                        if newline >= 0:
                            raw = raw[newline + 1 :].strip()
                    self._notes = raw or None
                except Exception:
                    self._notes = None
        return self._notes

    def get_scheduler_state(self) -> dict[str, Any]:
        return {
            "status": self._extract_status,
            "last_reason": self._last_reason,
            "tool_call_count": self._tool_call_count,
            "token_count_at_last_extract": self._token_count_at_last_extract,
            "tool_calls_at_last_extract": self._tool_calls_at_last_extract,
            "turn_count": self._turn_count,
            "turn_at_last_extract": self._turn_at_last_extract,
            "extraction_count": self._extraction_count,
        }

    def tick(
        self,
        messages: list[Any],
        tool_call_delta: int = 0,
        current_token_count: int = 0,
    ) -> bool:
        self._tool_call_count += max(0, int(tool_call_delta or 0))
        self._turn_count += 1

        if len(messages) < self._config.min_messages_for_extraction:
            return False
        if not self._ready_for_new_extract():
            return False

        reason = self._resolve_trigger_reason(messages, current_token_count=current_token_count)
        if not reason:
            return False
        return self._extract(messages, current_token_count=current_token_count, reason=reason)

    def force_extract(self, messages: list[Any], current_token_count: int = 0) -> bool:
        if len(messages) < 2:
            return False
        if not self._ready_for_new_extract(force=True):
            return False
        return self._extract(messages, current_token_count=current_token_count, reason="forced")

    def _ready_for_new_extract(self, *, force: bool = False) -> bool:
        if self._extract_status != "running":
            return True
        if force:
            self._extract_status = "idle"
            return True
        if time.time() - self._extract_started_at >= max(1, int(self._config.stale_extract_after_seconds)):
            logger.debug("Resetting stale session-memory extraction")
            self._extract_status = "idle"
            return True
        return False

    def _resolve_trigger_reason(self, messages: list[Any], *, current_token_count: int) -> str:
        if self._tool_call_count - self._tool_calls_at_last_extract >= self._config.tool_call_interval:
            return "tool_density"
        if (
            current_token_count > 0
            and current_token_count - self._token_count_at_last_extract >= self._config.token_growth_interval
        ):
            return "token_growth"
        if (
            self._turn_count - self._turn_at_last_extract >= self._config.natural_pause_interval
            and self._is_natural_pause(messages)
        ):
            return "natural_pause"
        return ""

    def _extract(self, messages: list[Any], *, current_token_count: int, reason: str) -> bool:
        self._extract_status = "running"
        self._extract_started_at = time.time()
        self._last_reason = reason
        try:
            if self._summarize_fn is not None:
                text = self._build_conversation_text(
                    messages,
                    workspace_view_context=self._build_workspace_view_context(),
                )
                if text:
                    notes = self._summarize_fn(_SESSION_NOTES_EXTRACTION_PROMPT.format(text=text))
                    if notes and len(notes.strip()) > 20:
                        self._commit_notes(notes.strip(), current_token_count=current_token_count)
                        return True
            return self._extract_fallback(messages, current_token_count=current_token_count)
        except Exception as exc:
            logger.warning("Session notes extraction failed: %s", exc)
            return self._extract_fallback(messages, current_token_count=current_token_count)
        finally:
            self._extract_status = "idle"

    def _extract_fallback(self, messages: list[Any], *, current_token_count: int) -> bool:
        files: list[str] = []
        tools_used: list[str] = []
        user_requests: list[str] = []

        for msg in messages:
            msg_type = getattr(msg, "type", "")
            if msg_type == "human":
                content = getattr(msg, "content", "")
                if isinstance(content, str) and content.strip():
                    user_requests.append(content.strip()[:120])
                continue

            if msg_type == "ai":
                for tool_call in getattr(msg, "tool_calls", []) or []:
                    tool_name = _as_text(tool_call.get("name"))
                    if tool_name:
                        tools_used.append(tool_name)
                    args = tool_call.get("args", {}) if isinstance(tool_call.get("args"), dict) else {}
                    for key in ("path", "file_path", "target_path", "command"):
                        value = _as_text(args.get(key))
                        if value:
                            files.append(value[:120])

        projection = self._workspace_view_projection()
        for item in projection.get("recent_views", []):
            if isinstance(item, dict) and _as_text(item.get("path")):
                files.append(_as_text(item.get("path"))[:120])

        if not user_requests and not tools_used and not files:
            return False

        lines = ["# Session Notes", "", "## Current objective"]
        if user_requests:
            lines.extend(f"- {item}" for item in user_requests[-5:])
        else:
            lines.append("- Continue the current thread")

        if tools_used:
            lines.extend(["", "## Tools used", ", ".join(dict.fromkeys(tools_used))])

        if files:
            lines.extend(["", "## Files touched"])
            lines.extend(f"- {item}" for item in dict.fromkeys(files))

        notes = "\n".join(lines).strip()
        self._commit_notes(notes, current_token_count=current_token_count)
        return True

    def _commit_notes(self, notes: str, *, current_token_count: int) -> None:
        capped = notes[:_MAX_NOTES_SIZE]
        self._notes = capped
        self._tool_calls_at_last_extract = self._tool_call_count
        self._token_count_at_last_extract = current_token_count
        self._turn_at_last_extract = self._turn_count
        self._extraction_count += 1

        path = self._notes_path
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"<!-- Updated: {timestamp} -->\n{capped}\n")
        except Exception as exc:
            logger.warning("Failed to persist session notes: %s", exc)

    @staticmethod
    def _build_conversation_text(messages: list[Any], workspace_view_context: str = "") -> str:
        parts: list[str] = []
        total = 0

        def append_chunk(chunk: str) -> bool:
            nonlocal total
            if not chunk:
                return True
            if total + len(chunk) > _MAX_INPUT_CHARS:
                return False
            parts.append(chunk)
            total += len(chunk)
            return True

        for msg in reversed(messages):
            msg_type = getattr(msg, "type", "")
            content = getattr(msg, "content", "")
            tool_calls = getattr(msg, "tool_calls", None) or []

            if msg_type == "ai" and tool_calls:
                previews: list[str] = []
                for tool_call in tool_calls[:3]:
                    args_preview = repr(tool_call.get("args", {}))
                    if len(args_preview) > 160:
                        args_preview = f"{args_preview[:157]}..."
                    previews.append(f"{tool_call.get('name', 'tool')}({args_preview})")
                if previews and not append_chunk(f"[Assistant tool calls]: {'; '.join(previews)}"):
                    break

            if not isinstance(content, str) or not content.strip():
                continue
            role = "User" if msg_type == "human" else "Assistant" if msg_type == "ai" else "Tool"
            snippet = content[:800] if len(content) > 800 else content
            if not append_chunk(f"[{role}]: {snippet}"):
                break

        parts.reverse()
        if workspace_view_context:
            parts.insert(0, workspace_view_context)
        return "\n\n".join(parts)

    def _workspace_view_projection(self) -> dict[str, Any]:
        if self._workspace_view is None or not hasattr(self._workspace_view, "build_projection"):
            return {}
        try:
            projection = self._workspace_view.build_projection(limit=6)
            return projection if isinstance(projection, dict) else {}
        except Exception as exc:
            logger.debug("Workspace view projection failed: %s", exc)
            return {}

    def _build_workspace_view_context(self) -> str:
        projection = self._workspace_view_projection()
        recent_views = projection.get("recent_views", [])
        if not isinstance(recent_views, list) or not recent_views:
            return ""

        lines = ["[Workspace views]"]
        for item in recent_views[-6:]:
            if not isinstance(item, dict):
                continue
            path = _as_text(item.get("path"))
            if not path:
                continue
            view_kind = _as_text(item.get("view_kind")) or "view"
            line_range = _as_text(item.get("line_range"))
            suffix = f" {line_range}" if line_range else ""
            lines.append(f"- {path} ({view_kind}{suffix})")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _is_natural_pause(messages: list[Any]) -> bool:
        if not messages:
            return False
        tail = messages[-3:]
        for msg in tail:
            if getattr(msg, "type", "") == "ai" and getattr(msg, "tool_calls", None):
                return False
        return True


# Keep the old import name but move the canonical behavior into the scheduler.
SessionMemoryExtractor = SessionMemoryScheduler


def _as_text(value: Any) -> str:
    return str(value or "").strip()


__all__ = [
    "SessionMemoryConfig",
    "SessionMemoryExtractor",
    "SessionMemoryScheduler",
]
