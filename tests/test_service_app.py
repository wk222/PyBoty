from __future__ import annotations

from collections import Counter
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware

from core.app_manager import AppManager
from core.approval_queue import ApprovalQueue
from core.skill_backends import InMemorySkillBackend
from core.skill_http_backend import HttpSkillBackend
from core.skill_registry import SkillRegistry
from core.skill_sources import SkillSource
from core.uv_env_manager import UvEnvManager
from core.version import get_pybot_version
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
