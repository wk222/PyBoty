"""Semantic (vector-backed) long-term memory.

Extends MemoryManager with vector search for relevant memory retrieval
instead of dumping the last 50 lines.

Architecture:
  - Memories stored as vector embeddings in the vector store
  - Each memory entry = one document with metadata (section, timestamp, source)
  - search_memories() performs semantic search over all stored memories
  - MemoryManager.append_memory() writes to MEMORY.md AND vector store
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .memory_manager import MemoryManager
from .memory_scoring import composite_score, encode_memory, recency_score
from .vector_store import Document, VectorStoreBackend

logger = logging.getLogger(__name__)

MEMORY_COLLECTION = "long_term_memory"


@dataclass
class MemoryEntry:
    """A single memory entry with metadata."""

    content: str
    section: str
    timestamp: float = 0.0
    source: str = "agent"
    relevance: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class SemanticMemoryManager(MemoryManager):
    """MemoryManager with vector-backed semantic search.

    Inherits file-based MEMORY.md persistence and adds:
    - Vector storage of each memory entry for semantic retrieval
    - search_memories() for finding relevant past memories
    - get_context_prompt() enhanced with semantic search
    """

    def __init__(
        self,
        workspace_dir: str = "workspace",
        vector_store: VectorStoreBackend | None = None,
        llm: Any = None,
    ):
        super().__init__(workspace_dir)
        self._vector_store = vector_store
        self._has_vector = vector_store is not None
        self._llm = llm

    def append_memory(self, section: str, content: str) -> None:
        """Write memory to MEMORY.md and optionally to vector store."""
        super().append_memory(section, content)

        if self._has_vector and self._vector_store is not None:
            ts_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            ts_epoch = time.time()

            metadata: dict[str, Any] = {
                "section": section,
                "timestamp": ts_str,
                "timestamp_epoch": ts_epoch,
                "source": "agent",
                "type": "memory",
            }

            if self._llm is not None:
                try:
                    mm = encode_memory(content, self._llm)
                    metadata["importance"] = mm.importance
                    metadata["categories"] = ",".join(mm.categories)
                    metadata["scope"] = mm.scope
                except Exception as exc:
                    logger.debug("LLM encoding failed, using defaults: %s", exc)

            doc = Document(page_content=content, metadata=metadata)
            try:
                self._vector_store.add_documents([doc], collection=MEMORY_COLLECTION)
            except Exception as exc:
                logger.warning("Failed to store memory in vector store: %s", exc)

    def search_memories(
        self,
        query: str,
        top_k: int = 5,
        section: str | None = None,
    ) -> list[MemoryEntry]:
        """Semantically search stored memories with composite scoring."""
        if not self._has_vector or self._vector_store is None:
            return self._fallback_search(query, top_k)

        fetch_k = max(top_k * 3, 20)
        results = self._vector_store.search(query, collection=MEMORY_COLLECTION, top_k=fetch_k)
        entries = []
        for r in results:
            meta = r.document.metadata
            if section and meta.get("section") != section:
                continue

            ts_epoch = meta.get("timestamp_epoch", 0)
            importance = meta.get("importance", 0.5)
            rec = recency_score(ts_epoch) if ts_epoch else 0.5
            score = composite_score(r.score, rec, importance)

            entries.append(
                MemoryEntry(
                    content=r.document.page_content,
                    section=meta.get("section", "unknown"),
                    timestamp=meta.get("timestamp", 0),
                    source=meta.get("source", "unknown"),
                    relevance=score,
                )
            )

        entries.sort(key=lambda e: e.relevance, reverse=True)
        return entries[:top_k]

    def _fallback_search(self, query: str, top_k: int) -> list[MemoryEntry]:
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
            if query_lower in content.lower():
                entries.append(
                    MemoryEntry(
                        content=content,
                        section=current_section,
                        relevance=1.0,
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
                    lines.append(f"[{e.section}] {e.content}")
                return "\n".join(lines)

        return super().get_context_prompt()

    def get_memory_stats(self) -> dict[str, Any]:
        """Get statistics about stored memories."""
        stats: dict[str, Any] = {"file_based": True, "vector_backed": self._has_vector}
        memory_text = self.load()
        stats["file_lines"] = len(memory_text.split("\n")) if memory_text else 0

        if self._has_vector and self._vector_store is not None:
            try:
                stats["vector_count"] = self._vector_store.count(MEMORY_COLLECTION)
            except Exception:
                stats["vector_count"] = 0
        return stats

    def clear_vector_memories(self) -> bool:
        """Clear all vector-stored memories (MEMORY.md untouched)."""
        if not self._has_vector or self._vector_store is None:
            return False
        try:
            self._vector_store.delete_collection(MEMORY_COLLECTION)
            return True
        except Exception:
            return False
