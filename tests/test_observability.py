"""Tests for core.observability and core.cost_tracker."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

from core.cost_tracker import (
    CostTracker,
    CostTrackerCallback,
    _estimate_cost,
)
from core.observability import (
    ObservabilityConfig,
    get_observability_config_from_dict,
    setup_tracing,
)


class TestObservabilityConfig:
    def test_defaults(self):
        cfg = ObservabilityConfig()
        assert cfg.backend == "none"
        assert cfg.langfuse_public_key is None

    def test_from_dict(self):
        d = {"observability": {"backend": "langsmith", "log_level": "DEBUG"}}
        cfg = get_observability_config_from_dict(d)
        assert cfg.backend == "langsmith"
        assert cfg.log_level == "DEBUG"

    def test_from_empty_dict(self):
        cfg = get_observability_config_from_dict({})
        assert cfg.backend == "none"


class TestSetupTracing:
    def test_none_backend(self):
        callbacks = setup_tracing(ObservabilityConfig(backend="none"))
        assert callbacks == []

    def test_disabled_backend(self):
        callbacks = setup_tracing(ObservabilityConfig(backend="disabled"))
        assert callbacks == []

    def test_console_backend(self):
        callbacks = setup_tracing(ObservabilityConfig(backend="console"))
        assert len(callbacks) == 1

    def test_unknown_backend(self):
        callbacks = setup_tracing(ObservabilityConfig(backend="unknown_thing"))
        assert callbacks == []

    def test_langsmith_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            callbacks = setup_tracing(ObservabilityConfig(backend="langsmith"))
            assert callbacks == []

    def test_langfuse_without_keys(self):
        callbacks = setup_tracing(ObservabilityConfig(backend="langfuse"))
        assert callbacks == []


class TestCostEstimate:
    def test_known_model(self):
        cost = _estimate_cost("gpt-4o", 1000, 500)
        assert cost > 0

    def test_unknown_model(self):
        cost = _estimate_cost("totally-unknown-model", 1000, 500)
        assert cost == 0.0

    def test_zero_tokens(self):
        cost = _estimate_cost("gpt-4o", 0, 0)
        assert cost == 0.0


class TestCostTracker:
    def test_record_llm_call(self):
        tracker = CostTracker()
        record = tracker.record_llm_call("gpt-4o", 100, 50, duration_ms=200)
        assert record.total_tokens == 150
        assert record.cost_usd > 0

    def test_record_tool_call(self):
        tracker = CostTracker()
        record = tracker.record_tool_call("search", duration_ms=100, success=True)
        assert record.tool_name == "search"
        assert record.success is True

    def test_summary_aggregation(self):
        tracker = CostTracker()
        tracker.record_llm_call("gpt-4o", 100, 50)
        tracker.record_llm_call("gpt-4o", 200, 100)
        tracker.record_tool_call("search", duration_ms=50)
        tracker.record_tool_call("search", duration_ms=30, success=False)

        summary = tracker.get_summary()
        assert summary.total_llm_calls == 2
        assert summary.total_tool_calls == 2
        assert summary.total_input_tokens == 300
        assert summary.total_output_tokens == 150
        assert summary.total_cost_usd > 0
        assert "gpt-4o" in summary.model_breakdown
        assert summary.model_breakdown["gpt-4o"]["calls"] == 2
        assert "search" in summary.tool_breakdown
        assert summary.tool_breakdown["search"]["failures"] == 1

    def test_summary_to_dict(self):
        tracker = CostTracker()
        tracker.record_llm_call("gpt-4o", 100, 50)
        d = tracker.get_summary().to_dict()
        assert isinstance(d, dict)
        assert "total_cost_usd" in d

    def test_persist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "costs.json")
            tracker = CostTracker(persist_path=path)
            tracker.record_llm_call("gpt-4o", 100, 50)
            assert os.path.exists(path)

    def test_empty_summary(self):
        tracker = CostTracker()
        summary = tracker.get_summary()
        assert summary.total_llm_calls == 0
        assert summary.total_cost_usd == 0.0


class TestCostTrackerCallback:
    def test_callback_records_tool_events(self):
        tracker = CostTracker()
        callback = CostTrackerCallback(tracker)

        callback.on_tool_start({}, "input", run_id="t1")
        callback.on_tool_end("output", run_id="t1", name="my_tool")

        summary = tracker.get_summary()
        assert summary.total_tool_calls == 1

    def test_callback_records_tool_error(self):
        tracker = CostTracker()
        callback = CostTrackerCallback(tracker)

        callback.on_tool_start({}, "input", run_id="t2")
        callback.on_tool_error(Exception("fail"), run_id="t2", name="bad_tool")

        summary = tracker.get_summary()
        assert summary.total_tool_calls == 1
        assert summary.tool_breakdown["bad_tool"]["failures"] == 1
