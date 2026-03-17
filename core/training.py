"""Human feedback loop for agent improvement.

Collects user ratings and feedback on agent outputs, persists them,
and formats historical feedback as prompt context so agents can
learn from past mistakes and successes.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_STORE_FILE = "feedback_store.json"


@dataclass
class FeedbackRecord:
    agent_name: str
    task_summary: str
    output_summary: str
    score: int  # 1-5
    feedback_text: str
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        self.score = max(1, min(5, self.score))


class FeedbackStore:
    """JSON-file-backed feedback storage."""

    def __init__(self, store_path: str | Path | None = None):
        self._path = Path(store_path) if store_path else None
        self._records: list[FeedbackRecord] = []
        if self._path and self._path.exists():
            self._load()

    def _load(self) -> None:
        if not self._path:
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._records = [FeedbackRecord(**r) for r in data if isinstance(r, dict)]
        except Exception as exc:
            logger.warning("Failed to load feedback store: %s", exc)

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps([asdict(r) for r in self._records], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to save feedback store: %s", exc)

    def add(self, record: FeedbackRecord) -> None:
        self._records.append(record)
        self._save()

    def get_for_agent(self, agent_name: str, *, limit: int = 10) -> list[FeedbackRecord]:
        matched = [r for r in self._records if r.agent_name == agent_name]
        matched.sort(key=lambda r: r.timestamp, reverse=True)
        return matched[:limit]

    def get_best_examples(self, agent_name: str, *, min_score: int = 4, limit: int = 5) -> list[FeedbackRecord]:
        matched = [r for r in self._records if r.agent_name == agent_name and r.score >= min_score]
        matched.sort(key=lambda r: (r.score, r.timestamp), reverse=True)
        return matched[:limit]

    def get_worst_patterns(self, agent_name: str, *, max_score: int = 2, limit: int = 5) -> list[FeedbackRecord]:
        matched = [r for r in self._records if r.agent_name == agent_name and r.score <= max_score]
        matched.sort(key=lambda r: (r.score, -r.timestamp))
        return matched[:limit]

    def get_all(self) -> list[FeedbackRecord]:
        return list(self._records)

    def count(self, agent_name: str | None = None) -> int:
        if agent_name is None:
            return len(self._records)
        return sum(1 for r in self._records if r.agent_name == agent_name)

    def export_training_data(self, agent_name: str) -> list[dict[str, Any]]:
        return [asdict(r) for r in self._records if r.agent_name == agent_name]


def format_feedback_prompt(records: list[FeedbackRecord]) -> str:
    """Format feedback records as a prompt section for agent context injection."""
    if not records:
        return ""

    good = [r for r in records if r.score >= 4]
    bad = [r for r in records if r.score <= 2]

    lines: list[str] = []

    if good:
        lines.append("以下是用户对你过去输出的正面评价，请保持这些优点：")
        for r in good[:3]:
            lines.append(f"  - 任务「{r.task_summary}」得分 {r.score}/5: {r.feedback_text}")

    if bad:
        lines.append("以下是用户对你过去输出的改进建议，请注意避免：")
        for r in bad[:3]:
            lines.append(f"  - 任务「{r.task_summary}」得分 {r.score}/5: {r.feedback_text}")

    return "\n".join(lines)
