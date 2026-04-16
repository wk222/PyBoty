"""MemoryFacade — 统一记忆检索门面。

属于 核心系统层 (Layer 1)，依赖同层 memory/ 下各子模块，不依赖 Layer 2+。

设计目标
--------
PyBot 有五套记忆机制，各司其职：

  1. MEMORY.md (MemoryManager / MemoryDistill)  — LLM 蒸馏后的长期事实（最紧凑）
  2. SemanticMemoryManager                   — 向量化语义检索（按查询相关性排序）
  3. MarkdownGardenManager                   — 结构化知识园林（用户撰写/agent 归档）
  4. AdminMemoryManager                      — 管理员任务的步骤压缩记忆（仅 admin 模式）
  5. MemoryDistillManager                     — 蒸馏流水线（写端，由 canvas 策略触发）

`MemoryFacade` 是五套机制的「读端统一门面」，根据 ExecutionCanvas 策略
决定注入哪些层、各层注入多少条目。

Canvas 策略映射
--------------
  focused  → 仅注入 MEMORY.md（最省 token，1200 字符上限）
  balanced → MEMORY.md + 语义检索 top-3 + Garden 关键词命中摘要（4000 字符）
  deep     → MEMORY.md + 语义检索 top-8 + Garden 全匹配摘要（8000 字符）

合并逻辑
--------
- 先注入 MEMORY.md（始终最高优先级）
- 再注入语义检索结果（去重：若内容已在 MEMORY.md 中出现则跳过）
- 最后注入 Garden 命中摘要
- 总字符数超过 canvas 限额时截断
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.systems.memory.memory_manager import MemoryManager
    from core.systems.memory.semantic_memory import SemanticMemoryManager
    from core.systems.memory.markdown_garden import MarkdownGardenManager

logger = logging.getLogger(__name__)

_CANVAS_CONFIG: dict[str, dict[str, Any]] = {
    "focused": {
        "use_digest": True,
        "semantic_top_k": 0,
        "garden_search": False,
        "max_chars": 1200,
        "label": "长期记忆（精简）",
    },
    "balanced": {
        "use_digest": True,
        "semantic_top_k": 3,
        "garden_search": True,
        "max_chars": 4000,
        "label": "长期记忆（均衡）",
    },
    "deep": {
        "use_digest": True,
        "semantic_top_k": 8,
        "garden_search": True,
        "max_chars": 8000,
        "label": "长期记忆（深度）",
    },
}

_DEFAULT_CANVAS = "balanced"


class MemoryFacade:
    """统一记忆检索门面，根据 canvas 策略聚合各记忆层。"""

    def __init__(
        self,
        *,
        memory_manager: "MemoryManager | None" = None,
        semantic_memory: "SemanticMemoryManager | None" = None,
        garden: "MarkdownGardenManager | None" = None,
    ) -> None:
        self._memory_manager = memory_manager
        self._semantic = semantic_memory
        self._garden = garden

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_context_prompt(
        self,
        canvas: str | None = None,
        query: str | None = None,
    ) -> str:
        """返回适合当前 canvas 的记忆注入文本。

        Parameters
        ----------
        canvas:
            ExecutionCanvas 名称（focused / balanced / deep）。
            None 时退回 balanced。
        query:
            当前用户输入，用于语义检索（balanced/deep 时使用）。
        """
        cfg = _CANVAS_CONFIG.get(canvas or _DEFAULT_CANVAS, _CANVAS_CONFIG[_DEFAULT_CANVAS])
        sections: list[str] = []

        # Stage 1: MEMORY.md（始终注入）
        digest_text = self._get_digest_text()
        seen_snippets: set[str] = set()
        if digest_text:
            sections.append(f"### MEMORY.md（蒸馏长期记忆）\n{digest_text}")
            for line in digest_text.splitlines():
                if line.strip():
                    seen_snippets.add(line.strip()[:60])

        # Stage 2: 语义检索（balanced / deep）
        if cfg["semantic_top_k"] > 0 and self._semantic and query:
            try:
                sem_lines = self._get_semantic_entries(
                    query=query,
                    top_k=cfg["semantic_top_k"],
                    seen=seen_snippets,
                )
                if sem_lines:
                    sections.append(f"### 相关记忆（语义检索）\n" + "\n".join(sem_lines))
            except Exception as exc:
                logger.debug("Semantic memory search failed: %s", exc)

        # Stage 3: Garden 关键词搜索（balanced / deep）
        if cfg["garden_search"] and self._garden and query:
            try:
                garden_lines = self._get_garden_hits(query=query)
                if garden_lines:
                    sections.append(f"### 知识园林（Garden）\n" + "\n".join(garden_lines))
            except Exception as exc:
                logger.debug("Garden search failed: %s", exc)

        if not sections:
            return ""

        raw = f"\n\n--- {cfg['label']} ---\n" + "\n\n".join(sections)

        # 截断到 canvas 限额
        max_chars: int = cfg["max_chars"]
        if len(raw) > max_chars:
            raw = raw[:max_chars] + "\n…（已截断）"

        return raw

    def query(self, text: str, k: int = 5, canvas: str | None = None) -> list[dict[str, Any]]:
        """跨层检索，返回结构化结果列表（供工具调用）。"""
        cfg = _CANVAS_CONFIG.get(canvas or _DEFAULT_CANVAS, _CANVAS_CONFIG[_DEFAULT_CANVAS])
        results: list[dict[str, Any]] = []

        if cfg["semantic_top_k"] > 0 and self._semantic:
            try:
                entries = self._semantic.search_memories(text, top_k=min(k, cfg["semantic_top_k"]))
                for e in entries:
                    results.append({
                        "source": "semantic",
                        "content": e.content,
                        "score": getattr(e, "relevance", 0.0),
                    })
            except Exception as exc:
                logger.debug("Semantic query failed: %s", exc)

        if cfg["garden_search"] and self._garden:
            try:
                hits = self._garden.search_notes(text)
                for h in hits[:k]:
                    results.append({
                        "source": "garden",
                        "path": h.get("path", ""),
                        "content": h.get("snippet", ""),
                        "score": 0.5,
                    })
            except Exception as exc:
                logger.debug("Garden query failed: %s", exc)

        results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        return results[:k]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_digest_text(self) -> str:
        if not self._memory_manager:
            return ""
        try:
            raw = self._memory_manager.load()
            if not raw or raw.strip() == "# MEMORY — 长期记忆":
                return ""
            lines = [
                ln for ln in raw.splitlines()
                if ln.strip() and not ln.startswith(">") and not ln.startswith("# MEMORY")
            ]
            return "\n".join(lines[-60:])
        except Exception as exc:
            logger.debug("MemoryManager load failed: %s", exc)
            return ""

    def _get_semantic_entries(
        self, query: str, top_k: int, seen: set[str]
    ) -> list[str]:
        entries = self._semantic.search_memories(query, top_k=top_k)  # type: ignore[union-attr]
        lines: list[str] = []
        for e in entries:
            snippet = e.content.strip()[:60]
            if snippet in seen:
                continue
            seen.add(snippet)
            cat = f"[{e.category}] " if getattr(e, "category", "other") != "other" else ""
            lines.append(f"- {cat}{e.content}")
        return lines

    def _get_garden_hits(self, query: str) -> list[str]:
        hits = self._garden.search_notes(query)  # type: ignore[union-attr]
        lines: list[str] = []
        for h in hits[:4]:
            path = h.get("path", "")
            snippet = h.get("snippet", "")
            lines.append(f"- [{path}] {snippet}")
        return lines
