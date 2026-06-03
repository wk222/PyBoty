"""Memory and knowledge system — Layer 1 (First Branch) of PyBot's tree.

Provides semantic memory, markdown garden (sparse long-term memory),
admin memory, session memory extraction, memory taxonomy, scoring,
and the LangChain memory tools that agents use at runtime.

Depends on Layer 0 (runtime/session/context) for config and event bus.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    # manager — core memory manager
    "MemoryManager": (".manager", "MemoryManager"),
    "extract_key_facts": (".manager", "extract_key_facts"),
    # semantic — semantic memory with categories
    "MEMORY_COLLECTION": (".semantic", "MEMORY_COLLECTION"),
    "MemoryCategory": (".semantic", "MemoryCategory"),
    "MemoryEntry": (".semantic", "MemoryEntry"),
    "SemanticMemoryManager": (".semantic", "SemanticMemoryManager"),
    # markdown_garden — sparse hierarchical long-term memory
    "MarkdownGarden": (".markdown_garden", "MarkdownGarden"),
    "GardenNote": (".markdown_garden", "GardenNote"),
    "MarkdownGardenManager": (".markdown_garden", "MarkdownGardenManager"),
    # garden_tools — LangChain tools for garden read/write/search
    "get_garden_tools": (".garden_tools", "get_garden_tools"),
    # admin_memory — persistent admin memory with compression
    "AdminMemory": (".admin_memory", "AdminMemory"),
    "AdminMemoryConfig": (".admin_memory", "AdminMemoryConfig"),
    "AdminMemoryManager": (".admin_memory", "AdminMemoryManager"),
    # memory_tools — LangChain tools for search/save/forget
    "get_memory_tools": (".memory_tools", "get_memory_tools"),
    # memory_taxonomy — typed memory layers and classification
    "MemoryTaxonomy": (".memory_taxonomy", "MemoryTaxonomy"),
    "MemoryLayerDescriptor": (".memory_taxonomy", "MemoryLayerDescriptor"),
    "normalize_memory_type": (".memory_taxonomy", "normalize_memory_type"),
    "default_layer_for_type": (".memory_taxonomy", "default_layer_for_type"),
    "section_for_memory_type": (".memory_taxonomy", "section_for_memory_type"),
    # memory_scoring — recency/relevance scoring
    "MemoryMetadata": (".memory_scoring", "MemoryMetadata"),
    "encode_memory": (".memory_scoring", "encode_memory"),
    "recency_score": (".memory_scoring", "recency_score"),
    "composite_score": (".memory_scoring", "composite_score"),
    # session_memory_extractor — extract durable notes from sessions
    "SessionMemoryExtractor": (".session_memory_extractor", "SessionMemoryExtractor"),
    "SessionMemoryConfig": (".session_memory_extractor", "SessionMemoryConfig"),
    "SessionMemoryScheduler": (".session_memory_extractor", "SessionMemoryScheduler"),
    # memory_distill — 记忆蒸馏流水线 (Journal → Distill → Archive)
    "MemoryDistillManager": (".memory_distill", "MemoryDistillManager"),
    "DeepDigestManager": (".memory_distill", "DeepDigestManager"),  # 兼容别名
    # facade — 统一记忆检索门面 (canvas-aware)
    "MemoryFacade": (".facade", "MemoryFacade"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
