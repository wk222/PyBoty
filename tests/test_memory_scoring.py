"""Tests for core.memory_scoring."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from core.memory_scoring import MemoryMetadata, composite_score, encode_memory, recency_score


class TestEncodeMemory:
    def test_successful_encoding(self):
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(
            content='{"importance": 0.8, "categories": ["code", "bug"], "scope": "technical"}'
        )
        meta = encode_memory("Found a null pointer bug in auth module", llm)
        assert meta.importance == 0.8
        assert "code" in meta.categories
        assert meta.scope == "technical"

    def test_llm_returns_bad_json(self):
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="not json")
        meta = encode_memory("some text", llm)
        assert meta.importance == 0.5
        assert meta.scope == "general"

    def test_llm_raises(self):
        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("boom")
        meta = encode_memory("some text", llm)
        assert isinstance(meta, MemoryMetadata)
        assert meta.importance == 0.5

    def test_importance_clamped(self):
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content='{"importance": 5.0, "categories": [], "scope": "general"}')
        meta = encode_memory("text", llm)
        assert meta.importance == 1.0

    def test_negative_importance_clamped(self):
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content='{"importance": -2.0, "categories": [], "scope": "general"}')
        meta = encode_memory("text", llm)
        assert meta.importance == 0.0


class TestRecencyScore:
    def test_now_is_1(self):
        assert recency_score(time.time()) == pytest.approx(1.0, abs=0.01)

    def test_half_life(self):
        half_life = 72.0
        ts = time.time() - half_life * 3600
        assert recency_score(ts, half_life_hours=half_life) == pytest.approx(0.5, abs=0.01)

    def test_very_old(self):
        ts = time.time() - 720 * 3600  # 30 days
        assert recency_score(ts) < 0.01

    def test_future_timestamp(self):
        ts = time.time() + 3600
        assert recency_score(ts) == 1.0

    def test_custom_half_life(self):
        half_life = 24.0
        ts = time.time() - half_life * 3600
        assert recency_score(ts, half_life_hours=half_life) == pytest.approx(0.5, abs=0.01)


class TestCompositeScore:
    def test_default_weights(self):
        score = composite_score(1.0, 1.0, 1.0)
        assert score == pytest.approx(1.0)

    def test_zero_all(self):
        assert composite_score(0.0, 0.0, 0.0) == 0.0

    def test_semantic_dominant(self):
        s1 = composite_score(1.0, 0.0, 0.0)
        s2 = composite_score(0.0, 1.0, 0.0)
        assert s1 > s2  # semantic weight (0.5) > recency weight (0.3)

    def test_custom_weights(self):
        score = composite_score(
            0.5,
            0.5,
            0.5,
            w_semantic=0.6,
            w_recency=0.3,
            w_importance=0.1,
        )
        assert score == pytest.approx(0.5)

    def test_mixed_values(self):
        score = composite_score(0.8, 0.6, 0.9)
        expected = 0.5 * 0.8 + 0.3 * 0.6 + 0.2 * 0.9
        assert score == pytest.approx(expected)
