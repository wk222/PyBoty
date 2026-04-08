"""Instruction assembly for static prompt layers and runtime state."""

from __future__ import annotations

from typing import Any

from core.systems.runtime.projected_runtime_view import coerce_projected_runtime_view, render_projected_runtime_view
from core.systems.runtime.hooks_runtime import HookPhase


class InstructionAssembly:
    """Single owner for stitching runtime prompt sections together."""

    @staticmethod
    def build_runtime_sections(
        *,
        workspace_context: str = "",
        memory_context: str = "",
        skill_extensions: str = "",
        projected_runtime_view: dict[str, Any] | None = None,
        hooks_runtime: Any | None = None,
    ) -> str:
        parts: list[str] = []
        runtime_view = coerce_projected_runtime_view(projected_runtime_view)
        if workspace_context.strip():
            parts.append(workspace_context.strip())
        skill_text = skill_extensions.strip() or "(none)"
        parts.append(f"### Active Skills\n{skill_text}")
        if memory_context.strip():
            parts.append(memory_context.strip())
        if runtime_view is not None:
            artifact_text = render_projected_runtime_view(runtime_view)
            if artifact_text:
                parts.append(artifact_text)
        if hooks_runtime is not None and hasattr(hooks_runtime, "run_phase"):
            try:
                hook_result = hooks_runtime.run_phase(
                    HookPhase.INSTRUCTION_ASSEMBLY,
                    {
                        "workspace_context": workspace_context,
                        "memory_context": memory_context,
                        "skill_extensions": skill_extensions,
                        "projected_runtime_view": runtime_view.to_payload() if runtime_view is not None else {},
                    },
                )
                prepend_sections = [str(item).strip() for item in hook_result.get("prepend_sections", []) if str(item).strip()]
                append_sections = [str(item).strip() for item in hook_result.get("append_sections", []) if str(item).strip()]
                parts = prepend_sections + parts + append_sections
            except Exception:
                pass
        return "\n\n".join(part for part in parts if part).strip()


__all__ = ["InstructionAssembly"]
