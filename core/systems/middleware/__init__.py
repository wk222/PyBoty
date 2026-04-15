"""Middleware chain — Layer 1 (First Branch) of PyBot's tree.

Provides the agent/tool middleware pipeline: summarization, reasoning frame,
loop guard, memory, insight vault, todo tracking, and prompt-section injection.

Depends on Layer 0 (runtime/session/context).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    # summarization_middleware
    "SummarizationConfig": (".summarization_middleware", "SummarizationConfig"),
    "SummarizationMiddleware": (".summarization_middleware", "SummarizationMiddleware"),
    # reasoning_frame_middleware
    "ReasoningFrameConfig": (".reasoning_frame_middleware", "ReasoningFrameConfig"),
    "ReasoningFrameMiddleware": (".reasoning_frame_middleware", "ReasoningFrameMiddleware"),
    # loop_guard_middleware
    "LoopGuardConfig": (".loop_guard_middleware", "LoopGuardConfig"),
    "LoopGuardMiddleware": (".loop_guard_middleware", "LoopGuardMiddleware"),
    "LoopGuardStats": (".loop_guard_middleware", "LoopGuardStats"),
    # insight_vault_middleware
    "InsightVaultConfig": (".insight_vault_middleware", "InsightVaultConfig"),
    "InsightVaultMiddleware": (".insight_vault_middleware", "InsightVaultMiddleware"),
    # todo_middleware
    "TodoItem": (".todo_middleware", "TodoItem"),
    "TodoState": (".todo_middleware", "TodoState"),
    "TodoListMiddleware": (".todo_middleware", "TodoListMiddleware"),
    # lc_memory_middleware
    "LCMemoryMiddleware": (".lc_memory_middleware", "LCMemoryMiddleware"),
    # middleware_stack (deprecated but still exported)
    "MiddlewareStack": (".middleware_stack", "MiddlewareStack"),
    "MiddlewareBase": (".middleware_stack", "MiddlewareBase"),
    # middleware_registry
    "MiddlewareDescriptor": (".middleware_registry", "MiddlewareDescriptor"),
    "query_middlewares": (".middleware_registry", "query_middlewares"),
    "get_middleware_summary_for_agent": (".middleware_registry", "get_middleware_summary_for_agent"),
    # agent_middleware_factory
    "build_root_langchain_middleware": (".agent_middleware_factory", "build_root_langchain_middleware"),
    "build_subagent_langchain_middleware": (".agent_middleware_factory", "build_subagent_langchain_middleware"),
    # agent_prompt_middleware
    "PromptSectionMiddleware": (".agent_prompt_middleware", "PromptSectionMiddleware"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
