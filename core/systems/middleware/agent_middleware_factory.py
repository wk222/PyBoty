"""Factories for assembling LangChain middleware stacks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.systems.bus.lc_bus_middleware import LCBusMiddleware
from core.systems.runtime.patch_tool_calls import PatchToolCallsMiddleware
from core.systems.runtime.instruction_assembly import InstructionAssembly

from .agent_prompt_middleware import PromptSectionMiddleware
from .insight_vault_middleware import InsightVaultConfig, InsightVaultMiddleware
from .lc_memory_middleware import LCMemoryMiddleware
from .loop_guard_middleware import LoopGuardConfig, LoopGuardMiddleware
from .reasoning_frame_middleware import ReasoningFrameConfig, ReasoningFrameMiddleware
from .summarization_middleware import SummarizationConfig, SummarizationMiddleware
from .todo_middleware import TodoListMiddleware


def build_root_langchain_middleware(
    *,
    runtime: Any,
    summarize_fn: Any | None = None,
    summarization_config: SummarizationConfig | None = None,
    session_compaction_callback: Any | None = None,
    session_memory_extractor: Any | None = None,
    eviction_dir: str | None = None,
    loop_guard_config: LoopGuardConfig | None = None,
    insight_vault_config: InsightVaultConfig | None = None,
    reasoning_frame_config: ReasoningFrameConfig | None = None,
    vector_store: Any | None = None,
    runtime_view_provider: Callable[[], dict | None] | None = None,
) -> list[Any]:
    """Assemble the root-agent LangChain middleware stack.

    Ordering:
      LoopGuard → TodoList → PromptContext → InsightVault → ReasoningFrame →
      Memory → Summarization → BusRecorder → ToolEviction →
      DynamicToolMiddleware → AnthropicCaching → PatchToolCalls
    """
    todo_middleware = TodoListMiddleware(task_runtime=getattr(runtime, "task_runtime", None))

    def _resolve_runtime_tool_projection() -> dict | None:
        tool_middleware = getattr(runtime, "middleware", None)
        if tool_middleware is None or not hasattr(tool_middleware, "get_control_snapshot"):
            return None
        try:
            snapshot = tool_middleware.get_control_snapshot()
        except Exception:
            return None
        observability = snapshot.get("observability", {}) if isinstance(snapshot, dict) else {}
        recent_events = observability.get("recent_events", []) if isinstance(observability, dict) else []
        runs: list[dict[str, Any]] = []
        for event in recent_events[-6:]:
            if not isinstance(event, dict):
                continue
            tool_name = str(event.get("tool_name", "")).strip()
            if not tool_name:
                continue
            status = "completed"
            if event.get("requires_approval"):
                status = "approval_required"
            elif not event.get("allowed", True):
                status = "blocked"
            runs.append(
                {
                    "title": tool_name,
                    "status": status,
                    "source": "tool_control",
                    "run_id": str(event.get("tool_call_id", "")).strip(),
                    "preview": str(event.get("args_preview", "")).strip(),
                    "timestamp": event.get("timestamp"),
                }
            )
        task_runtime = getattr(runtime, "task_runtime", None)
        if task_runtime is not None:
            try:
                task_runtime.ingest_tool_runs(runs, source="tool_control")
                permission_snapshot = snapshot.get("permission", {}) if isinstance(snapshot, dict) else {}
                if isinstance(permission_snapshot, dict):
                    task_runtime.ingest_permission_events(
                        list(permission_snapshot.get("recent_events", [])),
                        source="permission_projection",
                    )
            except Exception:
                pass
        if not runs:
            return None
        return {"recent_tool_runs": runs}

    def _resolve_task_runtime_projection() -> dict | None:
        task_runtime = getattr(runtime, "task_runtime", None)
        if task_runtime is None or not hasattr(task_runtime, "build_projection"):
            return None
        try:
            return task_runtime.build_projection()
        except Exception:
            return None

    def _resolve_resume_artifacts() -> dict | None:
        base_artifacts = runtime_view_provider() if runtime_view_provider is not None else None
        tool_projection = _resolve_runtime_tool_projection()
        todo_projection = todo_middleware.export_projection()
        task_runtime_projection = _resolve_task_runtime_projection()
        if todo_projection is None and tool_projection is None and task_runtime_projection is None:
            return base_artifacts
        from core.systems.runtime.session.session_runtime_view import (
            compile_runtime_resume_view,
            runtime_view_from_resume_dict,
        )
        from core.systems.runtime.projected_runtime_view import (
            build_projected_runtime_view,
            build_runtime_task_section,
            merge_projected_runtime_views,
        )

        system_context = dict(base_artifacts.get("system_context", {})) if isinstance(base_artifacts, dict) else {}
        overlay_view = build_projected_runtime_view(
            thread_id=str(system_context.get("thread_id", "")).strip() or "default",
            root_mode=str(system_context.get("primary_mode", "")).strip() or "assistant",
            system_context=system_context,
            tasks=build_runtime_task_section(
                task_runtime=task_runtime_projection or {},
                task_projection=todo_projection or {},
                recent_tool_runs=(
                    list((tool_projection or {}).get("recent_tool_runs", []))
                    if isinstance(tool_projection, dict)
                    else []
                ),
            ),
        )
        merged_view = merge_projected_runtime_views(runtime_view_from_resume_dict(base_artifacts), overlay_view) or overlay_view
        return compile_runtime_resume_view(merged_view)

    def _resolve_resume_runtime_view() -> dict | None:
        artifacts = _resolve_resume_artifacts()
        if not isinstance(artifacts, dict):
            return None
        runtime_view = artifacts.get("projected_runtime_view")
        return dict(runtime_view) if isinstance(runtime_view, dict) and runtime_view else None

    stack: list[Any] = [
        LoopGuardMiddleware(config=loop_guard_config),
        todo_middleware,
        PromptSectionMiddleware(
            name="RootPromptContextMiddleware",
            prompt_builder=lambda: InstructionAssembly.build_runtime_sections(
                workspace_context=runtime.workspace.build_system_context(),
                memory_context=runtime.memory.get_context_prompt(),
                skill_extensions=runtime.skill_registry.get_active_prompt_extensions(progressive=True),
                projected_runtime_view=_resolve_resume_runtime_view(),
                hooks_runtime=getattr(runtime, "hooks_runtime", None),
            ),
        ),
        InsightVaultMiddleware(
            vector_store=vector_store,
            config=insight_vault_config,
        ),
        ReasoningFrameMiddleware(config=reasoning_frame_config),
        LCMemoryMiddleware(runtime.memory),
        SummarizationMiddleware(
            summarize_fn=summarize_fn,
            config=summarization_config,
            compaction_callback=session_compaction_callback,
            session_memory_extractor=session_memory_extractor,
            runtime_view_provider=_resolve_resume_runtime_view,
            hooks_runtime=getattr(runtime, "hooks_runtime", None),
        ),
        LCBusMiddleware(runtime.capability_bus),
    ]
    if eviction_dir:
        from core.assets.tools import LCToolEvictionMiddleware
        stack.append(LCToolEvictionMiddleware(eviction_dir=eviction_dir))

    from core.assets.tools import ToolArgRepairMiddleware
    arg_repair = ToolArgRepairMiddleware()
    stack.append(arg_repair)

    stack.append(runtime.middleware)
    try:
        from langchain_anthropic.chat_models import AnthropicPromptCachingMiddleware

        stack.append(AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"))
    except ImportError:
        pass
    stack.append(PatchToolCallsMiddleware())
    return stack
