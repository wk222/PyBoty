"""Workspace view and context engine — Layer 0 (Root) of PyBot's tree.

Provides the token-aware context windowing, workspace file-view projection,
and pluggable context-engine / strategy framework that every higher layer
depends on for prompt assembly and context hygiene.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    # context_manager — token windowing
    "ContextConfig": (".context_manager", "ContextConfig"),
    "ContextWindowManager": (".context_manager", "ContextWindowManager"),
    "count_tokens_approx": (".context_manager", "count_tokens_approx"),
    # workspace_view — file-view projection
    "WorkspaceViewEntry": (".workspace_view", "WorkspaceViewEntry"),
    "WorkspaceViewService": (".workspace_view", "WorkspaceViewService"),
    # context_engine — pluggable assembly/compaction engines
    "AssembleResult": (".context_engine", "AssembleResult"),
    "CompactResult": (".context_engine", "CompactResult"),
    "ContextEngine": (".context_engine", "ContextEngine"),
    "DefaultContextEngine": (".context_engine", "DefaultContextEngine"),
    "register_engine": (".context_engine", "register_engine"),
    "unregister_engine": (".context_engine", "unregister_engine"),
    "get_engine": (".context_engine", "get_engine"),
    "list_engines": (".context_engine", "list_engines"),
    # context_strategies — composable context strategies
    "ContextStrategy": (".context_strategies", "ContextStrategy"),
    "BufferedChatContext": (".context_strategies", "BufferedChatContext"),
    "TokenLimitedChatContext": (".context_strategies", "TokenLimitedChatContext"),
    "HeadAndTailChatContext": (".context_strategies", "HeadAndTailChatContext"),
    "CompositeContextStrategy": (".context_strategies", "CompositeContextStrategy"),
    "build_context_strategy": (".context_strategies", "build_context_strategy"),
    "build_context_strategy_from_agent_config": (".context_strategies", "build_context_strategy_from_agent_config"),
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
