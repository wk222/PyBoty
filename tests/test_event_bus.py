"""Tests for core.systems.runtime.event_bus."""

from __future__ import annotations

import asyncio
import threading

from core.systems.runtime.event_bus import Event, EventBus, EventType, event_bus


class TestEventBusBasics:
    def setup_method(self):
        self.bus = EventBus()

    def test_subscribe_and_emit(self):
        received = []
        self.bus.subscribe(EventType.TOOL_CALL, lambda e: received.append(e))
        evt = Event(type=EventType.TOOL_CALL, payload={"tool": "calc"}, source="test")
        self.bus.emit(evt)
        assert len(received) == 1
        assert received[0].payload["tool"] == "calc"

    def test_unsubscribe(self):
        received = []

        def handler(event):
            received.append(event)

        self.bus.subscribe(EventType.TOOL_CALL, handler)
        assert self.bus.unsubscribe(EventType.TOOL_CALL, handler)
        self.bus.emit(Event(type=EventType.TOOL_CALL))
        assert len(received) == 0

    def test_unsubscribe_not_found(self):
        assert not self.bus.unsubscribe(EventType.TOOL_CALL, lambda e: None)

    def test_priority_ordering(self):
        order = []
        self.bus.subscribe(EventType.AGENT_START, lambda e: order.append("low"), priority=0)
        self.bus.subscribe(EventType.AGENT_START, lambda e: order.append("high"), priority=10)
        self.bus.subscribe(EventType.AGENT_START, lambda e: order.append("mid"), priority=5)
        self.bus.emit(Event(type=EventType.AGENT_START))
        assert order == ["high", "mid", "low"]

    def test_handler_isolation(self):
        received = []

        def bad_handler(e):
            raise RuntimeError("boom")

        self.bus.subscribe(EventType.ERROR, bad_handler, priority=10)
        self.bus.subscribe(EventType.ERROR, lambda e: received.append("ok"), priority=0)
        self.bus.emit(Event(type=EventType.ERROR))
        assert received == ["ok"]

    def test_event_type_filtering(self):
        tool_events = []
        agent_events = []
        self.bus.subscribe(EventType.TOOL_CALL, lambda e: tool_events.append(e))
        self.bus.subscribe(EventType.AGENT_START, lambda e: agent_events.append(e))
        self.bus.emit(Event(type=EventType.TOOL_CALL))
        self.bus.emit(Event(type=EventType.AGENT_START))
        self.bus.emit(Event(type=EventType.TOOL_CALL))
        assert len(tool_events) == 2
        assert len(agent_events) == 1


class TestEventBusHistory:
    def setup_method(self):
        self.bus = EventBus(history_limit=10)

    def test_history_records_events(self):
        self.bus.emit(Event(type=EventType.TOOL_CALL, payload={"n": 1}))
        self.bus.emit(Event(type=EventType.AGENT_START, payload={"n": 2}))
        h = self.bus.history()
        assert len(h) == 2

    def test_history_filter_by_type(self):
        self.bus.emit(Event(type=EventType.TOOL_CALL))
        self.bus.emit(Event(type=EventType.AGENT_START))
        self.bus.emit(Event(type=EventType.TOOL_CALL))
        h = self.bus.history(EventType.TOOL_CALL)
        assert len(h) == 2

    def test_history_limit(self):
        for i in range(20):
            self.bus.emit(Event(type=EventType.TOOL_CALL, payload={"i": i}))
        h = self.bus.history()
        assert len(h) == 10
        assert h[0].payload["i"] == 10

    def test_history_limit_param(self):
        for i in range(5):
            self.bus.emit(Event(type=EventType.TOOL_CALL, payload={"i": i}))
        h = self.bus.history(limit=2)
        assert len(h) == 2
        assert h[0].payload["i"] == 3


class TestEventBusClear:
    def test_clear_removes_subs_and_history(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.TOOL_CALL, lambda e: received.append(e))
        bus.emit(Event(type=EventType.TOOL_CALL))
        assert len(received) == 1
        assert len(bus.history()) == 1
        bus.clear()
        assert bus.history() == []
        bus.emit(Event(type=EventType.TOOL_CALL))
        assert len(received) == 1  # handler removed, not called again
        assert len(bus.history()) == 1  # but event still recorded in history


class TestEventBusSubscriberCount:
    def test_count(self):
        bus = EventBus()
        bus.subscribe(EventType.TOOL_CALL, lambda e: None)
        bus.subscribe(EventType.TOOL_CALL, lambda e: None)
        bus.subscribe(EventType.AGENT_START, lambda e: None)
        assert bus.subscriber_count(EventType.TOOL_CALL) == 2
        assert bus.subscriber_count(EventType.AGENT_START) == 1
        assert bus.subscriber_count() == 3


class TestEventBusAsync:
    def test_emit_async(self):
        bus = EventBus()
        received = []

        async def async_handler(e):
            received.append(e.payload)

        bus.subscribe(EventType.TOOL_RESULT, async_handler)
        asyncio.run(bus.emit_async(Event(type=EventType.TOOL_RESULT, payload={"r": 42})))
        assert len(received) == 1
        assert received[0]["r"] == 42

    def test_emit_async_mixed_handlers(self):
        bus = EventBus()
        results = []

        async def ah(e):
            results.append("async")

        def sh(e):
            results.append("sync")

        bus.subscribe(EventType.MCP_CONNECT, ah, priority=5)
        bus.subscribe(EventType.MCP_CONNECT, sh, priority=0)
        asyncio.run(bus.emit_async(Event(type=EventType.MCP_CONNECT)))
        assert results == ["async", "sync"]


class TestEventBusConcurrency:
    def test_concurrent_emit(self):
        bus = EventBus()
        counter = {"n": 0}
        lock = threading.Lock()

        def handler(e):
            with lock:
                counter["n"] += 1

        bus.subscribe(EventType.COST_RECORD, handler)
        threads = []
        for _ in range(50):
            t = threading.Thread(target=lambda: bus.emit(Event(type=EventType.COST_RECORD)))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert counter["n"] == 50
        assert len(bus.history(EventType.COST_RECORD)) == 50


class TestGlobalInstance:
    def test_global_event_bus_exists(self):
        assert isinstance(event_bus, EventBus)


class TestEventDataclass:
    def test_event_defaults(self):
        e = Event(type=EventType.ERROR)
        assert e.payload == {}
        assert e.source == ""
        assert e.session_id is None
        assert e.timestamp > 0

    def test_event_type_values(self):
        assert EventType.TOOL_CALL.value == "tool_call"
        assert EventType.GUARDRAIL_PASS.value == "guardrail_pass"
