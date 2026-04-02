from __future__ import annotations

import pytest

import core.assets.agents.subagent_registry as registry_module
from core.assets.agents import (
    SubagentConcurrencyLimitError,
    SubagentDepthLimitError,
    SubagentRegistry,
)
from core.event_bus import EventBus, EventType


def test_subagent_registry_spawn_records_depth_and_emits_event(monkeypatch):
    bus = EventBus()
    events = []
    bus.subscribe(EventType.SUBAGENT_SPAWNED, lambda event: events.append(event))
    monkeypatch.setattr(registry_module, "event_bus", bus)

    registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=60)
    record = registry.spawn(
        agent_name="helper",
        thread_id="thread-1",
        parent_agent_name="root",
        parent_depth=0,
    )

    assert record.depth == 1
    assert record.status == "running"
    assert registry.get_active(agent_name="helper", thread_id="thread-1") is record
    assert events[0].payload["agent_name"] == "helper"
    assert events[0].payload["depth"] == 1


def test_subagent_registry_enforces_depth_limit():
    registry = SubagentRegistry(max_depth=2, max_concurrent=5, default_timeout_seconds=60)

    with pytest.raises(SubagentDepthLimitError):
        registry.spawn(
            agent_name="worker",
            thread_id="thread-1",
            parent_agent_name="planner",
            parent_depth=2,
        )


def test_subagent_registry_enforces_concurrency_limit_for_waiting_runs():
    registry = SubagentRegistry(max_depth=3, max_concurrent=1, default_timeout_seconds=60)
    registry.spawn(agent_name="worker_a", thread_id="thread-a")
    registry.mark_waiting_approval(
        agent_name="worker_a",
        thread_id="thread-a",
        approval_id="approval-1",
    )

    with pytest.raises(SubagentConcurrencyLimitError):
        registry.spawn(agent_name="worker_b", thread_id="thread-b")


def test_subagent_registry_cleanup_marks_timeout_and_emits_event(monkeypatch):
    bus = EventBus()
    events = []
    bus.subscribe(EventType.SUBAGENT_TIMEOUT, lambda event: events.append(event))
    monkeypatch.setattr(registry_module, "event_bus", bus)

    registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=1)
    record = registry.spawn(agent_name="worker", thread_id="thread-1")

    timed_out = registry.cleanup_stale(now=record.updated_at + 5)

    assert len(timed_out) == 1
    assert timed_out[0].status == "timed_out"
    assert registry.get_active(agent_name="worker", thread_id="thread-1") is None
    assert registry.get_latest(agent_name="worker", thread_id="thread-1").status == "timed_out"
    assert events[0].payload["status"] == "timed_out"


def test_subagent_registry_records_steering_and_abort():
    registry = SubagentRegistry(max_depth=3, max_concurrent=2, default_timeout_seconds=60)
    registry.spawn(agent_name="worker", thread_id="thread-1")

    steered = registry.record_steer(
        agent_name="worker",
        thread_id="thread-1",
        instructions="Focus on the failing test first.",
    )
    aborted = registry.abort(
        agent_name="worker",
        thread_id="thread-1",
        reason="operator stop",
    )

    assert steered is not None
    assert steered.steering_instructions == ["Focus on the failing test first."]
    assert aborted is not None
    assert aborted.status == "aborted"
    assert registry.get_active(agent_name="worker", thread_id="thread-1") is None
    assert registry.get_latest(agent_name="worker", thread_id="thread-1").error == "operator stop"
