"""LangChain tools for the unified MemoryEngine.

Consolidates the previous ``memory_tools.py`` (search/save/forget) and
``garden_tools.py`` (list/read/update/search/reorganize) into a single
module bound to the new :class:`MemoryEngine`.

Public API:
* :func:`build_memory_tools(engine)` — agent-facing memory verbs
* :func:`build_garden_tools(garden)` — agent-facing markdown-garden verbs
* :func:`build_all_tools(engine)`   — convenience: both lists combined
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from .markdown_garden import MarkdownGardenManager

if TYPE_CHECKING:
    from .engine import MemoryEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Memory tools — wrap the engine's recall/ingest/feedback verbs
# ---------------------------------------------------------------------------


class SearchMemoryInput(BaseModel):
    query: str = Field(description="要搜索的记忆内容，支持自然语言查询")
    top_k: int = Field(default=5, description="返回结果数量上限")
    section: str | None = Field(
        default=None, description="限定搜索的记忆分区 (如 'preferences'、'facts')"
    )
    memory_type: str | None = Field(
        default=None,
        description="限定搜索的记忆类型 (如 'user'、'project'、'reference')",
    )


class SaveMemoryInput(BaseModel):
    section: str = Field(
        default="", description="记忆分区名称 (如 'preferences'、'facts'、'decisions')"
    )
    content: str = Field(description="要保存的记忆内容")
    memory_type: str = Field(
        default="",
        description="typed memory 类型 (如 'user'、'feedback'、'project'、'reference')",
    )
    occurred_on: str = Field(
        default="", description="绝对日期，适用于 project/reference 这类 durable memory"
    )
    verified: bool = Field(default=False, description="该记忆是否已人工或系统验证")


class ForgetMemoryInput(BaseModel):
    query: str = Field(description="描述要遗忘的记忆内容（语义搜索匹配）")
    top_k: int = Field(default=3, description="最多删除的记忆条目数")
    threshold: float = Field(default=0.6, description="最低匹配分数阈值(0-1)")


class FeedbackMemoryInput(BaseModel):
    content: str = Field(description="要给反馈的记忆条目（按内容匹配）")
    signal: str = Field(
        description="反馈信号: positive / negative / disproved / reconsolidated"
    )


class SearchMemoryTool(BaseTool):
    name: str = "search_memory"
    description: str = (
        "Search long-term memory for relevant information. "
        "Use this when you need to recall user preferences, past decisions, "
        "previously learned facts, or any stored knowledge from earlier conversations. "
        "Returns a list of matching memory entries with relevance scores."
    )
    args_schema: type[BaseModel] = SearchMemoryInput
    engine: Any = None

    def _run(
        self,
        query: str,
        top_k: int = 5,
        section: str | None = None,
        memory_type: str | None = None,
    ) -> str:
        if self.engine is None:
            return json.dumps(
                {"success": False, "error": "MemoryEngine not configured"},
                ensure_ascii=False,
            )
        try:
            entries = self.engine.recall(
                query,
                top_k=top_k,
                section=section,
                memory_type=memory_type,
                modality="fact",
                record_recall=False,
            )
        except Exception as exc:
            logger.error("search_memory failed: %s", exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
        results = [
            {
                "content": e.content,
                "section": e.section,
                "memory_type": e.memory_type,
                "relevance": getattr(e, "relevance", None),
            }
            for e in entries
        ]
        return json.dumps(
            {"success": True, "count": len(results), "results": results},
            ensure_ascii=False,
        )


class SaveMemoryTool(BaseTool):
    name: str = "save_memory"
    description: str = (
        "Save important information to long-term memory for future recall. "
        "Use this to store user preferences, key decisions, learned facts, "
        "or any information that should persist across conversations. "
        "Organize by section: 'preferences', 'facts', 'decisions', 'context'."
    )
    args_schema: type[BaseModel] = SaveMemoryInput
    engine: Any = None

    def _run(
        self,
        section: str,
        content: str,
        memory_type: str = "",
        occurred_on: str = "",
        verified: bool = False,
    ) -> str:
        if self.engine is None:
            return json.dumps(
                {"success": False, "error": "MemoryEngine not configured"},
                ensure_ascii=False,
            )
        try:
            if memory_type:
                self.engine.ingest(
                    "fact",
                    content,
                    metadata={
                        "memory_type": memory_type,
                        "occurred_on": occurred_on,
                        "verified": verified,
                        "source": "tool:save_memory",
                    },
                )
                return json.dumps(
                    {"success": True, "memory_type": memory_type, "saved": content[:200]},
                    ensure_ascii=False,
                )
            self.engine.ingest("fact", content, metadata={"section": section})
            return json.dumps(
                {"success": True, "section": section, "saved": content[:200]},
                ensure_ascii=False,
            )
        except Exception as exc:
            logger.error("Failed to save memory: %s", exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


class ForgetMemoryTool(BaseTool):
    name: str = "forget_memory"
    description: str = (
        "Delete memories that match a given description. "
        "Use this when a user asks to remove personal data, correct outdated information, "
        "or forget something that was previously stored. "
        "The tool searches semantically and deletes the closest matches above the threshold."
    )
    args_schema: type[BaseModel] = ForgetMemoryInput
    engine: Any = None

    def _run(self, query: str, top_k: int = 3, threshold: float = 0.6) -> str:
        if self.engine is None:
            return json.dumps(
                {"success": False, "error": "MemoryEngine not configured"},
                ensure_ascii=False,
            )
        try:
            deleted = self.engine.forget(query, top_k=top_k, threshold=threshold)
        except Exception as exc:
            logger.error("Failed to forget memory: %s", exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps(
            {
                "success": True,
                "deleted_count": len(deleted),
                "deleted": [d[:100] for d in deleted],
            },
            ensure_ascii=False,
        )


class FeedbackMemoryTool(BaseTool):
    name: str = "feedback_memory"
    description: str = (
        "Record user feedback on a stored memory. Adjusts adaptive importance. "
        "Use signal='positive' when the user confirms a fact, 'negative' for a "
        "weak doubt, 'disproved' for explicit contradiction, "
        "'reconsolidated' to boost a recovered memory."
    )
    args_schema: type[BaseModel] = FeedbackMemoryInput
    engine: Any = None

    def _run(self, content: str, signal: str) -> str:
        if self.engine is None:
            return json.dumps(
                {"success": False, "error": "MemoryEngine not configured"},
                ensure_ascii=False,
            )
        try:
            ok = self.engine.feedback_by_content(content, signal)
        except Exception as exc:
            logger.error("feedback_memory failed: %s", exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps(
            {"success": ok, "content": content[:120], "signal": signal},
            ensure_ascii=False,
        )


def build_memory_tools(engine: "MemoryEngine") -> list[BaseTool]:
    """Wire the memory verbs to a specific :class:`MemoryEngine`."""
    return [
        SearchMemoryTool(engine=engine),
        SaveMemoryTool(engine=engine),
        ForgetMemoryTool(engine=engine),
        FeedbackMemoryTool(engine=engine),
    ]


# Legacy alias used by upstream callers
def get_memory_tools(memory_manager: Any) -> list[BaseTool]:
    """Compat shim — accepts either an engine or a legacy memory manager."""
    return build_memory_tools(memory_manager)


# ---------------------------------------------------------------------------
# Garden tools — re-export from existing module
# ---------------------------------------------------------------------------


class ListGardenNotesInput(BaseModel):
    sub_dir: str = Field(
        description="要列出的子目录（可选，默认为根目录）", default=""
    )


class ListGardenNotesTool(BaseTool):
    name: str = "list_garden_notes"
    description: str = "列出记忆园林中所有的 Markdown 笔记及其路径。"
    args_schema: type[BaseModel] = ListGardenNotesInput
    manager: MarkdownGardenManager = Field(exclude=True)

    def _run(self, sub_dir: str = "") -> str:
        notes = self.manager.list_notes(sub_dir)
        if not notes:
            return "记忆园林中目前没有笔记。"
        return "记忆园林笔记列表：\n" + "\n".join(f"- {n}" for n in notes)


class ReadGardenNoteInput(BaseModel):
    note_path: str = Field(
        description="笔记的相对路径（例如 'engineering/api_contracts.md'）"
    )


class ReadGardenNoteTool(BaseTool):
    name: str = "read_garden_note"
    description: str = "读取记忆园林中特定笔记的完整内容。"
    args_schema: type[BaseModel] = ReadGardenNoteInput
    manager: MarkdownGardenManager = Field(exclude=True)

    def _run(self, note_path: str) -> str:
        content = self.manager.read_note(note_path)
        if content is None:
            return f"错误：找不到笔记 '{note_path}'"
        return f"--- 笔记内容: {note_path} ---\n\n{content}"


class UpdateGardenNoteInput(BaseModel):
    note_path: str = Field(description="笔记的相对路径")
    content: str = Field(description="笔记的新内容或要追加的内容")
    append: bool = Field(description="是否追加内容而不是覆盖", default=True)


class UpdateGardenNoteTool(BaseTool):
    name: str = "update_garden_note"
    description: str = "创建或更新记忆园林中的笔记。建议在决策、规范或项目结束时使用。"
    args_schema: type[BaseModel] = UpdateGardenNoteInput
    manager: MarkdownGardenManager = Field(exclude=True)

    def _run(self, note_path: str, content: str, append: bool = True) -> str:
        self.manager.update_note(note_path, content, append=append)
        action = "已更新" if append else "已创建/覆盖"
        return f"✅ 笔记 '{note_path}' {action}。"


class SearchGardenInput(BaseModel):
    query: str = Field(description="搜索关键词")


class SearchGardenTool(BaseTool):
    name: str = "search_garden"
    description: str = "在记忆园林的所有笔记中进行关键词搜索。"
    args_schema: type[BaseModel] = SearchGardenInput
    manager: MarkdownGardenManager = Field(exclude=True)

    def _run(self, query: str) -> str:
        results = self.manager.search_notes(query)
        if not results:
            return f"未找到包含 '{query}' 的笔记。"
        out = [f"找到 {len(results)} 条结果："]
        for r in results:
            out.append(f"- {r['path']}: {r['snippet']}")
        return "\n".join(out)


class ReorganizeGardenInput(BaseModel):
    action: str = Field(description="操作类型: 'move' 或 'delete'")
    src_path: str = Field(description="源笔记路径", default="")
    dst_path: str = Field(
        description="目标笔记路径（仅 move 操作需要）", default=""
    )


class ReorganizeGardenTool(BaseTool):
    name: str = "reorganize_garden"
    description: str = "重构记忆园林的结构。支持移动（重命名）笔记或删除陈旧笔记。"
    args_schema: type[BaseModel] = ReorganizeGardenInput
    manager: MarkdownGardenManager = Field(exclude=True)

    def _run(self, action: str, src_path: str = "", dst_path: str = "") -> str:
        if action == "delete":
            ok = self.manager.delete_note(src_path)
            return (
                f"{'✅' if ok else '❌'} 笔记 '{src_path}' "
                f"{'已删除' if ok else '未找到'}。"
            )
        if action == "move":
            if not dst_path:
                return "错误：移动操作需要提供 dst_path。"
            ok = self.manager.move_note(src_path, dst_path)
            return (
                f"{'✅' if ok else '❌'} 笔记 '{src_path}' "
                f"{'已移动到 ' + dst_path if ok else '移动失败'}。"
            )
        return f"错误：未知操作 '{action}'。"


def build_garden_tools(manager: MarkdownGardenManager) -> list[BaseTool]:
    """Wire garden tools to a pre-existing manager (preferred path)."""
    return [
        ListGardenNotesTool(manager=manager),
        ReadGardenNoteTool(manager=manager),
        UpdateGardenNoteTool(manager=manager),
        SearchGardenTool(manager=manager),
        ReorganizeGardenTool(manager=manager),
    ]


def get_garden_tools(
    workspace_dir: str = "workspace",
    summarize_fn: Any | None = None,
    max_note_chars: int = 8000,
) -> list[BaseTool]:
    """Legacy factory: build a brand-new manager + tools in one shot."""
    manager = MarkdownGardenManager(
        workspace_dir=workspace_dir,
        summarize_fn=summarize_fn,
        max_note_chars=max_note_chars,
    )
    return build_garden_tools(manager)


# ---------------------------------------------------------------------------
# Convenience: build everything at once
# ---------------------------------------------------------------------------


def build_all_tools(engine: "MemoryEngine") -> list[BaseTool]:
    tools = build_memory_tools(engine)
    if engine.garden is not None:
        tools.extend(build_garden_tools(engine.garden))
    return tools


__all__ = [
    "FeedbackMemoryTool",
    "ForgetMemoryTool",
    "ListGardenNotesTool",
    "ReadGardenNoteTool",
    "ReorganizeGardenTool",
    "SaveMemoryTool",
    "SearchGardenTool",
    "SearchMemoryTool",
    "UpdateGardenNoteTool",
    "build_all_tools",
    "build_garden_tools",
    "build_memory_tools",
    "get_garden_tools",
    "get_memory_tools",
]
