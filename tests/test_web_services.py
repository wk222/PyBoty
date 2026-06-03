"""Unified tests for PyBot Web Services, API Server, Gateway Guard, Task Routing, Debug Panel, and Session Events (Eighth Round).

Consolidated and merged from 5 individual test files:
* test_api_server.py
* test_gateway_guard.py
* test_tasks_router.py
* test_debug_panel.py
* test_session_events.py
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_server
from core.assets.agents.storage import AgentDefinition
from core.systems.runtime.project_paths import ProjectPaths
from core.systems.session import SessionEventQueue
from core.systems.tasks import task_registry
from core.systems.tasks.task_events import (
    TASK_EVENT_HEARTBEAT,
    TASK_EVENT_STEP,
    emit_task_event,
)
from web.gateway_guard import GatewayGuardMiddleware, RateLimitConfig
from web.routers.tasks import _task_event_stream


# ---------------------------------------------------------------------------
# 1. API Server / Chat / Health Endpoints Tests (formerly test_api_server.py)
# ---------------------------------------------------------------------------

def create_api_client(temp_paths, monkeypatch, *, stub_invoke: bool = True, api_key: str = "test-api-key"):
    monkeypatch.setenv("PYBOT_API_KEYS", f"{api_key}:*")
    app = api_server.create_app(
        paths=temp_paths,
        llm_config={"model": "gpt-4o-mini", "api_key": "test", "api_base": "https://example.com/v1"},
    )
    if stub_invoke:
        monkeypatch.setattr(
            api_server,
            "invoke_sub_agent",
            lambda **kwargs: {"response": f"echo:{kwargs['task']}", "thread_id": kwargs["thread_id"]},
        )
    headers = {"Authorization": f"Bearer {api_key}"}
    return app, TestClient(app, headers=headers)


class TestApiServerClass:
    def test_api_requires_auth_for_protected_routes(self, temp_paths, monkeypatch):
        monkeypatch.delenv("PYBOT_API_KEYS", raising=False)
        monkeypatch.delenv("PYBOT_ALLOW_DEV_KEY", raising=False)
        app = api_server.create_app(
            paths=temp_paths,
            llm_config={"model": "gpt-4o-mini", "api_key": "test", "api_base": "https://example.com/v1"},
        )
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/api/v1/agents").status_code == 401

    def test_api_lists_agents(self, temp_paths, monkeypatch):
        _, client = create_api_client(temp_paths, monkeypatch)
        with client:
            response = client.get("/api/v1/agents")
            assert response.status_code == 200
            assert response.json() == {"success": True, "count": 0, "agents": {}}

    def test_api_root_landing_page(self, temp_paths, monkeypatch):
        _, client = create_api_client(temp_paths, monkeypatch)
        with client:
            response = client.get("/")
            assert response.status_code == 200
            payload = response.json()
            assert payload["name"] == "PyBot API"
            assert payload["status"] == "ok"
            assert payload["docs_url"] == "/docs"
            assert payload["agents_url"] == "/api/v1/agents"

    def test_api_health_endpoint(self, temp_paths, monkeypatch):
        _, client = create_api_client(temp_paths, monkeypatch)
        with client:
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"
            assert response.json()["service"] == "api"

    def test_api_favicon_returns_empty_response(self, temp_paths, monkeypatch):
        _, client = create_api_client(temp_paths, monkeypatch)
        with client:
            response = client.get("/favicon.ico")
            assert response.status_code == 204
            assert response.content == b""

    def test_api_returns_404_for_unknown_agent(self, temp_paths, monkeypatch):
        _, client = create_api_client(temp_paths, monkeypatch, stub_invoke=False)
        with client:
            response = client.post(
                "/api/v1/agents/missing/chat",
                json={"message": "hi", "context": "", "thread_id": "t-1"},
            )
            assert response.status_code == 404
            assert "不存在" in response.json()["detail"]

    def test_api_chats_with_agent(self, temp_paths, monkeypatch):
        app, client = create_api_client(temp_paths, monkeypatch)
        app.state.agent_storage.add_agent(
            AgentDefinition(
                name="math_expert",
                role="math",
                description="Handles math questions",
                system_prompt="You are a math expert.",
            )
        )

        with client:
            response = client.post(
                "/api/v1/agents/math_expert/chat",
                json={"message": "2+2", "context": "quick", "thread_id": "demo-thread"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["success"] is True
            assert payload["agent_name"] == "math_expert"
            assert payload["response"]["response"] == "echo:2+2"
            assert payload["response"]["thread_id"] == "demo-thread"

    def test_api_hides_internal_errors(self, temp_paths, monkeypatch):
        app, client = create_api_client(temp_paths, monkeypatch, stub_invoke=False)
        app.state.agent_storage.add_agent(
            AgentDefinition(
                name="ops_helper",
                role="ops",
                description="Handles ops questions",
                system_prompt="You are an ops expert.",
            )
        )
        monkeypatch.setattr(
            api_server,
            "invoke_sub_agent",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("secret stack trace")),
        )

        with client:
            response = client.post(
                "/api/v1/agents/ops_helper/chat",
                json={"message": "deploy", "context": "", "thread_id": "ops-thread"},
            )
            assert response.status_code == 500
            assert response.json()["detail"] == "Internal server error"
            assert "secret stack trace" not in response.text


# ---------------------------------------------------------------------------
# 2. Gateway Guard Middleware Tests (formerly test_gateway_guard.py)
# ---------------------------------------------------------------------------

class TestGatewayGuardClass:
    def test_gateway_guard_authentication(self):
        app = FastAPI()

        @app.get("/api/chat/message")
        def chat_endpoint():
            return {"status": "ok"}

        app.add_middleware(
            GatewayGuardMiddleware,
            api_keys={"valid-key": ["chat"]},
        )

        client = TestClient(app)

        # Missing auth
        response = client.get("/api/chat/message")
        assert response.status_code == 401

        # Invalid auth
        response = client.get("/api/chat/message", headers={"Authorization": "Bearer wrong-key"})
        assert response.status_code == 401

        # Valid auth
        response = client.get("/api/chat/message", headers={"Authorization": "Bearer valid-key"})
        assert response.status_code == 200

    def test_gateway_guard_method_scopes(self):
        app = FastAPI()

        @app.get("/api/admin/config")
        def admin_endpoint():
            return {"status": "ok"}

        @app.get("/api/chat/message")
        def chat_endpoint():
            return {"status": "ok"}

        app.add_middleware(
            GatewayGuardMiddleware,
            api_keys={
                "admin-key": ["*"],
                "chat-key": ["chat"],
            },
        )

        client = TestClient(app)

        # Admin key can access both
        assert client.get("/api/admin/config", headers={"Authorization": "Bearer admin-key"}).status_code == 200
        assert client.get("/api/chat/message", headers={"Authorization": "Bearer admin-key"}).status_code == 200

        # Chat key can access chat but not admin
        assert client.get("/api/chat/message", headers={"Authorization": "Bearer chat-key"}).status_code == 200
        assert client.get("/api/admin/config", headers={"Authorization": "Bearer chat-key"}).status_code == 403

    def test_gateway_guard_rate_limiting(self):
        app = FastAPI()

        @app.get("/api/chat/fast")
        def fast_endpoint():
            return {"status": "ok"}

        app.add_middleware(
            GatewayGuardMiddleware,
            api_keys={"test-key": ["chat"]},
            rate_limits={
                "chat": RateLimitConfig(tokens_per_second=2.0, burst_capacity=3),
            },
        )

        client = TestClient(app)
        headers = {"Authorization": "Bearer test-key"}

        # Consume burst capacity
        assert client.get("/api/chat/fast", headers=headers).status_code == 200
        assert client.get("/api/chat/fast", headers=headers).status_code == 200
        assert client.get("/api/chat/fast", headers=headers).status_code == 200

        # Next request should be rate limited
        assert client.get("/api/chat/fast", headers=headers).status_code == 429

        # Wait for token refill (0.5s should refill 1 token)
        time.sleep(0.6)
        assert client.get("/api/chat/fast", headers=headers).status_code == 200

    def test_gateway_guard_exclude_paths(self):
        app = FastAPI()

        @app.get("/health")
        def health_endpoint():
            return {"status": "healthy"}

        app.add_middleware(
            GatewayGuardMiddleware,
            api_keys={"valid-key": ["*"]},
            exclude_paths={"/health"},
        )

        client = TestClient(app)

        # Health check should pass without auth
        response = client.get("/health")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 3. Tasks Router & Persistent Tasks Tests (formerly test_tasks_router.py)
# ---------------------------------------------------------------------------

@dataclass
class _FakePersistentTask:
    task_id: str = "demo-1"
    name: str = "demo"
    description: str = "demo task"
    status_value: str = "running"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    heartbeat_at: float | None = None
    progress: float = 0.25
    error: str | None = None
    current_step: Any = None

    @property
    def status(self) -> Any:
        class _S:
            def __init__(self, value: str) -> None:
                self.value = value

        return _S(self.status_value)


class _FakeRunner:
    def __init__(self, task: _FakePersistentTask) -> None:
        self._task = task
        self.cancel_calls = 0

    def get_task(self, task_id: str) -> Any:
        return self._task if task_id == self._task.task_id else None

    def cancel_task(self, _task_id: str) -> bool:
        self.cancel_calls += 1
        self._task.status_value = "cancelled"
        return True

    def pause_task(self, _task_id: str) -> bool:
        self._task.status_value = "paused"
        return True

    def resume_task(self, _task_id: str) -> bool:
        self._task.status_value = "running"
        return True


class TestTasksRouterClass:
    @pytest.fixture(autouse=True)
    def clean_registry(self):
        task_registry.clear()
        yield
        task_registry.clear()

    @pytest.fixture
    def auth(self):
        return {"Authorization": "Bearer dev-key"}

    @pytest.fixture
    def attached_task(self) -> _FakePersistentTask:
        task = _FakePersistentTask()
        runner = _FakeRunner(task)
        task_registry.attach_persistent(runner, task, parent_thread_id="thread-X")
        return task

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from web.app import create_app

        monkeypatch.setenv("PYBOT_API_KEYS", "dev-key:*")
        paths = ProjectPaths.from_root(tmp_path)
        paths.ensure_runtime_dirs()
        app = create_app(paths=paths)
        return TestClient(app)

    def test_list_returns_attached_task(self, client, auth, attached_task):
        res = client.get("/api/tasks", headers=auth)
        assert res.status_code == 200
        body = res.json()
        assert body["count"] == 1
        snap = body["tasks"][0]
        assert snap["task_id"] == attached_task.task_id
        assert snap["status"] == "running"
        assert snap["kind"] == "persistent"
        assert snap["parent_thread_id"] == "thread-X"

    def test_list_filters_by_kind(self, client, auth, attached_task):
        res = client.get("/api/tasks?kind=monitor", headers=auth)
        assert res.status_code == 200
        assert res.json()["count"] == 0

    def test_list_rejects_unknown_kind(self, client, auth):
        res = client.get("/api/tasks?kind=nonsense", headers=auth)
        assert res.status_code == 400

    def test_list_filters_by_thread(self, client, auth, attached_task):
        res = client.get("/api/tasks?thread_id=other", headers=auth)
        assert res.json()["count"] == 0
        res2 = client.get("/api/tasks?thread_id=thread-X", headers=auth)
        assert res2.json()["count"] == 1

    def test_get_task_returns_snapshot(self, client, auth, attached_task):
        res = client.get(f"/api/tasks/{attached_task.task_id}", headers=auth)
        assert res.status_code == 200
        snap = res.json()
        assert snap["progress"] == 0.25
        assert snap["last_step"] is None  # current_step was None

    def test_get_task_404(self, client, auth):
        res = client.get("/api/tasks/missing", headers=auth)
        assert res.status_code == 404

    def test_cancel_propagates(self, client, auth, attached_task):
        res = client.post(f"/api/tasks/{attached_task.task_id}/cancel", headers=auth)
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert attached_task.status_value == "cancelled"

    def test_pause_then_resume(self, client, auth, attached_task):
        p = client.post(f"/api/tasks/{attached_task.task_id}/pause", headers=auth)
        assert p.status_code == 200
        r = client.post(f"/api/tasks/{attached_task.task_id}/resume", headers=auth)
        assert r.status_code == 200
        assert attached_task.status_value == "running"

    def test_event_stream_404_for_unknown_task(self, client, auth):
        res = client.get("/api/tasks/unknown/events", headers=auth)
        assert res.status_code == 404

    @staticmethod
    async def _drain(gen, *, max_frames: int) -> list[bytes]:
        frames: list[bytes] = []
        while len(frames) < max_frames:
            try:
                frames.append(await asyncio.wait_for(gen.__anext__(), timeout=2.0))
            except (StopAsyncIteration, asyncio.TimeoutError):
                break
        return frames

    def test_event_stream_yields_initial_snapshot(self, attached_task):
        async def _run():
            gen = _task_event_stream(task_id=attached_task.task_id)
            try:
                frames = await self._drain(gen, max_frames=1)
                assert frames, "expected bootstrap frame"
                text = frames[0].decode("utf-8")
                assert "event: snapshot" in text
                assert attached_task.task_id in text
            finally:
                await gen.aclose()

        asyncio.run(_run())

    def test_global_stream_includes_subsequent_events(self, attached_task):
        async def _run():
            gen = _task_event_stream(task_id=None)
            try:
                frames = await self._drain(gen, max_frames=1)  # bootstrap
                assert frames, "expected bootstrap frame"

                emit_task_event(
                    task_event=TASK_EVENT_HEARTBEAT,
                    task_id=attached_task.task_id,
                    payload={"snapshot": {"progress": 0.5}},
                )
                emit_task_event(
                    task_event=TASK_EVENT_STEP,
                    task_id=attached_task.task_id,
                    payload={"step": {"index": 1, "description": "Compiling"}},
                )

                events = await self._drain(gen, max_frames=2)
                assert len(events) == 2
                text_all = "".join(f.decode("utf-8") for f in events)
                assert "event: task.heartbeat" in text_all
                assert "event: task.step" in text_all
                assert "Compiling" in text_all
            finally:
                await gen.aclose()

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. Phase 8 Debug Panel Endpoints Tests (formerly test_debug_panel.py)
# ---------------------------------------------------------------------------

class TestDebugPanelClass:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from web.app import create_app

        monkeypatch.setenv("PYBOT_API_KEYS", "dev-key:*")
        paths = ProjectPaths.from_root(tmp_path)
        paths.ensure_runtime_dirs()
        app = create_app(paths=paths)
        return TestClient(app, headers={"Authorization": "Bearer dev-key"})

    def test_get_cost_summary(self, client):
        resp = client.get("/api/debug/cost")
        assert resp.status_code == 200
        data = resp.json()
        assert "cost_summary" in data

    def test_get_tasks(self, client):
        resp = client.get("/api/debug/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "summary" in data

    def test_get_mcp_status(self, client):
        resp = client.get("/api/debug/mcp")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data

    def test_get_memory_status(self, client):
        resp = client.get("/api/debug/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert "memory" in data

    def test_get_rag_status(self, client):
        resp = client.get("/api/debug/rag")
        assert resp.status_code == 200
        data = resp.json()
        assert "rag" in data

    def test_get_providers(self, client):
        resp = client.get("/api/debug/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "openai" in data["providers"]


class TestTraceEndpoints:
    def test_global_trace_returns_event_list(self, client):
        from core.systems.runtime.event_bus import Event, EventType, event_bus

        event_bus.emit(
            Event(
                type=EventType.TOOL_CALL,
                source="test.trace",
                payload={"tool": "grep"},
                session_id="trace-thread",
            )
        )
        resp = client.get("/api/trace/global?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert "events" in body
        assert "type_counts" in body

    def test_conversation_trace_returns_payload(self, client):
        resp = client.get("/api/conversations/demo-thread/trace?limit=5")
        assert resp.status_code == 200
        body = resp.json()
        assert body["thread_id"] == "demo-thread"
        assert "events" in body
        assert body["scope"] in {"session", "global"}


# ---------------------------------------------------------------------------
# 5. Session Ephemeral Event Queue Tests (formerly test_session_events.py)
# ---------------------------------------------------------------------------

class TestSessionEventQueueClass:
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
