"""Tests for Phase 8 debug panel API endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.project_paths import ProjectPaths


@pytest.fixture
def client(tmp_path):
    from web.app import create_app

    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_runtime_dirs()
    app = create_app(paths=paths)
    return TestClient(app, headers={"Authorization": "Bearer dev-key"})


class TestDebugCostEndpoint:
    def test_get_cost_summary(self, client):
        resp = client.get("/api/debug/cost")
        assert resp.status_code == 200
        data = resp.json()
        assert "cost_summary" in data


class TestDebugTasksEndpoint:
    def test_get_tasks(self, client):
        resp = client.get("/api/debug/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "summary" in data


class TestDebugMCPEndpoint:
    def test_get_mcp_status(self, client):
        resp = client.get("/api/debug/mcp")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data


class TestDebugMemoryEndpoint:
    def test_get_memory_status(self, client):
        resp = client.get("/api/debug/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert "memory" in data


class TestDebugRAGEndpoint:
    def test_get_rag_status(self, client):
        resp = client.get("/api/debug/rag")
        assert resp.status_code == 200
        data = resp.json()
        assert "rag" in data


class TestDebugProvidersEndpoint:
    def test_get_providers(self, client):
        resp = client.get("/api/debug/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "openai" in data["providers"]
