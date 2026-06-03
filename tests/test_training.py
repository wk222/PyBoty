"""Tests for core.training — feedback store and prompt formatting."""

from __future__ import annotations

import tempfile
from pathlib import Path

from core.systems.runtime.training import FeedbackRecord, FeedbackStore, format_feedback_prompt


class TestFeedbackRecord:
    def test_basic_creation(self):
        r = FeedbackRecord(
            agent_name="analyst",
            task_summary="Analyze sales data",
            output_summary="Generated report with charts",
            score=4,
            feedback_text="Good analysis",
        )
        assert r.agent_name == "analyst"
        assert r.score == 4
        assert r.timestamp > 0

    def test_score_clamped_high(self):
        r = FeedbackRecord(
            agent_name="a",
            task_summary="t",
            output_summary="o",
            score=10,
            feedback_text="f",
        )
        assert r.score == 5

    def test_score_clamped_low(self):
        r = FeedbackRecord(
            agent_name="a",
            task_summary="t",
            output_summary="o",
            score=-1,
            feedback_text="f",
        )
        assert r.score == 1


class TestFeedbackStoreInMemory:
    def setup_method(self):
        self.store = FeedbackStore()

    def _rec(self, agent="bot", score=3, task="task"):
        return FeedbackRecord(
            agent_name=agent,
            task_summary=task,
            output_summary="out",
            score=score,
            feedback_text="fb",
        )

    def test_add_and_count(self):
        self.store.add(self._rec())
        self.store.add(self._rec())
        assert self.store.count() == 2

    def test_count_by_agent(self):
        self.store.add(self._rec(agent="a"))
        self.store.add(self._rec(agent="b"))
        self.store.add(self._rec(agent="a"))
        assert self.store.count("a") == 2
        assert self.store.count("b") == 1

    def test_get_for_agent(self):
        r1 = self._rec(agent="a", task="t1")
        r1.timestamp = 100.0
        r2 = self._rec(agent="b", task="t2")
        r3 = self._rec(agent="a", task="t3")
        r3.timestamp = 200.0
        self.store.add(r1)
        self.store.add(r2)
        self.store.add(r3)
        records = self.store.get_for_agent("a")
        assert len(records) == 2
        assert records[0].task_summary == "t3"  # most recent first

    def test_get_for_agent_limit(self):
        for i in range(10):
            self.store.add(self._rec(agent="a", task=f"t{i}"))
        assert len(self.store.get_for_agent("a", limit=3)) == 3

    def test_get_best_examples(self):
        self.store.add(self._rec(agent="a", score=5, task="great"))
        self.store.add(self._rec(agent="a", score=2, task="bad"))
        self.store.add(self._rec(agent="a", score=4, task="good"))
        best = self.store.get_best_examples("a", min_score=4)
        assert len(best) == 2
        assert best[0].score >= best[1].score

    def test_get_worst_patterns(self):
        self.store.add(self._rec(agent="a", score=1, task="terrible"))
        self.store.add(self._rec(agent="a", score=5, task="great"))
        self.store.add(self._rec(agent="a", score=2, task="meh"))
        worst = self.store.get_worst_patterns("a", max_score=2)
        assert len(worst) == 2
        assert worst[0].score <= worst[1].score

    def test_export_training_data(self):
        self.store.add(self._rec(agent="a", task="t1"))
        self.store.add(self._rec(agent="b", task="t2"))
        data = self.store.export_training_data("a")
        assert len(data) == 1
        assert data[0]["task_summary"] == "t1"

    def test_get_all(self):
        self.store.add(self._rec())
        self.store.add(self._rec())
        assert len(self.store.get_all()) == 2


class TestFeedbackStorePersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "feedback.json"

            store1 = FeedbackStore(path)
            store1.add(
                FeedbackRecord(
                    agent_name="a",
                    task_summary="t",
                    output_summary="o",
                    score=4,
                    feedback_text="nice",
                )
            )
            store1.add(
                FeedbackRecord(
                    agent_name="b",
                    task_summary="t2",
                    output_summary="o2",
                    score=2,
                    feedback_text="meh",
                )
            )

            store2 = FeedbackStore(path)
            assert store2.count() == 2
            assert store2.count("a") == 1

    def test_load_nonexistent(self):
        store = FeedbackStore("/nonexistent/path/feedback.json")
        assert store.count() == 0

    def test_load_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "feedback.json"
            path.write_text("not json!!!", encoding="utf-8")
            store = FeedbackStore(path)
            assert store.count() == 0


class TestFormatFeedbackPrompt:
    def _rec(self, score, task="task", feedback="fb"):
        return FeedbackRecord(
            agent_name="a",
            task_summary=task,
            output_summary="o",
            score=score,
            feedback_text=feedback,
        )

    def test_empty(self):
        assert format_feedback_prompt([]) == ""

    def test_good_feedback(self):
        result = format_feedback_prompt([self._rec(5, "analysis", "excellent work")])
        assert "正面评价" in result
        assert "analysis" in result
        assert "excellent work" in result

    def test_bad_feedback(self):
        result = format_feedback_prompt([self._rec(1, "report", "too vague")])
        assert "改进建议" in result
        assert "report" in result
        assert "too vague" in result

    def test_mixed_feedback(self):
        records = [
            self._rec(5, "good_task", "great"),
            self._rec(1, "bad_task", "terrible"),
            self._rec(3, "mid_task", "ok"),
        ]
        result = format_feedback_prompt(records)
        assert "正面评价" in result
        assert "改进建议" in result
        assert "good_task" in result
        assert "bad_task" in result
        assert "mid_task" not in result  # score 3 is neither good nor bad

    def test_limits_to_3_each(self):
        records = [self._rec(5, f"good_{i}", "great") for i in range(10)]
        records += [self._rec(1, f"bad_{i}", "awful") for i in range(10)]
        result = format_feedback_prompt(records)
        assert result.count("good_") == 3
        assert result.count("bad_") == 3
