"""LLM-augmented memory encoding and composite scoring.

Encoding: when storing a memory, use an LLM to extract importance,
categories, and scope.  This metadata is stored alongside the vector
embedding for richer retrieval.

Scoring: at retrieval time, rank results by a weighted combination of
semantic similarity, recency, and importance.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_ENCODE_PROMPT = """Analyze this memory and return a JSON object with:
- "importance": float 0.0-1.0 (how likely this will be useful later)
- "categories": list of 1-3 short category tags
- "scope": one of "project", "user", "general", "technical"

Memory: {text}

Respond with ONLY the JSON object, no other text."""


@dataclass
class MemoryMetadata:
    importance: float = 0.5
    categories: list[str] = field(default_factory=list)
    scope: str = "general"


def encode_memory(text: str, llm: Any) -> MemoryMetadata:
    """Use LLM to extract metadata from a memory text."""
    try:
        prompt = _ENCODE_PROMPT.replace("{text}", text)
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        data = json.loads(content)
        return MemoryMetadata(
            importance=max(0.0, min(1.0, float(data.get("importance", 0.5)))),
            categories=list(data.get("categories", [])),
            scope=str(data.get("scope", "general")),
        )
    except Exception as exc:
        logger.debug("encode_memory LLM call failed, using defaults: %s", exc)
        return MemoryMetadata()


def recency_score(timestamp: float, *, half_life_hours: float = 72.0) -> float:
    """Exponential decay score based on how recent the memory is.

    Returns 1.0 for now, 0.5 at half_life_hours ago, approaching 0 for old.
    """
    age_hours = (time.time() - timestamp) / 3600.0
    if age_hours <= 0:
        return 1.0
    decay = math.log(2) / half_life_hours
    return math.exp(-decay * age_hours)


def composite_score(
    semantic: float,
    recency: float,
    importance: float,
    *,
    w_semantic: float = 0.5,
    w_recency: float = 0.3,
    w_importance: float = 0.2,
) -> float:
    """Weighted combination of semantic similarity, recency, and importance."""
    return w_semantic * semantic + w_recency * recency + w_importance * importance
