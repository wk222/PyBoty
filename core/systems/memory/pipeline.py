"""MemoryPipeline — async journal / distill / reflect orchestration.

Single owner of the LLM-driven side of the memory engine. Replaces the
old ``MemoryDistillManager`` (290 lines) and ``SessionMemoryScheduler``
(300 lines) with a leaner thread-safe pipeline that writes everything
through the engine's ``store`` instead of touching markdown files.

Stages:

  Stage 1 — JOURNAL    Convert raw conversation messages into a list of
                       categorised event lines + an optional ``[EPISODE]``
                       block. Each parsed event becomes one ``modality=fact``
                       record (legacy compatibility) and one ``modality=journal``
                       record (full text per day for audit / debug).
                       Episodes are stored as ``modality=episode``.

  Stage 2 — DISTILL    Read the most recent N journal entries and emit
                       a ``[MEMORY]`` block (importance-tagged facts) +
                       an optional ``[INSIGHT]`` paragraph. Existing facts
                       are upserted, insights become ``modality=insight``.

  Stage 3 — REFLECT    Look at the freshly distilled fact set + recent
                       reflections and emit 3-5 metacognitive observations
                       as ``modality=reflection`` records. The protected
                       reflection tag (``[反思]``) is exempt from GC.

  Stage 4 — GC         Trigger ``engine.gc()`` after distill so MEMORY.md
                       size stays bounded.

Each stage is a separate thread; all writes go through the engine /
store so concurrent agents never race on a markdown file.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from .engine import MemoryEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompts (kept compatible with the previous distill prompts)
# ---------------------------------------------------------------------------


_JOURNAL_SYSTEM = """你是对话记录助手。把以下对话归纳为当天的「分类事件日记」。

输出格式（严格遵守）：
首先按行输出分类事件，每条格式：- [分类] 事件描述
然后**可选**追加情节段，开头一行写 [EPISODE]，之后每条情节一行，
格式：- 时间|角色|场景|动作|结果

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
- [EPISODE] 段最多 5 条，时间用 YYYY-MM-DD HH:MM 格式

若对话无记录价值，直接输出「无」。"""

_JOURNAL_USER = "请归纳以下对话的分类事件日记：\n\n{conversation}"

_DISTILL_SYSTEM = """你是记忆整理助手，负责把每日日记提炼为精简长期记忆。

输出格式（严格遵守）：
[MEMORY]
- [分类|重要性] 记忆条目

分类标签同日记（偏好/决策/事实/人物/工具/待办/其他）。
重要性：★★★ 高 / ★★ 中 / ★ 低

[INSIGHT]
本次整理的观察和发现（简短叙述，2-5句话）

规则：
- 合并含义相近的条目
- 优先删除 ★ 低重要性条目来腾出空间
- 总条目控制在 60 条以内
- 严禁编造材料中不存在的信息"""

_DISTILL_USER = """## 现有长期记忆（MEMORY.md）

{memory_content}

## 近期日记（最近 {days} 天）

{daily_content}{prior_insights}"""

_PRIOR_INSIGHT_SECTION = """

## 历史洞见摘录（避免重复，必要时整合）

{prior}"""

_REFLECT_SYSTEM = """你是元认知助手，负责对长期记忆进行反思。

输入：当前 MEMORY.md 完整内容（包括既有反思和事实条目）。

输出格式（严格遵守）：
[REFLECT]
- [反思|★★★] <一条独立观察>
- [反思|★★★] <另一条独立观察>
- ...

要求：
- 输出 3-5 条新观察，每条聚焦一个独立的"模式 / 信念变化 / 反例 / 行为倾向"
- 优先记录信念变化：如"之前以为 X，现在更倾向 Y"
- 直接陈述结论，不解释推理过程
- 与已有反思条目重复或高度相似的不要再写
- 全部以 `- [反思|★★★] ` 开头，每条不超过 80 字
- 若没有有价值的新观察，直接输出「无」"""

_REFLECT_USER = "## 当前 MEMORY.md 全文\n\n{memory_content}\n\n请输出反思。"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


_FACT_LINE = re.compile(r"^-\s*\[([^\]]+)\]\s*(.+?)\s*$")
_TAG_SOFT = re.compile(r"([^:,\s]+)\s*:\s*([0-9.]+)")
_STAR_TO_IMPORTANCE = {"★★★": 1.0, "★★": 0.66, "★": 0.33}
_KNOWN_TAGS = ("偏好", "决策", "事实", "人物", "工具", "待办", "反思", "情节", "其他")
_PROTECTED_TAGS = ("反思",)
_EPISODE_LINE = re.compile(
    r"^\s*-\s*(?P<ts>[^|]+)\|(?P<actor>[^|]+)\|(?P<scene>[^|]+)\|(?P<action>[^|]+)\|(?P<outcome>.+?)\s*$"
)

_SYSTEM_NOISE_MARKERS = (
    "你正在作为 PyBot",
    "You are extracting structured session notes",
    "Produce a concise markdown summary covering",
    "你是对话记录助手",
    "你是记忆整理助手",
    "你是元认知助手",
    "持久化管理员运行时",
    "Auto-Repair from Telemetry",
)


def _format_messages(messages: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "")
        if role == "system":
            continue
        content = m.get("content", "")
        if not content or not content.strip():
            continue
        if any(marker in content for marker in _SYSTEM_NOISE_MARKERS):
            continue
        label = "用户" if role == "user" else "助手"
        if role in ("tool", "function"):
            label = "工具结果"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


def _parse_tag_field(tag_field: str) -> tuple[dict[str, float], float]:
    importance = 0.5
    if "|" in tag_field:
        tag_part, _, star_part = tag_field.rpartition("|")
        importance = _STAR_TO_IMPORTANCE.get(star_part.strip(), 0.5)
    else:
        tag_part = tag_field
    soft_tags: dict[str, float] = {}
    if ":" in tag_part:
        for tag, weight in _TAG_SOFT.findall(tag_part):
            try:
                soft_tags[tag.strip()] = float(weight)
            except ValueError:
                continue
    if not soft_tags:
        for tag in _KNOWN_TAGS:
            if tag in tag_part:
                soft_tags[tag] = 1.0
                break
    if not soft_tags:
        soft_tags["其他"] = 1.0
    total = sum(soft_tags.values()) or 1.0
    return {t: w / total for t, w in soft_tags.items()}, importance


def _parse_fact_lines(text: str) -> list[tuple[str, dict[str, float], float]]:
    """Parse `- [tag|stars] content` lines from any text blob."""
    out: list[tuple[str, dict[str, float], float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        match = _FACT_LINE.match(line)
        if match is None:
            continue
        tag_field, content = match.group(1), match.group(2)
        soft_tags, importance = _parse_tag_field(tag_field)
        out.append((content, soft_tags, importance))
    return out


def _parse_episode_block(raw: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    if not raw or "[EPISODE]" not in raw:
        return events
    body = raw.split("[EPISODE]", 1)[1]
    # Stop at the next bracketed marker (e.g. [REFLECT] / [MEMORY])
    next_block = body.find("[", body.find("]") + 1) if "]" in body else -1
    if next_block > 0:
        body = body[:next_block]
    for line in body.splitlines():
        m = _EPISODE_LINE.match(line)
        if not m:
            continue
        events.append({k: m.group(k).strip() for k in ("ts", "actor", "scene", "action", "outcome")})
    return events


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


MAX_REFLECTIONS = 8
DISTILL_DAYS = 7
MIN_MESSAGES_TO_JOURNAL = 4
MIN_NEW_DAYS_TO_DISTILL = 1


class MemoryPipeline:
    """LLM-driven journal/distill/reflect for :class:`MemoryEngine`."""

    def __init__(
        self,
        *,
        engine: "MemoryEngine",
        llm_caller: Optional[Callable[[str, str], str]] = None,
        journal_caller: Optional[Callable[[str, str], str]] = None,
        distill_caller: Optional[Callable[[str, str], str]] = None,
        reflect_caller: Optional[Callable[[str, str], str]] = None,
    ) -> None:
        self._engine = engine
        self._lock = threading.Lock()
        self._journal_caller = journal_caller or llm_caller
        self._distill_caller = distill_caller or llm_caller
        # Reflection runs after distill on the same content; reuse the
        # low-temperature distill caller as the safe default.
        self._reflect_caller = reflect_caller or distill_caller or llm_caller
        self._reflect_callback: Optional[Callable[[list[str]], None]] = None
        self._post_distill_callback: Optional[Callable[[], None]] = None

    # ---- lifecycle hooks ---------------------------------------------

    def set_reflect_callback(self, fn: Optional[Callable[[list[str]], None]]) -> None:
        self._reflect_callback = fn

    def set_post_distill_callback(self, fn: Optional[Callable[[], None]]) -> None:
        self._post_distill_callback = fn

    # ---- Stage 1: Journal --------------------------------------------

    def journal_async(self, messages: list[dict[str, str]], *, date_str: str | None = None) -> None:
        if len(messages) < MIN_MESSAGES_TO_JOURNAL:
            return
        content_hash = self._hash_messages(messages)
        if self._engine.store.journal_seen(content_hash):
            return
        self._engine.store.journal_mark(content_hash)
        threading.Thread(
            target=self._journal_sync,
            args=(messages, date_str),
            daemon=True,
        ).start()

    def _journal_sync(
        self, messages: list[dict[str, str]], date_str: str | None
    ) -> None:
        caller = self._journal_caller
        if not caller:
            return
        try:
            text = caller(_JOURNAL_SYSTEM, _JOURNAL_USER.format(conversation=_format_messages(messages)))
        except Exception as exc:
            logger.warning("[MemoryPipeline] journal failed: %s", exc)
            return
        if not text or text.strip() in ("无", "无。"):
            return
        cleaned = text.strip()
        today = date_str or datetime.now().strftime("%Y-%m-%d")
        # Persist the raw journal text so the distill stage can read it.
        from .engine import Modality

        self._engine.ingest(
            Modality.JOURNAL,
            cleaned,
            metadata={"date_str": today},
            importance=0.4,
        )
        # Optional [EPISODE] events
        for ev in _parse_episode_block(cleaned):
            try:
                self._engine.ingest_episode(
                    ts_label=ev["ts"],
                    actor=ev["actor"],
                    scene=ev["scene"],
                    action=ev["action"],
                    outcome=ev["outcome"],
                )
            except Exception as exc:
                logger.debug("episode ingest failed: %s", exc)

    # ---- Stage 2: Distill --------------------------------------------

    def distill_async(self, *, force: bool = False) -> None:
        threading.Thread(
            target=self._distill_sync,
            kwargs={"force": force},
            daemon=True,
        ).start()

    def _distill_sync(self, *, force: bool = False) -> None:
        caller = self._distill_caller
        if not caller:
            return
        from .engine import Modality

        with self._lock:
            try:
                journals = self._engine.store.list(
                    modality=Modality.JOURNAL.value,
                    order_by="first_seen_ts DESC",
                    limit=DISTILL_DAYS * 2,
                )
                last_run, _ = self._engine.store.get_pipeline_state("distill")
                fresh = [j for j in journals if j.first_seen_ts > last_run]
                if not force and len(fresh) < MIN_NEW_DAYS_TO_DISTILL:
                    return
                journal_text = "\n\n---\n\n".join(j.content for j in (fresh or journals[:1]))
                memory_content = self._engine.export_memory_md()
                prior_insights = self._fetch_prior_insights()
                prior_section = (
                    _PRIOR_INSIGHT_SECTION.format(prior=prior_insights)
                    if prior_insights
                    else ""
                )
                result = caller(
                    _DISTILL_SYSTEM,
                    _DISTILL_USER.format(
                        memory_content=memory_content,
                        daily_content=journal_text,
                        days=len(fresh) or 1,
                        prior_insights=prior_section,
                    ),
                )
                self._apply_distill_result(result)
                self._engine.store.set_pipeline_state(
                    "distill", last_run_ts=time.time()
                )
                # Stage 3: Reflect
                if self._engine.config.enable_reflection:
                    self._reflect_sync()
                # Stage 4: GC
                cb = self._post_distill_callback
                if cb is not None:
                    try:
                        cb()
                    except Exception as exc:
                        logger.debug("post-distill callback failed: %s", exc)
                else:
                    try:
                        self._engine.gc()
                    except Exception as exc:
                        logger.debug("post-distill gc failed: %s", exc)
                # Sync MEMORY.md for human inspection
                self._engine.sync_memory_md()
            except Exception as exc:
                logger.warning("[MemoryPipeline] distill failed: %s", exc)

    def _apply_distill_result(self, result: str) -> None:
        if "[MEMORY]" not in result:
            return
        from .engine import Modality

        memory_part = result.split("[MEMORY]", 1)[1]
        insight_part = ""
        if "[INSIGHT]" in memory_part:
            memory_part, _, insight_part = memory_part.partition("[INSIGHT]")
        # Stop reading at any subsequent block marker so we don't ingest noise.
        for marker in ("[REFLECT]", "[EPISODE]"):
            if marker in memory_part:
                memory_part = memory_part.split(marker, 1)[0]
        for content, soft_tags, importance in _parse_fact_lines(memory_part):
            self._engine.ingest(
                Modality.FACT,
                content,
                metadata={"soft_tags": soft_tags},
                importance=importance,
            )
        insight = insight_part.strip()
        if insight:
            self._engine.ingest(
                Modality.INSIGHT,
                insight,
                importance=0.6,
            )

    def _fetch_prior_insights(self, top_k: int = 5) -> str:
        from .engine import Modality

        rows = self._engine.store.list(
            modality=Modality.INSIGHT.value,
            order_by="first_seen_ts DESC",
            limit=top_k,
        )
        return "\n".join(f"- {r.content}" for r in rows if r.content)

    # ---- Stage 3: Reflect --------------------------------------------

    def reflect_sync(self) -> list[str]:
        caller = self._reflect_caller
        if not caller:
            return []
        from .engine import Modality

        memory_content = self._engine.export_memory_md()
        try:
            text = caller(_REFLECT_SYSTEM, _REFLECT_USER.format(memory_content=memory_content))
        except Exception as exc:
            logger.warning("[MemoryPipeline] reflect failed: %s", exc)
            return []
        new_lines = self._parse_reflect_lines(text or "")
        if not new_lines:
            return []
        # Ingest as REFLECTION records (importance ★★★ → 1.0)
        accepted: list[str] = []
        for content, _soft_tags, importance in _parse_fact_lines("\n".join(new_lines)):
            self._engine.ingest(
                Modality.REFLECTION,
                content,
                metadata={"soft_tags": {"反思": 1.0}},
                importance=max(importance, 0.9),
            )
            accepted.append(content)
        # Cap reflections — drop oldest beyond MAX_REFLECTIONS.
        all_reflections = self._engine.store.list(
            modality=Modality.REFLECTION.value,
            order_by="first_seen_ts DESC",
            limit=MAX_REFLECTIONS * 5,
        )
        if len(all_reflections) > MAX_REFLECTIONS:
            for stale in all_reflections[MAX_REFLECTIONS:]:
                self._engine.store.update_status(stale.id, "archived")
        cb = self._reflect_callback
        if cb is not None:
            try:
                cb([f"- [反思|★★★] {c}" for c in accepted])
            except Exception as exc:
                logger.debug("reflect callback failed: %s", exc)
        return accepted

    @staticmethod
    def _parse_reflect_lines(raw: str) -> list[str]:
        if not raw or raw.strip() in ("无", "无。"):
            return []
        if "[REFLECT]" in raw:
            raw = raw.split("[REFLECT]", 1)[1]
        out: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("- [反思"):
                out.append(stripped)
        return out

    # ---- helpers ------------------------------------------------------

    @staticmethod
    def _hash_messages(messages: list[dict[str, str]]) -> str:
        text = "".join(m.get("content", "") for m in messages)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "DISTILL_DAYS",
    "MAX_REFLECTIONS",
    "MIN_MESSAGES_TO_JOURNAL",
    "MIN_NEW_DAYS_TO_DISTILL",
    "MemoryPipeline",
]
