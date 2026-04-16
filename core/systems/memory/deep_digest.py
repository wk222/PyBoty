"""
Deep Digest — 记忆蒸馏流水线

把对话历史 → 每日摘要日记 → 长期记忆精炼（MEMORY.md）

与 CowAgent 的 Deep Dream 机制同理，但名称和细节完全独立。

触发时机：
1. 会话结束（对话数超过阈值时）
2. 每日定时（外部调度器调用）
3. 手动调用 /memory digest

流水线三阶段：
  Stage 1 (Journal)  — 将当天对话归纳为「事件日记」(daily/YYYY-MM-DD.md)
  Stage 2 (Distill)  — 读取多天日记，提炼精华至 MEMORY.md（≤60 条）
  Stage 3 (Archive)  — 将已处理的日记归档至 archive/，防止重复蒸馏
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ─── Prompts ──────────────────────────────────────────────────────────────────

_JOURNAL_SYSTEM = """你是对话记录助手。把以下对话归纳为当天的「事件日记」。

要求：
- 按事件维度归纳，不按对话轮次逐条列举
- 每条一行，以「- 」开头
- 合并同一件事的多轮对话
- 只记录有意义的事件（忽略问候/闲聊）
- 保留关键决策、结论、待办

若对话无记录价值，直接输出「无」。"""

_JOURNAL_USER = "请归纳以下对话的事件日记：\n\n{conversation}"

_DISTILL_SYSTEM = """你是记忆整理助手，负责把每日日记提炼为精简长期记忆。

你会收到：
1. 现有 MEMORY.md 全文
2. 若干天的日记内容

输出格式（严格遵守）：
[MEMORY]
- 记忆条目（每条一行，以「- 」开头）
...

[INSIGHT]
本次整理的观察和发现（简短叙述，2-5句话）

规则：
- 合并含义相近的条目
- 从日记中萃取值得永久记住的新信息（偏好/决策/经验/人物）
- 新信息与旧条目矛盾时，以新为准
- 删除临时性/无意义/重复条目
- 总条目控制在 60 条以内，每条一句话
- 严禁编造材料中不存在的信息"""

_DISTILL_USER = """## 现有长期记忆（MEMORY.md）

{memory_content}

## 近期日记（最近 {days} 天）

{daily_content}"""


# ─── Deep Digest Manager ──────────────────────────────────────────────────────

class DeepDigestManager:
    """
    记忆蒸馏流水线管理器。
    
    设计为无状态、可重入，每个 workspace 独立实例。
    """

    DAILY_DIR = "memory/daily"
    ARCHIVE_DIR = "memory/archive"
    MEMORY_FILE = "MEMORY.md"
    MIN_MESSAGES_TO_JOURNAL = 4    # 至少 4 条消息才值得归纳
    DISTILL_DAYS = 7               # 读取最近 N 天日记
    MIN_NEW_DAYS_TO_DISTILL = 1    # 至少有 1 天新日记才触发蒸馏

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
            共享 LLM 调用器（兼容旧 API），journal 和 distill 都使用它。
        journal_llm_caller:
            专用 journal 调用器（温度可略高，如 0.5）。设置后覆盖 llm_caller 的 journal 阶段。
        distill_llm_caller:
            专用 distill 调用器（温度应低，如 0.2，保证蒸馏稳定性）。设置后覆盖 llm_caller 的 distill 阶段。
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
        """异步归纳对话 → 事件日记（不阻塞主线程）。"""
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
        """异步蒸馏最近日记 → MEMORY.md。"""
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
            logger.warning("[DeepDigest] read memory failed: %s", e)
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
                logger.debug("[DeepDigest] journal: nothing to record")
                return
            self._append_to_daily(summary.strip(), date_str)
            logger.info("[DeepDigest] journal written (%d chars)", len(summary))
        except Exception as e:
            logger.warning("[DeepDigest] journal failed: %s", e)

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
            # filter to last N days
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
            logger.info("[DeepDigest] distill complete, %d journal files archived", len(recent))
        except Exception as e:
            logger.warning("[DeepDigest] distill failed: %s", e)

    def _apply_distill_result(self, result: str) -> None:
        """Parse [MEMORY] section and overwrite MEMORY.md."""
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
            logger.warning("[DeepDigest] apply distill failed: %s", e)

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
            role = "用户" if m.get("role") == "user" else "助手"
            lines.append(f"{role}: {m.get('content', '')}")
        return "\n".join(lines)

    @staticmethod
    def _hash_messages(messages: list[dict[str, str]]) -> str:
        text = "".join(m.get("content", "") for m in messages)
        return hashlib.sha256(text.encode()).hexdigest()[:16]
