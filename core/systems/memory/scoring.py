"""Memory scoring & relevance — single source of truth for the engine.

Consolidates what used to live in three places:

* ``memory_scoring`` — recency / composite formulae
* ``embedding_scorer`` — cosine over embeddings + per-fact cache
* ``memory_router`` — temporal decay + soft attention

The ``Scorer`` class wraps an optional embeddings provider and is
queried by :class:`MemoryEngine` for every recall. When no provider is
attached it gracefully falls back to BM25-lite token overlap so the
engine still works in zero-API/zero-vector environments.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
    np = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants & base formulae
# ---------------------------------------------------------------------------

DEFAULT_DECAY_ALPHA = 0.95
DEFAULT_DECAY_FLOOR = 0.05
DEFAULT_TEMPERATURE = 0.5
DEFAULT_RECALL_BONUS = 0.02
DEFAULT_HALF_LIFE_DAYS = 30.0


def temporal_decay_weight(
    last_recall_ts: float,
    *,
    now: float | None = None,
    alpha: float = DEFAULT_DECAY_ALPHA,
    a_min: float = DEFAULT_DECAY_FLOOR,
    recall_count: int = 0,
) -> float:
    """``max(a_min, alpha**age_days)``; never drives a fact to zero.
    Supports Adaptive Forgetting Rate Consolidation (Ebbinghaus-style).
    """
    if last_recall_ts <= 0:
        return 1.0
    now = now if now is not None else time.time()
    age_days = max(0.0, (now - last_recall_ts) / 86400.0)
    
    if recall_count > 0:
        # alpha consolidates and approaches 1.0 as recall_count increases
        c = 0.15
        adaptive_alpha = alpha + (1.0 - alpha) * (1.0 - math.exp(-c * recall_count))
    else:
        adaptive_alpha = alpha
        
    return max(a_min, adaptive_alpha ** age_days)


def recency_score(timestamp_epoch: float, *, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    """Exponential decay → in [0, 1]; recent timestamp ≈ 1.0."""
    if timestamp_epoch <= 0:
        return 0.5
    age_seconds = max(time.time() - timestamp_epoch, 0.0)
    half_seconds = max(half_life_days, 0.001) * 86400.0
    return 0.5 ** (age_seconds / half_seconds)


def composite_score(relevance: float, recency: float, importance: float) -> float:
    """Weighted blend used as the legacy ``MemorySearch`` ranker."""
    relevance = max(0.0, min(1.0, relevance))
    recency = max(0.0, min(1.0, recency))
    importance = max(0.0, min(1.5, importance))
    return 0.55 * relevance + 0.20 * recency + 0.25 * (importance / 1.5)


def softmax(scores: list[float], *, temperature: float = DEFAULT_TEMPERATURE) -> list[float]:
    if not scores:
        return []
    temp = max(1e-3, float(temperature))
    if _HAS_NUMPY:
        arr = np.array(scores, dtype=np.float32) / temp
        arr_max = np.max(arr)
        exps = np.exp(arr - arr_max)
        sums = np.sum(exps)
        if sums == 0.0:
            sums = 1.0
        return (exps / sums).tolist()

    # Pure Python fallback
    shifted = [s / temp for s in scores]
    m = max(shifted)
    exps = [math.exp(s - m) for s in shifted]
    z = sum(exps) or 1.0
    return [e / z for e in exps]


# ---------------------------------------------------------------------------
# BM25-lite tokenizer (used as fallback when no embeddings)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z0-9]+", re.UNICODE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """Mixed-script tokenizer: Latin words + per-character CJK."""
    if not text:
        return []
    out: list[str] = [w.lower() for w in _WORD_RE.findall(text)]
    out.extend(_CJK_RE.findall(text))
    return out


def bm25_lite(query_tokens: list[str], doc_tokens: list[str]) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    doc_set = set(doc_tokens)
    overlap = sum(1.0 for q in query_tokens if q in doc_set)
    denom = math.log(len(doc_tokens) + 1.0) + 1.0
    return overlap / denom


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    if _HAS_NUMPY:
        arr_a = np.array(a, dtype=np.float32)
        arr_b = np.array(b, dtype=np.float32)
        norm_a = np.linalg.norm(arr_a)
        norm_b = np.linalg.norm(arr_b)
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return float(np.dot(arr_a, arr_b) / (norm_a * norm_b))
    
    # Pure Python fallback
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# Embeddings adapter (LangChain Embeddings-compatible)
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingsAdapter:
    """Wrap any LangChain-style ``Embeddings`` object behind two callables.

    Returns ``None`` if the supplied object lacks the required methods,
    which lets the engine fall back to BM25-lite without crashing.
    """

    embed_documents: Callable[[list[str]], list[list[float]]]
    embed_query: Callable[[str], list[float]]

    @classmethod
    def from_object(cls, obj: Any) -> "EmbeddingsAdapter | None":
        ed = getattr(obj, "embed_documents", None)
        eq = getattr(obj, "embed_query", None)
        if not callable(ed) or not callable(eq):
            return None
        return cls(embed_documents=ed, embed_query=eq)

    def safe_query(self, text: str) -> list[float] | None:
        text = (text or "").strip()
        if not text:
            return None
        try:
            vec = list(self.embed_query(text))
        except Exception as exc:
            logger.debug("embed_query failed: %s", exc)
            return None
        return vec or None

    def safe_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            vectors = self.embed_documents(texts)
        except Exception as exc:
            logger.debug("embed_documents failed: %s", exc)
            return []
        return [list(v) for v in vectors]


# ---------------------------------------------------------------------------
# Adaptive importance — feedback signals
# ---------------------------------------------------------------------------

FEEDBACK_DELTAS: dict[str, float] = {
    "positive": 0.15,
    "negative": -0.10,
    "disproved": -0.50,
    "reconsolidated": 0.30,
}


def feedback_delta(signal: str) -> float | None:
    return FEEDBACK_DELTAS.get(signal)


# ---------------------------------------------------------------------------
# Public Scorer — used by MemoryEngine.recall
# ---------------------------------------------------------------------------


@dataclass
class ScorerConfig:
    decay_alpha: float = DEFAULT_DECAY_ALPHA
    decay_floor: float = DEFAULT_DECAY_FLOOR
    temperature: float = DEFAULT_TEMPERATURE
    recall_bonus: float = DEFAULT_RECALL_BONUS


class Scorer:
    """Combines BM25-lite / cosine / decay / adaptive importance.

    The scorer is *stateless* — feed it a query plus iterable of
    candidate records and it returns ``[(record, combined_score)]``.

    The combined score is::

        relevance × effective_importance × decay

    where ``relevance`` is cosine similarity if the candidate has an
    embedding (and an embedding query is available), otherwise BM25-lite.
    """

    def __init__(
        self,
        *,
        embeddings: EmbeddingsAdapter | None = None,
        config: ScorerConfig | None = None,
    ) -> None:
        self.embeddings = embeddings
        self.config = config or ScorerConfig()

    def score(
        self,
        query: str,
        candidates: Iterable[Any],
        *,
        get_content: Callable[[Any], str] = lambda r: r.content,
        get_importance: Callable[[Any], float] = lambda r: r.effective_importance,
        get_last_recall: Callable[[Any], float] = lambda r: r.last_recall_ts,
        get_recall_count: Callable[[Any], int] = lambda r: getattr(r, "recall_count", 0),
        get_embedding: Callable[[Any], list[float] | None] | None = None,
    ) -> list[tuple[Any, float]]:
        """Return ``[(record, combined)]`` sorted desc."""
        query = (query or "").strip()
        q_tokens = tokenize(query) if query else []
        q_vec: list[float] | None = None
        if query and self.embeddings is not None:
            q_vec = self.embeddings.safe_query(query)
        now = time.time()
        cand_list = list(candidates)
        
        # Batch vectorized embedding computation if numpy is available
        relevances: dict[int, float] = {}
        if q_vec is not None and get_embedding is not None and _HAS_NUMPY:
            embs = []
            emb_indices = []
            for idx, rec in enumerate(cand_list):
                v = get_embedding(rec)
                if v and len(v) == len(q_vec):
                    embs.append(v)
                    emb_indices.append(idx)
            if embs:
                matrix = np.array(embs, dtype=np.float32)  # shape (N, D)
                q_arr = np.array(q_vec, dtype=np.float32)  # shape (D,)
                norms = np.linalg.norm(matrix, axis=1)     # shape (N,)
                q_norm = np.linalg.norm(q_arr)
                if q_norm > 0:
                    dots = np.dot(matrix, q_arr)           # shape (N,)
                    denom = norms * q_norm
                    denom[denom == 0.0] = 1.0
                    raw_similarities = dots / denom
                    mapped = np.clip((raw_similarities + 1.0) / 2.0, 0.0, 1.0)
                    for idx, mapped_val in zip(emb_indices, mapped.tolist()):
                        relevances[idx] = mapped_val

        scored: list[tuple[Any, float]] = []
        for idx, rec in enumerate(cand_list):
            content = get_content(rec)
            relevance = relevances.get(idx, 0.0)
            
            # Non-numpy or fallback manual single cosine similarity
            if relevance <= 0.0 and q_vec is not None and get_embedding is not None and not _HAS_NUMPY:
                vec = get_embedding(rec)
                if vec:
                    raw = cosine(q_vec, vec)
                    relevance = max(0.0, (raw + 1.0) / 2.0)
            
            # Fallback to BM25 if embedding similarity not available or zero
            if relevance <= 0.0 and q_tokens:
                relevance = bm25_lite(q_tokens, tokenize(content))
                
            decay = temporal_decay_weight(
                get_last_recall(rec),
                now=now,
                alpha=self.config.decay_alpha,
                a_min=self.config.decay_floor,
                recall_count=get_recall_count(rec),
            )
            combined = relevance * get_importance(rec) * decay
            scored.append((rec, combined))
            
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def soft_distribution(
        self,
        scored: list[tuple[Any, float]],
        *,
        top_k: int,
    ) -> list[tuple[Any, float]]:
        if top_k <= 0:
            return []
        head = scored[:top_k]
        if not head:
            return []
        probs = softmax([s for _, s in head], temperature=self.config.temperature)
        return list(zip([r for r, _ in head], probs))


__all__ = [
    "DEFAULT_DECAY_ALPHA",
    "DEFAULT_DECAY_FLOOR",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_RECALL_BONUS",
    "EmbeddingsAdapter",
    "FEEDBACK_DELTAS",
    "Scorer",
    "ScorerConfig",
    "bm25_lite",
    "composite_score",
    "cosine",
    "feedback_delta",
    "recency_score",
    "softmax",
    "temporal_decay_weight",
    "tokenize",
]
