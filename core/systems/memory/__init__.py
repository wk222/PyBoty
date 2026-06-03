"""Memory subsystem — single :class:`MemoryEngine` over SQLite.

After the *T3 收编* refactor this package exports exactly one entry
point class (:class:`MemoryEngine`) plus its enums, record type, store,
scorer, pipeline, and LangChain tools. There are NO legacy aliases:
old names (``UnifiedMemory`` / ``SemanticMemoryManager`` /
``MemoryRouter`` / ``MemoryDistillManager`` / ``EpisodicStore`` /
``InsightSink`` / ``MemoryEntry`` / ``MemoryFact`` / …) have been
deleted along with their source files.

Use::

    from core.systems.memory import MemoryEngine, build_memory_engine

The engine owns:
  * **ingest** — write any modality (fact / episode / reflection /
    insight / journal / session_note) into a single SQLite table.
  * **recall** — scoped, modality-filtered, embedding-aware retrieval
    with adaptive importance and BM25-lite fallback.
  * **feedback** — explicit signals that adjust importance over time.
  * **gc / reconsolidate** — forgetting curve with restoration on hit.
  * **journal / distill / reflect** — async LLM pipelines (delegated
    to :class:`MemoryPipeline`).
  * **as_tools()** — emit LangChain BaseTools for agent use.
"""

from __future__ import annotations

from .engine import (
    EngineConfig,
    MemoryEngine,
    MemoryRecord,
    Modality,
    Scope,
    Signal,
    Status,
    build_memory_engine,
)
from .scoring import (
    EmbeddingsAdapter,
    Scorer,
    ScorerConfig,
    bm25_lite,
    cosine,
    feedback_delta,
    softmax,
    tokenize,
)
from .store import SqliteMemoryStore, StoredRecord
from .pipeline import MemoryPipeline
from .tools import (
    build_all_tools,
    build_garden_tools,
    build_memory_tools,
    get_garden_tools,
)
from .markdown_garden import GardenNote, MarkdownGardenManager
from .admin_memory import AdminMemoryConfig, AdminMemoryManager, create_llm_summarizer

__all__ = [
    "AdminMemoryConfig",
    "AdminMemoryManager",
    "EmbeddingsAdapter",
    "EngineConfig",
    "GardenNote",
    "MarkdownGardenManager",
    "MemoryEngine",
    "MemoryPipeline",
    "MemoryRecord",
    "Modality",
    "Scope",
    "Scorer",
    "ScorerConfig",
    "Signal",
    "SqliteMemoryStore",
    "Status",
    "StoredRecord",
    "bm25_lite",
    "build_all_tools",
    "build_garden_tools",
    "build_memory_engine",
    "build_memory_tools",
    "cosine",
    "create_llm_summarizer",
    "feedback_delta",
    "get_garden_tools",
    "softmax",
    "tokenize",
]
