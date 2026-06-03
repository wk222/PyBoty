"""
Legacy middleware stack — DEPRECATED.

Most functionality has migrated to the unified LangChain AgentMiddleware
pipeline (see ``agent_middleware_factory.py``):

  - Context trimming/summarization → ``SummarizationMiddleware``
  - Tool-output eviction → ``LCToolEvictionMiddleware``
  - Dangling tool-call patching → ``PatchToolCallsMiddleware``
  - Structured task tracking → ``TodoListMiddleware``

This module is retained only for ``MemoryMiddleware`` (post-invoke memory
extraction) and ``BusMiddleware`` (capability-bus event recording), which
are still called via ``mw_stack.after_invoke()`` in ``agent.py``.

Will be fully removed once those two are also migrated to LangChain hooks.
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)
import time
from abc import ABC, abstractmethod
from typing import Any

from core.systems.capability.capability_reporting import CapabilityBusReporter

try:
    from langchain.agents.middleware.types import AgentMiddleware as LCAgentMiddleware  # noqa: F401
    from langchain.agents.middleware.types import ModelRequest, ModelResponse  # noqa: F401

    _HAS_LC_MIDDLEWARE = True
except ImportError:
    _HAS_LC_MIDDLEWARE = False


class MiddlewareBase(ABC):
    """PyBot 中间件基类 — 兼容旧 API。"""

    @abstractmethod
    def before_invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        return state

    @abstractmethod
    def after_invoke(self, state: dict[str, Any], response: Any) -> Any:
        return response

    def wrap_tool_output(self, tool_name: str, output: str) -> str:
        return output


class ContextMiddleware(MiddlewareBase):
    """上下文窗口管理：消息裁剪和摘要压缩。

    对齐 DeepAgents 的 SummarizationMiddleware 思路：
    在消息历史过长时自动触发裁剪/摘要，而非等到溢出。
    """

    def __init__(self, context_manager, trim_threshold: int = 20):
        self.context_manager = context_manager
        self.trim_threshold = trim_threshold

    def before_invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages", [])
        if len(messages) > self.trim_threshold:
            msg_dicts = []
            for m in messages:
                if hasattr(m, "content"):
                    role = getattr(m, "type", "human")
                    msg_dicts.append({"role": role, "content": m.content})
            trimmed = self.context_manager.trim_messages(msg_dicts)
            if len(trimmed) < len(msg_dicts):
                print(f"[ContextMW] 消息裁剪: {len(msg_dicts)} → {len(trimmed)}")
        return state

    def after_invoke(self, state: dict[str, Any], response: Any) -> Any:
        return response


class MemoryMiddleware(MiddlewareBase):
    """长期记忆中间件：对话后自动提取关键事实写入 MEMORY.md。"""

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    def before_invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        return state

    def after_invoke(self, state: dict[str, Any], response: Any) -> Any:
        try:
            messages = state.get("messages", [])
            if messages and len(messages) >= 2 and hasattr(self.memory_manager, "auto_capture"):
                conversation: list[dict[str, str]] = []
                for m in messages[-4:]:
                    if hasattr(m, "content"):
                        role = "user" if getattr(m, "type", "") == "human" else "assistant"
                        conversation.append({"role": role, "content": m.content})
                if len(conversation) >= 2:
                    self.memory_manager.auto_capture(conversation)
        except Exception as e:
            logger.warning("[MemoryMW] auto_capture failed: %s", e)
        return response


class ToolEvictionMiddleware(MiddlewareBase):
    """大工具输出驱逐中间件。

    借鉴 DeepAgents 的 FilesystemMiddleware 策略：
    - 超过阈值的输出写入文件，返回截断预览 + 文件路径
    - 排除本身就会截断的工具（如 ls, grep, read_file）
    """

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
        }
    )

    def __init__(self, eviction_dir: str = "workspace/data/evicted", max_output_chars: int = 8000):
        self.eviction_dir = eviction_dir
        self.max_output_chars = max_output_chars
        os.makedirs(eviction_dir, exist_ok=True)

    def before_invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        return state

    def after_invoke(self, state: dict[str, Any], response: Any) -> Any:
        return response

    def wrap_tool_output(self, tool_name: str, output: str) -> str:
        if tool_name in self.EXCLUDED_TOOLS:
            return output

        if len(output) <= self.max_output_chars:
            return output

        eviction_file = os.path.join(self.eviction_dir, f"{tool_name}_{int(time.time())}.txt")
        try:
            with open(eviction_file, "w", encoding="utf-8") as f:
                f.write(output)
            truncated = output[:2000]
            return f"{truncated}\n\n... [输出已截断，完整结果已保存到 {eviction_file}] (原始长度: {len(output)} 字符)"
        except Exception:
            return output[: self.max_output_chars]


class BusMiddleware(MiddlewareBase):
    """能力总线中间件：记录调用事件和共享上下文。"""

    def __init__(self, capability_bus):
        self.bus = capability_bus
        self._reporter = CapabilityBusReporter(capability_bus)
        self._local = threading.local()

    def before_invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self._local.invoke_start = time.time()
        return state

    def after_invoke(self, state: dict[str, Any], response: Any) -> Any:
        start = getattr(self._local, "invoke_start", None)
        if start:
            duration = (time.time() - start) * 1000
            message_count = len(state.get("messages", []) or []) if isinstance(state, dict) else 0
            self._reporter.record_model_call(
                duration_ms=duration,
                message_count=message_count,
                source="bus_middleware",
            )
        return response

    def wrap_tool_output(self, tool_name: str, output: str) -> str:
        self._reporter.record_tool_call(
            tool_name=tool_name,
            success=True,
            duration_ms=0,
            source="bus_middleware",
            operation="tool_output",
            metadata={"output_preview": output[:120]},
        )
        return output


class MiddlewareStack:
    """中间件栈包装器。

    同时支持两种使用方式：
    1. 旧 API：手动调用 before_invoke / after_invoke（用于 service_mode 等）
    2. 新 API：通过 to_langchain_middleware() 获取可传入 create_agent 的列表
    """

    def __init__(self):
        self._middlewares: list[MiddlewareBase] = []

    def add(self, middleware: MiddlewareBase) -> "MiddlewareStack":
        self._middlewares.append(middleware)
        return self

    def before_invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        for mw in self._middlewares:
            state = mw.before_invoke(state)
        return state

    def after_invoke(self, state: dict[str, Any], response: Any) -> Any:
        for mw in reversed(self._middlewares):
            response = mw.after_invoke(state, response)
        return response

    def wrap_tool_output(self, tool_name: str, output: str) -> str:
        for mw in self._middlewares:
            output = mw.wrap_tool_output(tool_name, output)
        return output

    @property
    def layers(self) -> list[str]:
        return [type(mw).__name__ for mw in self._middlewares]

    @property
    def middlewares(self) -> list[MiddlewareBase]:
        return list(self._middlewares)


class PromptCachingMiddleware(MiddlewareBase):
    """Prompt 缓存中间件（借鉴 DeepAgents 的 AnthropicPromptCachingMiddleware）。

    当使用 Anthropic 模型时，标记 system prompt 中的稳定部分为可缓存，
    避免每次调用都重新处理完整的 system prompt，降低 token 成本。

    对于非 Anthropic 模型（如 OpenAI），此中间件透传不做处理。
    """

    def __init__(self, model_provider: str = "auto"):
        self.model_provider = model_provider
        self._system_hash: str | None = None

    def before_invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        if self.model_provider not in ("anthropic", "auto"):
            return state

        messages = state.get("messages", [])
        if not messages:
            return state

        import hashlib

        first = messages[0] if messages else None
        if first and hasattr(first, "content"):
            content_str = str(first.content)
            new_hash = hashlib.md5(content_str[:2000].encode()).hexdigest()
            if new_hash == self._system_hash:
                state["_prompt_cache_hit"] = True
            else:
                self._system_hash = new_hash
                state["_prompt_cache_hit"] = False

        return state

    def after_invoke(self, state: dict[str, Any], response: Any) -> Any:
        return response
