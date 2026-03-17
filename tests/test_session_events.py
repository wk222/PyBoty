"""Tests for session-scoped ephemeral event queue."""

from __future__ import annotations

import time

from core.session_events import SessionEventQueue


class TestSessionEventQueue:
    def test_enqueue_and_flush(self):
        q = SessionEventQueue()
        q.enqueue("s1", "User connected")
        q.enqueue("s1", "Tool executed")
        events = q.flush("s1")
        assert len(events) == 2
        assert events[0].text == "User connected"
        assert events[1].text == "Tool executed"

    def test_flush_consumes(self):
        q = SessionEventQueue()
        q.enqueue("s1", "event1")
        q.flush("s1")
        assert q.flush("s1") == []

    def test_dedup_consecutive(self):
        q = SessionEventQueue()
        q.enqueue("s1", "same event")
        q.enqueue("s1", "same event")
        events = q.flush("s1")
        assert len(events) == 1

    def test_dedup_resets_on_different(self):
        q = SessionEventQueue()
        q.enqueue("s1", "A")
        q.enqueue("s1", "B")
        q.enqueue("s1", "A")
        events = q.flush("s1")
        assert len(events) == 3

    def test_max_events(self):
        q = SessionEventQueue(max_events=3)
        for i in range(10):
            q.enqueue("s1", f"event {i}")
        events = q.flush("s1")
        assert len(events) == 3
        assert events[0].text == "event 7"

    def test_peek_does_not_consume(self):
        q = SessionEventQueue()
        q.enqueue("s1", "peek me")
        peeked = q.peek("s1")
        assert len(peeked) == 1
        assert q.has_events("s1")

    def test_has_events(self):
        q = SessionEventQueue()
        assert not q.has_events("s1")
        q.enqueue("s1", "x")
        assert q.has_events("s1")

    def test_clear(self):
        q = SessionEventQueue()
        q.enqueue("s1", "x")
        q.clear("s1")
        assert not q.has_events("s1")

    def test_clear_all(self):
        q = SessionEventQueue()
        q.enqueue("s1", "x")
        q.enqueue("s2", "y")
        q.clear_all()
        assert q.session_count() == 0

    def test_session_count(self):
        q = SessionEventQueue()
        q.enqueue("s1", "x")
        q.enqueue("s2", "y")
        assert q.session_count() == 2

    def test_format_prompt_prefix(self):
        q = SessionEventQueue()
        q.enqueue("s1", "Config reloaded")
        q.enqueue("s1", "New skill installed")
        result = q.format_prompt_prefix("s1")
        assert "[System Events]" in result
        assert "- Config reloaded" in result
        assert "- New skill installed" in result
        assert not q.has_events("s1")

    def test_format_prompt_prefix_empty(self):
        q = SessionEventQueue()
        assert q.format_prompt_prefix("s1") == ""

    def test_prune_stale(self):
        q = SessionEventQueue()
        q.enqueue("s1", "old event")
        for event in q._queues.get("s1", []):
            event.ts = time.time() - 7200
        q.enqueue("s1", "new event")
        pruned = q.prune_stale(max_age_seconds=3600)
        assert pruned == 1
        events = q.flush("s1")
        assert len(events) == 1
        assert events[0].text == "new event"

    def test_multiple_sessions_isolated(self):
        q = SessionEventQueue()
        q.enqueue("s1", "event for s1")
        q.enqueue("s2", "event for s2")
        e1 = q.flush("s1")
        e2 = q.flush("s2")
        assert len(e1) == 1
        assert len(e2) == 1
        assert e1[0].text == "event for s1"
        assert e2[0].text == "event for s2"

    def test_context_key(self):
        q = SessionEventQueue()
        q.enqueue("s1", "skill loaded", context_key="skills")
        events = q.flush("s1")
        assert events[0].context_key == "skills"
