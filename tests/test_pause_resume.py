"""Tests for core.pause_resume."""

from __future__ import annotations

import threading
import time

from core.pause_resume import (
    PauseContext,
    PauseManager,
    PauseState,
    SimplePausableAgent,
)


class TestSimplePausableAgent:
    def test_initial_state(self):
        a = SimplePausableAgent("agent1")
        assert a.agent_name == "agent1"
        assert a.pause_state == PauseState.RUNNING
        assert a.saved_context is None

    def test_pause(self):
        a = SimplePausableAgent("a")
        ctx = PauseContext(reason="test", paused_by="user")
        a.on_pause(ctx)
        assert a.pause_state == PauseState.PAUSED
        assert a.saved_context is not None
        assert a.saved_context.reason == "test"

    def test_resume(self):
        a = SimplePausableAgent("a")
        a.on_pause(PauseContext(reason="pause"))
        a.on_resume(PauseContext(reason="resume"))
        assert a.pause_state == PauseState.RUNNING
        assert a.saved_context is None

    def test_wait_if_paused_immediate(self):
        a = SimplePausableAgent("a")
        assert a.wait_if_paused(timeout=0.01)

    def test_wait_if_paused_blocks(self):
        a = SimplePausableAgent("a")
        a.on_pause(PauseContext(reason="block"))
        result = a.wait_if_paused(timeout=0.05)
        assert not result

    def test_wait_then_resume(self):
        a = SimplePausableAgent("a")
        a.on_pause(PauseContext(reason="wait"))

        def resume_later():
            time.sleep(0.05)
            a.on_resume(PauseContext())

        t = threading.Thread(target=resume_later)
        t.start()
        result = a.wait_if_paused(timeout=1.0)
        t.join()
        assert result
        assert a.pause_state == PauseState.RUNNING


class TestPauseManager:
    def setup_method(self):
        self.mgr = PauseManager()
        self.a1 = SimplePausableAgent("agent1")
        self.a2 = SimplePausableAgent("agent2")
        self.mgr.register(self.a1)
        self.mgr.register(self.a2)

    def test_pause_all(self):
        count = self.mgr.pause_all("maintenance")
        assert count == 2
        assert self.a1.pause_state == PauseState.PAUSED
        assert self.a2.pause_state == PauseState.PAUSED
        assert self.mgr.global_state == PauseState.PAUSED

    def test_resume_all(self):
        self.mgr.pause_all("test")
        count = self.mgr.resume_all()
        assert count == 2
        assert self.a1.pause_state == PauseState.RUNNING
        assert self.a2.pause_state == PauseState.RUNNING
        assert self.mgr.global_state == PauseState.RUNNING

    def test_resume_not_paused(self):
        count = self.mgr.resume_all()
        assert count == 0

    def test_pause_single(self):
        assert self.mgr.pause_agent("agent1", "individual")
        assert self.a1.pause_state == PauseState.PAUSED
        assert self.a2.pause_state == PauseState.RUNNING

    def test_resume_single(self):
        self.mgr.pause_all()
        assert self.mgr.resume_agent("agent1")
        assert self.a1.pause_state == PauseState.RUNNING
        assert self.a2.pause_state == PauseState.PAUSED

    def test_pause_nonexistent(self):
        assert not self.mgr.pause_agent("ghost")

    def test_resume_nonexistent(self):
        assert not self.mgr.resume_agent("ghost")

    def test_resume_already_running(self):
        assert not self.mgr.resume_agent("agent1")

    def test_unregister(self):
        assert self.mgr.unregister("agent1")
        assert not self.mgr.unregister("agent1")
        status = self.mgr.status()
        assert status["agent_count"] == 1

    def test_status(self):
        self.mgr.pause_agent("agent1")
        status = self.mgr.status()
        assert status["agent_count"] == 2
        assert status["paused_count"] == 1
        assert status["agents"]["agent1"] == "paused"
        assert status["agents"]["agent2"] == "running"

    def test_resume_with_data(self):
        self.mgr.pause_all()
        self.mgr.resume_all(data={"approval": True})
        assert self.a1.pause_state == PauseState.RUNNING

    def test_concurrent_pause_resume(self):
        agents = [SimplePausableAgent(f"a{i}") for i in range(10)]
        for a in agents:
            self.mgr.register(a)
        errors = []

        def cycle():
            try:
                for _ in range(5):
                    self.mgr.pause_all("concurrent")
                    self.mgr.resume_all()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=cycle) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
