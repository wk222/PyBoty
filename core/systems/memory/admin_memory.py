"""Memory helpers for the ultimate-agent mode."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage


def create_llm_summarizer(llm: BaseChatModel | None) -> Callable[[str], str] | None:
    """Build a lightweight text summarizer from a base chat model."""
    if llm is None:
        return None

    def _summarize(text: str) -> str:
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "Summarize the following execution trace into a compact memory note. "
                        "Preserve key decisions, artifacts, blockers, and outcomes. "
                        "Return plain text only."
                    )
                ),
                HumanMessage(content=text[:6000]),
            ]
        )
        return response.content if hasattr(response, "content") else str(response)

    return _summarize


@dataclass
class AdminMemoryConfig:
    recent_entries_limit: int = 4
    max_entry_chars: int = 400
    max_field_chars: int = 600
    max_summary_chars: int = 1200
    max_artifacts: int = 8


class AdminMemoryManager:
    """Maintains compressed step memory for durable admin tasks."""

    def __init__(
        self,
        *,
        summarize_fn: Callable[[str], str] | None = None,
        config: AdminMemoryConfig | None = None,
    ) -> None:
        self._summarize_fn = summarize_fn
        self._config = config or AdminMemoryConfig()

    def build_prompt_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """Project a large task context into a compact prompt-friendly view."""
        prompt_context: dict[str, Any] = {}

        for key in ("plan_summary", "planning_notes", "success_criteria", "last_step_summary", "last_replan_reason"):
            if key in context:
                prompt_context[key] = context[key]

        plan = context.get("admin_plan")
        if isinstance(plan, dict):
            prompt_context["admin_plan"] = {
                "summary": plan.get("summary", ""),
                "steps": list(plan.get("steps", []))[:8],
                "success_criteria": list(plan.get("success_criteria", []))[:5],
            }

        memory = context.get("admin_memory")
        if isinstance(memory, dict):
            prompt_context["memory_summary"] = memory.get("summary", "")
            prompt_context["recent_memory_entries"] = list(memory.get("recent_entries", []))
            prompt_context["artifacts"] = memory.get("artifacts", {})

        compact_scalars: dict[str, Any] = {}
        for key, value in context.items():
            if key in {
                "admin_plan",
                "admin_memory",
                "success_criteria",
                "planning_notes",
                "plan_summary",
                "last_step_summary",
                "last_replan_reason",
            }:
                continue
            compact_scalars[key] = self._compact_value(value, self._config.max_field_chars)
        if compact_scalars:
            prompt_context["state"] = compact_scalars
        return prompt_context

    def build_context_update(
        self,
        *,
        task_name: str,
        step_id: str,
        step_description: str,
        raw_output: dict[str, Any],
        current_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Create the context update persisted after a step completes."""
        context_update = self._project_output_fields(raw_output)
        memory = self._load_memory(current_context)

        entry = {
            "step_id": step_id,
            "description": step_description,
            "summary": self._summarize_output(
                task_name=task_name,
                step_description=step_description,
                raw_output=raw_output,
            ),
        }
        memory["recent_entries"].append(entry)
        if len(memory["recent_entries"]) > self._config.recent_entries_limit:
            overflow = memory["recent_entries"][: -self._config.recent_entries_limit]
            memory["recent_entries"] = memory["recent_entries"][-self._config.recent_entries_limit :]
            memory["summary"] = self._merge_summary(memory.get("summary", ""), overflow)
        elif not memory.get("summary") and len(memory["recent_entries"]) > 1:
            memory["summary"] = self._merge_summary("", memory["recent_entries"][:-1])

        self._update_artifacts(memory, raw_output)
        context_update["admin_memory"] = memory
        context_update["last_step_summary"] = entry["summary"]
        return context_update

    def _load_memory(self, context: dict[str, Any]) -> dict[str, Any]:
        memory = context.get("admin_memory")
        if isinstance(memory, dict):
            return {
                "summary": str(memory.get("summary", ""))[: self._config.max_summary_chars],
                "recent_entries": list(memory.get("recent_entries", []))[-self._config.recent_entries_limit :],
                "artifacts": dict(memory.get("artifacts", {})),
            }
        return {"summary": "", "recent_entries": [], "artifacts": {}}

    def _update_artifacts(self, memory: dict[str, Any], raw_output: dict[str, Any]) -> None:
        artifacts = dict(memory.get("artifacts", {}))
        for key, value in raw_output.items():
            if key in {"replan_required", "replan_reason", "replacement_steps", "next_steps"}:
                continue
            artifacts[key] = self._compact_value(value, self._config.max_field_chars)

        ordered_items = list(artifacts.items())[-self._config.max_artifacts :]
        memory["artifacts"] = {key: value for key, value in ordered_items}

    def _project_output_fields(self, raw_output: dict[str, Any]) -> dict[str, Any]:
        context_update: dict[str, Any] = {}
        for key, value in raw_output.items():
            if key in {"replan_required", "replan_reason", "replacement_steps", "next_steps"}:
                context_update[key] = value
                continue
            context_update[key] = self._compact_value(value, self._config.max_field_chars)
        return context_update

    def _summarize_output(
        self,
        *,
        task_name: str,
        step_description: str,
        raw_output: dict[str, Any],
    ) -> str:
        text = (
            f"Task: {task_name}\n"
            f"Step: {step_description}\n"
            f"Output: {json.dumps(raw_output, ensure_ascii=False, default=str)[:4000]}"
        )
        if self._summarize_fn is not None:
            try:
                summary = self._summarize_fn(text)
                return summary[: self._config.max_entry_chars]
            except Exception:
                pass
        return self._compact_value(raw_output, self._config.max_entry_chars)

    def _merge_summary(self, existing: str, overflow_entries: list[dict[str, Any]]) -> str:
        merged_text = existing.strip()
        overflow_text = "\n".join(f"- {entry.get('step_id')}: {entry.get('summary', '')}" for entry in overflow_entries)
        if not overflow_text:
            return merged_text[: self._config.max_summary_chars]
        combined = "\n".join(part for part in [merged_text, overflow_text] if part)
        if self._summarize_fn is not None:
            try:
                return self._summarize_fn(combined)[: self._config.max_summary_chars]
            except Exception:
                pass
        return combined[-self._config.max_summary_chars :]

    def _compact_value(self, value: Any, limit: int) -> Any:
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        if isinstance(value, str):
            return value if len(value) <= limit else value[:limit] + "...(truncated)"
        if isinstance(value, list):
            items = [self._compact_value(item, max(80, limit // 2)) for item in value[:10]]
            if len(value) > 10:
                items.append(f"...({len(value) - 10} more items)")
            return items
        if isinstance(value, dict):
            compacted = {
                str(key): self._compact_value(item, max(80, limit // 2)) for key, item in list(value.items())[:10]
            }
            if len(value) > 10:
                compacted["__truncated__"] = f"{len(value) - 10} more keys"
            return compacted
        rendered = str(value)
        return rendered if len(rendered) <= limit else rendered[:limit] + "...(truncated)"


# Keep old name but prefer shorter one for public API
AdminMemory = AdminMemoryManager

