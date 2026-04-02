"""LangChain middleware for evicting large tool outputs to files.

Supersedes the old MiddlewareStack-based ToolEvictionMiddleware with a
proper ``wrap_tool_call`` implementation so it participates in the
LangChain agent middleware pipeline.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ToolCallRequest
    from langchain_core.messages import ToolMessage
    from langgraph.types import Command

    _HAS_LC = True
except ImportError:
    _HAS_LC = False
    AgentMiddleware = object  # type: ignore[assignment,misc]
    ToolCallRequest = object  # type: ignore[assignment,misc]

EXCLUDED_TOOLS = frozenset(
    {
        "ls",
        "glob",
        "grep",
        "read_file",
        "read_app_file",
        "edit_file",
        "write_file",
        "list_workflows",
        "list_agents",
        "list_tools",
        "tool_stats",
        "capability_bus",
        "write_todos",
        "compact_conversation",
    }
)


class LCToolEvictionMiddleware(AgentMiddleware if _HAS_LC else object):  # type: ignore[misc]
    """Evict large tool outputs to disk and return a truncated preview."""

    def __init__(
        self,
        eviction_dir: str = "workspace/data/evicted",
        max_output_chars: int = 8000,
    ):
        self.eviction_dir = eviction_dir
        self.max_output_chars = max_output_chars
        os.makedirs(eviction_dir, exist_ok=True)

    @property
    def name(self) -> str:
        return "LCToolEvictionMiddleware"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = handler(request)
        return self._maybe_evict(request, result)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        return self._maybe_evict(request, result)

    def _maybe_evict(
        self,
        request: Any,
        result: ToolMessage | Command,
    ) -> ToolMessage | Command:
        if not isinstance(result, ToolMessage):
            return result
        tool_name = getattr(request, "name", "") or ""
        if tool_name in EXCLUDED_TOOLS:
            return result
        content = result.content
        if not isinstance(content, str) or len(content) <= self.max_output_chars:
            return result
        from core.path_utils import sanitize_tool_call_id

        tool_call_id = getattr(result, "tool_call_id", "") or ""
        safe_id = sanitize_tool_call_id(tool_call_id) if tool_call_id else ""
        filename = f"{safe_id}.txt" if safe_id else f"{tool_name}_{int(time.time())}.txt"
        eviction_file = os.path.join(self.eviction_dir, filename)
        try:
            with open(eviction_file, "w", encoding="utf-8") as f:
                f.write(content)
            truncated = content[:2000]
            new_content = (
                f"{truncated}\n\n... [output truncated, full result saved to "
                f"{eviction_file}] (original length: {len(content)} chars)"
            )
            return ToolMessage(
                content=new_content,
                tool_call_id=result.tool_call_id,
                name=result.name,
            )
        except Exception:
            return ToolMessage(
                content=content[: self.max_output_chars],
                tool_call_id=result.tool_call_id,
                name=result.name,
            )
