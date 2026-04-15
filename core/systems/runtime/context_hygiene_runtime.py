"""Context hygiene runtime for microcompact, compaction, and history snips."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

try:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, get_buffer_string

    _HAS_LC = True
except ImportError:
    _HAS_LC = False
    AIMessage = object  # type: ignore[assignment,misc]
    HumanMessage = object  # type: ignore[assignment,misc]
    ToolMessage = object  # type: ignore[assignment,misc]

from core.systems.context import count_tokens_approx

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "Summarize the following conversation in 3-8 sentences. "
    "Preserve key facts, decisions, action items, and any file "
    "paths or code snippets that are still relevant:\n\n{text}"
)

EPISODE_SUMMARY_PROMPT = (
    "Condense this tool interaction into one concise line. "
    "Include: tool name, key input, and outcome. Example: "
    "'write_file(main.py) -> created 45-line Flask app'\n\n{text}"
)

TRUNCATABLE_TOOLS = frozenset({"write_file", "edit_file", "create_file"})
COMPACTABLE_TOOLS = frozenset(
    {
        "read_file",
        "bash",
        "grep_files",
        "glob_files",
        "write_file",
        "str_replace",
        "web_fetch",
    }
)
MICROCOMPACT_STUB_PREFIX = "[microcompact] "
MICROCOMPACT_MAX_STUB_CHARS = 200
MICROCOMPACT_MIN_CONTENT_CHARS = 500


def count_message_tokens(messages: list[Any]) -> int:
    total = 0
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            total += count_tokens_approx(content)
        elif isinstance(content, list):
            for part in content:
                total += count_tokens_approx(str(part))
        for tool_call in getattr(msg, "tool_calls", []) or []:
            total += count_tokens_approx(str(tool_call.get("args", "")))
    return total


class ContextHygieneRuntime:
    """Single owner for runtime context cleanup and compaction state."""

    def __init__(self, *, config: Any, hooks_runtime: Any | None = None) -> None:
        self._config = config
        self._hooks_runtime = hooks_runtime
        self.summary_message: HumanMessage | None = None
        self.cutoff_index: int = 0
        self.last_microcompact_count: int = 0
        self._snip_boundaries: list[dict[str, Any]] = []

    def get_effective_messages(self, messages: list[Any]) -> list[Any]:
        if self.summary_message is None:
            return list(messages)
        effective: list[Any] = [self.summary_message]
        if self.cutoff_index < len(messages):
            effective.extend(messages[self.cutoff_index :])
        return effective

    def prepare_effective_messages(self, messages: list[Any]) -> list[Any]:
        effective = self.get_effective_messages(messages)
        effective = self.microcompact(effective)
        return self.truncate_tool_args(effective)

    def microcompact(self, messages: list[Any]) -> list[Any]:
        if not _HAS_LC:
            return messages
        message_count = len(messages)
        cutoff = max(0, message_count - max(self._config.keep_recent_messages, self._config.microcompact_age))
        if cutoff == 0:
            self.last_microcompact_count = 0
            return messages

        compacted_count = 0
        compacted: list[Any] = []
        for index, message in enumerate(messages):
            if (
                index < cutoff
                and isinstance(message, ToolMessage)
                and getattr(message, "name", None) in COMPACTABLE_TOOLS
            ):
                content = getattr(message, "content", "")
                if (
                    isinstance(content, str)
                    and len(content) >= MICROCOMPACT_MIN_CONTENT_CHARS
                    and not content.startswith(MICROCOMPACT_STUB_PREFIX)
                ):
                    replacement = message.model_copy()
                    replacement.content = self.make_microcompact_stub(
                        self.tool_call_preview(messages, index, message),
                        content,
                    )
                    compacted.append(replacement)
                    compacted_count += 1
                    continue
            compacted.append(message)

        self.last_microcompact_count = compacted_count
        if compacted_count:
            logger.debug("context hygiene microcompact replaced %d tool result(s)", compacted_count)
        return compacted

    def truncate_tool_args(self, messages: list[Any]) -> list[Any]:
        if len(messages) < self._config.tool_arg_trigger_messages:
            return messages
        cutoff = max(0, len(messages) - self._config.keep_recent_messages)
        truncated_messages: list[Any] = []
        for index, message in enumerate(messages):
            if index >= cutoff or not isinstance(message, AIMessage) or not getattr(message, "tool_calls", None):
                truncated_messages.append(message)
                continue
            modified = False
            tool_calls: list[dict[str, Any]] = []
            for tool_call in message.tool_calls:
                if tool_call.get("name") not in TRUNCATABLE_TOOLS:
                    tool_calls.append(tool_call)
                    continue
                next_args: dict[str, Any] = {}
                for key, value in tool_call.get("args", {}).items():
                    if isinstance(value, str) and len(value) > self._config.max_tool_arg_chars:
                        next_args[key] = value[:20] + self._config.tool_arg_truncation_text
                        modified = True
                    else:
                        next_args[key] = value
                tool_calls.append({**tool_call, "args": next_args})
            if modified:
                replacement = message.model_copy()
                replacement.tool_calls = tool_calls
                truncated_messages.append(replacement)
            else:
                truncated_messages.append(message)
        return truncated_messages

    def suggest_garden_updates(
        self,
        summary: str,
        *,
        summarize_fn: Callable[[str], str] | None,
    ) -> list[str]:
        """Review a summary and suggest high-importance facts for the long-term Garden."""
        if summarize_fn is None or not summary.strip():
            return []

        prompt = (
            "Review the following conversation summary. Identify any high-importance technical "
            "contracts, architectural decisions, or long-term facts that should be persisted in "
            "a 'Markdown Garden' (long-term memory). "
            "Return a JSON array of specific, concise strings describing these facts. "
            "If nothing is important enough, return [].\n\n"
            f"Summary:\n{summary}"
        )
        try:
            res = summarize_fn(prompt)
            import json

            try:
                # Basic cleaning of markdown json blocks
                content = res.strip()
                if content.startswith("```json"):
                    content = content[7:]
                if content.endswith("```"):
                    content = content[:-3]
                suggestions = json.loads(content.strip())
                return [str(s) for s in suggestions if str(s).strip()]
            except Exception:
                return [s.strip() for s in res.split("\n") if s.strip()]
        except Exception:
            return []

    def summarize(
        self,
        effective: list[Any],
        *,
        summarize_fn: Callable[[str], str] | None,
        session_notes: str = "",
        resume_bundle_text: str = "",
        compaction_callback: Callable[[dict[str, Any]], None] | None = None,
        projected_runtime_view: dict[str, Any] | None = None,
    ) -> int | None:
        keep = self._config.keep_recent_messages
        mid = self._config.mid_tier_messages
        hook_result = self._run_hook_phase(
            "context_hygiene_compact_decision",
            {
                "projected_runtime_view": dict(projected_runtime_view or {}),
                "message_count": len(effective),
                "keep_recent_messages": keep,
                "session_notes_present": bool(session_notes.strip()),
                "resume_bundle_present": bool(resume_bundle_text.strip()),
            },
        )
        force_compact = bool(hook_result.get("force_compact"))
        if hook_result.get("force_skip"):
            return None
        if len(effective) <= keep and not force_compact:
            return None

        cutoff = len(effective) - keep if len(effective) > keep else max(1, len(effective) - 1)
        to_summarize = effective[:cutoff]
        had_summary = self.summary_message is not None
        self.offload(to_summarize)

        if cutoff > mid:
            old_batch = to_summarize[: cutoff - mid]
            mid_batch = to_summarize[cutoff - mid :]
            parts: list[str] = []
            bulk_summary = self.generate_summary(old_batch, summarize_fn=summarize_fn)
            if bulk_summary:
                parts.append(f"### Earlier context\n{bulk_summary}")
            episode_summaries = self.generate_episode_summaries(mid_batch, summarize_fn=summarize_fn)
            if episode_summaries:
                parts.append("### Recent actions\n" + "\n".join(f"- {item}" for item in episode_summaries))
            summary_text = "\n\n".join(parts).strip()
        else:
            episode_summaries = self.generate_episode_summaries(to_summarize, summarize_fn=summarize_fn)
            if episode_summaries:
                summary_text = "### Recent actions\n" + "\n".join(f"- {item}" for item in episode_summaries)
            else:
                summary_text = self.generate_summary(to_summarize, summarize_fn=summarize_fn) or ""

        if not summary_text:
            return None

        # Suggest garden updates based on the summary
        garden_suggestions = self.suggest_garden_updates(summary_text, summarize_fn=summarize_fn)

        rebuild_text = self.build_post_compact_rebuild(
            resume_bundle_text=resume_bundle_text,
            session_notes=session_notes,
            projected_runtime_view=projected_runtime_view,
        )
        if rebuild_text:
            summary_text = f"{rebuild_text}\n\n{summary_text}"

        file_hint = ""
        if self._config.offload_dir:
            file_hint = f"\n\nFull history saved to {self._config.offload_dir}/{self._config.thread_id}.md"

        self.summary_message = HumanMessage(
            content=f"[Previous conversation summarized]{file_hint}\n\n<summary>\n{summary_text}\n</summary>",
            additional_kwargs={"lc_source": "summarization"},
        )

        previous_cutoff = self.cutoff_index
        advance = cutoff - (1 if had_summary else 0)
        self.cutoff_index = previous_cutoff + advance

        boundary = self._record_history_snip(
            summary=summary_text,
            message_count=len(to_summarize),
            retained_recent_window=self._config.keep_recent_messages,
        )
        if garden_suggestions:
            boundary["garden_suggestions"] = garden_suggestions

        writeback_hook = self._run_hook_phase(
            "context_hygiene_writeback",
            {
                "projected_runtime_view": dict(projected_runtime_view or {}),
                "boundary": dict(boundary),
                "summary": summary_text,
            },
        )
        if writeback_hook:
            metadata = dict(boundary.get("metadata", {})) if isinstance(boundary.get("metadata"), dict) else {}
            hook_notes = [str(item).strip() for item in writeback_hook.get("notes", []) if str(item).strip()]
            session_tags = [str(item).strip() for item in writeback_hook.get("session_tags", []) if str(item).strip()]
            if hook_notes:
                metadata["hook_notes"] = hook_notes
            if session_tags:
                metadata["hook_session_tags"] = session_tags
            if isinstance(writeback_hook.get("boundary_annotations"), dict):
                metadata["boundary_annotations"] = dict(writeback_hook.get("boundary_annotations", {}))
            boundary["metadata"] = metadata
        if compaction_callback is not None:
            try:
                compaction_callback(boundary)
            except Exception as exc:
                logger.warning("Compaction callback failed: %s", exc)
        return len(to_summarize)

    def build_post_compact_rebuild(
        self,
        *,
        resume_bundle_text: str = "",
        session_notes: str = "",
        projected_runtime_view: dict[str, Any] | None = None,
    ) -> str:
        sections: list[str] = []
        if resume_bundle_text.strip():
            sections.append(f"### Resume bundle\n{resume_bundle_text.strip()}")
        elif session_notes.strip():
            sections.append(f"### Session notes\n{session_notes.strip()}")

        hook_result = self._run_hook_phase(
            "context_hygiene_rebuild",
            {
                "projected_runtime_view": dict(projected_runtime_view or {}),
                "resume_bundle_text": resume_bundle_text,
                "session_notes": session_notes,
            },
        )
        sections = [str(item).strip() for item in hook_result.get("prepend_sections", []) if str(item).strip()] + sections
        sections += [str(item).strip() for item in hook_result.get("append_sections", []) if str(item).strip()]
        note_lines = [str(item).strip() for item in hook_result.get("notes", []) if str(item).strip()]
        if note_lines:
            sections.append("### Hook Notes\n" + "\n".join(f"- {item}" for item in note_lines))
        if not sections:
            return ""
        return "### Post-compact rebuild\n" + "\n\n".join(sections)

    def generate_episode_summaries(
        self,
        messages: list[Any],
        *,
        summarize_fn: Callable[[str], str] | None,
    ) -> list[str]:
        summaries: list[str] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
                tool_call = message.tool_calls[0]
                tool_name = tool_call.get("name", "unknown")
                args_preview = str(tool_call.get("args", {}))[:80]
                result_preview = ""
                if index + 1 < len(messages):
                    next_message = messages[index + 1]
                    next_content = getattr(next_message, "content", "")
                    if isinstance(next_content, str):
                        result_preview = next_content[:100]
                if summarize_fn is not None:
                    try:
                        text = f"Tool: {tool_name}, Args: {args_preview}, Result: {result_preview}"
                        summaries.append(summarize_fn(EPISODE_SUMMARY_PROMPT.format(text=text)).strip())
                        index += 2
                        continue
                    except Exception:
                        pass
                summaries.append(f"{tool_name}({args_preview[:40]}) -> {result_preview[:60]}")
                index += 2
                continue
            index += 1
        return summaries

    def generate_summary(
        self,
        messages: list[Any],
        *,
        summarize_fn: Callable[[str], str] | None,
    ) -> str | None:
        if summarize_fn is not None:
            try:
                text = get_buffer_string(messages)[:8000]
                return summarize_fn(SUMMARY_PROMPT.format(text=text))
            except Exception as exc:
                logger.warning("LLM summarization failed: %s", exc)

        topics: list[str] = []
        for message in messages:
            content = getattr(message, "content", "")
            if isinstance(content, str) and len(content) > 5 and getattr(message, "type", "unknown") == "human":
                topics.append(content[:120])
        if not topics:
            return None
        return "Topics discussed: " + "; ".join(topics[-8:])

    def offload(self, messages: list[Any], *, projected_runtime_view: dict[str, Any] | None = None) -> None:
        offload_dir = self._config.offload_dir
        if not messages:
            return

        hook_result = self._run_hook_phase(
            "context_hygiene_offload",
            {
                "projected_runtime_view": dict(projected_runtime_view or {}),
                "message_count": len(messages),
                "offload_dir": offload_dir or "",
            },
        )
        if hook_result.get("abort_offload"):
            return

        final_offload_dir = hook_result.get("offload_strategy") or hook_result.get("offload_path") or offload_dir
        if not final_offload_dir:
            return

        try:
            os.makedirs(final_offload_dir, exist_ok=True)
            path = os.path.join(final_offload_dir, f"{self._config.thread_id}.md")
            timestamp = datetime.now(timezone.utc).isoformat()
            text = get_buffer_string([item for item in messages if not self.is_summary_msg(item)])
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"\n\n## Summarized at {timestamp}\n\n{text}\n")
        except Exception as exc:
            logger.warning("Offload failed: %s", exc)

    def build_projection(self) -> dict[str, Any]:
        latest_boundary = dict(self._snip_boundaries[-1]) if self._snip_boundaries else {}
        return {
            "summary_active": self.summary_message is not None,
            "current_cutoff_index": self.cutoff_index,
            "last_microcompact_count": self.last_microcompact_count,
            "history_snip_count": len(self._snip_boundaries),
            "latest_boundary": latest_boundary,
        }

    def _run_hook_phase(self, phase: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._hooks_runtime is None or not hasattr(self._hooks_runtime, "run_phase"):
            return {}
        try:
            result = self._hooks_runtime.run_phase(phase, payload)
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            logger.debug("Context hygiene hook failed for %s: %s", phase, exc)
            return {}

    def _record_history_snip(
        self,
        *,
        summary: str,
        message_count: int,
        retained_recent_window: int,
    ) -> dict[str, Any]:
        boundary = {
            "boundary_id": f"snip:{self._config.thread_id}:{len(self._snip_boundaries) + 1}",
            "thread_id": self._config.thread_id,
            "source": "middleware.summarization",
            "reason": "conversation_compaction",
            "summary": summary,
            "timestamp": time.time(),
            "message_count": int(message_count or 0),
            "recent_window": int(retained_recent_window or 0),
            "microcompact_count": self.last_microcompact_count,
            "history_snip_count": len(self._snip_boundaries) + 1,
            "offload_path": (
                os.path.join(self._config.offload_dir, f"{self._config.thread_id}.md")
                if self._config.offload_dir
                else ""
            ),
            "source_event_range": {
                "message_count": int(message_count or 0),
            },
            "retained_recent_window": {
                "recent_window_messages": int(retained_recent_window or 0),
            },
            "metadata": {
                "offload_path": (
                    os.path.join(self._config.offload_dir, f"{self._config.thread_id}.md")
                    if self._config.offload_dir
                    else ""
                ),
                "microcompact_count": self.last_microcompact_count,
                "history_snip_count": len(self._snip_boundaries) + 1,
                "cutoff_index": self.cutoff_index,
            },
        }
        self._snip_boundaries.append(boundary)
        self._snip_boundaries = self._snip_boundaries[-16:]
        return dict(boundary)

    @staticmethod
    def make_microcompact_stub(tool_label: str, content: str) -> str:
        first_line = content.split("\n", 1)[0].strip()
        if len(first_line) > MICROCOMPACT_MAX_STUB_CHARS:
            first_line = first_line[:MICROCOMPACT_MAX_STUB_CHARS] + "..."
        char_count = len(content)
        line_count = content.count("\n") + 1
        return (
            f"{MICROCOMPACT_STUB_PREFIX}{tool_label} result compacted "
            f"({line_count} lines, {char_count} chars)\n"
            f"First line: {first_line}"
        )

    @staticmethod
    def format_tool_call_preview(tool_name: str, args: Any) -> str:
        normalized_name = str(tool_name or "tool").strip() or "tool"
        if not isinstance(args, dict) or not args:
            return normalized_name
        for key in ("path", "file_path", "command", "pattern", "url"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                preview = value.strip()
                if len(preview) > 72:
                    preview = preview[:69] + "..."
                return f"{normalized_name}({key}={preview})"
        preview = repr(args)
        if len(preview) > 80:
            preview = preview[:77] + "..."
        return f"{normalized_name}({preview})"

    def tool_call_preview(self, messages: list[Any], tool_index: int, tool_message: ToolMessage) -> str:
        tool_name = getattr(tool_message, "name", None) or "tool"
        tool_call_id = str(getattr(tool_message, "tool_call_id", "") or "").strip()
        fallback_match: dict[str, Any] | None = None
        for index in range(tool_index - 1, -1, -1):
            message = messages[index]
            if not isinstance(message, AIMessage):
                continue
            for tool_call in reversed(getattr(message, "tool_calls", None) or []):
                if tool_call_id and str(tool_call.get("id", "")).strip() == tool_call_id:
                    return self.format_tool_call_preview(tool_call.get("name", tool_name), tool_call.get("args", {}))
                if fallback_match is None and tool_call.get("name") == tool_name:
                    fallback_match = tool_call
            if fallback_match is not None:
                break
        if fallback_match is not None:
            return self.format_tool_call_preview(fallback_match.get("name", tool_name), fallback_match.get("args", {}))
        return str(tool_name)

    @staticmethod
    def is_summary_msg(message: Any) -> bool:
        if not isinstance(message, HumanMessage):
            return False
        return message.additional_kwargs.get("lc_source") == "summarization"


__all__ = [
    "COMPACTABLE_TOOLS",
    "ContextHygieneRuntime",
    "EPISODE_SUMMARY_PROMPT",
    "MICROCOMPACT_STUB_PREFIX",
    "SUMMARY_PROMPT",
    "TRUNCATABLE_TOOLS",
    "count_message_tokens",
]
