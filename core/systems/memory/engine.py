"""MemoryEngine — single unified entry point for PyBot's memory subsystem.

Replaces the previous tangle of 11 collaborating classes
(``UnifiedMemory`` / ``SemanticMemoryManager`` / ``MemorySearch`` /
``MemoryRouter`` / ``MemoryDistillManager`` / ``EpisodicStore`` /
``InsightSink`` / ``SessionMemoryScheduler`` / ``MemoryTaxonomy`` /
``MemoryScoring`` / ``EmbeddingRelevanceScorer``) with a single
SQLite-backed engine that owns:

* **ingest** — write any modality (fact / episode / reflection /
  insight / journal / session_note) into a single table.
* **recall** — scoped, modality-filtered, embedding-aware retrieval
  with adaptive importance and BM25-lite fallback.
* **feedback** — explicit signals that adjust importance over time.
* **gc / reconsolidate** — forgetting curve with restoration on hit.
* **journal / distill / reflect** — async LLM pipelines (delegated
  to :class:`MemoryPipeline`).
* **legacy compat** — all method signatures used by upper layers
  (``append_memory`` / ``search_memories`` / ``router_digest`` / …)
  remain available as thin shims so the 25+ caller sites don't break.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

# ---------------------------------------------------------------------------
# Secret Redaction Guard
# ---------------------------------------------------------------------------

_SECRET_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("OPENAI_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("ANTHROPIC_KEY", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("GENERIC_SECRET", re.compile(r"(?i)\b[a-z0-9_-]*(secret|token|api[_-]?key|password|passwd|pwd)[a-z0-9_-]*\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.\/]{10,}")),
]

def redact_secrets(text: str) -> str:
    """Scan and redact high-risk credentials from memory content before persistence."""
    if not text:
        return text
    redacted = text
    for label, pattern in _SECRET_RULES:
        # We can either replace the match or sub a placeholder
        # For assignment matches, we can preserve the assignment key and redact the value
        if label == "GENERIC_SECRET":
            def sub_generic(match: re.Match[str]) -> str:
                # e.g., secret="abc123xyz789" -> secret="[REDACTED_SECRET]"
                full = match.group(0)
                # Find the splitting separator (= or :)
                sep_match = re.search(r'[:=]', full)
                if sep_match:
                    sep_idx = sep_match.start()
                    key_part = full[:sep_idx + 1]
                    # Retain quotes if present
                    quote_part = '"' if '"' in full[sep_idx:] else ("'" if "'" in full[sep_idx:] else "")
                    return f"{key_part} {quote_part}[REDACTED_SECRET]{quote_part}"
                return f"[REDACTED_{label}]"
            redacted = pattern.sub(sub_generic, redacted)
        else:
            redacted = pattern.sub(f"[REDACTED_{label}]", redacted)
    return redacted

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

if TYPE_CHECKING:
    from .markdown_garden import MarkdownGardenManager
    from .pipeline import MemoryPipeline

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Scope(str, Enum):
    SESSION = "session"
    AGENT = "agent"
    ADMIN = "admin"
    GLOBAL = "global"


class Modality(str, Enum):
    FACT = "fact"
    EPISODE = "episode"
    REFLECTION = "reflection"
    INSIGHT = "insight"
    JOURNAL = "journal"
    SESSION_NOTE = "session_note"


class Status(str, Enum):
    ACTIVE = "active"
    FORGOTTEN = "forgotten"
    ARCHIVED = "archived"


class Signal(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    DISPROVED = "disproved"
    RECONSOLIDATED = "reconsolidated"


# ---------------------------------------------------------------------------
# Public record type — implements both legacy MemoryEntry and MemoryFact APIs
# ---------------------------------------------------------------------------


@dataclass
class MemoryRecord:
    """A single memory record returned to callers.

    Wraps the raw :class:`StoredRecord` from the SQLite layer with
    convenience properties that mirror the legacy ``MemoryEntry`` and
    ``MemoryFact`` shapes so upper layers don't need to be updated.
    """

    id: str
    scope: str
    modality: str
    content: str
    metadata: dict[str, Any]
    importance: float
    importance_delta: float
    recall_count: int
    last_recall_ts: float
    last_feedback_ts: float
    first_seen_ts: float
    status: str
    ts_event: float
    relevance: float = 0.0

    # ---- canonical helpers --------------------------------------------

    @property
    def effective_importance(self) -> float:
        return max(0.0, min(1.5, self.importance + self.importance_delta))

    @classmethod
    def from_stored(cls, raw: StoredRecord, *, relevance: float = 0.0) -> "MemoryRecord":
        return cls(
            id=raw.id,
            scope=raw.scope,
            modality=raw.modality,
            content=raw.content,
            metadata=dict(raw.metadata),
            importance=raw.importance,
            importance_delta=raw.importance_delta,
            recall_count=raw.recall_count,
            last_recall_ts=raw.last_recall_ts,
            last_feedback_ts=raw.last_feedback_ts,
            first_seen_ts=raw.first_seen_ts,
            status=raw.status,
            ts_event=raw.ts_event,
            relevance=relevance,
        )

    # ---- legacy MemoryEntry compat ------------------------------------

    @property
    def section(self) -> str:
        return str(self.metadata.get("section", ""))

    @property
    def category(self) -> str:
        return str(self.metadata.get("category", "other"))

    @property
    def memory_type(self) -> str:
        return str(self.metadata.get("memory_type", "session_note"))

    @property
    def source(self) -> str:
        return str(self.metadata.get("source", "agent"))

    @property
    def timestamp(self) -> float:
        return self.ts_event or self.first_seen_ts

    @property
    def doc_id(self) -> str:
        return self.id

    # ---- legacy MemoryFact compat -------------------------------------

    @property
    def fact_id(self) -> str:
        return self.id

    @property
    def soft_tags(self) -> dict[str, float]:
        tags = self.metadata.get("soft_tags")
        if isinstance(tags, dict) and tags:
            return {str(k): float(v) for k, v in tags.items()}
        return {"其他": 1.0}

    @property
    def primary_tag(self) -> str:
        return max(self.soft_tags.items(), key=lambda kv: kv[1])[0]

    def to_line(self) -> str:
        """Render as a MEMORY.md fact line (compatible with the old format)."""
        stars = "★★★" if self.importance >= 0.9 else ("★★" if self.importance >= 0.5 else "★")
        soft = self.soft_tags
        if len(soft) == 1:
            tag = next(iter(soft))
            return f"- [{tag}|{stars}] {self.content}"
        joined = ",".join(
            f"{t}:{w:.2f}" for t, w in sorted(soft.items(), key=lambda kv: -kv[1])
        )
        return f"- [{joined}|{stars}] {self.content}"

    # ---- episode helpers ----------------------------------------------

    @property
    def actor(self) -> str:
        return str(self.metadata.get("actor", ""))

    @property
    def scene(self) -> str:
        return str(self.metadata.get("scene", ""))

    @property
    def action(self) -> str:
        return str(self.metadata.get("action", ""))

    @property
    def outcome(self) -> str:
        return str(self.metadata.get("outcome", ""))

    @property
    def ts(self) -> float:
        return self.ts_event

    @property
    def ts_label(self) -> str:
        return str(self.metadata.get("ts_label", ""))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


_CANVAS_CONFIG: dict[str, dict[str, Any]] = {
    "focused": {
        "digest_top_k": 8,
        "semantic_top_k": 0,
        "garden_search": False,
        "max_chars": 1200,
        "label": "长期记忆（精简）",
    },
    "balanced": {
        "digest_top_k": 16,
        "semantic_top_k": 3,
        "garden_search": True,
        "max_chars": 4000,
        "label": "长期记忆（均衡）",
    },
    "deep": {
        "digest_top_k": 32,
        "semantic_top_k": 8,
        "garden_search": True,
        "max_chars": 8000,
        "label": "长期记忆（深度）",
    },
}


_PROTECTED_TAGS_DEFAULT = ("反思",)


@dataclass
class EngineConfig:
    decay_alpha: float = 0.95
    decay_floor: float = 0.05
    temperature: float = 0.5
    recall_bonus: float = 0.02
    forget_age_days: float = 30.0
    forget_importance_floor: float = 0.4
    reconsolidate_threshold: float = 0.4
    enable_reconsolidation: bool = True
    enable_forgetting: bool = True
    enable_reflection: bool = True
    enable_episodic: bool = True
    enable_graph_associations: bool = True
    protected_tags: tuple[str, ...] = _PROTECTED_TAGS_DEFAULT


class MemoryEngine:
    """Single entry point for all memory operations."""

    def __init__(
        self,
        workspace_dir: str | Path,
        *,
        embeddings: Any | None = None,
        llm: Any = None,
        journal_caller: Callable[[str, str], str] | None = None,
        distill_caller: Callable[[str, str], str] | None = None,
        reflect_caller: Callable[[str, str], str] | None = None,
        garden: "MarkdownGardenManager | None" = None,
        config: EngineConfig | None = None,
    ) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.config = config or EngineConfig()
        self._store = SqliteMemoryStore(self.workspace_dir)
        self._embeddings = EmbeddingsAdapter.from_object(embeddings)
        self._scorer = Scorer(
            embeddings=self._embeddings,
            config=ScorerConfig(
                decay_alpha=self.config.decay_alpha,
                decay_floor=self.config.decay_floor,
                temperature=self.config.temperature,
                recall_bonus=self.config.recall_bonus,
            ),
        )
        self._llm = llm
        self._garden = garden
        self._session_scheduler: Any | None = None
        self._memory_path = self.workspace_dir / "MEMORY.md"
        self._forgotten_path = self.workspace_dir / "FORGOTTEN.md"
        # Pipeline (journal/distill/reflect) — lazy-built so the engine
        # is usable without LLM callers.
        from .pipeline import MemoryPipeline

        self._pipeline = MemoryPipeline(
            engine=self,
            llm_caller=llm if callable(llm) else None,
            journal_caller=journal_caller,
            distill_caller=distill_caller,
            reflect_caller=reflect_caller,
        )

    # ==================================================================
    # New unified API — ingest
    # ==================================================================

    def ingest(
        self,
        modality: Modality | str,
        content: str,
        *,
        scope: Scope | str = Scope.AGENT,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
        ts: float | None = None,
    ) -> str:
        """Insert (or upsert) a single memory record."""
        # Run security check: redact credentials to prevent storage leaks
        content_cleaned = redact_secrets(content.strip())
        
        modality_v = modality.value if isinstance(modality, Modality) else str(modality)
        scope_v = scope.value if isinstance(scope, Scope) else str(scope)
        record_id, inserted = self._store.upsert(
            scope=scope_v,
            modality=modality_v,
            content=content_cleaned,
            metadata=metadata,
            importance=importance,
            ts_event=ts,
        )
        # Compute & cache embedding lazily so subsequent recalls are cheap.
        cached_emb = None
        if inserted and self._embeddings is not None:
            try:
                vecs = self._embeddings.safe_documents([content_cleaned])
                if vecs:
                    cached_emb = vecs[0]
                    self._store.set_embedding(record_id, cached_emb)
            except Exception as exc:
                logger.debug("embedding for ingest failed: %s", exc)

        # Trigger Counterfactual Correction (Belief Revision & Truth Maintenance)
        if inserted:
            self._resolve_counterfactuals(
                record_id=record_id,
                content_cleaned=content_cleaned,
                scope_v=scope_v,
                modality_v=modality_v,
                embedding=cached_emb,
            )

        return record_id

    def auto_capture(self, conversation: list[dict[str, str]]) -> list[str]:
        """Persist memories from a recent conversation turn (middleware hook).

        When an LLM journal caller is configured, schedules asynchronous
        journal extraction via :class:`MemoryPipeline`. Otherwise falls back
        to heuristic ingestion of substantive assistant replies as session notes.
        """
        if len(conversation) < 2:
            return []
        if self._pipeline._journal_caller:
            self._pipeline.journal_async(conversation)
            return ["journal_scheduled"]
        captured: list[str] = []
        for msg in conversation:
            role = str(msg.get("role", "")).lower()
            content = str(msg.get("content", "")).strip()
            if role not in {"assistant", "ai"}:
                continue
            if len(content) < 20:
                continue
            if content.startswith(("❌", "Error", "error:")):
                continue
            record_id = self.ingest(
                Modality.SESSION_NOTE,
                content,
                importance=0.35,
            )
            captured.append(record_id)
        return captured

    def auto_recall(self, query: str, *, top_k: int = 5) -> str:
        """Return formatted memory context for ``query`` (middleware hook)."""
        _ = top_k  # canvas profile governs effective top-k in get_context_prompt
        return self.get_context_prompt(canvas="balanced", query=query) or ""

    def _resolve_counterfactuals(
        self,
        record_id: str,
        content_cleaned: str,
        scope_v: str,
        modality_v: str,
        embedding: list[float] | None = None,
    ) -> None:
        """Scan active memories for semantic contradictions and archive them."""
        # We only resolve counterfactuals for facts, reflections, or insights
        if modality_v not in (Modality.FACT, "fact", Modality.REFLECTION, "reflection", Modality.INSIGHT, "insight"):
            return

        try:
            active_records = self._store.list(scope=scope_v, modality=modality_v, status="active", limit=100)
            for old_rec in active_records:
                if old_rec.id == record_id:
                    continue
                if old_rec.content == content_cleaned:
                    continue

                similarity = 0.0
                is_jaccard = False
                if embedding is not None:
                    old_vec = self._store.get_embedding(old_rec.id)
                    if old_vec:
                        similarity = cosine(embedding, old_vec)
                
                # Fallback to Jaccard word-overlap check if similarity is 0.0 or embedding not found
                if similarity == 0.0:
                    is_jaccard = True
                    words1 = set(tokenize(content_cleaned.lower()))
                    words2 = set(tokenize(old_rec.content.lower()))
                    intersection = words1.intersection(words2)
                    union = words1.union(words2)
                    similarity = len(intersection) / len(union) if union else 0.0

                # Define thresholds (embedding is more dense, jaccard is more sparse)
                threshold = 0.60 if is_jaccard else 0.70

                # If similarity is above our threshold, update old record status & link them
                if similarity >= threshold:
                    self._store.update_status(old_rec.id, "forgotten")
                    # Build bidirectional graph linkages to record history of updates
                    self._store.add_link(old_rec.id, record_id, "contradicted_by")
                    self._store.add_link(record_id, old_rec.id, "supersedes")
                    logger.info(
                        "[MemoryEngine] Counterfactual Correction: Old fact '%s' (ID %s) "
                        "superseded by new fact '%s' (ID %s) with similarity %.2f",
                        old_rec.content, old_rec.id, content_cleaned, record_id, similarity
                    )
        except Exception as exc:
            logger.debug("Failed to resolve counterfactuals for record %s: %s", record_id, exc)

    def ingest_episode(
        self,
        *,
        ts_label: str,
        actor: str,
        scene: str,
        action: str,
        outcome: str,
        refs: list[str] | None = None,
        tags: list[str] | None = None,
        scope: Scope | str = Scope.AGENT,
        ts: float | None = None,
    ) -> str:
        """Convenience helper for the EPISODE modality."""
        content = f"{actor}@{scene}: {action} → {outcome}"
        if ts is None:
            ts = _parse_ts_label(ts_label)
        return self.ingest(
            Modality.EPISODE,
            content,
            scope=scope,
            metadata={
                "ts_label": ts_label,
                "actor": actor,
                "scene": scene,
                "action": action,
                "outcome": outcome,
                "refs": refs or [],
                "tags": tags or [],
            },
            importance=0.5,
            ts=ts,
        )

    # ==================================================================
    # New unified API — recall
    # ==================================================================

    def recall(
        self,
        query: str,
        *,
        scope: Scope | str | None = None,
        modality: Modality | str | None = None,
        top_k: int = 5,
        section: str | None = None,
        category: str | None = None,
        memory_type: str | None = None,
        record_recall: bool = True,
        include_forgotten: bool = True,
    ) -> list[MemoryRecord]:
        """Unified recall: FTS + (optional) embedding + decay + importance.

        ``section / category / memory_type`` are kept as legacy filters
        applied to the fact metadata.
        """
        if top_k <= 0:
            return []
        # Reconsolidation pass — try to wake matching forgotten facts first.
        if include_forgotten and self.config.enable_reconsolidation and (query or "").strip():
            try:
                self.reconsolidate(query, top_k=1)
            except Exception as exc:
                logger.debug("reconsolidate skipped: %s", exc)

        if isinstance(scope, (list, tuple, set, frozenset)):
            scope_v = [s.value if isinstance(s, Scope) else str(s) for s in scope]
        else:
            scope_v = scope.value if isinstance(scope, Scope) else (str(scope) if scope else None)
            
        modality_v = modality.value if isinstance(modality, Modality) else (modality if modality else None)

        candidates = self._gather_candidates(
            query, scope_v=scope_v, modality_v=modality_v, fetch_k=max(top_k * 4, 30)
        )
        if section or category or memory_type:
            candidates = [
                rec for rec in candidates
                if (not section or rec.metadata.get("section") == section)
                and (not category or rec.metadata.get("category") == category)
                and (not memory_type or rec.metadata.get("memory_type") == memory_type)
            ]
        if not candidates:
            return []
        scored = self._scorer.score(
            query,
            candidates,
            get_content=lambda r: r.content,
            get_importance=lambda r: r.effective_importance,
            get_last_recall=lambda r: r.last_recall_ts,
            get_recall_count=lambda r: r.recall_count,
            get_embedding=self._store.get_embedding,
        )
        chosen_pairs = scored[:top_k]
        records: list[MemoryRecord] = []
        now = time.time()
        for stored, score in chosen_pairs:
            if score <= 0 and (query or "").strip():
                continue
            rec = MemoryRecord.from_stored(stored, relevance=float(score))
            records.append(rec)
            if record_recall:
                self._store.touch_recall(
                    stored.id, ts=now, recall_bonus=self.config.recall_bonus
                )
                rec.recall_count += 1
                rec.last_recall_ts = now

        # Graph-Lite Association Expansion (关联图谱语义联想)
        if records and self.config.enable_graph_associations:
            associated_seen = {r.id for r in records}
            expanded_records = []
            for rec in records:
                expanded_records.append(rec)
                try:
                    for assoc_rec, path in self.get_associated_memories(rec.id, max_depth=1):
                        if assoc_rec.id not in associated_seen:
                            associated_seen.add(assoc_rec.id)
                            assoc_rec.relevance = rec.relevance * 0.8  # Decayed relevance for 1-hop association
                            assoc_rec.metadata["associated_via"] = path
                            assoc_rec.metadata["associated_parent"] = rec.content[:30]
                            expanded_records.append(assoc_rec)
                except Exception as exc:
                    logger.debug("Failed graph association recall: %s", exc)
            records = expanded_records[:top_k + 2]  # Slight context buffer expansion for associated records

        return records

    def _gather_candidates(
        self,
        query: str,
        *,
        scope_v: str | None,
        modality_v: str | None,
        fetch_k: int,
    ) -> list[StoredRecord]:
        """Union of FTS hits + tag-protected fallback list."""
        seen: dict[str, StoredRecord] = {}
        if (query or "").strip():
            for rec in self._store.search_fts(
                query, scope=scope_v, modality=modality_v, limit=fetch_k
            ):
                seen[rec.id] = rec
        # When we still have room (or query is empty) backfill with the
        # most-important recent records so callers always get a response.
        if len(seen) < fetch_k:
            for rec in self._store.list(
                scope=scope_v,
                modality=modality_v,
                order_by="(importance + importance_delta) DESC, first_seen_ts DESC",
                limit=fetch_k,
            ):
                if rec.id not in seen:
                    seen[rec.id] = rec
                if len(seen) >= fetch_k:
                    break
        return list(seen.values())

    def recall_recent_episodes(self, *, top_k: int = 5) -> list[MemoryRecord]:
        rows = self._store.list(
            modality=Modality.EPISODE.value,
            order_by="ts_event DESC",
            limit=top_k,
        )
        return [MemoryRecord.from_stored(r) for r in rows]

    def recall_episodes(
        self, query: str | None = None, *, top_k: int = 5
    ) -> list[MemoryRecord]:
        """Search episodes by query, or fall back to most-recent."""
        if query and query.strip():
            return self.recall(
                query, modality=Modality.EPISODE, top_k=top_k, record_recall=False
            )
        return self.recall_recent_episodes(top_k=top_k)

    def soft_distribution(
        self, query: str, *, top_k: int
    ) -> list[tuple[MemoryRecord, float]]:
        """Return ``[(record, prob)]`` after softmax — for attention-style display."""
        if top_k <= 0:
            return []
        records = self.recall(query, top_k=top_k, record_recall=False)
        if not records:
            return []
        probs = softmax(
            [r.relevance * r.effective_importance for r in records],
            temperature=self.config.temperature,
        )
        return list(zip(records, probs))

    # ==================================================================
    # Graph-Lite Operations (关联图谱操作)
    # ==================================================================

    def add_link(self, source_id: str, target_id: str, relation: str) -> None:
        """Create or update a logic relationship link between two memories."""
        self._store.add_link(source_id, target_id, relation)

    def remove_link(self, source_id: str, target_id: str) -> None:
        """Delete a relationship link between two memories."""
        self._store.remove_link(source_id, target_id)

    def get_associated_memories(self, record_id: str, max_depth: int = 1) -> list[tuple[MemoryRecord, str]]:
        """Retrieve bidirectionally linked memory records up to max_depth."""
        raw_list = self._store.get_associated_records(record_id, max_depth=max_depth)
        return [(MemoryRecord.from_stored(raw), path) for raw, path in raw_list]

    # ==================================================================
    # New unified API — feedback / forgetting / reconsolidation
    # ==================================================================

    def feedback(self, record_id: str, signal: Signal | str) -> bool:
        sig = signal.value if isinstance(signal, Signal) else str(signal)
        delta = feedback_delta(sig)
        if delta is None:
            return False
        return self._store.apply_feedback(record_id, delta=delta)

    def feedback_by_content(
        self,
        content: str,
        signal: Signal | str,
        *,
        modality: Modality | str = Modality.FACT,
        scope: Scope | str = Scope.AGENT,
    ) -> bool:
        modality_v = modality.value if isinstance(modality, Modality) else str(modality)
        scope_v = scope.value if isinstance(scope, Scope) else str(scope)
        existing = self._store.find_by_content(
            content.strip(), scope=scope_v, modality=modality_v
        )
        if existing is None:
            return False
        return self.feedback(existing.id, signal)

    def update_content(self, record_id: str, content: str) -> bool:
        """Directly update the content of a memory record (with secret redaction and embedding regeneration)."""
        import sqlite3
        content_cleaned = redact_secrets(content.strip())
        if not content_cleaned:
            return False

        emb = None
        if self._embeddings is not None:
            try:
                emb = self._embeddings.embed_query(content_cleaned)
            except Exception:
                pass

        with self._store._lock:
            blob = sqlite3.Binary(self._store._serialize_vector(emb)) if emb else None
            cur = self._store._conn.execute(
                "UPDATE memories SET content=?, embedding=COALESCE(?, embedding) WHERE id=?",
                (content_cleaned, blob, record_id)
            )
            ok = cur.rowcount > 0

        if ok:
            self.sync_memory_md()
        return ok

    def delete_record(self, record_id: str) -> bool:
        """Directly delete a memory record by ID."""
        ok = self._store.delete(record_id)
        if ok:
            self.sync_memory_md()
        return ok

    def forget(
        self, query: str, *, top_k: int = 3, threshold: float = 0.6
    ) -> list[str]:
        """GDPR-friendly hard delete by semantic match."""
        records = self.recall(query, top_k=top_k, record_recall=False)
        deleted: list[str] = []
        for rec in records:
            if rec.relevance >= threshold:
                if self._store.delete(rec.id):
                    deleted.append(rec.content[:200])
        return deleted

    def gc(
        self,
        *,
        age_days: float | None = None,
        importance_floor: float | None = None,
        now: float | None = None,
    ) -> int:
        """Soft-archive stale low-importance facts (forgetting curve).

        Records become ``status='forgotten'`` instead of being deleted
        so :meth:`reconsolidate` can wake them up later.
        """
        if not self.config.enable_forgetting:
            return 0
        threshold_secs = (
            age_days if age_days is not None else self.config.forget_age_days
        ) * 86400.0
        floor = (
            importance_floor
            if importance_floor is not None
            else self.config.forget_importance_floor
        )
        clock = now if now is not None else time.time()
        candidates = self._store.list(
            modality=Modality.FACT.value,
            status=Status.ACTIVE.value,
            order_by="first_seen_ts ASC",
            limit=10000,
        )
        protected = set(self.config.protected_tags)
        demoted = 0
        for rec in candidates:
            soft_tags = rec.metadata.get("soft_tags") or {}
            primary = (
                max(soft_tags.items(), key=lambda kv: kv[1])[0]
                if isinstance(soft_tags, dict) and soft_tags
                else "其他"
            )
            if primary in protected:
                continue
            if rec.recall_count > 0:
                continue
            if rec.effective_importance >= floor:
                continue
            seen = rec.first_seen_ts or rec.last_recall_ts or 0
            if seen <= 0 or (clock - seen) < threshold_secs:
                continue
            if self._store.update_status(rec.id, Status.FORGOTTEN.value):
                demoted += 1
        if demoted:
            logger.info("[MemoryEngine] gc demoted %d fact(s) to forgotten", demoted)
        return demoted

    def reconsolidate(
        self, query: str, *, top_k: int = 1
    ) -> list[MemoryRecord]:
        """Promote forgotten facts that strongly match ``query`` back to active."""
        if not self.config.enable_reconsolidation or top_k <= 0:
            return []
        # Search FTS over forgotten-status rows
        forgotten = self._store.search_fts(
            query, modality=Modality.FACT.value, status=Status.FORGOTTEN.value, limit=top_k * 4
        )
        if not forgotten:
            return []
        scored = self._scorer.score(
            query,
            forgotten,
            get_content=lambda r: r.content,
            get_importance=lambda r: r.effective_importance,
            get_last_recall=lambda r: r.last_recall_ts,
            get_embedding=self._store.get_embedding,
        )
        promoted: list[MemoryRecord] = []
        for stored, score in scored[:top_k]:
            if score < self.config.reconsolidate_threshold:
                continue
            if self._store.update_status(stored.id, Status.ACTIVE.value):
                self._store.apply_feedback(
                    stored.id, delta=feedback_delta("reconsolidated") or 0.3
                )
                promoted.append(MemoryRecord.from_stored(stored, relevance=float(score)))
        if promoted:
            logger.info("[MemoryEngine] reconsolidated %d fact(s)", len(promoted))
        return promoted

    # Legacy aliases for the router gc / reconsolidate methods
    def gc_forgotten(
        self,
        *,
        now: float | None = None,
        age_days: float | None = None,
        importance_floor: float | None = None,
    ) -> list[MemoryRecord]:
        before = set(
            r.id for r in self._store.list(status=Status.FORGOTTEN.value, limit=10000)
        )
        self.gc(age_days=age_days, importance_floor=importance_floor, now=now)
        after = self._store.list(status=Status.FORGOTTEN.value, limit=10000)
        new_demoted = [MemoryRecord.from_stored(r) for r in after if r.id not in before]
        return new_demoted

    # ==================================================================
    # Pipeline triggers — delegate to MemoryPipeline
    # ==================================================================

    def journal(self, messages: list[dict[str, str]], *, date_str: str | None = None) -> None:
        self._pipeline.journal_async(messages, date_str=date_str)

    # Legacy alias used by LCMemoryMiddleware / routers
    def schedule_journal(
        self,
        messages: list[dict[str, str]],
        *,
        date_str: str | None = None,
        include_session_notes: bool = True,
    ) -> None:
        if include_session_notes:
            notes = self.session_notes()
            if notes:
                messages = [{"role": "system", "content": f"[会话即时摘要]\n{notes}"}] + list(messages)
        self.journal(messages, date_str=date_str)

    def distill(self, *, force: bool = False) -> None:
        self._pipeline.distill_async(force=force)

    def distill_now(self, *, force: bool = False) -> None:
        self.distill(force=force)

    def reflect_now(self) -> list[str]:
        return self._pipeline.reflect_sync()

    def session_tick(
        self,
        messages: list[Any],
        *,
        tool_call_delta: int = 0,
        current_token_count: int = 0,
    ) -> bool:
        """Delegate session-note scheduling to a registered SessionTrigger."""
        if self._session_scheduler is None:
            return False
        try:
            return bool(
                self._session_scheduler.tick(
                    messages,
                    tool_call_delta=tool_call_delta,
                    current_token_count=current_token_count,
                )
            )
        except Exception as exc:
            logger.debug("session_tick failed: %s", exc)
            return False

    def attach_session_scheduler(self, scheduler: Any | None) -> None:
        """Optional: plug an external SessionTrigger / scheduler in."""
        self._session_scheduler = scheduler

    @property
    def session_scheduler(self) -> Any | None:
        return self._session_scheduler

    def session_notes(self) -> str:
        if self._session_scheduler is None:
            return ""
        try:
            getter = getattr(self._session_scheduler, "get_notes", None)
            if callable(getter):
                value = getter()
                return value or ""
        except Exception as exc:
            logger.debug("session_notes read failed: %s", exc)
        return ""

    def get_today_journal(self) -> str:
        """Legacy compat: return today's journal content joined as one blob."""
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        rows = self._store.list(modality=Modality.JOURNAL.value, limit=200)
        today_lines = [
            r.content for r in rows
            if r.metadata.get("date_str") == today
        ]
        return "\n\n".join(today_lines)

    # ==================================================================
    # Markdown / prompt export
    # ==================================================================

    def export_memory_md(self) -> str:
        """Render the active fact set into a MEMORY.md-style markdown blob.

        Includes the protected reflection section at the top so the
        next distill cycle still sees previous reflections.
        """
        from datetime import datetime

        reflections = self._store.list(
            modality=Modality.REFLECTION.value, status=Status.ACTIVE.value, limit=20
        )
        facts = self._store.list(
            modality=Modality.FACT.value, status=Status.ACTIVE.value, limit=200
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        out: list[str] = ["# MEMORY — 长期记忆", "", f"> 最后蒸馏：{now}", ""]
        if reflections:
            out.append("## 反思（自动生成，请勿手改）")
            for r in reflections:
                out.append(MemoryRecord.from_stored(r).to_line())
            out.append("")
        out.append("[MEMORY]")
        for f in facts:
            out.append(MemoryRecord.from_stored(f).to_line())
        return "\n".join(out) + "\n"

    def sync_memory_md(self) -> None:
        """Persist :meth:`export_memory_md` to disk for human inspection."""
        try:
            self._memory_path.parent.mkdir(parents=True, exist_ok=True)
            self._memory_path.write_text(self.export_memory_md(), encoding="utf-8")
        except Exception as exc:
            logger.debug("sync_memory_md failed: %s", exc)

    def digest(self, *, max_lines: int = 60, scope: Any | None = None) -> str:
        """Return a flat list of recent fact lines (no header / blockquote)."""
        facts = self._store.list(
            scope=scope,
            modality=Modality.FACT.value,
            status=Status.ACTIVE.value,
            order_by="(importance + importance_delta) DESC, first_seen_ts DESC",
            limit=max_lines,
        )
        if not facts:
            return ""
        return "\n".join(MemoryRecord.from_stored(f).to_line() for f in facts)

    def get_context_prompt(
        self, canvas: str | None = None, query: str | None = None, scope: Any | None = None
    ) -> str:
        cfg = _CANVAS_CONFIG.get(canvas or "balanced", _CANVAS_CONFIG["balanced"])
        sections: list[str] = []
        seen: set[str] = set()
        
        # Smart Scope Resolution: if a specific scope is requested (excluding GLOBAL),
        # we pull memories from BOTH that specific scope AND Scope.GLOBAL (as fallback/shared).
        resolved_scope: Any | None = None
        if scope is not None:
            if isinstance(scope, (list, tuple, set, frozenset)):
                resolved_scope = list(scope)
                if "global" not in resolved_scope and Scope.GLOBAL not in resolved_scope:
                    resolved_scope.append(Scope.GLOBAL.value)
            elif str(scope) != "global" and scope != Scope.GLOBAL:
                resolved_scope = [scope, Scope.GLOBAL.value]
            else:
                resolved_scope = scope
                
        digest_top_k = int(cfg.get("digest_top_k", 0) or 0)
        if query and digest_top_k > 0:
            records = self.recall(
                query,
                top_k=digest_top_k,
                modality=Modality.FACT,
                scope=resolved_scope,
                record_recall=False,
            )
            digest_text = "\n".join(rec.to_line() for rec in records) if records else ""
            digest_label = f"MEMORY.md（按当前问题软选择 top-{digest_top_k}）"
        else:
            digest_text = self.digest(scope=resolved_scope)
            digest_label = "MEMORY.md（蒸馏长期记忆）"
        if digest_text:
            sections.append(f"### {digest_label}\n{digest_text}")
            for line in digest_text.splitlines():
                if line.strip():
                    seen.add(line.strip()[:60])
        if cfg["semantic_top_k"] > 0 and query:
            try:
                lines: list[str] = []
                for rec in self.recall(
                    query, top_k=cfg["semantic_top_k"], scope=resolved_scope, record_recall=False
                ):
                    snippet = rec.content.strip()[:60]
                    if snippet in seen:
                        continue
                    seen.add(snippet)
                    cat = f"[{rec.category}] " if rec.category != "other" else ""
                    
                    # Freshness / adaptive weight annotation
                    meta_parts = [f"重要性: {rec.effective_importance:.2f}"]
                    if rec.recall_count > 0:
                        meta_parts.append(f"召回: {rec.recall_count}次")
                    meta_suffix = f" [{', '.join(meta_parts)}]"
                    
                    lines.append(f"- {cat}{rec.content}{meta_suffix}")
                if lines:
                    sections.append("### 相关记忆（语义检索）\n" + "\n".join(lines))
            except Exception as exc:
                logger.debug("semantic recall failed: %s", exc)
        if cfg["garden_search"] and query:
            try:
                hits = self.read_garden(query)
                if hits:
                    lines = [f"- [{h.get('path', '')}] {h.get('snippet', '')}" for h in hits]
                    sections.append("### 知识园林（Garden）\n" + "\n".join(lines))
            except Exception as exc:
                logger.debug("garden lookup failed: %s", exc)
        if not sections:
            return ""
        raw = f"\n\n--- {cfg['label']} ---\n" + "\n\n".join(sections)
        max_chars = int(cfg["max_chars"])
        return raw if len(raw) <= max_chars else raw[:max_chars] + "\n…（已截断）"

    def query(self, text: str, *, k: int = 5, canvas: str | None = None) -> list[dict[str, Any]]:
        cfg = _CANVAS_CONFIG.get(canvas or "balanced", _CANVAS_CONFIG["balanced"])
        results: list[dict[str, Any]] = []
        if cfg["semantic_top_k"] > 0:
            try:
                for rec in self.recall(text, top_k=min(k, cfg["semantic_top_k"])):
                    results.append({
                        "source": "semantic",
                        "content": rec.content,
                        "score": rec.relevance,
                    })
            except Exception as exc:
                logger.debug("query.semantic failed: %s", exc)
        if cfg["garden_search"]:
            try:
                for h in self.read_garden(text, max_hits=k):
                    results.append({
                        "source": "garden",
                        "path": h.get("path", ""),
                        "content": h.get("snippet", ""),
                        "score": 0.5,
                    })
            except Exception as exc:
                logger.debug("query.garden failed: %s", exc)
        results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        return results[:k]

    def read_garden(self, query: str, *, max_hits: int = 4) -> list[dict[str, str]]:
        if self._garden is None:
            return []
        try:
            return self._garden.search_notes(query)[:max_hits]
        except Exception as exc:
            logger.debug("garden search failed: %s", exc)
            return []

    # ==================================================================
    # Legacy compat — SemanticMemoryManager surface (deleted)
    # ==================================================================

    def get_memory_stats(self) -> dict[str, Any]:
        store_stats = self._store.stats()
        out: dict[str, Any] = {**store_stats}
        if self._garden is not None:
            try:
                out["garden_notes"] = len(list(self._garden.garden_root.rglob("*.md")))
            except Exception:
                out["garden_notes"] = 0
        return out

    def load(self) -> str:
        """Legacy: return MEMORY.md-style text (rebuild from store)."""
        return self.export_memory_md()

    # ==================================================================
    # Tools / introspection
    # ==================================================================

    def as_tools(
        self,
        *,
        include_memory: bool = True,
        include_garden: bool = True,
    ) -> list[Any]:
        from .tools import build_memory_tools, build_garden_tools

        tools: list[Any] = []
        if include_memory:
            tools.extend(build_memory_tools(self))
        if include_garden and self._garden is not None:
            tools.extend(build_garden_tools(self._garden))
        return tools

    def stats(self) -> dict[str, Any]:
        store = self._store.stats()
        active_facts = self._store.count(modality=Modality.FACT.value)
        recall_total = sum(
            r.recall_count for r in self._store.list(modality=Modality.FACT.value, limit=10000)
        )
        return {
            **store,
            "facts_active": active_facts,
            "recall_total": recall_total,
        }

    # ==================================================================
    # Properties — UnifiedMemory legacy attributes
    # ==================================================================

    @property
    def garden(self) -> "MarkdownGardenManager | None":
        return self._garden

    # ==================================================================
    # Internal helpers used by pipeline
    # ==================================================================

    @property
    def store(self) -> SqliteMemoryStore:
        return self._store

    @property
    def memory_path(self) -> Path:
        return self._memory_path

    @property
    def pipeline(self) -> "MemoryPipeline":
        return self._pipeline

    # ==================================================================
    # Lifecycle
    # ==================================================================

    def close(self) -> None:
        """Release the SQLite connection. Idempotent."""
        try:
            self._store.close()
        except Exception:
            pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_ts_label(label: str) -> float:
    label = (label or "").strip()
    if not label:
        return time.time()
    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
    from datetime import datetime

    for fmt in fmts:
        try:
            return datetime.strptime(label, fmt).timestamp()
        except ValueError:
            continue
    return time.time()


def build_memory_engine(
    workspace_dir: str | Path,
    *,
    vector_store: Any | None = None,  # back-compat (ignored if embeddings provided)
    llm: Any = None,
    embeddings: Any | None = None,
    enable_garden: bool = True,
    enable_episodic: bool = True,
    enable_reflection: bool = True,
    enable_forgetting: bool = True,
    distill_llm_caller: Callable[[str, str], str] | None = None,
    journal_llm_caller: Callable[[str, str], str] | None = None,
    reflect_llm_caller: Callable[[str, str], str] | None = None,
    insight_sink: Any | None = None,  # ignored — insights now live in the store
    config: EngineConfig | None = None,
) -> MemoryEngine:
    """Compatibility factory mirroring the old ``build_unified_memory`` signature."""
    cfg = config or EngineConfig(
        enable_episodic=enable_episodic,
        enable_reflection=enable_reflection,
        enable_forgetting=enable_forgetting,
    )
    garden = None
    if enable_garden:
        try:
            from .markdown_garden import MarkdownGardenManager

            garden = MarkdownGardenManager(str(workspace_dir))
        except Exception as exc:
            logger.debug("garden init skipped: %s", exc)
    # If caller passed a chromadb-style vector_store instead of an
    # embeddings object, try to extract its embedder.
    if embeddings is None and vector_store is not None:
        embeddings = getattr(vector_store, "_embedding_function", None)
    return MemoryEngine(
        workspace_dir,
        embeddings=embeddings,
        llm=llm,
        journal_caller=journal_llm_caller,
        distill_caller=distill_llm_caller,
        reflect_caller=reflect_llm_caller,
        garden=garden,
        config=cfg,
    )


__all__ = [
    "EngineConfig",
    "MemoryEngine",
    "MemoryRecord",
    "Modality",
    "Scope",
    "Signal",
    "Status",
    "build_memory_engine",
]
