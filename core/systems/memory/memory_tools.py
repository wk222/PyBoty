"""Agent-callable tools for long-term memory management.

Wraps the MemoryManager / SemanticMemoryManager capabilities as LangChain
tools so that agents can proactively search and save memories, rather than
relying solely on the passive LCMemoryMiddleware extraction pipeline.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SearchMemoryInput(BaseModel):
    query: str = Field(description="要搜索的记忆内容，支持自然语言查询")
    top_k: int = Field(default=5, description="返回结果数量上限")
    section: str | None = Field(default=None, description="限定搜索的记忆分区 (如 'preferences'、'facts')")
    memory_type: str | None = Field(default=None, description="限定搜索的记忆类型 (如 'user'、'project'、'reference')")


class SaveMemoryInput(BaseModel):
    section: str = Field(default="", description="记忆分区名称 (如 'preferences'、'facts'、'decisions')")
    content: str = Field(description="要保存的记忆内容")
    memory_type: str = Field(
        default="", description="typed memory 类型 (如 'user'、'feedback'、'project'、'reference')"
    )
    occurred_on: str = Field(default="", description="绝对日期，适用于 project/reference 这类 durable memory")
    verified: bool = Field(default=False, description="该记忆是否已人工或系统验证")


class ForgetMemoryInput(BaseModel):
    query: str = Field(description="描述要遗忘的记忆内容（语义搜索匹配）")
    top_k: int = Field(default=3, description="最多删除的记忆条目数")
    threshold: float = Field(default=0.6, description="最低匹配分数阈值(0-1)")


class SearchMemoryTool(BaseTool):
    """Search long-term memory by semantic similarity or keyword."""

    name: str = "search_memory"
    description: str = (
        "Search long-term memory for relevant information. "
        "Use this when you need to recall user preferences, past decisions, "
        "previously learned facts, or any stored knowledge from earlier conversations. "
        "Returns a list of matching memory entries with relevance scores."
    )
    args_schema: type[BaseModel] = SearchMemoryInput
    memory_manager: Any = None

    def _run(
        self,
        query: str,
        top_k: int = 5,
        section: str | None = None,
        memory_type: str | None = None,
    ) -> str:
        if self.memory_manager is None:
            return json.dumps({"success": False, "error": "Memory manager not configured"}, ensure_ascii=False)

        if hasattr(self.memory_manager, "search_memories"):
            entries = self.memory_manager.search_memories(
                query,
                top_k=top_k,
                section=section,
                memory_type=memory_type,
            )
            results = [
                {
                    "content": e.content,
                    "section": e.section,
                    "memory_type": getattr(e, "memory_type", None),
                    "relevance": getattr(e, "relevance", None),
                }
                for e in entries
            ]
        else:
            raw = self.memory_manager.load()
            lines = [line.strip() for line in raw.split("\n") if query.lower() in line.lower()]
            results = [{"content": line, "section": "unknown", "relevance": None} for line in lines[:top_k]]

        return json.dumps({"success": True, "count": len(results), "results": results}, ensure_ascii=False)


class SaveMemoryTool(BaseTool):
    """Save important information to long-term memory."""

    name: str = "save_memory"
    description: str = (
        "Save important information to long-term memory for future recall. "
        "Use this to store user preferences, key decisions, learned facts, "
        "or any information that should persist across conversations. "
        "Organize by section: 'preferences', 'facts', 'decisions', 'context'."
    )
    args_schema: type[BaseModel] = SaveMemoryInput
    memory_manager: Any = None

    def _run(
        self,
        section: str,
        content: str,
        memory_type: str = "",
        occurred_on: str = "",
        verified: bool = False,
    ) -> str:
        if self.memory_manager is None:
            return json.dumps({"success": False, "error": "Memory manager not configured"}, ensure_ascii=False)

        try:
            if memory_type and hasattr(self.memory_manager, "append_typed_memory"):
                self.memory_manager.append_typed_memory(
                    memory_type=memory_type,
                    content=content,
                    occurred_on=occurred_on,
                    verified=verified,
                    source="tool:save_memory",
                )
                return json.dumps(
                    {"success": True, "memory_type": memory_type, "saved": content[:200]},
                    ensure_ascii=False,
                )
            self.memory_manager.append_memory(section=section, content=content)
            return json.dumps({"success": True, "section": section, "saved": content[:200]}, ensure_ascii=False)
        except Exception as exc:
            logger.error("Failed to save memory: %s", exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


class ForgetMemoryTool(BaseTool):
    """Delete memories matching a semantic query (GDPR-friendly)."""

    name: str = "forget_memory"
    description: str = (
        "Delete memories that match a given description. "
        "Use this when a user asks to remove personal data, correct outdated information, "
        "or forget something that was previously stored. "
        "The tool searches semantically and deletes the closest matches above the threshold."
    )
    args_schema: type[BaseModel] = ForgetMemoryInput
    memory_manager: Any = None

    def _run(self, query: str, top_k: int = 3, threshold: float = 0.6) -> str:
        if self.memory_manager is None:
            return json.dumps({"success": False, "error": "Memory manager not configured"}, ensure_ascii=False)

        if not hasattr(self.memory_manager, "forget_memory"):
            return json.dumps(
                {
                    "success": False,
                    "error": "Memory manager does not support forget_memory (需要 SemanticMemoryManager)",
                },
                ensure_ascii=False,
            )

        try:
            deleted = self.memory_manager.forget_memory(query, top_k=top_k, threshold=threshold)
            return json.dumps(
                {"success": True, "deleted_count": len(deleted), "deleted": [d[:100] for d in deleted]},
                ensure_ascii=False,
            )
        except Exception as exc:
            logger.error("Failed to forget memory: %s", exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def get_memory_tools(memory_manager: Any) -> list[BaseTool]:
    """Build memory tools bound to a specific MemoryManager instance."""
    return [
        SearchMemoryTool(memory_manager=memory_manager),
        SaveMemoryTool(memory_manager=memory_manager),
        ForgetMemoryTool(memory_manager=memory_manager),
    ]
