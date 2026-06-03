"""
MemoryDistill — 记忆蒸馏流水线

把对话历史 → 分类事件日记 → 长期记忆精炼（MEMORY.md）

触发时机：
1. 会话结束（对话数超过阈值时）
2. 每日定时（外部调度器调用）
3. 手动调用 /memory distill

流水线三阶段：
  Stage 1 (Journal)  — 将当天对话归纳为「分类事件日记」(daily/YYYY-MM-DD.md)
  Stage 2 (Distill)  — 读取多天日记，提炼精华至 MEMORY.md（≤60 条，带分类和重要性）
  Stage 3 (Archive)  — 将已处理的日记归档至 archive/，防止重复蒸馏
"""
from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ─── Prompts ──────────────────────────────────────────────────────────────────

_JOURNAL_SYSTEM = """你是对话记录助手。把以下对话归纳为当天的「分类事件日记」。

输出格式（严格遵守）：
每条一行，格式：- [分类] 事件描述

可用分类标签（选最贴切的一个）：
  [偏好]  — 用户表达的喜好/厌恶/习惯
  [决策]  — 做出的重要选择或结论
  [事实]  — 了解到的客观信息/技术细节
  [人物]  — 涉及到的人名/角色/关系
  [工具]  — 使用了哪些工具/API/命令，产生了什么结果
  [待办]  — 提到但尚未完成的事项
  [其他]  — 以上分类都不适合时使用

规则：
- 按事件维度归纳，不按对话轮次逐条列举
- 合并同一件事的多轮对话
- 只记录有意义的事件（忽略问候/闲聊）
- 保留关键决策、结论、待办
- 如果对话中有工具调用结果，提取关键产出

若对话无记录价值，直接输出「无」。"""

_JOURNAL_USER = "请归纳以下对话的分类事件日记：\n\n{conversation}"

_DISTILL_SYSTEM = """你是记忆整理助手，负责把每日日记提炼为精简长期记忆。

你会收到：
1. 现有 MEMORY.md 全文
2. 若干天的日记内容

输出格式（严格遵守）：
[MEMORY]
- [分类|重要性] 记忆条目

分类标签同日记（偏好/决策/事实/人物/工具/待办/其他）。
重要性：★★★ 高 / ★★ 中 / ★ 低

示例：
- [偏好|★★★] 用户偏好 Python 类型注解，讨厌冗余注释
- [事实|★★] PyBot 采用四层架构：基础层/核心系统层/领域对象层/身份层
- [工具|★] 用户常用 ruff 做代码检查

[INSIGHT]
本次整理的观察和发现（简短叙述，2-5句话）

规则：
- 合并含义相近的条目
- 从日记中萃取值得永久记住的新信息
- 新信息与旧条目矛盾时，以新为准
- 优先删除 ★ 低重要性条目来腾出空间
- 总条目控制在 60 条以内
- 严禁编造材料中不存在的信息"""

_DISTILL_USER = """## 现有长期记忆（MEMORY.md）

{memory_content}

## 近期日记（最近 {days} 天）

{daily_content}"""


# ─── MemoryDistill Manager ────────────────────────────────────────────────────

class MemoryDistillManager:
    """
    记忆蒸馏流水线管理器。

    设计为无状态、可重入，每个 workspace 独立实例。
    """

    DAILY_DIR = "memory/daily"
    ARCHIVE_DIR = "memory/archive"
    MEMORY_FILE = "MEMORY.md"
    MIN_MESSAGES_TO_JOURNAL = 4
    DISTILL_DAYS = 7
    MIN_NEW_DAYS_TO_DISTILL = 1

    def __init__(
        self,
        workspace_dir: str | Path,
        llm_caller: Optional[Callable[[str, str], str]] = None,
        *,
        journal_llm_caller: Optional[Callable[[str, str], str]] = None,
        distill_llm_caller: Optional[Callable[[str, str], str]] = None,
    ):
        """
        Parameters
        ----------
        llm_caller:
            共享 LLM 调用器（兼容旧 API）。
        journal_llm_caller:
            专用 journal 调用器（温度可略高，如 0.5）。
        distill_llm_caller:
            专用 distill 调用器（温度应低，如 0.2）。
        """
        self.workspace_dir = Path(workspace_dir)
        self.daily_dir = self.workspace_dir / self.DAILY_DIR
        self.archive_dir = self.workspace_dir / self.ARCHIVE_DIR
        self.memory_path = self.workspace_dir / self.MEMORY_FILE
        self._llm_caller = llm_caller
        self._journal_llm_caller = journal_llm_caller or llm_caller
        self._distill_llm_caller = distill_llm_caller or llm_caller
        self._flushed_hashes: set[str] = set()
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def journal_async(self, messages: list[dict[str, str]], date_str: str | None = None) -> None:
        """异步归纳对话 → 分类事件日记（不阻塞主线程）。"""
        if len(messages) < self.MIN_MESSAGES_TO_JOURNAL:
            return
        content_hash = self._hash_messages(messages)
        with self._lock:
            if content_hash in self._flushed_hashes:
                return
            self._flushed_hashes.add(content_hash)
        t = threading.Thread(
            target=self._journal_sync,
            args=(messages, date_str, content_hash),
            daemon=True,
        )
        t.start()

    def distill_async(self, force: bool = False) -> None:
        """异步蒸馏最近日记 → MEMORY.md（带分类和重要性）。"""
        t = threading.Thread(
            target=self._distill_sync,
            kwargs={"force": force},
            daemon=True,
        )
        t.start()

    def get_memory_context(self) -> str:
        """读取 MEMORY.md，返回适合注入提示词的格式。"""
        if not self.memory_path.exists():
            return ""
        try:
            content = self.memory_path.read_text(encoding="utf-8")
            lines = [
                ln for ln in content.splitlines()
                if ln.strip() and not ln.startswith(">") and not ln.startswith("# MEMORY")
            ]
            if not lines:
                return ""
            return "\n\n--- 长期记忆 ---\n" + "\n".join(lines[-60:])
        except Exception as e:
            logger.warning("[MemoryDistill] read memory failed: %s", e)
            return ""

    def get_today_journal(self) -> str:
        """读取今日日记（如存在）。"""
        today = datetime.now().strftime("%Y-%m-%d")
        path = self.daily_dir / f"{today}.md"
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                return ""
        return ""

    # ── Stage 1: Journal ──────────────────────────────────────────────────────

    def _journal_sync(
        self,
        messages: list[dict[str, str]],
        date_str: str | None,
        content_hash: str,
    ) -> None:
        caller = self._journal_llm_caller or self._llm_caller
        if not caller:
            return
        try:
            conv_text = self._format_messages(messages)
            summary = caller(
                _JOURNAL_SYSTEM,
                _JOURNAL_USER.format(conversation=conv_text),
            )
            if not summary or summary.strip() in ("无", "无。"):
                logger.debug("[MemoryDistill] journal: nothing to record")
                return
            self._append_to_daily(summary.strip(), date_str)
            logger.info("[MemoryDistill] journal written (%d chars)", len(summary))
        except Exception as e:
            logger.warning("[MemoryDistill] journal failed: %s", e)

    def _append_to_daily(self, content: str, date_str: str | None = None) -> None:
        today = date_str or datetime.now().strftime("%Y-%m-%d")
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        path = self.daily_dir / f"{today}.md"
        header = f"# 每日日记 {today}\n\n" if not path.exists() else ""
        timestamp = datetime.now().strftime("%H:%M")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{header}## {timestamp}\n\n{content}\n\n")

    # ── Stage 2: Distill ──────────────────────────────────────────────────────

    def _distill_sync(self, force: bool = False) -> None:
        caller = self._distill_llm_caller or self._llm_caller
        if not caller:
            return
        try:
            daily_files = sorted(self.daily_dir.glob("*.md")) if self.daily_dir.exists() else []
            recent = daily_files[-self.DISTILL_DAYS:] if daily_files else []
            if not force and len(recent) < self.MIN_NEW_DAYS_TO_DISTILL:
                return

            daily_content = "\n\n---\n\n".join(
                f.read_text(encoding="utf-8") for f in recent
            )
            memory_content = (
                self.memory_path.read_text(encoding="utf-8")
                if self.memory_path.exists()
                else "（暂无长期记忆）"
            )
            result = caller(
                _DISTILL_SYSTEM,
                _DISTILL_USER.format(
                    memory_content=memory_content,
                    daily_content=daily_content,
                    days=len(recent),
                ),
            )
            self._apply_distill_result(result)
            self._archive_daily_files(recent)
            logger.info("[MemoryDistill] distill complete, %d journal files archived", len(recent))
        except Exception as e:
            logger.warning("[MemoryDistill] distill failed: %s", e)

    def _apply_distill_result(self, result: str) -> None:
        if "[MEMORY]" not in result:
            return
        try:
            memory_part = result.split("[MEMORY]")[1]
            if "[INSIGHT]" in memory_part:
                memory_part = memory_part.split("[INSIGHT]")[0]
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            header = f"# MEMORY — 长期记忆\n\n> 最后蒸馏：{now}\n\n"
            self.memory_path.write_text(header + memory_part.strip() + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning("[MemoryDistill] apply distill failed: %s", e)

    def _archive_daily_files(self, files: list[Path]) -> None:
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            try:
                dest = self.archive_dir / f.name
                f.rename(dest)
            except Exception:
                pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _format_messages(messages: list[dict[str, str]]) -> str:
        lines = []
        for m in messages:
            role = m.get("role", "")
            label = "用户" if role == "user" else "助手"
            content = m.get("content", "")
            if role == "tool" or role == "function":
                label = "工具结果"
            lines.append(f"{label}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _hash_messages(messages: list[dict[str, str]]) -> str:
        text = "".join(m.get("content", "") for m in messages)
        return hashlib.sha256(text.encode()).hexdigest()[:16]


# 兼容别名
DeepDigestManager = MemoryDistillManager
