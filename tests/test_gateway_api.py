from __future__ import annotations

import threading
import time
from typing import Any

from web.routers import gateway as gateway_router


class _FakeGatewayAgent:
    def __init__(self, mode: str):
        self.mode = mode
        self.last_prompt = ""

    def chat(self, message: str) -> str:
        self.last_prompt = message
        return f"[{self.mode}] {message}"

    def chat_stream(self, message: str):
        self.last_prompt = message
        yield {"type": "step", "content": f"working:{self.mode}", "icon": "ℹ️"}
        yield {"type": "done", "content": f"[{self.mode}] {message}"}


def _receive_until(
    websocket,
    *,
    event: str | None = None,
    request_id: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    for _ in range(limit):
        frame = websocket.receive_json()
        if event is not None:
            if frame.get("type") == "event" and frame.get("event") == event:
                return frame
            continue
        if request_id is not None:
            if frame.get("type") == "res" and frame.get("id") == request_id:
                return frame
            continue
        return frame
    raise AssertionError(f"Did not receive expected gateway frame event={event!r} request_id={request_id!r}")


def test_gateway_models_endpoint_lists_pybot_modes(client):
    response = client.get("/v1/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    model_ids = [item["id"] for item in payload["data"]]
    assert model_ids == ["pybot:assistant", "pybot:app_matrix", "pybot:admin"]


def test_gateway_responses_endpoint_returns_openresponses_shape_and_mode(client, monkeypatch):
    created: list[tuple[str, str, _FakeGatewayAgent]] = []

    def fake_get_or_create_mode(mode: str, thread_id: str) -> _FakeGatewayAgent:
        agent = _FakeGatewayAgent(mode)
        created.append((mode, thread_id, agent))
        return agent

    monkeypatch.setattr(client.app.state.services.agents, "get_or_create_mode", fake_get_or_create_mode)

    response = client.post(
        "/v1/responses",
        json={
            "model": "pybot:app_matrix",
            "instructions": "Prefer orchestration-first plans.",
            "input": "Connect sales and support apps",
            "user": "alice",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "response"
    assert payload["status"] == "completed"
    assert payload["output"][0]["role"] == "assistant"
    assert payload["output_text"].startswith("[app_matrix]")
    assert payload["pybot"]["mode"] == "app_matrix"
    assert payload["pybot"]["run_id"] == payload["id"]
    assert payload["pybot"]["session_key"].startswith("user-")
    assert payload["pybot"]["thread_id"].startswith("gateway-app_matrix-user-")
    assert created[0][0] == "app_matrix"
    assert "Operator instructions" in created[0][2].last_prompt


def test_gateway_responses_endpoint_accepts_explicit_session_header(client, monkeypatch):
    created: list[tuple[str, str]] = []

    def fake_get_or_create_mode(mode: str, thread_id: str) -> _FakeGatewayAgent:
        created.append((mode, thread_id))
        return _FakeGatewayAgent(mode)

    monkeypatch.setattr(client.app.state.services.agents, "get_or_create_mode", fake_get_or_create_mode)

    response = client.post(
        "/v1/responses",
        headers={"x-openclaw-session-key": "demo/operator session"},
        json={"input": "hello"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pybot"]["session_key"] == "demo-operator-session"
    assert created[0][1] == "gateway-assistant-demo-operator-session"


def test_gateway_responses_endpoint_honors_bearer_auth(client, monkeypatch):
    monkeypatch.setattr(
        gateway_router,
        "get_gateway_config",
        lambda: {
            "auth": {"mode": "token", "token": "secret-token", "password": None},
            "http": {
                "endpoints": {"responses": {"enabled": True, "stream_enabled": True}, "models": {"enabled": True}}
            },
        },
    )
    monkeypatch.setattr(
        client.app.state.services.agents,
        "get_or_create_mode",
        lambda mode, thread_id: _FakeGatewayAgent(mode),
    )

    unauthorized = client.post("/v1/responses", headers={"Authorization": "Bearer wrong-token"}, json={"input": "hello"})
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer secret-token"},
        json={"input": "hello"},
    )
    assert authorized.status_code == 200


def test_gateway_responses_stream_returns_sse_events(client, monkeypatch):
    monkeypatch.setattr(
        client.app.state.services.agents,
        "get_or_create_mode",
        lambda mode, thread_id: _FakeGatewayAgent(mode),
    )

    with client.stream("POST", "/v1/responses", json={"input": "stream hello", "stream": True}) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: response.created" in body
    assert "event: pybot.step" in body
    assert "event: response.output_text.delta" in body
    assert "event: response.completed" in body
    assert "data: [DONE]" in body


def test_gateway_responses_client_tool_loop_returns_incomplete_function_call(client, monkeypatch):
    monkeypatch.setattr(
        client.app.state.services.agents,
        "get_or_create_mode",
        lambda mode, thread_id: (_ for _ in ()).throw(AssertionError("agent should not run before tool output")),
    )

    response = client.post(
        "/v1/responses",
        json={
            "input": "查一下北京天气",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_weather",
                        "description": "Look up current weather",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            "tool_choice": "required",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "incomplete"
    assert payload["output"][0]["type"] == "function_call"
    assert payload["output"][0]["name"] == "lookup_weather"
    assert payload["required_action"]["submit_tool_outputs"]["tool_calls"][0]["name"] == "lookup_weather"


def test_gateway_responses_client_tool_loop_streams_function_call_events(client, monkeypatch):
    monkeypatch.setattr(
        client.app.state.services.agents,
        "get_or_create_mode",
        lambda mode, thread_id: (_ for _ in ()).throw(AssertionError("agent should not run before tool output")),
    )

    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "input": "查一下北京天气",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_weather",
                        "description": "Look up current weather",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            "tool_choice": "required",
            "stream": True,
        },
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: response.created" in body
    assert "event: response.in_progress" in body
    assert "event: response.output_item.added" in body
    assert "event: response.output_item.done" in body
    assert "event: response.completed" in body
    assert "lookup_weather" in body


def test_gateway_responses_accepts_function_call_output_followup(client, monkeypatch):
    created: list[tuple[str, str, _FakeGatewayAgent]] = []

    def fake_get_or_create_mode(mode: str, thread_id: str) -> _FakeGatewayAgent:
        agent = _FakeGatewayAgent(mode)
        created.append((mode, thread_id, agent))
        return agent

    monkeypatch.setattr(client.app.state.services.agents, "get_or_create_mode", fake_get_or_create_mode)

    response = client.post(
        "/v1/responses",
        headers={"x-openclaw-session-key": "tool-session"},
        json={
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "总结一下"}],
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_123",
                    "output": '{"weather":"sunny"}',
                },
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert "TOOL[call_123]" in created[0][2].last_prompt


def test_gateway_responses_previous_response_id_reuses_prior_session(client, monkeypatch):
    created: list[tuple[str, str]] = []

    def fake_get_or_create_mode(mode: str, thread_id: str) -> _FakeGatewayAgent:
        created.append((mode, thread_id))
        return _FakeGatewayAgent(mode)

    monkeypatch.setattr(client.app.state.services.agents, "get_or_create_mode", fake_get_or_create_mode)

    first = client.post(
        "/v1/responses",
        headers={"x-openclaw-session-key": "carry-session"},
        json={"input": "first turn"},
    )
    assert first.status_code == 200
    first_payload = first.json()

    second = client.post(
        "/v1/responses",
        json={
            "input": "second turn",
            "previous_response_id": first_payload["id"],
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["pybot"]["session_key"] == "carry-session"
    assert second_payload["pybot"]["thread_id"] == first_payload["pybot"]["thread_id"]
    assert "previous_response_id" not in second_payload["pybot"]["ignored_features"]
    assert second_payload["metadata"]["continued_from_response_id"] == first_payload["id"]


def test_gateway_status_reports_channels_and_auth_mode(client, monkeypatch):
    class _FakeChannelManager:
        def list_channels(self) -> dict[str, Any]:
            return {"wechat": object(), "wecom": object()}

    class _FakeSystemAgent:
        channel_manager = _FakeChannelManager()

    monkeypatch.setattr(client.app.state.services, "system_agent", lambda: _FakeSystemAgent())
    monkeypatch.setattr(
        gateway_router,
        "get_gateway_config",
        lambda: {
            "auth": {"mode": "none", "token": None, "password": None},
            "http": {
                "endpoints": {"responses": {"enabled": True, "stream_enabled": True}, "models": {"enabled": True}}
            },
        },
    )

    response = client.get("/api/gateway/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["responses_enabled"] is True
    assert payload["auth_mode"] == "none"
    assert payload["supported_channels"] == ["wechat", "wecom"]
    assert "pybot:assistant" in payload["supported_models"]
    assert "session_count" in payload


def test_gateway_session_routes_surface_history(client):
    services = client.app.state.services
    services.gateway_runtime.sessions.touch(
        "demo-user",
        mode="assistant",
        thread_id="gateway-assistant-demo-user",
        source="test",
        user="demo",
        device_id="device-1",
        client_id="client-1",
    )
    services.conversations.ensure_conversation("gateway-assistant-demo-user", title_hint="demo")
    services.conversations.append_message("gateway-assistant-demo-user", "user", "hello")
    services.conversations.append_message("gateway-assistant-demo-user", "assistant", "hi there")

    listed = client.get("/api/gateway/sessions")
    assert listed.status_code == 200
    sessions = listed.json()["sessions"]
    matched = next(item for item in sessions if item["session_key"] == "demo-user")
    assert matched["mode"] == "assistant"
    assert matched["active_connections"] == 0
    assert matched["device_ids"] == ["device-1"]

    detail = client.get("/api/gateway/sessions/demo-user")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["thread_id"] == "gateway-assistant-demo-user"
    assert payload["session_meta"]["user"] == "demo"
    assert payload["presence"] == []
    assert [item["role"] for item in payload["history"]] == ["user", "assistant"]


def test_gateway_channel_routes_and_nodes_routes(client, monkeypatch):
    class _FakeRouteManager:
        def set_route_callback(self, callback):  # noqa: ANN001
            self.callback = callback

        def list_routes(self):
            return [{"name": "wechat-app-matrix", "channel": "wechat", "mode": "app_matrix"}]

        def preview_route(self, channel_name: str, payload: dict[str, Any]):
            return {
                "matched": True,
                "message": payload,
                "decision": {
                    "rule_name": "wechat-app-matrix",
                    "target": "agent",
                    "mode": "app_matrix",
                    "thread_id": "wechat:user_1",
                    "workflow_name": "",
                    "metadata": {"channel": channel_name},
                },
            }

        def list_channels(self):
            return ["wechat"]

        def get_channel(self, name: str):
            return None

    class _FakeSystemAgent:
        channel_manager = _FakeRouteManager()

    monkeypatch.setattr(client.app.state.services, "system_agent", lambda: _FakeSystemAgent())
    monkeypatch.setattr(
        client.app.state.services.agents,
        "get_or_create_mode",
        lambda mode, thread_id: _FakeGatewayAgent(mode),
    )

    routes = client.get("/api/gateway/channel-routes")
    assert routes.status_code == 200
    assert routes.json()["routes"][0]["name"] == "wechat-app-matrix"

    preview = client.post(
        "/api/gateway/channel-routes/preview",
        json={"channel_name": "wechat", "payload": {"user_id": "user_1", "message": "hi", "thread_id": "t1"}},
    )
    assert preview.status_code == 200
    assert preview.json()["decision"]["mode"] == "app_matrix"

    with client.websocket_connect("/gateway/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "req",
                "id": "conn-node",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "role": "operator",
                    "scopes": ["operator.read"],
                    "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                    "device": {"id": "node-alpha"},
                    "sessionKey": "node-session",
                },
            }
        )
        hello = websocket.receive_json()
        assert hello["ok"] is True
        assert hello["payload"]["snapshot"]["nodes"]["items"][0]["device_id"] == "node-alpha"

        websocket.send_json({"type": "req", "id": "nodes-1", "method": "nodes.list", "params": {}})
        nodes = websocket.receive_json()
        assert nodes["ok"] is True
        assert any(item["device_id"] == "node-alpha" for item in nodes["payload"]["nodes"])

    node_list = client.get("/api/gateway/nodes")
    assert node_list.status_code == 200
    assert any(item["device_id"] == "node-alpha" for item in node_list.json()["nodes"])

    node_detail = client.get("/api/gateway/nodes/node-alpha")
    assert node_detail.status_code == 200
    assert node_detail.json()["node"]["device_id"] == "node-alpha"


def test_gateway_node_invoke_rest_and_pending_ack_routes(client):
    services = client.app.state.services
    services.gateway_runtime.nodes.touch_from_presence(
        gateway_router._build_presence_entry(  # type: ignore[attr-defined]
            connection_id="gw-node-rest",
            params={
                "role": "node",
                "client": {"id": "pytest-node", "version": "1.0", "platform": "test", "mode": "node"},
                "device": {"id": "node-rest"},
                "commands": ["sync.todo"],
            },
            session_key="node-rest-session",
        ),
        approved=True,
    )

    invoked = client.post(
        "/api/gateway/nodes/node-rest/invoke",
        json={
            "command": "sync.todo",
            "payload": {"task": "ship"},
            "idempotency_key": "idem-rest-1",
        },
    )
    assert invoked.status_code == 200
    command = invoked.json()["command"]
    assert command["command"] == "sync.todo"
    assert command["status"] == "pending"

    pending = client.get("/api/gateway/nodes/node-rest/pending")
    assert pending.status_code == 200
    assert pending.json()["pending"][0]["command_id"] == command["command_id"]

    acked = client.post(
        f"/api/gateway/nodes/node-rest/pending/{command['command_id']}/ack",
        json={"status": "completed", "result": {"ok": True}},
    )
    assert acked.status_code == 200
    assert acked.json()["command"]["status"] == "completed"
    assert acked.json()["command"]["result"] == {"ok": True}


def test_gateway_websocket_node_invoke_pull_and_ack(client):
    with client.websocket_connect("/gateway/ws") as operator_ws:
        operator_ws.receive_json()
        operator_ws.send_json(
            {
                "type": "req",
                "id": "operator-connect",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "role": "operator",
                    "scopes": ["operator.read", "operator.write"],
                    "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                    "device": {"id": "operator-node-control"},
                    "sessionKey": "operator-node-session",
                },
            }
        )
        operator_hello = operator_ws.receive_json()
        assert operator_hello["ok"] is True

        with client.websocket_connect("/gateway/ws") as node_ws:
            node_ws.receive_json()
            node_ws.send_json(
                {
                    "type": "req",
                    "id": "node-connect",
                    "method": "connect",
                    "params": {
                        "minProtocol": 3,
                        "maxProtocol": 3,
                        "role": "node",
                        "client": {"id": "pytest-node", "version": "1.0", "platform": "test", "mode": "node"},
                        "device": {"id": "node-ws"},
                        "sessionKey": "node-ws-session",
                        "commands": ["sync.todo"],
                    },
                }
            )
            node_hello = node_ws.receive_json()
            assert node_hello["ok"] is True

            operator_ws.send_json(
                {
                    "type": "req",
                    "id": "invoke-1",
                    "method": "node.invoke",
                    "params": {
                        "device_id": "node-ws",
                        "command": "sync.todo",
                        "payload": {"task": "follow up"},
                        "idempotency_key": "idem-ws-1",
                    },
                }
            )
            invoke_response = _receive_until(operator_ws, request_id="invoke-1")
            assert invoke_response["ok"] is True
            command_id = invoke_response["payload"]["command"]["command_id"]

            node_ws.send_json({"type": "req", "id": "pull-1", "method": "node.pending.pull", "params": {}})
            pulled = _receive_until(node_ws, request_id="pull-1")
            assert pulled["ok"] is True
            assert pulled["payload"]["commands"][0]["command_id"] == command_id
            assert pulled["payload"]["commands"][0]["status"] == "dispatched"

            node_ws.send_json(
                {
                    "type": "req",
                    "id": "ack-1",
                    "method": "node.pending.ack",
                    "params": {
                        "command_id": command_id,
                        "status": "completed",
                        "result": {"done": True},
                    },
                }
            )
            acked = _receive_until(node_ws, request_id="ack-1")
            assert acked["ok"] is True
            assert acked["payload"]["command"]["status"] == "completed"

            updated = _receive_until(operator_ws, event="node.command.updated")
            assert updated["payload"]["command"]["command_id"] == command_id


def test_gateway_channels_tools_and_approvals_routes(client, monkeypatch):
    class _FakeChannelConfig:
        name = "wechat"
        kind = "wechat"
        enabled = True
        reply_mode = "passive"
        token = "secret"
        app_id = "appid"
        app_secret = "appsecret"
        corp_id = None
        agent_id = None
        secret = None
        encoding_aes_key = None
        api_base = None
        extra = {"region": "cn"}

    class _FakeChannel:
        config = _FakeChannelConfig()

    class _FakeChannelManager:
        def list_channels(self):
            return ["wechat"]

        def get_channel(self, name: str):
            return _FakeChannel() if name == "wechat" else None

    class _FakeSystemAgent:
        channel_manager = _FakeChannelManager()

        def list_tools(self):
            return {"search_web": "Search the web"}

        def get_mode_profile(self):
            return {"name": "assistant"}

    services = client.app.state.services
    services.approval_queue.create_request(
        kind="tool_approval",
        scope="gateway",
        summary="Need approval",
        prompt="Allow tool call?",
    )
    monkeypatch.setattr(services, "system_agent", lambda: _FakeSystemAgent())

    channels = client.get("/api/gateway/channels")
    assert channels.status_code == 200
    channel_payload = channels.json()["channels"][0]
    assert channel_payload["name"] == "wechat"
    assert channel_payload["config"]["has_token"] is True
    assert channel_payload["config"]["extra_keys"] == ["region"]

    tools = client.get("/api/gateway/tools/catalog")
    assert tools.status_code == 200
    assert tools.json()["tools"] == {"search_web": "Search the web"}

    approvals = client.get("/api/gateway/approvals")
    assert approvals.status_code == 200
    assert approvals.json()["counts"]["pending"] >= 1


def test_gateway_websocket_connect_presence_and_chat_send(client, monkeypatch):
    monkeypatch.setattr(
        client.app.state.services.agents,
        "get_or_create_mode",
        lambda mode, thread_id: _FakeGatewayAgent(mode),
    )
    with client.websocket_connect("/gateway/ws") as websocket:
        challenge = websocket.receive_json()
        assert challenge["event"] == "connect.challenge"
        websocket.send_json(
            {
                "type": "req",
                "id": "conn-1",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "role": "operator",
                    "scopes": ["operator.read", "operator.write"],
                    "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                    "device": {"id": "device-alpha"},
                    "sessionKey": "alpha-session",
                    "userAgent": "pytest",
                },
            }
        )
        hello = websocket.receive_json()
        assert hello["ok"] is True
        assert hello["payload"]["protocol"] == 3
        assert hello["payload"]["connection"]["sessionKey"] == "alpha-session"
        assert "tick" in hello["payload"]["features"]["events"]
        assert "run.updated" in hello["payload"]["features"]["events"]
        assert "device.pair.approve" in hello["payload"]["features"]["operatorMethods"]
        assert "runs.abort" in hello["payload"]["features"]["operatorMethods"]
        assert "chat.abort" in hello["payload"]["features"]["methods"]
        assert hello["payload"]["snapshot"]["presence"]["items"][0]["device_id"] == "device-alpha"
        assert hello["payload"]["snapshot"]["sessions"]["items"][0]["session_key"] == "alpha-session"
        assert hello["payload"]["snapshot"]["runs"]["items"] == []

        websocket.send_json({"type": "req", "id": "presence-1", "method": "system.presence", "params": {}})
        presence = websocket.receive_json()
        assert presence["ok"] is True
        assert presence["payload"]["items"][0]["device_id"] == "device-alpha"
        assert presence["payload"]["presence"][0]["device_id"] == "device-alpha"
        assert "last_seen_at" in presence["payload"]["items"][0]

        websocket.send_json({"type": "req", "id": "ping-1", "method": "ping", "params": {}})
        ping = websocket.receive_json()
        assert ping["ok"] is True
        assert ping["payload"]["pong"] is True

        websocket.send_json(
            {
                "type": "req",
                "id": "chat-1",
                "method": "chat.send",
                "params": {"message": "hello from ws", "model": "pybot:assistant"},
            }
        )
        chat = _receive_until(websocket, request_id="chat-1")
        assert chat["ok"] is True
        assert chat["payload"]["object"] == "response"
        assert chat["payload"]["output_text"]
        assert chat["payload"]["pybot"]["run_id"] == chat["payload"]["id"]

        websocket.send_json(
            {
                "type": "req",
                "id": "inject-1",
                "method": "chat.inject",
                "params": {"message": "operator note", "role": "system", "session_key": "alpha-session"},
            }
        )
        injected = _receive_until(websocket, request_id="inject-1")
        assert injected["ok"] is True
        assert injected["payload"]["injected"] == 1

        websocket.send_json(
            {
                "type": "req",
                "id": "history-1",
                "method": "chat.history",
                "params": {"session_key": "alpha-session"},
            }
        )
        history = _receive_until(websocket, request_id="history-1")
        assert history["ok"] is True
        assert any(item["content"] == "operator note" for item in history["payload"]["history"])

    session_detail = client.get("/api/gateway/sessions/alpha-session")
    assert session_detail.status_code == 200
    session_payload = session_detail.json()
    assert session_payload["session_meta"]["last_source"] == "ws.chat"
    assert session_payload["session_meta"]["device_ids"] == ["device-alpha"]
    assert session_payload["latest_run"]["run_id"].startswith("resp_")


def test_gateway_run_and_response_lookup_routes(client, monkeypatch):
    monkeypatch.setattr(
        client.app.state.services.agents,
        "get_or_create_mode",
        lambda mode, thread_id: _FakeGatewayAgent(mode),
    )

    response = client.post(
        "/v1/responses",
        headers={"x-openclaw-session-key": "lookup-session"},
        json={"input": "hello lookup"},
    )
    assert response.status_code == 200
    payload = response.json()
    run_id = payload["pybot"]["run_id"]

    fetched = client.get(f"/v1/responses/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == run_id

    run_detail = client.get(f"/api/gateway/runs/{run_id}")
    assert run_detail.status_code == 200
    run_payload = run_detail.json()
    assert run_payload["run"]["run_id"] == run_id
    assert run_payload["response"]["id"] == run_id

    runs = client.get("/api/gateway/runs")
    assert runs.status_code == 200
    assert any(item["run_id"] == run_id for item in runs.json()["items"])

    cancelled = client.delete(f"/v1/responses/{run_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["id"] == run_id


def test_gateway_session_inject_and_abort_routes(client):
    services = client.app.state.services
    services.gateway_runtime.runs.start(
        run_id="resp_manual",
        response_id="resp_manual",
        session_key="manual-session",
        thread_id="gateway-assistant-manual-session",
        mode="assistant",
        requested_model="pybot:assistant",
        source="test",
        display_input="manual input",
    )

    injected = client.post(
        "/api/gateway/sessions/manual-session/inject",
        json={"message": "operator memo", "role": "system"},
    )
    assert injected.status_code == 200
    assert injected.json()["injected"] == 1

    detail = client.get("/api/gateway/sessions/manual-session")
    assert detail.status_code == 200
    assert any(item["content"] == "operator memo" for item in detail.json()["history"])

    aborted = client.post(
        "/api/gateway/sessions/manual-session/abort",
        json={"note": "stop session run"},
    )
    assert aborted.status_code == 200
    assert aborted.json()["run"]["status"] == "cancelling"


def test_gateway_websocket_runs_methods_and_chat_abort(client):
    services = client.app.state.services
    services.gateway_runtime.runs.start(
        run_id="resp_ws_abort",
        response_id="resp_ws_abort",
        session_key="ws-abort-session",
        thread_id="gateway-assistant-ws-abort-session",
        mode="assistant",
        requested_model="pybot:assistant",
        source="test",
        display_input="pending message",
    )

    with client.websocket_connect("/gateway/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "req",
                "id": "conn-1",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "role": "operator",
                    "scopes": ["operator.read", "operator.write"],
                    "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                    "device": {"id": "device-runs"},
                    "sessionKey": "operator-runs",
                },
            }
        )
        hello = websocket.receive_json()
        assert hello["ok"] is True
        assert any(item["run_id"] == "resp_ws_abort" for item in hello["payload"]["snapshot"]["runs"]["items"])

        websocket.send_json({"type": "req", "id": "runs-1", "method": "runs.list", "params": {}})
        runs = websocket.receive_json()
        assert runs["ok"] is True
        assert any(item["run_id"] == "resp_ws_abort" for item in runs["payload"]["items"])

        websocket.send_json(
            {"type": "req", "id": "runs-2", "method": "runs.get", "params": {"run_id": "resp_ws_abort"}}
        )
        run_detail = websocket.receive_json()
        assert run_detail["ok"] is True
        assert run_detail["payload"]["run"]["run_id"] == "resp_ws_abort"

        websocket.send_json(
            {
                "type": "req",
                "id": "abort-1",
                "method": "chat.abort",
                "params": {"run_id": "resp_ws_abort", "note": "operator stop"},
            }
        )
        abort_result = websocket.receive_json()
        assert abort_result["ok"] is True
        assert abort_result["payload"]["run"]["status"] == "cancelling"

        run_updated = _receive_until(websocket, event="run.updated")
        assert run_updated["payload"]["run"]["run_id"] == "resp_ws_abort"
        assert run_updated["payload"]["run"]["abort_requested"] is True


def test_gateway_response_cancel_can_mark_inflight_run_cancelled(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    result: dict[str, Any] = {}

    class _SlowGatewayAgent(_FakeGatewayAgent):
        def chat(self, message: str) -> str:
            self.last_prompt = message
            started.set()
            release.wait(timeout=5)
            return f"[{self.mode}] {message}"

    monkeypatch.setattr(
        client.app.state.services.agents,
        "get_or_create_mode",
        lambda mode, thread_id: _SlowGatewayAgent(mode),
    )

    def _invoke_response() -> None:
        response = client.post(
            "/v1/responses",
            headers={"x-openclaw-session-key": "cancel-session"},
            json={"input": "long running"},
        )
        result["status_code"] = response.status_code
        result["json"] = response.json()

    worker = threading.Thread(target=_invoke_response, daemon=True)
    worker.start()
    assert started.wait(timeout=5)

    services = client.app.state.services
    deadline = time.time() + 5
    active_run_id = ""
    while time.time() < deadline:
        record = services.gateway_runtime.runs.latest_active_for_session("cancel-session")
        if record is not None:
            active_run_id = record.run_id
            break
        time.sleep(0.05)
    assert active_run_id

    cancelled = client.post(f"/v1/responses/{active_run_id}/cancel")
    assert cancelled.status_code == 200

    release.set()
    worker.join(timeout=5)
    assert result["status_code"] == 200
    assert result["json"]["status"] == "cancelled"
    assert result["json"]["id"] == active_run_id


def test_gateway_websocket_pairing_flow(client, monkeypatch):
    monkeypatch.setattr(
        gateway_router,
        "get_gateway_config",
        lambda: {
            "auth": {"mode": "none", "token": None, "password": None},
            "ws": {
                "enabled": True,
                "protocol_version": 3,
                "tick_interval_ms": 15000,
                "require_device_id": True,
            },
            "pairing": {"enabled": True, "auto_approve_local": False},
            "http": {
                "endpoints": {
                    "responses": {"enabled": True, "stream_enabled": True},
                    "models": {"enabled": True},
                }
            },
        },
    )

    with client.websocket_connect("/gateway/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "req",
                "id": "conn-1",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "role": "operator",
                    "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                    "device": {"id": "device-pairing"},
                },
            }
        )
        error = websocket.receive_json()
        assert error["ok"] is False
        assert error["error"]["code"] == "PAIRING_REQUIRED"
        assert error["error"]["details"]["reason"] == "device-not-approved"
        assert error["error"]["details"]["requestId"].startswith("pair_")

    listed = client.get("/api/gateway/pairings")
    assert listed.status_code == 200
    assert listed.json()["pending"][0]["device_id"] == "device-pairing"
    assert listed.json()["pending"][0]["request_id"].startswith("pair_")

    approved = client.post("/api/gateway/pairings/device-pairing/approve")
    assert approved.status_code == 200
    assert approved.json()["pairing"]["status"] == "approved"

    with client.websocket_connect("/gateway/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "req",
                "id": "conn-2",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "role": "operator",
                    "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                    "device": {"id": "device-pairing"},
                },
            }
        )
        hello = websocket.receive_json()
        assert hello["ok"] is True

        websocket.send_json({"type": "req", "id": "pairings-1", "method": "device.pair.list", "params": {}})
        pairings = websocket.receive_json()
        assert pairings["ok"] is True
        assert any(item["device_id"] == "device-pairing" for item in pairings["payload"]["approved"])
        assert any(item["request_id"].startswith("pair_") for item in pairings["payload"]["approved"])


def test_gateway_websocket_can_require_paired_device_token(client, monkeypatch):
    monkeypatch.setattr(
        gateway_router,
        "get_gateway_config",
        lambda: {
            "auth": {"mode": "none", "token": None, "password": None},
            "ws": {
                "enabled": True,
                "protocol_version": 3,
                "tick_interval_ms": 15000,
                "require_device_id": True,
                "require_paired_device_token": True,
            },
            "pairing": {"enabled": True, "auto_approve_local": False},
            "http": {
                "endpoints": {
                    "responses": {"enabled": True, "stream_enabled": True},
                    "models": {"enabled": True},
                }
            },
        },
    )
    services = client.app.state.services
    services.gateway_runtime.pairings.ensure_request(
        device_id="device-token",
        role="operator",
        client_id="pytest-device",
        scopes=["operator.read"],
        platform="test",
        mode="operator",
        user_agent="pytest",
    )
    pairing = services.gateway_runtime.pairings.approve("device-token", approved_by="setup")
    assert pairing is not None
    assert pairing.device_token

    with client.websocket_connect("/gateway/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "req",
                "id": "conn-missing-token",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "role": "operator",
                    "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                    "device": {"id": "device-token"},
                },
            }
        )
        denied = websocket.receive_json()
        assert denied["ok"] is False
        assert denied["error"]["code"] == "DEVICE_TOKEN_REQUIRED"

    with client.websocket_connect("/gateway/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "req",
                "id": "conn-valid-token",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "role": "operator",
                    "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                    "device": {"id": "device-token"},
                    "auth": {"device_token": pairing.device_token},
                },
            }
        )
        hello = websocket.receive_json()
        assert hello["ok"] is True
        assert hello["payload"]["policy"]["pairedDeviceTokenRequired"] is True


def test_gateway_websocket_operator_receives_pairing_and_presence_events(client, monkeypatch):
    monkeypatch.setattr(
        gateway_router,
        "get_gateway_config",
        lambda: {
            "auth": {"mode": "none", "token": None, "password": None},
            "ws": {
                "enabled": True,
                "protocol_version": 3,
                "tick_interval_ms": 15000,
                "require_device_id": True,
            },
            "pairing": {"enabled": True, "auto_approve_local": False},
            "http": {
                "endpoints": {
                    "responses": {"enabled": True, "stream_enabled": True},
                    "models": {"enabled": True},
                }
            },
        },
    )
    services = client.app.state.services
    services.gateway_runtime.pairings.ensure_request(
        device_id="operator-device",
        role="operator",
        client_id="pytest-operator",
        scopes=["operator.read", "operator.pairing"],
        platform="test",
        mode="operator",
        user_agent="pytest",
    )
    services.gateway_runtime.pairings.approve("operator-device", approved_by="setup")

    with client.websocket_connect("/gateway/ws") as operator_ws:
        operator_ws.receive_json()
        operator_ws.send_json(
            {
                "type": "req",
                "id": "operator-connect",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "role": "operator",
                    "scopes": ["operator.read", "operator.pairing"],
                    "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                    "device": {"id": "operator-device"},
                    "sessionKey": "operator-session",
                },
            }
        )
        operator_hello = operator_ws.receive_json()
        assert operator_hello["ok"] is True

        with client.websocket_connect("/gateway/ws") as pending_ws:
            pending_ws.receive_json()
            pending_ws.send_json(
                {
                    "type": "req",
                    "id": "pending-connect",
                    "method": "connect",
                    "params": {
                        "minProtocol": 3,
                        "maxProtocol": 3,
                        "role": "operator",
                        "scopes": ["operator.read"],
                        "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                        "device": {"id": "device-pairing-events"},
                    },
                }
            )
            pending_error = pending_ws.receive_json()
            assert pending_error["ok"] is False
            request_id = pending_error["error"]["details"]["requestId"]

        pairing_requested = _receive_until(operator_ws, event="device.pair.requested")
        assert pairing_requested["payload"]["device_id"] == "device-pairing-events"
        assert pairing_requested["payload"]["request_id"] == request_id

        operator_ws.send_json(
            {
                "type": "req",
                "id": "approve-1",
                "method": "device.pair.approve",
                "params": {"request_id": request_id, "note": "approved from operator ws"},
            }
        )
        approve_response = _receive_until(operator_ws, request_id="approve-1")
        assert approve_response["ok"] is True
        assert approve_response["payload"]["request_id"] == request_id
        assert approve_response["payload"]["status"] == "approved"

        pairing_resolved = _receive_until(operator_ws, event="device.pair.resolved")
        assert pairing_resolved["payload"]["request_id"] == request_id
        assert pairing_resolved["payload"]["status"] == "approved"

        with client.websocket_connect("/gateway/ws") as approved_ws:
            approved_ws.receive_json()
            approved_ws.send_json(
                {
                    "type": "req",
                    "id": "approved-connect",
                    "method": "connect",
                    "params": {
                        "minProtocol": 3,
                        "maxProtocol": 3,
                        "role": "operator",
                        "scopes": ["operator.read"],
                        "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                        "device": {"id": "device-pairing-events"},
                        "sessionKey": "paired-device-session",
                    },
                }
            )
            approved_hello = approved_ws.receive_json()
            assert approved_hello["ok"] is True
            assert any(
                item["request_id"] == request_id
                for item in approved_hello["payload"]["snapshot"]["pairings"]["approved"]
            )

            presence_joined = _receive_until(operator_ws, event="presence")
            joined_devices = {item["device_id"] for item in presence_joined["payload"]["presence"]}
            assert {"operator-device", "device-pairing-events"} <= joined_devices

        presence_left = _receive_until(operator_ws, event="presence")
        left_devices = {item["device_id"] for item in presence_left["payload"]["presence"]}
        assert "operator-device" in left_devices
        assert "device-pairing-events" not in left_devices


def test_gateway_websocket_auth_and_approval_resolution(client, monkeypatch):
    monkeypatch.setattr(
        gateway_router,
        "get_gateway_config",
        lambda: {
            "auth": {"mode": "token", "token": "ws-secret", "password": None},
            "ws": {
                "enabled": True,
                "protocol_version": 3,
                "tick_interval_ms": 15000,
                "require_device_id": True,
            },
            "pairing": {"enabled": False, "auto_approve_local": False},
            "http": {
                "endpoints": {
                    "responses": {"enabled": True, "stream_enabled": True},
                    "models": {"enabled": True},
                }
            },
        },
    )
    approval = client.app.state.services.approval_queue.create_request(
        kind="tool_approval",
        scope="gateway",
        summary="Need approval",
        prompt="approve?",
    )

    with client.websocket_connect("/gateway/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "req",
                "id": "conn-1",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "role": "operator",
                    "auth": {"token": "ws-secret"},
                    "client": {"id": "pytest", "version": "1.0", "platform": "test", "mode": "operator"},
                    "device": {"id": "device-auth"},
                },
            }
        )
        hello = websocket.receive_json()
        assert hello["ok"] is True

        websocket.send_json({"type": "req", "id": "approvals-1", "method": "approvals.list", "params": {}})
        pending = websocket.receive_json()
        assert pending["ok"] is True
        assert any(item["approval_id"] == approval.approval_id for item in pending["payload"]["pending"])

        websocket.send_json(
            {
                "type": "req",
                "id": "resolve-1",
                "method": "exec.approval.resolve",
                "params": {"approval_id": approval.approval_id, "approved": True, "note": "ok"},
            }
        )
        resolved = websocket.receive_json()
        assert resolved["ok"] is True
        assert resolved["payload"]["success"] is True
