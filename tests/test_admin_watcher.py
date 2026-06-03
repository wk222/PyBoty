from __future__ import annotations

from core.systems.runtime.admin_watcher import AdminWatcherDaemon
from core.systems.runtime.event_bus import Event, EventType


def test_admin_watcher_extracts_capability_gap_candidates():
    events = [
        Event(type=EventType.ERROR, source="app:parser", payload={"error": "timeout while parsing"}),
        Event(type=EventType.ERROR, source="app:parser", payload={"error": "timeout while parsing"}),
        Event(type=EventType.SUBAGENT_FAILED, source="agent:builder", payload={"error": "missing tool xyz"}),
        Event(type=EventType.SUBAGENT_FAILED, source="agent:builder", payload={"error": "missing tool xyz"}),
    ]

    candidates = AdminWatcherDaemon.extract_gap_candidates(events)

    assert len(candidates) == 2
    parser_candidate = next(item for item in candidates if item["source"] == "app:parser")
    builder_candidate = next(item for item in candidates if item["source"] == "agent:builder")
    assert parser_candidate["gap_type"] == "latency_or_batching_gap"
    assert builder_candidate["gap_type"] == "missing_capability_gap"
