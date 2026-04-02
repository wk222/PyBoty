from __future__ import annotations

from collections import Counter
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware

from core.assets.apps.manager import AppManager
from core.channel_manager import ChannelManager
from core.skill_backends import InMemorySkillBackend
from core.skill_http_backend import HttpSkillBackend
from core.skill_registry import SkillRegistry
from core.skill_sources import SkillSource
from core.systems.governance import ApprovalQueue
from core.systems.integration.channel_runtime import ChannelConfig
from core.systems.integration.wechat_channel import WeChatOfficialChannel, _sha1_signature
from core.systems.runtime import UvEnvManager, config_impl, get_pybot_version
from core.systems.runtime.event_bus import Event, EventType
from tests.support.skill_http_server import HttpRouteResponse, serve_skill_http
from web.app import create_app
from web.state import ConversationStore


def _create_service_app(temp_paths):
    return create_app(
        paths=temp_paths,
        llm_config={"model": "gpt-4o-mini", "api_key": "test", "api_base": "https://example.com/v1"},
        control_config={"mode": "balanced"},
    )


def _cors_options(app):
    middleware = next(item for item in app.user_middleware if item.cls is CORSMiddleware)
    return middleware.kwargs


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["version"] == get_pybot_version()
    assert payload["llm_configured"] is True
    assert payload["system_summary"]["root_modes"] == 3
    assert payload["system_summary"]["product_concepts"] == 5


def test_favicon_endpoint_returns_empty_response_when_missing(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 204
    assert response.content == b""


def test_conversation_lifecycle(client):
    created = client.post("/api/conversations", json={"title": "demo"})
    assert created.status_code == 200
    thread_id = created.json()["thread_id"]

    listed = client.get("/api/conversations")
    assert listed.status_code == 200
    assert any(item["thread_id"] == thread_id for item in listed.json()["conversations"])

    history = client.get(f"/api/conversations/{thread_id}/history")
    assert history.status_code == 200
    assert history.json()["messages"] == []

    deleted = client.delete(f"/api/conversations/{thread_id}")
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True


def test_session_endpoints_track_conversations(client):
    created = client.post("/api/conversations", json={"title": "session-demo"})
    assert created.status_code == 200
    session_key = created.json()["session_key"]
    services = client.app.state.services
    services.session_runtime.add_timeline_event(
        thread_id=created.json()["thread_id"],
        session_key=session_key,
        kind="durable_task",
        title="Warm up orchestration",
        status="running",
        source="test",
        preview="Preparing background task",
        root_mode="admin",
    )

    listed = client.get("/api/sessions")
    assert listed.status_code == 200
    assert any(item["session_key"] == session_key for item in listed.json()["sessions"])

    detail = client.get(f"/api/sessions/{session_key}")
    assert detail.status_code == 200
    payload = detail.json()["session"]
    assert payload["thread_id"] == created.json()["thread_id"]
    assert payload["title"] == "session-demo"
    assert payload["timeline"][-1]["kind"] == "durable_task"

    timeline = client.get(f"/api/sessions/{session_key}/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["timeline"][-1]["kind"] == "durable_task"

    events = client.get(f"/api/sessions/{session_key}/events")
    assert events.status_code == 200
    assert any(item["op"] == "timeline_event" for item in events.json()["events"])

    overview = client.get(f"/api/sessions/{session_key}/overview")
    assert overview.status_code == 200
    assert overview.json()["overview"]["counts"]["by_kind"]["durable_task"] == 1


def test_session_file_view_endpoint_returns_recorded_views(client):
    created = client.post("/api/conversations", json={"title": "file-view-demo"})
    assert created.status_code == 200
    session_key = created.json()["session_key"]
    thread_id = created.json()["thread_id"]
    services = client.app.state.services
    services.session_runtime.record_file_view(
        thread_id=thread_id,
        session_key=session_key,
        path="/repo/main.py",
        tool_name="read_file",
        preview="print('hello world')",
        offset=10,
        limit=120,
        is_partial_view=True,
    )

    response = client.get(f"/api/sessions/{session_key}/file-views")

    assert response.status_code == 200
    payload = response.json()
    assert payload["file_views"][-1]["path"] == "/repo/main.py"
    assert payload["file_views"][-1]["is_partial_view"] is True


def test_session_memory_endpoints_update_summary_and_notes(client):
    created = client.post("/api/conversations", json={"title": "memory-demo"})
    session_key = created.json()["session_key"]

    summary = client.post(
        f"/api/sessions/{session_key}/summary",
        json={"summary": "Track the operator's deployment investigation"},
    )
    assert summary.status_code == 200
    assert summary.json()["session"]["working_summary"] == "Track the operator's deployment investigation"

    note = client.post(
        f"/api/sessions/{session_key}/notes",
        json={"note": "User prefers concise updates"},
    )
    assert note.status_code == 200
    assert note.json()["session"]["context_notes"] == ["User prefers concise updates"]

    durable = client.post(
        f"/api/sessions/{session_key}/notes",
        json={
            "note": "User prefers short check-ins",
            "layer": "workspace",
            "memory_type": "user",
            "durable": True,
            "verified": True,
        },
    )
    assert durable.status_code == 200
    entries = durable.json()["session"]["memory_layers"]["workspace"]["entries"]
    assert entries[-1]["memory_type"] == "user"

    invalid = client.post(
        f"/api/sessions/{session_key}/notes",
        json={
            "note": "The repository has workflow_node_runtime.py",
            "layer": "workspace",
            "memory_type": "project",
            "durable": True,
            "occurred_on": "2026-04-02",
        },
    )
    assert invalid.status_code == 400

    compact = client.post(
        f"/api/sessions/{session_key}/compact",
        json={"reason": "manual"},
    )
    assert compact.status_code == 200
    assert compact.json()["session"]["compaction_state"]["last_reason"] == "manual"


def test_session_artifact_kernel_and_checkpoint_endpoints(client):
    created = client.post("/api/conversations", json={"title": "artifact-demo"})
    assert created.status_code == 200
    session_key = created.json()["session_key"]
    thread_id = created.json()["thread_id"]
    services = client.app.state.services

    services.session_runtime.record_file_view(
        thread_id=thread_id,
        session_key=session_key,
        path="/repo/app.py",
        preview="def app():\n    return 'ok'\n",
        offset=0,
        limit=60,
        is_partial_view=False,
    )

    prompt = client.post(
        f"/api/sessions/{session_key}/prompt-injection",
        json={"prompt_injection": "Prefer concise operator-facing status updates."},
    )
    assert prompt.status_code == 200

    durable = client.post(
        f"/api/sessions/{session_key}/notes",
        json={
            "note": "User prefers concise operator-facing status updates",
            "layer": "workspace",
            "memory_type": "user",
            "durable": True,
            "verified": True,
        },
    )
    assert durable.status_code == 200

    artifacts = client.get(f"/api/sessions/{session_key}/artifacts")
    assert artifacts.status_code == 200
    artifact_payload = artifacts.json()["artifacts"]
    assert artifact_payload["system_context"]["prompt_injection"] == "Prefer concise operator-facing status updates."
    assert artifact_payload["file_view_projection"]["view_hashes"]

    kernel = client.get(f"/api/sessions/{session_key}/kernel")
    assert kernel.status_code == 200
    assert kernel.json()["kernel"]["mutable_artifacts"]["prompt_injection"] == (
        "Prefer concise operator-facing status updates."
    )

    sidechains = client.get(f"/api/sessions/{session_key}/sidechains")
    assert sidechains.status_code == 200
    assert any(item["purpose"] == "memory_extraction" for item in sidechains.json()["sidechains"])

    invalidated = client.post(
        f"/api/sessions/{session_key}/artifacts/invalidate",
        json={"reason": "test", "scopes": ["compiled_artifacts", "user_context"]},
    )
    assert invalidated.status_code == 200
    assert invalidated.json()["artifact_version"] >= 1

    rebuilt = client.post("/api/sessions/checkpoint/rebuild")
    assert rebuilt.status_code == 200
    assert rebuilt.json()["checkpoint"]["session_count"] >= 1


def test_service_agents_share_session_spine(client):
    services = client.app.state.services

    agent = services.system_agent()

    assert agent.session_runtime is services.session_runtime
    assert agent.pyflow_engine.execution_runtime._session_runtime is services.session_runtime


def test_workflow_trigger_auto_binds_session_context(client):
    services = client.app.state.services
    engine = services.system_agent().pyflow_engine
    engine.create_workflow_definition(
        "timeline_workflow",
        {
            "name": "timeline_workflow",
            "nodes": [],
        },
    )

    response = client.post(
        "/api/workflows/trigger",
        json={"name": "timeline_workflow", "input_vars": {"topic": "ops"}, "root_mode": "admin"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["session_key"]
    assert payload["thread_id"].startswith("workflow-")
    assert payload["result"]["run_id"]

    session = client.get(f"/api/sessions/{payload['session_key']}")
    assert session.status_code == 200
    timeline = session.json()["session"]["timeline"]
    assert timeline[-1]["kind"] == "workflow_run"
    assert timeline[-1]["status"] == payload["result"]["status"]


def test_default_cors_settings_only_allow_local_origins(monkeypatch, temp_paths):
    monkeypatch.delenv("PYBOT_CORS_ORIGINS", raising=False)
    app = _create_service_app(temp_paths)

    cors_options = _cors_options(app)
    assert cors_options["allow_origins"] == [
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5000",
        "http://127.0.0.1:5000",
    ]
    assert cors_options["allow_credentials"] is True


def test_wildcard_cors_disables_credentials(monkeypatch, temp_paths):
    monkeypatch.setenv("PYBOT_CORS_ORIGINS", "*")
    app = _create_service_app(temp_paths)

    cors_options = _cors_options(app)
    assert cors_options["allow_origins"] == ["*"]
    assert cors_options["allow_credentials"] is False


def test_chat_endpoint_hides_internal_errors(client, monkeypatch):
    class BrokenAgent:
        def chat(self, _message):
            raise RuntimeError("leaked internal detail")

    monkeypatch.setattr(client.app.state.services.agents, "get_or_create", lambda _thread_id: BrokenAgent())

    response = client.post(
        "/api/chat",
        json={"message": "hello", "thread_id": "broken-thread"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "处理对话请求时发生内部错误"
    assert "leaked internal detail" not in response.text


def test_conversation_store_recovers_from_invalid_metadata_file(temp_paths):
    temp_paths.conversations_file.parent.mkdir(parents=True, exist_ok=True)
    temp_paths.conversations_file.write_text("{not valid json", encoding="utf-8")

    store = ConversationStore(temp_paths)

    assert store.list_conversations() == []
    created = store.create_conversation("demo")
    assert created["thread_id"].startswith("session-")


def test_conversation_store_recovers_from_invalid_history_file(temp_paths):
    store = ConversationStore(temp_paths)
    thread_id = store.create_conversation("demo")["thread_id"]
    history_path = temp_paths.chat_history_dir / f"{thread_id}.json"
    history_path.write_text("{not valid json", encoding="utf-8")

    assert store.get_history(thread_id) == []

    store.append_message(thread_id, "user", "hello")

    history = store.get_history(thread_id)
    assert len(history) == 1
    assert history[0]["content"] == "hello"


def test_no_duplicate_routes(app):
    routes = []
    for route in app.router.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            routes.append((tuple(sorted(route.methods or [])), route.path))

    duplicates = Counter(routes)
    assert not [key for key, count in duplicates.items() if count > 1]


def test_app_manager_blocks_path_traversal(tmp_path):
    manager = AppManager(str(tmp_path / "apps"))
    created = manager.create_app("demo", "Demo", "test")
    assert created["success"] is True

    escaped = manager.update_app_file("demo", "../demo2/secret.txt", "oops")
    assert escaped["success"] is False


def test_agent_control_endpoint(client):
    response = client.get("/api/agent-control")

    assert response.status_code == 200
    payload = response.json()
    assert payload["policy"]["mode"] == "balanced"
    assert "delegate_to_agent" in payload["policy"]["risky_tools"]
    assert payload["policy"]["max_subagent_depth"] == 3
    assert payload["policy"]["max_concurrent_subagents"] == 5


def test_uv_env_routes_list_and_detail(client):
    services = client.app.state.services
    (services.paths.uv_envs_dir / "demo" / ".venv").mkdir(parents=True, exist_ok=True)
    services.uv_env_mgr = UvEnvManager(str(services.paths.uv_envs_dir))

    listed = client.get("/api/uv/envs")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["envs"]] == ["demo"]

    detail = client.get("/api/uv/envs/demo")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["name"] == "demo"
    assert payload["description"] == "(auto-discovered)"
    assert "disk_size" in payload


def test_uv_env_detail_route_returns_404_for_missing_env(client):
    response = client.get("/api/uv/envs/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "环境不存在"


def test_update_agent_capability_profile(client):
    system_agent = client.app.state.services.system_agent()
    from core.agent_storage import AgentDefinition

    system_agent.agent_storage.add_agent(
        AgentDefinition(name="helper", role="helper", description="helper", system_prompt="help")
    )

    response = client.patch(
        "/api/agents/helper/capabilities",
        json={
            "capability_profile": {"preset": "builder"},
            "middleware_profile": {"preset": "coordinator"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["capability_profile"]["allow_local_tool_creation"] is True
    assert payload["middleware_profile"]["preset"] == "coordinator"
    assert "delegation_context" in payload["governance"]["middleware_stack"]
    assert payload["governance"]["sandbox"]["adapter"] == "isolated"


def test_capability_registry_endpoints(client):
    services = client.app.state.services
    skills_dir = services.paths.workspace_dir / "skills" / "pdf_skill"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.joinpath("SKILL.md").write_text(
        "---\nname: pdf_skill\ndescription: Extract PDF tables\n---\n\nUse this skill.\n",
        encoding="utf-8",
    )
    services.skill_registry.reload()
    services.capability_registry.refresh_local_index(save=True)

    registry_response = client.get("/api/capabilities/registry")
    assert registry_response.status_code == 200
    registry_payload = registry_response.json()
    assert registry_payload["stats"]["total_capabilities"] >= 1

    discover_response = client.post(
        "/api/capabilities/discover",
        json={"query": "pdf", "include_marketplace": True},
    )
    assert discover_response.status_code == 200
    discover_payload = discover_response.json()
    assert any(item["name"] == "pdf_skill" for item in discover_payload["local"])

    contract_response = client.get("/api/capabilities/pdf_skill/contract")
    assert contract_response.status_code == 200
    assert contract_response.json()["layer"] == "skill"


def test_gateway_session_detail_surfaces_unified_session_state(client):
    services = client.app.state.services
    services.gateway_runtime.sessions.touch(
        "gw-demo",
        mode="assistant",
        thread_id="gateway-assistant-gw-demo",
        source="http.responses",
        user="demo-user",
        device_id="device-1",
        client_id="client-1",
    )
    services.gateway_runtime.runs.start(
        run_id="run-demo",
        response_id="resp-demo",
        session_key="gw-demo",
        thread_id="gateway-assistant-gw-demo",
        mode="assistant",
        requested_model="pybot:assistant",
        source="http.responses",
        display_input="hello",
    )
    services.gateway_runtime.runs.complete("run-demo", output_text="done")

    detail = client.get("/api/gateway/sessions/gw-demo")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["session"]["session_key"] == "gw-demo"
    assert payload["session"]["gateway"]["user"] == "demo-user"
    assert payload["latest_run"]["run_id"] == "run-demo"


def test_app_matrix_service_discovery_and_grant_endpoints(client):
    services = client.app.state.services
    services.app_manager.create_app(
        "parser",
        display_name="Parser",
        description="Parses documents",
        exports=["parse.excel"],
        require_auth=True,
    )
    services.app_manager.create_app("crm", display_name="CRM", description="Caller app")
    services.app_manager.update_app_file(
        "parser",
        "api.py",
        (
            "if action == 'parse':\n"
            "    result = {'ok': True, 'kind': payload['kind']}\n"
            "else:\n"
            "    result = {'ok': False}\n"
        ),
    )
    services.app_manager.reload_apps()
    services.capability_registry.refresh_local_index(save=True)

    discover = client.post("/api/app-matrix/services/discover", json={"provides": "parse.excel"})
    assert discover.status_code == 200
    assert discover.json()["providers"][0]["name"] == "parser"

    grant = client.post(
        "/api/app-matrix/services/grants",
        json={"caller_app": "crm", "provides": "parse.excel", "requested_quota": 2},
    )
    assert grant.status_code == 200
    token = grant.json()["grant"]["token"]

    grants = client.get("/api/app-matrix/services/grants", params={"caller_app": "crm"})
    assert grants.status_code == 200
    assert grants.json()["count"] == 1
    assert grants.json()["grants"][0]["provider_app"] == "parser"
    assert grants.json()["grants"][0]["caller_identity"]["app_name"] == "crm"
    assert grants.json()["grants"][0]["provider_policy"]["require_auth"] is True

    invoke = client.post(
        "/api/app-matrix/services/invoke",
        json={
            "caller_app": "crm",
            "grant_token": token,
            "action": "parse",
            "payload": {"kind": "excel"},
        },
    )
    assert invoke.status_code == 200
    assert invoke.json()["provider_app"] == "parser"
    assert invoke.json()["quota_remaining"] == 1


def test_app_matrix_service_invoke_enforces_provider_policy(client):
    services = client.app.state.services
    services.app_manager.create_app(
        "parser",
        display_name="Parser",
        description="Parses documents",
        exports=["parse.excel"],
        require_auth=True,
    )
    services.app_manager.create_app("crm", display_name="CRM", description="Caller app")
    services.app_manager.update_app_file(
        "parser",
        "api.py",
        "result = {'ok': True, 'action': action, 'payload': payload}\n",
    )
    services.app_manager.reload_apps()
    services.capability_registry.refresh_local_index(save=True)

    grant = client.post(
        "/api/app-matrix/services/grants",
        json={
            "caller_app": "crm",
            "provides": "parse.excel",
            "metadata": {"allowed_actions": ["parse"], "max_payload_bytes": 32},
        },
    )
    assert grant.status_code == 200
    token = grant.json()["grant"]["token"]

    denied_action = client.post(
        "/api/app-matrix/services/invoke",
        json={
            "caller_app": "crm",
            "grant_token": token,
            "action": "delete",
            "payload": {"kind": "excel"},
        },
    )
    assert denied_action.status_code == 400
    assert "not permitted" in denied_action.json()["detail"]

    denied_payload = client.post(
        "/api/app-matrix/services/invoke",
        json={
            "caller_app": "crm",
            "grant_token": token,
            "action": "parse",
            "payload": {"blob": "x" * 128},
        },
    )
    assert denied_payload.status_code == 400
    assert "Payload exceeds provider policy limit" in denied_payload.json()["detail"]


def test_admin_capability_gap_routes(client):
    services = client.app.state.services
    admin_agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    admin_agent.admin._on_capability_gap_detected(  # noqa: SLF001
        Event(
            type=EventType.CAPABILITY_GAP_DETECTED,
            source="AdminWatcherDaemon",
            payload={
                "source": "app:parser",
                "event_type": "error",
                "gap_type": "missing_capability_gap",
                "suggested_capability_name": "parser_missing_capability_gap",
                "occurrences": 3,
                "samples": [{"error": "missing extractor"}],
            },
        )
    )

    listed = client.get("/api/admin/capability-gaps")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    candidate_id = listed.json()["candidates"][0]["candidate_id"]
    assert listed.json()["candidates"][0]["recommended_publish_target"] == "app_matrix"

    detail = client.get(f"/api/admin/capability-gaps/{candidate_id}")
    assert detail.status_code == 200
    assert detail.json()["draft_contract"]["name"] == "parser_missing_capability_gap"

    drafted = client.post(
        f"/api/admin/capability-gaps/{candidate_id}/draft",
        json={"target_name": "parser_gap_draft", "overwrite": True},
    )
    assert drafted.status_code == 200
    assert drafted.json()["draft"]["asset_kind"] == "app"
    assert drafted.json()["candidate"]["draft_artifact"]["name"] == "parser_gap_draft"

    validated = client.post(f"/api/admin/capability-gaps/{candidate_id}/validate")
    assert validated.status_code == 200
    assert validated.json()["validation"]["asset_kind"] == "app"
    assert validated.json()["validation"]["verification"]["verdict"] in {"PASS", "NEEDS_IMPROVEMENT"}

    published = client.post(
        f"/api/admin/capability-gaps/{candidate_id}/publish",
        json={},
    )
    assert published.status_code == 200
    assert published.json()["publish"]["success"] is True
    assert published.json()["candidate"]["status"] == "published"

    promoted = client.post(
        f"/api/admin/capability-gaps/{candidate_id}/promote",
        json={"auto_start": False},
    )
    assert promoted.status_code == 200
    payload = promoted.json()
    assert payload["success"] is True
    assert payload["candidate"]["status"] == "promoted"
    assert payload["task"]["name"] == "synthesize_parser_missing_capability_gap"
    assert payload["task"]["context"]["capability_gap_blueprint"]["recommended_publish_target"] == "app_matrix"


def test_admin_capability_gap_close_loop_route(client):
    services = client.app.state.services
    admin_agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    admin_agent.admin._on_capability_gap_detected(  # noqa: SLF001
        Event(
            type=EventType.CAPABILITY_GAP_DETECTED,
            source="AdminWatcherDaemon",
            payload={
                "source": "worker:toolchain",
                "event_type": "error",
                "gap_type": "general_runtime_gap",
                "suggested_capability_name": "toolchain_general_runtime_gap",
                "occurrences": 2,
                "samples": [{"error": "needs reusable helper"}],
            },
        )
    )

    listed = client.get("/api/admin/capability-gaps")
    candidate_id = next(
        item["candidate_id"]
        for item in listed.json()["candidates"]
        if item["suggested_capability_name"] == "toolchain_general_runtime_gap"
    )

    closed = client.post(
        f"/api/admin/capability-gaps/{candidate_id}/close-loop",
        json={},
    )
    assert closed.status_code == 200
    payload = closed.json()
    assert payload["success"] is True
    assert payload["draft"]["asset_kind"] == "skill"
    assert payload["validation"]["valid"] is True
    assert payload["publish"]["success"] is True
    assert payload["candidate"]["status"] == "published"


def test_admin_capability_gap_rollout_routes(client):
    services = client.app.state.services
    admin_agent = services.agents.get_or_create_mode("admin", "__admin_control__")
    admin_agent.admin._on_capability_gap_detected(  # noqa: SLF001
        Event(
            type=EventType.CAPABILITY_GAP_DETECTED,
            source="AdminWatcherDaemon",
            payload={
                "source": "worker:toolchain",
                "event_type": "error",
                "gap_type": "general_runtime_gap",
                "suggested_capability_name": "toolchain_rollout_gap",
                "occurrences": 2,
                "samples": [{"error": "needs reusable helper"}],
            },
        )
    )

    listed = client.get("/api/admin/capability-gaps")
    candidate_id = next(
        item["candidate_id"]
        for item in listed.json()["candidates"]
        if item["suggested_capability_name"] == "toolchain_rollout_gap"
    )

    closed = client.post(
        f"/api/admin/capability-gaps/{candidate_id}/close-loop",
        json={},
    )
    assert closed.status_code == 200

    rollout = client.post(
        f"/api/admin/capability-gaps/{candidate_id}/rollout",
        json={"strategy": "shadow", "target": "ecosystem"},
    )
    assert rollout.status_code == 200
    rollout_payload = rollout.json()
    assert rollout_payload["rollout"]["status"] == "active"

    evaluated = client.post(
        f"/api/admin/capability-gaps/{candidate_id}/rollout/evaluate",
        json={
            "outcome": "healthy",
            "note": "telemetry is stable",
            "telemetry_sample": {"error_rate": 0.0},
        },
    )
    assert evaluated.status_code == 200
    evaluated_payload = evaluated.json()
    assert evaluated_payload["rollout"]["status"] == "verified"
    assert evaluated_payload["candidate"]["status"] == "resolved"


def test_governance_options_expose_presets_and_sandbox_adapters(client):
    response = client.get("/api/agents/governance/options")

    assert response.status_code == 200
    payload = response.json()
    capability_presets = {item["name"]: item for item in payload["capability_presets"]}
    middleware_presets = {item["name"]: item for item in payload["middleware_presets"]}
    sandbox_adapters = {item["name"]: item for item in payload["sandbox_adapters"]}

    assert "researcher" in capability_presets
    assert capability_presets["coordinator"]["config"]["allow_agent_delegation"] is True
    assert capability_presets["maintainer"]["config"]["sandbox_adapter"] == "workspace"
    assert "coordinator" in middleware_presets
    assert "shared_tools" in sandbox_adapters
    assert "balanced" in payload["control_modes"]


def test_governance_center_endpoint_unifies_approvals_policy_and_gateway_state(client):
    services = client.app.state.services
    services.approval_queue.create_request(
        kind="tool",
        scope="root:test",
        summary="dangerous action",
        prompt="approve dangerous action",
        metadata={"tool_name": "exec_code"},
    )
    services.gateway_runtime.pairings.ensure_request(
        device_id="device-1",
        role="operator",
        client_id="browser-1",
        scopes=["operator"],
        platform="web",
        mode="assistant",
        user_agent="pytest",
        metadata={"source": "test"},
    )

    response = client.get("/api/governance/center")

    assert response.status_code == 200
    payload = response.json()
    assert payload["approvals"]["counts"]["pending"] == 1
    assert payload["policy"]["policy"]["mode"] == "balanced"
    assert "balanced" in payload["options"]["control_modes"]
    assert payload["gateway"]["status"]["pending_pairings"] == 1
    assert payload["gateway"]["pairings"]["pending"][0]["device_id"] == "device-1"
    assert "webhook" in payload["gateway"]["status"]["supported_channels"]


def test_system_model_endpoint_surfaces_canonical_concept_boundaries(client):
    response = client.get("/api/system/model")

    assert response.status_code == 200
    payload = response.json()
    assert payload["root_mode_progression"] == ["assistant", "app_matrix", "admin"]
    assert [item["name"] for item in payload["product_concepts"]] == [
        "tools",
        "skills",
        "agents",
        "workflows",
        "apps",
    ]
    assert "approvals" in payload["not_product_concepts"]
    assert any(question.startswith("这是新的产品概念") for question in payload["anti_sprawl_questions"])


def test_system_modes_endpoint_surfaces_profiles_and_current_runtime_mode(client):
    response = client.get("/api/system/modes")

    assert response.status_code == 200
    payload = response.json()
    modes = {item["name"]: item for item in payload["modes"]}

    assert set(modes) == {"assistant", "app_matrix", "admin"}
    assert "start_admin_loop" in modes["admin"]["api_methods"]
    assert "plan_app_matrix_topology" in modes["app_matrix"]["api_methods"]
    assert payload["current"]["name"] == "assistant"
    assert payload["current"]["effective_capabilities"]["interactive_chat"] is True


def test_plugin_routes_discover_and_manage_runtime_state(client, tmp_path: Path):
    from core.plugin_manifest import reset_plugin_registry

    reset_plugin_registry()
    plugin_dir = tmp_path / "demo_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        """
from core.plugin_sdk import on_message, pybot_plugin


@pybot_plugin(id="demo_plugin", name="Demo Plugin")
class DemoPlugin:
    @on_message
    def before_message(self, ctx):
        ctx.content = ctx.content + "!"
""".strip(),
        encoding="utf-8",
    )
    (plugin_dir / "pybot.plugin.json").write_text(
        """
{
  "id": "demo_plugin",
  "name": "Demo Plugin",
  "version": "1.0.0",
  "capabilities": ["hooks"],
  "entry_point": "demo_plugin",
  "enabled": true
}
""".strip(),
        encoding="utf-8",
    )

    discovered = client.post(
        "/api/plugins/discover",
        json={"directories": [str(tmp_path)], "reset": True, "autoload_enabled": True},
    )
    assert discovered.status_code == 200
    payload = discovered.json()
    assert payload["discovered"][0]["id"] == "demo_plugin"
    assert payload["plugins"][0]["runtime"]["loaded"] is True
    assert payload["plugins"][0]["runtime"]["message_handlers"] == 1

    listed = client.get("/api/plugins")
    assert listed.status_code == 200
    assert listed.json()["plugins"][0]["id"] == "demo_plugin"

    disabled = client.post("/api/plugins/demo_plugin/disable")
    assert disabled.status_code == 200
    assert disabled.json()["runtime"]["enabled"] is False

    enabled = client.post("/api/plugins/demo_plugin/enable")
    assert enabled.status_code == 200
    assert enabled.json()["runtime"]["enabled"] is True

    unloaded = client.post("/api/plugins/demo_plugin/unload")
    assert unloaded.status_code == 200
    assert unloaded.json()["success"] is True
    assert unloaded.json()["plugin"]["runtime"]["loaded"] is False


def test_app_matrix_routes_surface_topology(client):
    services = client.app.state.services
    services.app_manager.create_app(
        "crm",
        display_name="CRM",
        description="Customer coordination app",
        tags=["sales"],
        agent_binding="crm_specialist",
    )
    services.app_manager.create_app(
        "audit",
        display_name="Audit",
        description="Compliance review app",
        tags=["risk"],
        workflow_binding="audit_flow",
    )
    services.app_manager.reload_apps()

    sync_response = client.post("/api/app-matrix/sync", json={"clear_missing": False})
    overview_response = client.get("/api/app-matrix/overview")
    node_response = client.get("/api/app-matrix/nodes/crm")

    assert sync_response.status_code == 200
    assert len(sync_response.json()["synced"]) == 2

    assert overview_response.status_code == 200
    overview_payload = overview_response.json()
    assert overview_payload["topology"]["stats"]["total_nodes"] == 2
    assert {item["name"] for item in overview_payload["apps"]} == {"crm", "audit"}

    assert node_response.status_code == 200
    node_payload = node_response.json()
    assert node_payload["node"]["name"] == "crm"
    assert node_payload["node"]["metadata"]["agent_binding"] == "crm_specialist"


def test_app_matrix_node_route_returns_404_for_missing_node(client):
    response = client.get("/api/app-matrix/nodes/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "APP Brain node not found"


def test_app_matrix_node_metadata_route_updates_contracts(client):
    services = client.app.state.services
    services.app_manager.create_app(
        "crm",
        display_name="CRM",
        description="Customer coordination app",
        tags=["sales"],
    )
    services.app_manager.reload_apps()
    client.post("/api/app-matrix/sync", json={"clear_missing": False})

    response = client.patch(
        "/api/app-matrix/nodes/crm/metadata",
        json={
            "shared_datastores": ["customer_db"],
            "shared_schemas": [{"name": "customer_profile", "version": "v1"}],
            "data_contracts": [{"name": "customer_sync", "consumer": "audit"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["app"]["shared_datastores"] == ["customer_db"]
    assert payload["summary"]["contracts"]["shared_schemas"][0]["name"] == "customer_profile"
    assert payload["summary"]["contracts"]["data_contracts"][0]["consumer"] == "audit"


def test_agent_detail_includes_governance_snapshot(client):
    system_agent = client.app.state.services.system_agent()
    from core.agent_storage import AgentDefinition

    system_agent.agent_storage.add_agent(
        AgentDefinition(
            name="planner",
            role="planner",
            description="plans work",
            system_prompt="plan",
            capability_profile={"preset": "manager"},
            middleware_profile={"preset": "coordinator"},
        )
    )

    response = client.get("/api/agents/planner")

    assert response.status_code == 200
    payload = response.json()
    assert payload["governance"]["root_policy"]["mode"] == "balanced"
    assert "delegation_context" in payload["governance"]["middleware_stack"]
    assert payload["governance"]["permissions"]["allow_agent_delegation"] is True
    assert payload["governance"]["sandbox"]["adapter"] == "shared_tools"


def test_agent_tools_endpoint_separates_assigned_and_local_tools(client):
    system_agent = client.app.state.services.system_agent()
    from core.agent_storage import AgentDefinition

    system_agent.agent_storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="helper",
            system_prompt="help",
            tools=["shared_tool", "missing_tool"],
        )
    )
    system_agent.storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Shared helper",
            "parameters": [],
            "code": "result = 'ok'",
            "dependencies": [],
            "usage_guide": "shared",
        },
    )
    local_storage = system_agent.agent_storage.tools_dir_for("helper")
    local_storage.mkdir(parents=True, exist_ok=True)
    from core.tool_storage import ToolStorage

    ToolStorage(str(local_storage)).add_tool(
        "local_helper",
        {
            "name": "local_helper",
            "description": "Local helper",
            "parameters": [],
            "code": "result = 'ok'",
            "dependencies": [],
            "usage_guide": "local",
        },
    )

    response = client.get("/api/agents/helper/tools")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_inventory"]["assigned_global_tool_names"] == ["shared_tool"]
    assert payload["tool_inventory"]["local_tool_names"] == ["local_helper"]
    assert payload["tool_inventory"]["missing_assigned_tools"][0]["name"] == "missing_tool"
    assert payload["tool_inventory"]["assigned_global_tools"][0]["sync_status"] == "global_only"
    assert payload["tool_inventory"]["local_tools"][0]["sync_status"] == "local_only"


def test_sync_local_agent_tool_to_global_library(client):
    system_agent = client.app.state.services.system_agent()
    from core.agent_storage import AgentDefinition
    from core.tool_storage import ToolStorage

    system_agent.agent_storage.add_agent(
        AgentDefinition(name="helper", role="helper", description="helper", system_prompt="help")
    )
    ToolStorage(str(system_agent.agent_storage.tools_dir_for("helper"))).add_tool(
        "local_helper",
        {
            "name": "local_helper",
            "description": "Local helper",
            "parameters": [],
            "code": "result = 'ok'",
            "dependencies": [],
            "usage_guide": "local",
        },
    )

    response = client.post(
        "/api/agents/helper/tools/local_helper/sync",
        json={"direction": "to_global"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "promoted_to_global"
    assert payload["tool_inventory"]["local_tools"][0]["sync_status"] == "in_sync"
    assert system_agent.storage.get_tool("local_helper") is not None


def test_sync_global_tool_to_local_agent_library(client):
    system_agent = client.app.state.services.system_agent()
    from core.agent_storage import AgentDefinition
    from core.tool_storage import ToolStorage

    system_agent.agent_storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="helper",
            system_prompt="help",
            tools=["shared_tool"],
        )
    )
    system_agent.storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Shared helper",
            "parameters": [],
            "code": "result = 'global'",
            "dependencies": [],
            "usage_guide": "shared",
        },
    )

    response = client.post(
        "/api/agents/helper/tools/shared_tool/sync",
        json={"direction": "from_global"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "pulled_to_local"
    assert payload["tool_inventory"]["assigned_global_tools"][0]["sync_status"] == "in_sync"
    local_storage = ToolStorage(str(system_agent.agent_storage.tools_dir_for("helper")))
    assert local_storage.get_tool("shared_tool") is not None


def test_sync_global_tool_to_local_requires_overwrite_for_conflicts(client):
    system_agent = client.app.state.services.system_agent()
    from core.agent_storage import AgentDefinition
    from core.tool_storage import ToolStorage

    system_agent.agent_storage.add_agent(
        AgentDefinition(
            name="helper",
            role="helper",
            description="helper",
            system_prompt="help",
            tools=["shared_tool"],
        )
    )
    system_agent.storage.add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Shared helper",
            "parameters": [],
            "code": "result = 'global'",
            "dependencies": [],
            "usage_guide": "shared",
        },
    )
    ToolStorage(str(system_agent.agent_storage.tools_dir_for("helper"))).add_tool(
        "shared_tool",
        {
            "name": "shared_tool",
            "description": "Local helper",
            "parameters": [],
            "code": "result = 'local'",
            "dependencies": [],
            "usage_guide": "local",
        },
    )

    conflict = client.post(
        "/api/agents/helper/tools/shared_tool/sync",
        json={"direction": "from_global"},
    )
    assert conflict.status_code == 409

    resolved = client.post(
        "/api/agents/helper/tools/shared_tool/sync",
        json={"direction": "from_global", "overwrite": True},
    )
    assert resolved.status_code == 200
    assert resolved.json()["action"] == "overwrote_local"


def test_skill_file_routes_follow_registry_source_layering(client, tmp_path: Path):
    external_dir = tmp_path / "external_skills"
    skill_dir = external_dir / "external_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: external_skill
description: Provided by an external source
version: 1.0.0
author: tests
enabled: true
---

# external_skill
""",
        encoding="utf-8",
    )

    client.app.state.services.skill_registry = SkillRegistry(
        skill_sources=[
            SkillSource(name="vendor", path=external_dir, writable=False),
            SkillSource(name="workspace", path=client.app.state.services.paths.skills_dir, writable=True),
        ]
    )

    listed = client.get("/api/skills/external_skill/files")
    assert listed.status_code == 200
    assert listed.json()["files"][0]["path"] == "SKILL.md"

    fetched = client.get("/api/skills/external_skill/files/SKILL.md")
    assert fetched.status_code == 200
    assert "external_skill" in fetched.json()["content"]

    updated = client.put(
        "/api/skills/external_skill/files/SKILL.md",
        json={"content": "# rewritten"},
    )
    assert updated.status_code == 403


def test_skill_file_routes_support_non_filesystem_sources(client):
    backend = InMemorySkillBackend(
        files={
            "/memory_skills/memory_skill/SKILL.md": """---
name: memory_skill
description: Provided by a backend source
version: 1.0.0
author: tests
enabled: true
---

# memory_skill
""",
            "/memory_skills/memory_skill/notes.txt": "from memory",
        }
    )

    client.app.state.services.skill_registry = SkillRegistry(
        skill_sources=[SkillSource(name="memory", path="/memory_skills", writable=True, backend=backend)]
    )

    listed = client.get("/api/skills/memory_skill/files")
    assert listed.status_code == 200
    assert [item["path"] for item in listed.json()["files"]] == ["SKILL.md", "notes.txt"]

    fetched = client.get("/api/skills/memory_skill/files/notes.txt")
    assert fetched.status_code == 200
    assert fetched.json()["content"] == "from memory"

    updated = client.put(
        "/api/skills/memory_skill/files/notes.txt",
        json={"content": "rewritten"},
    )
    assert updated.status_code == 200

    reloaded = client.get("/api/skills/memory_skill/files/notes.txt")
    assert reloaded.status_code == 200
    assert reloaded.json()["content"] == "rewritten"


def test_skill_source_routes_expose_backend_metadata_and_copy_skills(client):
    vendor_backend = InMemorySkillBackend(
        files={
            "/vendor/shared_skill/SKILL.md": """---
name: shared_skill
description: Vendor skill
version: 1.0.0
author: tests
enabled: true
---

# shared_skill
""",
            "/vendor/shared_skill/references.txt": "vendor reference",
        }
    )
    workspace_backend = InMemorySkillBackend()

    client.app.state.services.skill_registry = SkillRegistry(
        skill_sources=[
            SkillSource(name="vendor", path="/vendor", writable=False, backend=vendor_backend),
            SkillSource(name="workspace", path="/workspace", writable=True, backend=workspace_backend),
        ]
    )

    sources = client.get("/api/skill-sources")
    assert sources.status_code == 200
    payload = sources.json()["sources"]
    assert [item["name"] for item in payload] == ["vendor", "workspace"]
    assert payload[0]["backend"] == "memory"
    assert payload[0]["capabilities"]["supports_python_tools"] is True

    copied = client.post(
        "/api/skills/shared_skill/copy",
        json={"target_source": "workspace"},
    )
    assert copied.status_code == 200
    assert copied.json()["result"]["source_name"] == "workspace"

    exported = client.get("/api/skills/shared_skill/bundle")
    assert exported.status_code == 200
    assert exported.json()["files"]["references.txt"] == "vendor reference"


def test_skill_routes_surface_openclaw_metadata_and_rendered_skill_paths(client, tmp_path: Path):
    repo_root = tmp_path / "openclaw-main"
    skill_dir = repo_root / "skills" / "gh_issues"
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        """---
name: gh-issues
description: "Manage GitHub issues"
metadata: { "openclaw": { "requires": { "bins": ["git", "curl"] }, "primaryEnv": "GH_TOKEN" } }
---

# gh-issues

python {baseDir}/scripts/run.py
""",
        encoding="utf-8",
    )

    client.app.state.services.skill_registry = SkillRegistry(
        skill_sources=[SkillSource(name="openclaw", path=repo_root, writable=False, flavor="openclaw")]
    )

    detail = client.get("/api/skills/gh-issues")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["skill_format"] == "openclaw"
    assert payload["requires_bins"] == ["git", "curl"]
    assert payload["primary_env"] == "GH_TOKEN"
    assert payload["source_name"] == "openclaw"

    listed = client.get("/api/skill-sources")
    assert listed.status_code == 200
    assert listed.json()["sources"][0]["flavor"] == "openclaw"

    rendered = client.get("/api/skills/gh-issues/files/SKILL.md")
    assert rendered.status_code == 200
    assert "{baseDir}" not in rendered.json()["content"]
    assert "/skills/gh_issues/scripts/run.py" in rendered.json()["content"].replace("\\", "/")


def test_openclaw_register_route_persists_source_and_reloads_registry(client, temp_paths, monkeypatch):
    config_path = temp_paths.root_dir / "config.json"
    config_impl.get_config.cache_clear()
    monkeypatch.setattr(config_impl, "_CONFIG_PATH", config_path)

    repo_root = temp_paths.root_dir / "openclaw-main"
    skill_dir = repo_root / "skills" / "weather"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: weather
description: Weather skill
metadata: { "openclaw": { "requires": { "bins": ["curl"] } } }
---

# weather
""",
        encoding="utf-8",
    )

    response = client.post(
        "/api/skill-sources/openclaw/register",
        json={"path": str(repo_root), "name": "openclaw_vendor", "persist": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["persisted"] is True
    assert payload["source"]["name"] == "openclaw_vendor"
    assert payload["source"]["flavor"] == "openclaw"
    assert payload["detected"]["skill_count"] == 1
    assert "weather" in payload["skills"]

    saved = config_impl.reload_config(config_path)
    assert saved["extra_skill_sources"] == [{"name": "openclaw_vendor", "path": str(repo_root), "flavor": "openclaw"}]

    config_impl.get_config.cache_clear()


def test_skill_diagnostics_route_surfaces_missing_bins_and_config_state(client, tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_impl.get_config.cache_clear()
    monkeypatch.setattr(config_impl, "_CONFIG_PATH", config_path)
    config_impl.save_config({"channels": {"weather": {"token": "configured"}}}, config_path)

    monkeypatch.setenv("WEATHER_TOKEN", "secret-weather-token")

    repo_root = tmp_path / "openclaw-main"
    skill_dir = repo_root / "skills" / "weather"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: weather
description: Weather skill
metadata:
  openclaw:
    requires:
      bins: ["python", "definitely-missing-pybot-bin"]
      config: ["channels.weather"]
    primaryEnv: "WEATHER_TOKEN"
---

# weather
python {baseDir}/scripts/run.py
""",
        encoding="utf-8",
    )

    client.app.state.services.skill_registry = SkillRegistry(
        skill_sources=[SkillSource(name="openclaw", path=repo_root, writable=False, flavor="openclaw")]
    )

    response = client.get("/api/skills/weather/diagnostics")

    assert response.status_code == 200
    diagnostics = response.json()["diagnostics"]
    assert diagnostics["skill_format"] == "openclaw"
    assert diagnostics["summary"]["healthy"] is False
    assert diagnostics["summary"]["missing_bins"] == ["definitely-missing-pybot-bin"]
    assert diagnostics["summary"]["missing_env"] == []
    assert diagnostics["summary"]["missing_config"] == []
    assert diagnostics["requires"]["env"][0]["name"] == "WEATHER_TOKEN"
    assert diagnostics["requires"]["env"][0]["present"] is True
    assert diagnostics["requires"]["config"][0]["path"] == "channels.weather"
    assert diagnostics["requires"]["config"][0]["present"] is True

    config_impl.get_config.cache_clear()


def test_openclaw_import_route_bridges_repo_config_and_extra_dirs(client, temp_paths, monkeypatch):
    config_path = temp_paths.root_dir / "config.json"
    openclaw_config_path = temp_paths.root_dir / ".openclaw" / "openclaw.json"
    shared_skills_root = openclaw_config_path.parent / "shared-skills"
    config_impl.get_config.cache_clear()
    monkeypatch.setattr(config_impl, "_CONFIG_PATH", config_path)

    repo_root = temp_paths.root_dir / "openclaw-main"
    weather_skill = repo_root / "skills" / "weather"
    weather_skill.mkdir(parents=True, exist_ok=True)
    (weather_skill / "SKILL.md").write_text(
        """---
name: weather
description: Weather skill
metadata: { "openclaw": { "requires": { "bins": ["curl"] } } }
---

# weather
""",
        encoding="utf-8",
    )

    ops_skill = shared_skills_root / "ops"
    ops_skill.mkdir(parents=True, exist_ok=True)
    (ops_skill / "SKILL.md").write_text(
        """---
name: ops
description: Shared OpenClaw skill
---

# ops
""",
        encoding="utf-8",
    )
    openclaw_config_path.parent.mkdir(parents=True, exist_ok=True)
    openclaw_config_path.write_text(
        """
skills:
  load:
    extraDirs:
      - ./shared-skills
  entries:
    weather:
      enabled: true
      apiKey: WEATHER-KEY
channels:
  webhook:
    enabled: true
  wechat:
    token: wechat-token
  discord:
    enabled: true
  telegram:
    token: tg-token
""".strip(),
        encoding="utf-8",
    )

    response = client.post(
        "/api/openclaw/import",
        json={
            "repo_path": str(repo_root),
            "config_path": str(openclaw_config_path),
            "source_name": "openclaw_vendor",
            "persist": True,
            "import_extra_dirs": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["persisted"] is True
    assert payload["bridge"]["config_loaded"] is True
    assert len(payload["sources"]) == 2
    assert payload["report"]["config"]["summary"]["channels"] == ["discord", "telegram", "webhook", "wechat"]
    assert payload["channel_import"]["imported"]["webhook"]["kind"] == "webhook"
    assert payload["channel_import"]["imported"]["wechat"]["kind"] == "wechat"
    assert payload["channel_import"]["skipped"] == [
        {"name": "discord", "reason": "unsupported_by_pybot"},
        {"name": "telegram", "reason": "unsupported_by_pybot"},
    ]
    assert {item["name"] for item in payload["sources"]} == {
        "openclaw_vendor",
        "openclaw_vendor_extra_1_shared_skills",
    }
    assert {"weather", "ops"}.issubset(payload["skills"])

    saved = config_impl.reload_config(config_path)
    assert saved["openclaw_compat"]["repo_path"] == str(repo_root)
    assert saved["openclaw_compat"]["config_path"] == str(openclaw_config_path.resolve())
    assert saved["openclaw_compat"]["channels"] == ["discord", "telegram", "webhook", "wechat"]
    assert saved["openclaw_compat"]["channel_import"]["imported"] == ["webhook", "wechat"]
    assert [item["name"] for item in saved["extra_skill_sources"]] == [
        "openclaw_vendor",
        "openclaw_vendor_extra_1_shared_skills",
    ]
    assert saved["channels"]["webhook"]["enabled"] is True
    assert saved["channels"]["wechat"]["token"] == "wechat-token"

    config_impl.get_config.cache_clear()


def test_openclaw_report_and_skill_diagnostics_surface_entry_bridge_details(client, tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    openclaw_config_path = tmp_path / ".openclaw" / "openclaw.json"
    config_impl.get_config.cache_clear()
    monkeypatch.setattr(config_impl, "_CONFIG_PATH", config_path)

    repo_root = tmp_path / "openclaw-main"
    skill_dir = repo_root / "skills" / "weather"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: weather
description: Weather skill
metadata:
  openclaw:
    skillKey: "weather"
    requires:
      config: ["channels.weather"]
    primaryEnv: "WEATHER_TOKEN"
---

# weather
""",
        encoding="utf-8",
    )
    openclaw_config_path.parent.mkdir(parents=True, exist_ok=True)
    openclaw_config_path.write_text(
        """
skills:
  entries:
    weather:
      enabled: true
      apiKey: WEATHER-KEY
      env:
        WEATHER_TOKEN: from-openclaw
channels:
  weather:
    token: bridge-token
""".strip(),
        encoding="utf-8",
    )

    imported = client.post(
        "/api/openclaw/import",
        json={
            "repo_path": str(repo_root),
            "config_path": str(openclaw_config_path),
            "source_name": "openclaw",
            "persist": True,
        },
    )
    assert imported.status_code == 200

    diagnostics_response = client.get("/api/skills/weather/diagnostics")

    assert diagnostics_response.status_code == 200
    diagnostics = diagnostics_response.json()["diagnostics"]
    assert diagnostics["summary"]["missing_config"] == ["channels.weather"]
    assert diagnostics["openclaw"]["compatibility"]["entry_present"] is True
    assert diagnostics["openclaw"]["compatibility"]["entry_enabled"] is True
    assert diagnostics["openclaw"]["compatibility"]["entry_api_key_present"] is True
    assert diagnostics["openclaw"]["compatibility"]["primary_env_bridge"][0]["available_via_api_key"] is True
    assert diagnostics["openclaw"]["compatibility"]["primary_env_bridge"][0]["available_via_entry_env"] is True
    assert diagnostics["openclaw"]["compatibility"]["global_config_bridge"][0]["path"] == "channels.weather"
    assert diagnostics["openclaw"]["compatibility"]["global_config_bridge"][0]["present"] is True
    assert diagnostics["openclaw"]["compatibility"]["runtime_env"]["WEATHER_TOKEN"] == "from-openclaw"

    report = client.get("/api/openclaw/report")

    assert report.status_code == 200
    report_payload = report.json()["report"]
    assert report_payload["config"]["loaded"] is True
    assert report_payload["config"]["summary"]["skill_entries"] == ["weather"]
    assert report_payload["config"]["summary"]["channels"] == ["weather"]
    assert report_payload["skills"][0]["entry_key"] == "weather"

    config_impl.get_config.cache_clear()


def test_skill_source_refresh_route_supports_http_backends(client):
    payloads = {
        "/remote/index.json": {"skills": [{"name": "http_skill"}]},
        "/remote/http_skill/bundle.json": {
            "files": {
                "SKILL.md": """---
name: http_skill
description: Loaded over HTTP
version: 1.0.0
author: tests
enabled: true
---

# http_skill
""",
                "notes.txt": "first version",
            }
        },
    }

    with serve_skill_http(payloads) as base_url:
        client.app.state.services.skill_registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(),
                )
            ]
        )

        payloads["/remote/http_skill/bundle.json"] = {
            "files": {
                "SKILL.md": """---
name: http_skill
description: Loaded over HTTP
version: 1.0.0
author: tests
enabled: true
---

# http_skill
""",
                "notes.txt": "second version",
            }
        }

        refreshed = client.post("/api/skill-sources/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["sources"][0]["backend"] == "http"
        assert refreshed.json()["sources"][0]["remote"] is True

        fetched = client.get("/api/skills/http_skill/files/notes.txt")
        assert fetched.status_code == 200
        assert fetched.json()["content"] == "second version"


def test_skill_source_routes_surface_paginated_registry_metadata(client):
    payloads = {
        "/remote/index.json": {
            "skills": [{"name": "alpha_skill"}],
            "next": "page-2.json",
            "registry": {"namespace": "vendor-team", "display_name": "Vendor Registry"},
        },
        "/remote/page-2.json": {
            "skills": {
                "beta_skill": {
                    "files": {
                        "SKILL.md": """---
name: beta_skill
description: Beta skill
version: 1.0.0
author: tests
enabled: true
---

# beta_skill
"""
                    }
                }
            }
        },
        "/remote/alpha_skill/bundle.json": {
            "files": {
                "SKILL.md": """---
name: alpha_skill
description: Alpha skill
version: 1.0.0
author: tests
enabled: true
---

# alpha_skill
"""
            }
        },
    }

    with serve_skill_http(payloads) as base_url:
        client.app.state.services.skill_registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(page_limit=4, max_concurrency=2),
                )
            ]
        )

        listed = client.get("/api/skill-sources")

        assert listed.status_code == 200
        source = listed.json()["sources"][0]
        assert source["metadata"]["namespace"] == "vendor-team"
        assert source["metadata"]["page_count"] == 2
        assert source["capabilities"]["supports_catalog_pagination"] is True


def test_skill_source_refresh_route_surfaces_refresh_report(client):
    with serve_skill_http(
        {
            "/remote/index.json": {
                "skills": [{"name": "alpha_skill"}],
                "registry": {"namespace": "vendor-team"},
            },
            "/remote/alpha_skill/bundle.json": {
                "files": {
                    "SKILL.md": """---
name: alpha_skill
description: Alpha skill
version: 1.0.0
author: tests
enabled: true
---

# alpha_skill
"""
                }
            },
        },
        expected_auth="Bearer secret-token",
        etags={"/remote/index.json": "registry-v1"},
    ) as base_url:
        client.app.state.services.skill_registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(bearer_token="secret-token"),
                )
            ]
        )

        refreshed = client.post("/api/skill-sources/refresh")

        assert refreshed.status_code == 200
        source = refreshed.json()["sources"][0]
        assert source["refresh_report"]["status"] == "not_modified"
        assert source["refresh_report"]["conditional_request"] is True
        assert source["refresh_report"]["auth_configured"] is True
        assert source["metadata"]["etag"] == "registry-v1"


def test_skill_source_routes_surface_registry_descriptor_and_backpressure(client):
    with serve_skill_http(
        {
            "/remote/.well-known/skill-registry.json": {
                "catalog_path": "catalog/index.json",
                "bundle_path_template": "bundles/{skill_name}.json",
                "registry": {"namespace": "descriptor-team", "display_name": "Descriptor Registry"},
            },
            "/remote/catalog/index.json": [
                HttpRouteResponse(status=429, headers={"Retry-After": "0"}),
                HttpRouteResponse(
                    body={
                        "skills": [{"name": "descriptor_skill"}],
                        "registry": {"topic": "descriptors"},
                    },
                    headers={"ETag": "catalog-v2"},
                ),
            ],
            "/remote/bundles/descriptor_skill.json": {
                "files": {
                    "SKILL.md": """---
name: descriptor_skill
description: Descriptor-backed skill
version: 1.0.0
author: tests
enabled: true
---

# descriptor_skill
"""
                }
            },
        }
    ) as base_url:
        client.app.state.services.skill_registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(retry_attempts=3),
                )
            ]
        )

        listed = client.get("/api/skill-sources")

        assert listed.status_code == 200
        source = listed.json()["sources"][0]
        assert source["descriptor"]["catalog_path"] == "catalog/index.json"
        assert source["descriptor"]["bundle_path_template"] == "bundles/{skill_name}.json"
        assert source["metadata"]["descriptor_loaded"] is True
        assert source["metadata"]["namespace"] == "descriptor-team"
        assert source["metadata"]["topic"] == "descriptors"
        assert source["metadata"]["backpressure_events"] == 1
        assert source["capabilities"]["supports_registry_descriptor"] is True


def test_skill_source_detail_route_returns_single_source_state(client):
    with serve_skill_http(
        {
            "/remote/.well-known/skill-registry.json": {
                "catalog_path": "catalog/index.json",
                "registry": {"namespace": "detail-view"},
            },
            "/remote/catalog/index.json": {
                "skills": [{"name": "detail_skill"}],
            },
            "/remote/detail_skill/bundle.json": {
                "files": {
                    "SKILL.md": """---
name: detail_skill
description: Detail route test
version: 1.0.0
author: tests
enabled: true
---

# detail_skill
"""
                }
            },
        }
    ) as base_url:
        client.app.state.services.skill_registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(),
                )
            ]
        )

        listed = client.get("/api/skill-sources/remote")

        assert listed.status_code == 200
        source = listed.json()["source"]
        assert source["name"] == "remote"
        assert source["metadata"]["namespace"] == "detail-view"
        assert source["descriptor"]["catalog_path"] == "catalog/index.json"


def test_skill_source_refresh_route_accepts_single_source(client):
    with serve_skill_http(
        {
            "/remote/.well-known/skill-registry.json": {
                "catalog_path": "catalog/index.json",
                "registry": {"namespace": "single-refresh"},
            },
            "/remote/catalog/index.json": {
                "skills": [{"name": "single_refresh_skill"}],
            },
            "/remote/single_refresh_skill/bundle.json": {
                "files": {
                    "SKILL.md": """---
name: single_refresh_skill
description: Single source refresh test
version: 1.0.0
author: tests
enabled: true
---

# single_refresh_skill
"""
                }
            },
        }
    ) as base_url:
        client.app.state.services.skill_registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(),
                )
            ]
        )

        refreshed = client.post("/api/skill-sources/remote/refresh")

        assert refreshed.status_code == 200
        payload = refreshed.json()
        assert payload["source"]["name"] == "remote"
        assert payload["source"]["metadata"]["namespace"] == "single-refresh"
        assert "single_refresh_skill" in payload["skills"]


def test_skill_source_detail_route_returns_404_for_missing_source(client):
    listed = client.get("/api/skill-sources/missing")

    assert listed.status_code == 404


def test_skill_source_import_route_installs_bundle_into_target_source(client):
    workspace_backend = InMemorySkillBackend()
    client.app.state.services.skill_registry = SkillRegistry(
        skill_sources=[SkillSource(name="workspace", path="/workspace", writable=True, backend=workspace_backend)]
    )

    imported = client.post(
        "/api/skill-sources/workspace/skills",
        json={
            "name": "imported_skill",
            "files": {
                "SKILL.md": """---
name: imported_skill
description: Imported through API
version: 1.0.0
author: tests
enabled: true
---

# imported_skill
""",
                "notes.txt": "hello",
            },
        },
    )

    assert imported.status_code == 200
    assert imported.json()["result"]["source_name"] == "workspace"

    listed = client.get("/api/skills/imported_skill/files")
    assert listed.status_code == 200
    assert [item["path"] for item in listed.json()["files"]] == ["SKILL.md", "notes.txt"]


def test_workflow_approval_uses_shared_queue(client):
    system_agent = client.app.state.services.system_agent()
    workflow = system_agent.pyflow_engine.parse_workflow(
        """
name: gated_flow
nodes:
  - id: gate
    type: approve
    prompt: continue?
""".strip()
    )

    result = system_agent.pyflow_engine.run_workflow(workflow)

    assert result["status"] == "waiting_approval"
    approval_id = result["approval_id"]

    listed = client.get("/api/approvals")
    assert listed.status_code == 200
    approvals = listed.json()["approvals"]
    assert any(item["approval_id"] == approval_id for item in approvals)

    resolved = client.post(
        f"/api/approvals/{approval_id}/resolve",
        json={"approved": True, "approver": "ops", "note": "ship it"},
    )
    assert resolved.status_code == 200
    payload = resolved.json()
    assert payload["success"] is True
    assert payload["approval"]["approved"] is True
    assert payload["approval"]["resolved_by"] == "ops"
    assert payload["approval"]["resolution_note"] == "ship it"
    assert payload["result"]["status"] == "completed"

    recent = client.get("/api/approvals")
    assert recent.status_code == 200
    assert any(item["approval_id"] == approval_id for item in recent.json()["recent"])


def test_delegated_approval_route_resumes_parent_agent(client, monkeypatch):
    services = client.app.state.services
    request = services.approval_queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="delegated approval",
        prompt="allow?",
        callback=lambda approved, note: {"status": "completed", "response": "subagent done"},
    )
    services.approval_queue.update_request_metadata(request.approval_id, parent_thread_id="session-1")

    class DummyAgent:
        def resolve_approval(self, approval_id, *, approved, note="", approver=""):
            return {
                "success": True,
                "approval": {
                    "approval_id": approval_id,
                    "approved": approved,
                    "resolved_by": approver,
                    "resolution_note": note,
                },
                "result": {"status": "completed", "response": "parent resumed"},
            }

    monkeypatch.setattr(services.agents, "get_or_create", lambda thread_id: DummyAgent())

    resolved = client.post(
        f"/api/approvals/{request.approval_id}/resolve",
        json={"approved": True, "note": "ok", "approver": "lead"},
    )

    assert resolved.status_code == 200
    payload = resolved.json()
    assert payload["success"] is True
    assert payload["approval"]["resolved_by"] == "lead"
    assert payload["result"]["response"] == "parent resumed"


def test_approval_history_persists_to_disk(client):
    services = client.app.state.services
    request = services.approval_queue.create_request(
        kind="tool_call",
        scope="root:test",
        summary="persist me",
        prompt="allow?",
    )

    resolved = client.post(
        f"/api/approvals/{request.approval_id}/resolve",
        json={"approved": False, "note": "blocked", "approver": "reviewer"},
    )

    assert resolved.status_code == 200
    approval_file = services.paths.approvals_file
    assert approval_file.exists()

    reloaded_queue = ApprovalQueue(storage_path=approval_file)
    history = reloaded_queue.list_history()

    assert history[0]["approval_id"] == request.approval_id
    assert history[0]["resolved_by"] == "reviewer"
    assert history[0]["resolution_note"] == "blocked"


def test_delegated_workflow_approval_resolution_resumes_workflow(client, monkeypatch):
    services = client.app.state.services
    request = services.approval_queue.create_request(
        kind="tool_call",
        scope="subagent:helper",
        summary="delegated workflow approval",
        prompt="allow helper?",
        callback=lambda approved, note: {
            "status": "completed",
            "success": approved,
            "response": "helper done",
            "thread_id": "delegate-thread",
        },
    )
    services.approval_queue.update_request_metadata(
        request.approval_id,
        workflow_id="wf_delegate",
        workflow_resume_token="resume-123",
        workflow_pause_kind="delegated_subagent",
    )

    class DummyEngine:
        def resume_workflow(self, workflow_id, resume_token, approved, *, approval_id="", note="", resolved_by=""):
            return {
                "status": "completed",
                "workflow_id": workflow_id,
                "resume_token": resume_token,
                "approved": approved,
                "approval_id": approval_id,
                "note": note,
                "resolved_by": resolved_by,
            }

    class DummySystemAgent:
        pyflow_engine = DummyEngine()

    monkeypatch.setattr(services, "system_agent", lambda: DummySystemAgent())

    resolved = client.post(
        f"/api/approvals/{request.approval_id}/resolve",
        json={"approved": True, "note": "ok", "approver": "ops"},
    )

    assert resolved.status_code == 200
    payload = resolved.json()
    assert payload["success"] is True
    assert payload["subagent_result"]["response"] == "helper done"
    assert payload["result"]["workflow_id"] == "wf_delegate"
    assert payload["result"]["approval_id"] == request.approval_id
    assert payload["result"]["resolved_by"] == "ops"


def test_channel_webhook_get_verifies_wechat_signature(client):
    services = client.app.state.services
    system_agent = services.system_agent()
    channel = WeChatOfficialChannel(ChannelConfig(name="wechat", kind="wechat", token="wechat-token"))
    system_agent.channel_manager.register_channel(channel)

    timestamp = "1700000000"
    nonce = "nonce-1"
    signature = _sha1_signature("wechat-token", timestamp, nonce)

    response = client.get(
        "/api/webhook/wechat",
        params={
            "signature": signature,
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": "challenge-ok",
        },
    )

    assert response.status_code == 200
    assert response.text == "challenge-ok"
    assert response.headers["content-type"].startswith("text/plain")


def test_channel_webhook_post_returns_passive_wechat_reply(client):
    services = client.app.state.services
    system_agent = services.system_agent()
    system_agent.channel_manager.set_agent_callback(lambda message, thread_id: f"{thread_id}:{message.upper()}")
    channel = WeChatOfficialChannel(
        ChannelConfig(name="wechat", kind="wechat", token="wechat-token", app_id="gh_test", reply_mode="passive")
    )
    system_agent.channel_manager.register_channel(channel)

    timestamp = "1700000000"
    nonce = "nonce-2"
    signature = _sha1_signature("wechat-token", timestamp, nonce)
    xml_body = (
        "<xml>"
        "<ToUserName><![CDATA[gh_test]]></ToUserName>"
        "<FromUserName><![CDATA[user_123]]></FromUserName>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[hello]]></Content>"
        "<MsgId>123</MsgId>"
        "</xml>"
    )

    response = client.post(
        "/api/webhook/wechat",
        params={"signature": signature, "timestamp": timestamp, "nonce": nonce},
        content=xml_body,
        headers={"content-type": "application/xml"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<ToUserName><![CDATA[user_123]]></ToUserName>" in response.text
    assert "<Content><![CDATA[wechat:user_123:HELLO]]></Content>" in response.text


def test_channel_webhook_uses_channel_routes_for_mode_dispatch(client, monkeypatch):
    services = client.app.state.services

    class FakeAgent:
        def __init__(self, mode: str):
            self.mode = mode

        def chat(self, message: str) -> str:
            return f"[{self.mode}] {message}"

    monkeypatch.setattr(services.agents, "get_or_create_mode", lambda mode, thread_id: FakeAgent(mode))

    channel_manager = ChannelManager(
        str(services.paths.workspace_dir),
        channel_routes=[
            {
                "name": "wechat-app-matrix",
                "channel": "wechat",
                "target": "agent",
                "mode": "app_matrix",
                "thread_template": "wechat:{user_id}",
            }
        ],
    )
    channel_manager.register_channel(
        WeChatOfficialChannel(
            ChannelConfig(name="wechat", kind="wechat", token="wechat-token", app_id="gh_test", reply_mode="passive")
        )
    )

    class FakeSystemAgent:
        def __init__(self, manager: ChannelManager):
            self.channel_manager = manager

    monkeypatch.setattr(services, "system_agent", lambda: FakeSystemAgent(channel_manager))

    timestamp = "1700000000"
    nonce = "nonce-route"
    signature = _sha1_signature("wechat-token", timestamp, nonce)
    xml_body = (
        "<xml>"
        "<ToUserName><![CDATA[gh_test]]></ToUserName>"
        "<FromUserName><![CDATA[user_route]]></FromUserName>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[route me]]></Content>"
        "<MsgId>999</MsgId>"
        "</xml>"
    )

    response = client.post(
        "/api/webhook/wechat",
        params={"signature": signature, "timestamp": timestamp, "nonce": nonce},
        content=xml_body,
        headers={"content-type": "application/xml"},
    )

    assert response.status_code == 200
    assert "[app_matrix] route me" in response.text


def test_channel_webhook_rejects_invalid_wechat_signature(client):
    services = client.app.state.services
    system_agent = services.system_agent()
    system_agent.channel_manager.register_channel(
        WeChatOfficialChannel(ChannelConfig(name="wechat", kind="wechat", token="wechat-token"))
    )

    response = client.get(
        "/api/webhook/wechat",
        params={
            "signature": "bad-signature",
            "timestamp": "1700000000",
            "nonce": "nonce-3",
            "echostr": "challenge-ok",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid WeChat webhook signature"
