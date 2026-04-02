"""Semantic (vector-backed) long-term memory.

Extends MemoryManager with vector search for relevant memory retrieval
instead of dumping the last 50 lines.

Architecture:
  - Memories stored as vector embeddings in the vector store
  - Each memory entry = one document with metadata (section, timestamp, source, category)
  - search_memories() performs semantic search over all stored memories
  - MemoryManager.append_memory() writes to MEMORY.md AND vector store
  - Dedup: entries with cosine similarity >= DEDUP_THRESHOLD are skipped
  - Injection guard: reject entries that look like prompt injections
  - Auto-recall / auto-capture lifecycle hooks
  - forget_memory(): semantic search → delete matching entries (GDPR-friendly)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from core.systems.knowledge.query_expansion import ContextCompactor, QueryExpansionEngine
from core.vector_store import Document, SearchResult, VectorStoreBackend

from .memory_manager import MemoryManager
from .memory_scoring import composite_score, encode_memory, recency_score
from .memory_taxonomy import (
    SESSION_MEMORY_TYPE,
    category_for_memory_type,
    memory_type_for_section,
    normalize_memory_type,
    section_for_memory_type,
)

logger = logging.getLogger(__name__)

MEMORY_COLLECTION = "long_term_memory"

DEDUP_THRESHOLD = 0.95

_MAX_MEMORY_LENGTH = 2000
_MIN_MEMORY_LENGTH = 5
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system:\s*you\s+are", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"\bDAN\b.*\bmode\b", re.IGNORECASE),
    re.compile(r"pretend\s+you\s+are\s+a\s+different", re.IGNORECASE),
]


class MemoryCategory(str, Enum):
    """Standard memory categories."""

    PREFERENCE = "preference"
    FACT = "fact"
    DECISION = "decision"
    ENTITY = "entity"
    OTHER = "other"


class SearchStrategy(str, Enum):
    """Memory retrieval strategies."""

    VECTOR = "vector"
    HYBRID = "hybrid"
    KEYWORD = "keyword"


@dataclass
class MemoryEntry:
    """A single memory entry with metadata."""

    content: str
    section: str
    timestamp: float = 0.0
    source: str = "agent"
    relevance: float = 0.0
    category: str = "other"
    memory_type: str = SESSION_MEMORY_TYPE
    doc_id: str | None = None

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


def _looks_like_injection(text: str) -> bool:
    """Return True if *text* matches a known prompt-injection pattern."""
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def _capture_filter(text: str) -> bool:
    """Return True if *text* passes the capture filter (safe to store)."""
    if len(text) < _MIN_MEMORY_LENGTH:
        return False
    if len(text) > _MAX_MEMORY_LENGTH:
        return False
    if _looks_like_injection(text):
        logger.warning("Rejected memory entry — injection pattern detected")
        return False
    emoji_count = sum(1 for ch in text if ord(ch) > 0x1F600)
    if len(text) > 0 and emoji_count / len(text) > 0.5:
        return False
    return True


class SemanticMemoryManager(MemoryManager):
    """MemoryManager with vector-backed semantic search.

    Inherits file-based MEMORY.md persistence and adds:
    - Vector storage of each memory entry for semantic retrieval
    - search_memories() for finding relevant past memories
    - get_context_prompt() enhanced with semantic search
    - Dedup: skip near-duplicate entries (similarity >= 0.95)
    - Injection guard: reject suspicious entries
    - Auto-recall / auto-capture hooks
    - forget_memory(): delete matching entries by semantic search
    """

    def __init__(
        self,
        workspace_dir: str = "workspace",
        vector_store: VectorStoreBackend | None = None,
        llm: Any = None,
        *,
        search_strategy: str = SearchStrategy.VECTOR.value,
        keyword_weight: float = 0.35,
        vector_weight: float = 0.65,
        mmr_enabled: bool = False,
        mmr_lambda: float = 0.7,
        temporal_decay_enabled: bool = False,
        temporal_half_life_days: float = 30.0,
        collection_name: str = MEMORY_COLLECTION,
    ):
        super().__init__(workspace_dir)
        self._vector_store = vector_store
        self._has_vector = vector_store is not None
        self._llm = llm
        self._search_strategy = SearchStrategy(search_strategy)
        self._keyword_weight = keyword_weight
        self._vector_weight = vector_weight
        self._mmr_enabled = mmr_enabled
        self._mmr_lambda = mmr_lambda
        self._temporal_decay_enabled = temporal_decay_enabled
        self._temporal_half_life_days = temporal_half_life_days
        self._collection_name = collection_name
        self._query_expansion = None
        self._context_compactor = ContextCompactor(max_tokens=4000)

    @property
    def query_expansion(self) -> QueryExpansionEngine:
        if self._query_expansion is None:
            self._query_expansion = QueryExpansionEngine(llm=self._llm)
        return self._query_expansion

    def _is_near_duplicate(self, content: str) -> bool:
        """Check if *content* is a near-duplicate of an existing memory."""
        if not self._has_vector or self._vector_store is None:
            return False
        try:
            results = self._vector_store.search(content, collection=self._collection_name, top_k=1)
            if results and results[0].score >= DEDUP_THRESHOLD:
                logger.debug("Near-duplicate detected (score=%.3f), skipping", results[0].score)
                return True
        except Exception:
            pass
        return False

    def append_memory(
        self,
        section: str,
        content: str,
        *,
        memory_type: str | None = None,
        occurred_on: str = "",
        verified: bool = False,
        source: str = "agent",
    ) -> None:
        """Write memory to MEMORY.md and optionally to vector store.

        Applies capture filter (length, injection, emoji) and dedup before storing.
        """
        if not _capture_filter(content):
            logger.debug("Memory entry rejected by capture filter: %.60s…", content)
            return

        if self._is_near_duplicate(content):
            return

        resolved_memory_type = normalize_memory_type(memory_type or memory_type_for_section(section))
        resolved_section = (
            section_for_memory_type(resolved_memory_type) if resolved_memory_type != SESSION_MEMORY_TYPE else section
        )

        super().append_memory(resolved_section, content)

        if self._has_vector and self._vector_store is not None:
            ts_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            ts_epoch = time.time()

            metadata: dict[str, Any] = {
                "section": resolved_section,
                "timestamp": ts_str,
                "timestamp_epoch": ts_epoch,
                "source": source,
                "type": "memory",
                "category": category_for_memory_type(resolved_memory_type),
                "memory_type": resolved_memory_type,
                "verified": bool(verified),
                "occurred_on": str(occurred_on).strip(),
            }

            if self._llm is not None:
                try:
                    mm = encode_memory(content, self._llm)
                    metadata["importance"] = mm.importance
                    metadata["categories"] = ",".join(mm.categories)
                    metadata["scope"] = mm.scope
                    if mm.categories and resolved_memory_type == SESSION_MEMORY_TYPE:
                        metadata["category"] = _classify_category(mm.categories)
                except Exception as exc:
                    logger.debug("LLM encoding failed, using defaults: %s", exc)

            doc = Document(page_content=content, metadata=metadata)
            try:
                self._vector_store.add_documents([doc], collection=self._collection_name)
            except Exception as exc:
                logger.warning("Failed to store memory in vector store: %s", exc)

    def append_typed_memory(
        self,
        *,
        memory_type: str,
        content: str,
        occurred_on: str = "",
        verified: bool = False,
        source: str = "agent",
    ) -> None:
        resolved_type = normalize_memory_type(memory_type)
        self.append_memory(
            section=section_for_memory_type(resolved_type),
            content=content,
            memory_type=resolved_type,
            occurred_on=occurred_on,
            verified=verified,
            source=source,
        )

    def forget_memory(self, query: str, top_k: int = 3, threshold: float = 0.6) -> list[str]:
        """Semantically search and delete matching memories (GDPR-friendly).

        Returns list of deleted content strings.
        """
        if not self._has_vector or self._vector_store is None:
            return []

        results = self._vector_store.search(query, collection=self._collection_name, top_k=top_k)
        to_delete: list[str] = []
        ids_to_delete: list[str] = []
        for r in results:
            if r.score >= threshold:
                to_delete.append(r.document.page_content)
                if r.document.doc_id:
                    ids_to_delete.append(r.document.doc_id)

        if ids_to_delete:
            self._vector_store.delete(ids_to_delete, collection=self._collection_name)
            logger.info("Forgot %d memory entries matching query: %.60s", len(ids_to_delete), query)

        return to_delete

    def auto_recall(self, prompt: str, top_k: int = 5) -> str:
        """Auto-inject relevant memories based on the current prompt.

        Called before agent starts (before_agent_start lifecycle hook).
        Returns a formatted string suitable for system prompt injection.
        """
        entries = self.search_memories(prompt, top_k=top_k)
        if not entries:
            return ""

        lines = ["[Auto-recalled memories]"]
        for e in entries:
            cat_label = f"[{e.category}]" if e.category != "other" else ""
            lines.append(f"- {cat_label} {e.content}")
        return "\n".join(lines)

    def auto_capture(self, conversation: list[dict[str, str]]) -> list[str]:
        """Auto-extract and store key facts from the conversation.

        Called after agent ends (agent_end lifecycle hook).
        Returns list of captured content strings.
        """
        if len(conversation) < 2:
            return []

        captured: list[str] = []
        try:
            from .memory_manager import extract_key_facts

            facts = extract_key_facts(conversation)
            for fact in facts:
                section = fact.get("section", "facts")
                content = fact.get("content", "")
                if content and _capture_filter(content) and not self._is_near_duplicate(content):
                    self.append_memory(section, content)
                    captured.append(content)
        except Exception as exc:
            logger.debug("Auto-capture failed: %s", exc)

        return captured

    def search_memories(
        self,
        query: str,
        top_k: int = 5,
        section: str | None = None,
        category: str | None = None,
        memory_type: str | None = None,
        expand_query: bool = False,
    ) -> list[MemoryEntry]:
        """Semantically search stored memories with composite scoring."""
        if not self._has_vector or self._vector_store is None or self._search_strategy == SearchStrategy.KEYWORD:
            return self._fallback_search(query, top_k, section=section, category=category, memory_type=memory_type)

        queries = self.query_expansion.expand_query(query) if expand_query else [query]
        all_vector_results = []
        seen_docs = set()

        fetch_k = max(top_k * 3, 20)

        for q in queries:
            v_results = self._vector_store.search(q, collection=self._collection_name, top_k=fetch_k)
            for r in v_results:
                if r.document.doc_id not in seen_docs:
                    all_vector_results.append(r)
                    if r.document.doc_id:
                        seen_docs.add(r.document.doc_id)

        # Sort combined results by score
        all_vector_results.sort(key=lambda r: r.score, reverse=True)
        vector_results = all_vector_results[:fetch_k]

        if self._search_strategy == SearchStrategy.VECTOR:
            entries = self._entries_from_vector_results(
                vector_results,
                section=section,
                category=category,
                memory_type=memory_type,
            )
            if self._mmr_enabled:
                entries = self._apply_mmr(entries, top_k)
            return entries[:top_k]

        entries = self._merge_hybrid_results(
            query,
            vector_results=vector_results,
            top_k=top_k,
            section=section,
            category=category,
            memory_type=memory_type,
        )
        if self._mmr_enabled:
            entries = self._apply_mmr(entries, top_k)
        return entries[:top_k]

    def _entries_from_vector_results(
        self,
        results: list[SearchResult],
        *,
        section: str | None = None,
        category: str | None = None,
        memory_type: str | None = None,
    ) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        for result in results:
            meta = result.document.metadata
            if section and meta.get("section") != section:
                continue
            if category and meta.get("category") != category:
                continue
            if memory_type and meta.get("memory_type") != normalize_memory_type(memory_type):
                continue

            entries.append(
                MemoryEntry(
                    content=result.document.page_content,
                    section=meta.get("section", "unknown"),
                    timestamp=float(meta.get("timestamp_epoch", 0) or 0),
                    source=meta.get("source", "unknown"),
                    relevance=self._score_result(result),
                    category=meta.get("category", "other"),
                    memory_type=meta.get("memory_type", memory_type_for_section(meta.get("section", ""))),
                    doc_id=result.document.doc_id,
                )
            )
        entries.sort(key=lambda item: item.relevance, reverse=True)
        return entries

    def _merge_hybrid_results(
        self,
        query: str,
        *,
        vector_results: list[SearchResult],
        top_k: int,
        section: str | None,
        category: str | None,
        memory_type: str | None,
    ) -> list[MemoryEntry]:
        keyword_entries = self._fallback_search(
            query,
            max(top_k * 3, 20),
            section=section,
            category=category,
            memory_type=memory_type,
        )
        merged: dict[str, MemoryEntry] = {}

        for result in vector_results:
            entry = self._entries_from_vector_results(
                [result],
                section=section,
                category=category,
                memory_type=memory_type,
            )
            if not entry:
                continue
            item = entry[0]
            merged[item.doc_id or item.content] = item

        for keyword_entry in keyword_entries:
            if section and keyword_entry.section != section:
                continue
            if category and keyword_entry.category != category:
                continue
            if memory_type and keyword_entry.memory_type != normalize_memory_type(memory_type):
                continue
            key = keyword_entry.doc_id or keyword_entry.content
            if key in merged:
                merged[key].relevance = (
                    self._vector_weight * merged[key].relevance + self._keyword_weight * keyword_entry.relevance
                )
            else:
                merged[key] = keyword_entry

        entries = list(merged.values())
        entries.sort(key=lambda item: item.relevance, reverse=True)
        return entries

    def _score_result(self, result: SearchResult) -> float:
        meta = result.document.metadata
        ts_epoch = float(meta.get("timestamp_epoch", 0) or 0)
        importance = float(meta.get("importance", 0.5) or 0.5)
        vector_score = result.vector_score if result.vector_score is not None else result.score
        keyword_component = result.keyword_score or 0.0
        if self._search_strategy == SearchStrategy.HYBRID:
            relevance = self._vector_weight * vector_score + self._keyword_weight * keyword_component
        else:
            relevance = vector_score

        rec = recency_score(ts_epoch) if ts_epoch else 0.5
        score = composite_score(relevance, rec, importance)
        if self._temporal_decay_enabled and ts_epoch:
            score *= self._temporal_decay_factor(ts_epoch)
        return score

    def _temporal_decay_factor(self, timestamp_epoch: float) -> float:
        age_seconds = max(time.time() - timestamp_epoch, 0.0)
        half_life_seconds = max(self._temporal_half_life_days, 0.001) * 86400.0
        return 0.5 ** (age_seconds / half_life_seconds)

    def _apply_mmr(self, entries: list[MemoryEntry], top_k: int) -> list[MemoryEntry]:
        if len(entries) <= 1:
            return entries
        remaining = entries[:]
        selected = [remaining.pop(0)]
        while remaining and len(selected) < top_k:
            best_index = 0
            best_score = float("-inf")
            for index, candidate in enumerate(remaining):
                max_similarity = max(self._entry_similarity(candidate, item) for item in selected)
                mmr_score = self._mmr_lambda * candidate.relevance - (1.0 - self._mmr_lambda) * max_similarity
                if mmr_score > best_score:
                    best_index = index
                    best_score = mmr_score
            selected.append(remaining.pop(best_index))
        selected.extend(remaining)
        return selected

    @staticmethod
    def _entry_similarity(left: MemoryEntry, right: MemoryEntry) -> float:
        left_tokens = set(re.findall(r"\w+", left.content.lower()))
        right_tokens = set(re.findall(r"\w+", right.content.lower()))
        if not left_tokens or not right_tokens:
            return 0.0
        intersection = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens)
        return intersection / union if union else 0.0

    def _fallback_search(
        self,
        query: str,
        top_k: int,
        *,
        section: str | None = None,
        category: str | None = None,
        memory_type: str | None = None,
    ) -> list[MemoryEntry]:
        """Keyword-based fallback when no vector store is available."""
        memory_text = self.load()
        if not memory_text:
            return []

        query_lower = query.lower()
        entries = []
        current_section = ""
        for line in memory_text.split("\n"):
            if line.startswith("## "):
                current_section = line[3:].strip()
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith(">"):
                continue
            if stripped.startswith("- "):
                content = stripped[2:]
            else:
                content = stripped
            inferred_memory_type = memory_type_for_section(current_section)
            inferred_category = category_for_memory_type(inferred_memory_type)
            if section and current_section != section:
                continue
            if category and inferred_category != category:
                continue
            if memory_type and inferred_memory_type != normalize_memory_type(memory_type):
                continue
            if query_lower in content.lower():
                entries.append(
                    MemoryEntry(
                        content=content,
                        section=current_section,
                        relevance=1.0,
                        category=inferred_category,
                        memory_type=inferred_memory_type,
                    )
                )
            else:
                query_words = set(query_lower.split())
                content_lower = content.lower()
                overlap = sum(1 for w in query_words if w in content_lower)
                if overlap > 0:
                    score = overlap / max(len(query_words), 1)
                    entries.append(
                        MemoryEntry(
                            content=content,
                            section=current_section,
                            relevance=score,
                            category=inferred_category,
                            memory_type=inferred_memory_type,
                        )
                    )

        entries.sort(key=lambda e: e.relevance, reverse=True)
        return entries[:top_k]

    def get_context_prompt(self, query: str | None = None, max_entries: int = 10) -> str:
        """Generate context prompt, optionally using semantic search."""
        if query and self._has_vector:
            entries = self.search_memories(query, top_k=max_entries)
            if entries:
                lines = ["\n\n--- 相关记忆 ---"]
                for e in entries:
                    cat = f"[{e.category}] " if e.category != "other" else ""
                    mem = f"[{e.memory_type}] " if e.memory_type != SESSION_MEMORY_TYPE else ""
                    lines.append(f"[{e.section}] {mem}{cat}{e.content}")
                return "\n".join(lines)

        return super().get_context_prompt()

    def get_memory_stats(self) -> dict[str, Any]:
        """Get statistics about stored memories."""
        stats: dict[str, Any] = {"file_based": True, "vector_backed": self._has_vector}
        memory_text = self.load()
        stats["file_lines"] = len(memory_text.split("\n")) if memory_text else 0

        if self._has_vector and self._vector_store is not None:
            try:
                stats["vector_count"] = self._vector_store.count(self._collection_name)
            except Exception:
                stats["vector_count"] = 0
        return stats

    def clear_vector_memories(self) -> bool:
        """Clear all vector-stored memories (MEMORY.md untouched)."""
        if not self._has_vector or self._vector_store is None:
            return False
        try:
            self._vector_store.delete_collection(self._collection_name)
            return True
        except Exception:
            return False


_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    MemoryCategory.PREFERENCE.value: ["prefer", "like", "dislike", "favorite", "偏好", "喜欢", "不喜欢"],
    MemoryCategory.FACT.value: ["fact", "know", "learn", "discover", "事实", "知道", "发现"],
    MemoryCategory.DECISION.value: ["decide", "chose", "decision", "plan", "决定", "计划", "选择"],
    MemoryCategory.ENTITY.value: ["name", "person", "company", "project", "人", "公司", "项目"],
}


def _classify_category(tags: list[str]) -> str:
    """Map LLM-produced category tags to a standard MemoryCategory."""
    joined = " ".join(t.lower() for t in tags)
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in joined for kw in keywords):
            return cat
    return MemoryCategory.OTHER.value
