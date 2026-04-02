from __future__ import annotations

from fastapi.testclient import TestClient

import api_server
from core.assets.agents.agent_storage import AgentDefinition


def create_api_client(temp_paths, monkeypatch, *, stub_invoke: bool = True):
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
    return app, TestClient(app)


def test_api_lists_agents(temp_paths, monkeypatch):
    _, client = create_api_client(temp_paths, monkeypatch)
    with client:
        response = client.get("/api/v1/agents")
        assert response.status_code == 200
        assert response.json() == {"success": True, "count": 0, "agents": {}}


def test_api_root_landing_page(temp_paths, monkeypatch):
    _, client = create_api_client(temp_paths, monkeypatch)
    with client:
        response = client.get("/")
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "PyBot API"
        assert payload["status"] == "ok"
        assert payload["docs_url"] == "/docs"
        assert payload["agents_url"] == "/api/v1/agents"


def test_api_health_endpoint(temp_paths, monkeypatch):
    _, client = create_api_client(temp_paths, monkeypatch)
    with client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["service"] == "api"


def test_api_favicon_returns_empty_response(temp_paths, monkeypatch):
    _, client = create_api_client(temp_paths, monkeypatch)
    with client:
        response = client.get("/favicon.ico")
        assert response.status_code == 204
        assert response.content == b""


def test_api_returns_404_for_unknown_agent(temp_paths, monkeypatch):
    _, client = create_api_client(temp_paths, monkeypatch, stub_invoke=False)
    with client:
        response = client.post(
            "/api/v1/agents/missing/chat",
            json={"message": "hi", "context": "", "thread_id": "t-1"},
        )
        assert response.status_code == 404
        assert "不存在" in response.json()["detail"]


def test_api_chats_with_agent(temp_paths, monkeypatch):
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


def test_api_hides_internal_errors(temp_paths, monkeypatch):
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
