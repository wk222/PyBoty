"""Data models shared by the evaluation framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalResult:
    test_name: str
    passed: bool
    score: float
    criteria_scores: dict[str, float] = field(default_factory=dict)
    feedback: str = ""
    duration_ms: float = 0
    timestamp: float = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_name": self.test_name,
            "passed": self.passed,
            "score": self.score,
            "criteria_scores": self.criteria_scores,
            "feedback": self.feedback,
            "duration_ms": round(self.duration_ms, 1),
            "timestamp": self.timestamp,
        }


@dataclass
class TestCase:
    name: str
    input_prompt: str
    expected_contains: list[str] = field(default_factory=list)
    expected_not_contains: list[str] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)
    min_score: float = 0.6
    timeout: int = 60
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "input_prompt": self.input_prompt,
            "expected_contains": self.expected_contains,
            "expected_not_contains": self.expected_not_contains,
            "criteria": self.criteria,
            "min_score": self.min_score,
            "tags": self.tags,
        }


DEFAULT_CRITERIA = [
    "准确性 — 回答是否事实准确",
    "完整性 — 是否完整回答了用户问题",
    "清晰度 — 表达是否清晰易懂",
    "可操作性 — 是否给出了可执行的建议或代码",
    "安全性 — 是否避免了不安全或有害的内容",
]
