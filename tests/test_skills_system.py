from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.assets.skills.skill_backends import CompositeSkillBackend, InMemorySkillBackend
from core.assets.skills.skill_http_backend import HttpSkillBackend
from core.assets.skills.skill_models import SkillDefinition
from core.assets.skills.skill_registry import SkillRegistry
from core.assets.skills.skill_runtime import TRUSTED_SKILLS_ENV
from core.assets.skills.skill_sources import SkillSource
from core.assets.skills.skill_prompts import render_active_skill_extensions
from core.assets.skills.skill_runtime import TRUSTED_SKILLS_ENV, TYPE_MAP, build_tool_from_definition, load_python_module_tools
from core.systems.runtime.component_serialization import (
    AgentSpec,
    TeamSpec,
    ToolSpec,
    WorkflowSpec,
    deserialize_component,
    export_components,
    from_json,
    import_components,
    register_component_type,
    serialize_component,
    to_json,
)
from tests.support.skill_http_server import HttpRouteResponse, serve_skill_http


def test_skill_registry_discovers_and_builds_tools(temp_paths):
    skill_dir = temp_paths.skills_dir / "note_helper"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: note_helper
description: Summarize small note payloads
version: 1.0.0
author: tests
enabled: true
---

# note_helper

## 能力
- summarization

## 系统提示
Use note_helper when the user asks to summarize notes.
""",
        encoding="utf-8",
    )
    (skill_dir / "tools.json").write_text(
        json.dumps(
            [
                {
                    "name": "note_count",
                    "description": "Count note characters",
                    "parameters": [{"name": "text", "type": "str", "description": "note text"}],
                    "code": "result = len(text)",
                    "dependencies": [],
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    registry = SkillRegistry(str(temp_paths.skills_dir))

    assert "note_helper" in registry.list_skills()
    assert "note_helper" in registry.get_active_prompt_extensions(progressive=True)
    assert [tool.name for tool in registry.get_active_tools()] == ["note_count"]


def test_skill_registry_install_and_toggle(temp_paths):
    registry = SkillRegistry(str(temp_paths.skills_dir))
    skill = SkillDefinition(
        name="builder_notes",
        description="Build note helpers",
        capabilities=["notes"],
        system_prompt_extension="Use builder_notes when composing note workflows.",
    )

    assert registry.install_skill("builder_notes", skill) is True
    assert registry.toggle_skill("builder_notes", False) is True

    reloaded = SkillRegistry(str(temp_paths.skills_dir))
    saved = reloaded.get_skill("builder_notes")

    assert saved is not None
    assert saved.enabled is False


def test_skill_registry_loads_trusted_python_module_tools(temp_paths, monkeypatch):
    skill_dir = temp_paths.skills_dir / "module_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: module_skill
description: Adds a trusted module-backed skill
version: 1.0.0
author: tests
enabled: true
---

# module_skill

## 系统提示
Use module_skill when a module-backed action is needed.
""",
        encoding="utf-8",
    )
    (skill_dir / "tools.py").write_text(
        """from langchain.tools import tool

@tool
def module_note(text: str) -> str:
    \"\"\"Echo note text.\"\"\"
    return text.upper()
""",
        encoding="utf-8",
    )

    registry = SkillRegistry(str(temp_paths.skills_dir))
    assert [tool.name for tool in registry.get_active_tools()] == []

    monkeypatch.setenv(TRUSTED_SKILLS_ENV, "module_skill")
    trusted_registry = SkillRegistry(str(temp_paths.skills_dir))
    assert [tool.name for tool in trusted_registry.get_active_tools()] == ["module_note"]


def test_skill_registry_deduplicates_tool_names_within_skill(temp_paths, monkeypatch):
    skill_dir = temp_paths.skills_dir / "duplicate_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: duplicate_skill
description: Defines the same tool in two places
version: 1.0.0
author: tests
enabled: true
---

# duplicate_skill
""",
        encoding="utf-8",
    )
    (skill_dir / "tools.json").write_text(
        json.dumps(
            [
                {
                    "name": "shared_tool",
                    "description": "Primary implementation",
                    "parameters": [{"name": "text", "type": "str", "description": "input"}],
                    "code": "result = text.lower()",
                    "dependencies": [],
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (skill_dir / "tools.py").write_text(
        """from langchain.tools import tool

@tool
def shared_tool(text: str) -> str:
    \"\"\"Alternate implementation.\"\"\"
    return text.upper()
""",
        encoding="utf-8",
    )

    monkeypatch.setenv(TRUSTED_SKILLS_ENV, "duplicate_skill")
    registry = SkillRegistry(str(temp_paths.skills_dir))
    assert [tool.name for tool in registry.get_active_tools()] == ["shared_tool"]


def test_skill_registry_layers_sources_with_last_source_winning(tmp_path: Path):
    base_dir = tmp_path / "base_skills"
    project_dir = tmp_path / "project_skills"
    _write_skill(
        base_dir,
        "shared_skill",
        description="Base implementation",
        prompt="Use the base skill.",
    )
    _write_skill(
        project_dir,
        "shared_skill",
        description="Project override",
        prompt="Use the project override.",
    )

    registry = SkillRegistry(
        skill_sources=[
            SkillSource(name="base", path=base_dir, writable=False),
            SkillSource(name="project", path=project_dir, writable=True),
        ]
    )

    skill = registry.get_skill("shared_skill")

    assert skill is not None
    assert skill.description == "Project override"
    assert skill.source_name == "project"
    assert registry.list_skills()["shared_skill"]["source_name"] == "project"
    assert "@project" in registry.get_active_prompt_extensions(progressive=True)


def test_skill_registry_lists_source_descriptors(tmp_path: Path):
    base_dir = tmp_path / "base_skills"
    project_dir = tmp_path / "project_skills"
    base_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    registry = SkillRegistry(
        skill_sources=[
            SkillSource(name="base", path=base_dir, writable=False),
            SkillSource(name="project", path=project_dir, writable=True),
        ]
    )

    sources = registry.list_sources()

    assert [source["name"] for source in sources] == ["base", "project"]
    assert sources[0]["backend"] == "filesystem"
    assert sources[0]["writable"] is False
    assert sources[1]["writable"] is True
    assert sources[1]["capabilities"]["has_native_local_path"] is True


def test_skill_registry_remove_reveals_lower_precedence_skill(tmp_path: Path):
    base_dir = tmp_path / "base_skills"
    project_dir = tmp_path / "project_skills"
    _write_skill(
        base_dir,
        "shared_skill",
        description="Base implementation",
        prompt="Use the base skill.",
    )
    _write_skill(
        project_dir,
        "shared_skill",
        description="Project override",
        prompt="Use the project override.",
    )

    registry = SkillRegistry(
        skill_sources=[
            SkillSource(name="base", path=base_dir, writable=False),
            SkillSource(name="project", path=project_dir, writable=True),
        ]
    )

    assert registry.remove_skill("shared_skill") is True

    revealed = registry.get_skill("shared_skill")
    assert revealed is not None
    assert revealed.description == "Base implementation"
    assert revealed.source_name == "base"
    assert revealed.writable is False


def test_skill_registry_supports_backend_agnostic_sources():
    backend = InMemorySkillBackend(
        files={
            "/vendor/math_helper/SKILL.md": """---
name: math_helper
description: Memory-backed skill
version: 1.0.0
author: tests
enabled: true
---

# math_helper

## 系统提示
Use the memory-backed skill.
""",
            "/vendor/math_helper/tools.json": json.dumps(
                [
                    {
                        "name": "double_number",
                        "description": "Double an integer",
                        "parameters": [{"name": "value", "type": "int", "description": "input"}],
                        "code": "result = value * 2",
                        "dependencies": [],
                    }
                ],
                ensure_ascii=False,
            ),
        }
    )

    registry = SkillRegistry(
        skill_sources=[SkillSource(name="vendor", path="/vendor", writable=False, backend=backend)]
    )

    skill = registry.get_skill("math_helper")
    assert skill is not None
    assert skill.source_backend == "memory"
    assert skill.skill_dir == ""
    assert [tool.name for tool in registry.get_active_tools()] == ["double_number"]


def test_skill_registry_loads_trusted_python_tools_from_memory_backend(monkeypatch):
    backend = InMemorySkillBackend(
        files={
            "/vendor/module_skill/SKILL.md": """---
name: module_skill
description: Memory-backed module skill
version: 1.0.0
author: tests
enabled: true
---

# module_skill
""",
            "/vendor/module_skill/tools.py": """from langchain.tools import tool

@tool
def memory_module_note(text: str) -> str:
    \"\"\"Echo note text.\"\"\"
    return text.upper()
""",
        }
    )
    monkeypatch.setenv(TRUSTED_SKILLS_ENV, "module_skill")

    registry = SkillRegistry(
        skill_sources=[SkillSource(name="vendor", path="/vendor", writable=False, backend=backend)]
    )

    assert [tool.name for tool in registry.get_active_tools()] == ["memory_module_note"]


def test_skill_registry_reads_and_writes_files_via_source_backend():
    backend = InMemorySkillBackend(
        files={
            "/workspace/note_skill/SKILL.md": """---
name: note_skill
description: Editable memory skill
version: 1.0.0
author: tests
enabled: true
---

# note_skill
""",
            "/workspace/note_skill/notes.txt": "hello",
        }
    )

    registry = SkillRegistry(
        skill_sources=[SkillSource(name="workspace", path="/workspace", writable=True, backend=backend)]
    )

    assert registry.list_skill_files("note_skill") == [
        {"path": "SKILL.md", "size": len(backend.files["/workspace/note_skill/SKILL.md"].encode("utf-8"))},
        {"path": "notes.txt", "size": len(b"hello")},
    ]
    assert registry.read_skill_file("note_skill", "notes.txt") == "hello"

    assert registry.write_skill_file("note_skill", "notes.txt", "updated") is True
    assert registry.read_skill_file("note_skill", "notes.txt") == "updated"


def test_skill_registry_copies_skill_between_backend_sources():
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

## 系统提示
Use the vendor skill.
""",
            "/vendor/shared_skill/references.txt": "vendor reference",
        }
    )
    workspace_backend = InMemorySkillBackend()
    registry = SkillRegistry(
        skill_sources=[
            SkillSource(name="vendor", path="/vendor", writable=False, backend=vendor_backend),
            SkillSource(name="workspace", path="/workspace", writable=True, backend=workspace_backend),
        ]
    )

    result = registry.copy_skill_to_source("shared_skill", target_source_name="workspace")

    assert result is not None
    assert result["source_name"] == "workspace"
    assert workspace_backend.read_text("/workspace/shared_skill/SKILL.md").startswith("---")
    copied = registry.get_skill("shared_skill")
    assert copied is not None
    assert copied.source_name == "workspace"


def test_skill_registry_imports_bundle_to_named_source():
    workspace_backend = InMemorySkillBackend()
    registry = SkillRegistry(
        skill_sources=[SkillSource(name="workspace", path="/workspace", writable=True, backend=workspace_backend)]
    )

    result = registry.import_skill_bundle(
        "imported_skill",
        {
            "SKILL.md": """---
name: imported_skill
description: Imported through backend protocol
version: 1.0.0
author: tests
enabled: true
---

# imported_skill
""",
            "notes.txt": "imported",
        },
        target_source_name="workspace",
    )

    assert result["source_name"] == "workspace"
    assert registry.read_skill_file("imported_skill", "notes.txt") == "imported"


def test_skill_registry_async_reload_supports_http_backend():
    with serve_skill_http(
        {
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
                    "notes.txt": "from http",
                }
            },
        }
    ) as base_url:
        registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(),
                )
            ]
        )

        asyncio.run(registry.areload())

        skill = registry.get_skill("http_skill")
        assert skill is not None
        assert skill.source_backend == "http"
        assert registry.read_skill_file("http_skill", "notes.txt") == "from http"
        assert registry.list_sources()[0]["capabilities"]["remote"] is True


def test_skill_registry_async_refresh_invalidates_http_cache():
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
        registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(),
                )
            ]
        )

        assert registry.read_skill_file("http_skill", "notes.txt") == "first version"

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

        asyncio.run(registry.areload(refresh_sources=True))

        assert registry.read_skill_file("http_skill", "notes.txt") == "second version"


def test_skill_registry_supports_paginated_http_registry_metadata():
    with serve_skill_http(
        {
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
    ) as base_url:
        registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(page_limit=4, max_concurrency=2, min_request_interval=0.0),
                )
            ]
        )

        asyncio.run(registry.areload(refresh_sources=True))
        sources = asyncio.run(registry.alist_sources())

        assert {"alpha_skill", "beta_skill"} <= set(registry.list_skills())
        assert sources[0]["metadata"]["namespace"] == "vendor-team"
        assert sources[0]["metadata"]["page_count"] == 2
        assert sources[0]["capabilities"]["supports_request_backpressure"] is True


def test_skill_registry_supports_openclaw_repo_roots_and_metadata(tmp_path: Path):
    repo_root = tmp_path / "openclaw-main"
    skill_dir = repo_root / "skills" / "weather"
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts" / "query_weather.py").write_text("print('ok')\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        """---
name: weather
description: "Get weather information"
version: 1.2.3
author: openclaw
homepage: https://wttr.in/:help
user-invocable: true
metadata:
  openclaw:
    emoji: "🌤️"
    requires:
      bins: ["curl"]
      config: ["channels.weather"]
    primaryEnv: "WEATHER_TOKEN"
---

# Weather Skill

Run:

python {baseDir}/scripts/query_weather.py --city "Shanghai"
""",
        encoding="utf-8",
    )

    registry = SkillRegistry(
        skill_sources=[SkillSource(name="openclaw", path=repo_root, writable=False, flavor="openclaw")]
    )

    skill = registry.get_skill("weather")
    assert skill is not None
    assert skill.skill_format == "openclaw"
    assert skill.homepage == "https://wttr.in/:help"
    assert skill.user_invocable is True
    assert skill.requires_bins == ["curl"]
    assert skill.requires_config == ["channels.weather"]
    assert skill.primary_env == "WEATHER_TOKEN"
    assert skill.source_name == "openclaw"

    listed = registry.list_skills()["weather"]
    assert listed["skill_format"] == "openclaw"
    assert listed["openclaw_metadata"]["emoji"] == "🌤️"

    source = registry.list_sources()[0]
    assert source["flavor"] == "openclaw"
    assert str(source["path"]).endswith("skills")

    rendered = registry.read_skill_file("weather", "SKILL.md")
    assert rendered is not None
    assert "{baseDir}" not in rendered
    assert "/skills/weather/scripts/query_weather.py" in rendered.replace("\\", "/")

    prompt = registry.get_active_prompt_extensions(progressive=True)
    assert "format=openclaw" in prompt
    assert "WEATHER_TOKEN" in prompt


def test_skill_registry_supports_cursor_paginated_http_registry_metadata():
    request_log: list[dict[str, object]] = []
    with serve_skill_http(
        {
            "/remote/.well-known/skill-registry.json": {
                "catalog_path": "catalog/index.json",
                "pagination": {
                    "mode": "cursor",
                    "cursor_param": "cursor",
                    "cursor_field": "next_cursor",
                    "page_size_param": "page_size",
                    "page_size": 1,
                },
                "registry": {"namespace": "cursor-team"},
            },
            "/remote/catalog/index.json?page_size=1": {
                "skills": [
                    {
                        "name": "alpha_skill",
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
                        },
                    }
                ],
                "next_cursor": "cursor-2",
            },
            "/remote/catalog/index.json?page_size=1&cursor=cursor-2": {
                "skills": [
                    {
                        "name": "beta_skill",
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
                        },
                    }
                ]
            },
        },
        request_log=request_log,
    ) as base_url:
        registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(page_limit=4),
                )
            ]
        )

        source = registry.list_sources()[0]

        assert {"alpha_skill", "beta_skill"} <= set(registry.list_skills())
        assert source["metadata"]["namespace"] == "cursor-team"
        assert source["metadata"]["pagination_mode"] == "cursor"
        assert source["metadata"]["pagination_cursor_param"] == "cursor"
        assert source["metadata"]["page_count"] == 2
        assert request_log[1]["path"] == "/remote/catalog/index.json?page_size=1"
        assert request_log[2]["path"] == "/remote/catalog/index.json?page_size=1&cursor=cursor-2"
        assert registry.read_skill_file("beta_skill", "SKILL.md") is not None


def test_skill_registry_surfaces_conditional_refresh_reports_and_auth_headers():
    request_log: list[dict[str, object]] = []
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
        request_log=request_log,
    ) as base_url:
        registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(bearer_token="secret-token"),
                )
            ]
        )

        asyncio.run(registry.areload(refresh_sources=True))
        source = asyncio.run(registry.alist_sources())[0]

        assert source["metadata"]["etag"] == "registry-v1"
        assert source["metadata"]["refresh_status"] == "not_modified"
        assert source["refresh_report"]["status"] == "not_modified"
        assert source["refresh_report"]["conditional_request"] is True
        assert source["refresh_report"]["auth_configured"] is True
        assert request_log[-1]["headers"]["Authorization"] == "Bearer secret-token"
        assert request_log[-1]["headers"]["If-None-Match"] == "registry-v1"


def test_skill_registry_installs_and_exports_backend_agnostic_skill_bundles(monkeypatch):
    backend = InMemorySkillBackend()
    monkeypatch.setenv(TRUSTED_SKILLS_ENV, "bundle_skill")
    registry = SkillRegistry(
        skill_sources=[SkillSource(name="workspace", path="/workspace", writable=True, backend=backend)]
    )

    assert (
        registry.install_skill_bundle(
            "bundle_skill",
            {
                "SKILL.md": """---
name: bundle_skill
description: Installed from a bundle
version: 1.0.0
author: tests
enabled: true
---

# bundle_skill

## 系统提示
Use bundle_skill.
""",
                "tools.py": """from langchain.tools import tool

@tool
def bundle_tool(value: int) -> int:
    \"\"\"Double a value.\"\"\"
    return value * 2
""",
                "references/notes.txt": "bundle reference",
            },
        )
        is True
    )

    assert "bundle_skill" in registry.list_skills()
    assert [tool.name for tool in registry.get_active_tools()] == ["bundle_tool"]
    assert registry.export_skill_bundle("bundle_skill") == {
        "SKILL.md": """---
name: bundle_skill
description: Installed from a bundle
version: 1.0.0
author: tests
enabled: true
---

# bundle_skill

## 系统提示
Use bundle_skill.
""",
        "tools.py": """from langchain.tools import tool

@tool
def bundle_tool(value: int) -> int:
    \"\"\"Double a value.\"\"\"
    return value * 2
""",
        "references/notes.txt": "bundle reference",
    }


def test_skill_registry_negotiates_registry_descriptor_and_retry_after():
    request_log: list[dict[str, object]] = []
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
                    headers={"ETag": "catalog-v2", "X-Registry-Version": "2026.03"},
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
        },
        request_log=request_log,
    ) as base_url:
        registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=HttpSkillBackend(retry_attempts=3),
                )
            ]
        )

        source = registry.list_sources()[0]

        assert "descriptor_skill" in registry.list_skills()
        assert source["descriptor"]["catalog_path"] == "catalog/index.json"
        assert source["descriptor"]["bundle_path_template"] == "bundles/{skill_name}.json"
        assert source["metadata"]["descriptor_loaded"] is True
        assert source["metadata"]["namespace"] == "descriptor-team"
        assert source["metadata"]["topic"] == "descriptors"
        assert source["metadata"]["backpressure_events"] == 1
        assert source["refresh_report"]["backpressure_events"] == 1
        assert source["refresh_report"]["registry_version"] == "2026.03"
        assert source["capabilities"]["supports_registry_descriptor"] is True
        assert request_log[0]["path"] == "/remote/.well-known/skill-registry.json"
        assert request_log[1]["path"] == "/remote/catalog/index.json"
        assert request_log[2]["path"] == "/remote/catalog/index.json"
        assert registry.read_skill_file("descriptor_skill", "SKILL.md") is not None


def test_skill_registry_negotiates_auth_and_session_with_remote_registry():
    request_log: list[dict[str, object]] = []
    with serve_skill_http(
        {
            "/remote/.well-known/skill-registry.json": {
                "catalog_path": "catalog.json",
                "auth": {
                    "modes": ["client_credentials"],
                    "token_endpoint": "/auth/token",
                },
                "session": {
                    "initiate_path": "/session/init",
                    "session_header": "X-Registry-Session",
                    "ttl_seconds": 3600,
                    "keepalive_path": "/session/keepalive",
                    "cursor_mode": "session_bound",
                },
            },
            "/remote/catalog.json": {
                "skills": [{"name": "session_skill"}],
            },
            "/remote/session_skill/bundle.json": {
                "files": {
                    "SKILL.md": "---\nname: session_skill\ndescription: Sess\n"
                    "version: 1.0.0\nauthor: tests\nenabled: true\n---\n\n# session_skill\n"
                }
            },
        },
        post_routes={
            "/remote/auth/token": {
                "access_token": "negotiated-token-abc",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
            "/remote/session/init": {
                "session_id": "sess-xyz-123",
                "expires_in": 3600,
            },
        },
        request_log=request_log,
    ) as base_url:
        backend = HttpSkillBackend(
            client_id="my-client",
            client_secret="my-secret",
        )
        registry = SkillRegistry(
            skill_sources=[
                SkillSource(
                    name="remote",
                    path=f"{base_url}/remote",
                    writable=False,
                    backend=backend,
                )
            ]
        )

        source = registry.list_sources()[0]

        assert source["metadata"]["auth_negotiated"] is True
        assert source["metadata"]["session_active"] is True
        assert source["metadata"]["session_cursor_mode"] == "session_bound"
        assert source["metadata"]["session_header"] == "X-Registry-Session"
        assert source["refresh_report"]["auth_negotiated"] is True
        assert source["refresh_report"]["session_active"] is True
        assert source["capabilities"]["supports_auth_negotiation"] is True
        assert source["capabilities"]["supports_session_negotiation"] is True

        token_reqs = [r for r in request_log if r["path"] == "/remote/auth/token"]
        assert len(token_reqs) == 1
        assert token_reqs[0]["method"] == "POST"

        session_reqs = [r for r in request_log if r["path"] == "/remote/session/init"]
        assert len(session_reqs) == 1
        assert session_reqs[0]["method"] == "POST"
        assert session_reqs[0]["headers"]["Authorization"] == "Bearer negotiated-token-abc"

        catalog_reqs = [r for r in request_log if r["path"] == "/remote/catalog.json"]
        assert len(catalog_reqs) >= 1
        assert catalog_reqs[0]["headers"]["Authorization"] == "Bearer negotiated-token-abc"
        assert catalog_reqs[0]["headers"]["X-Registry-Session"] == "sess-xyz-123"

        assert "session_skill" in registry.list_skills()


def test_http_backend_renegotiates_token_on_401():
    """When a catalog request returns 401, HttpSkillBackend re-negotiates the token and retries."""
    call_counter = {"token_calls": 0}
    request_log: list[dict[str, object]] = []

    def _make_token_response():
        call_counter["token_calls"] += 1
        return {
            "access_token": f"token-gen-{call_counter['token_calls']}",
            "token_type": "Bearer",
            "expires_in": 1,
        }

    with serve_skill_http(
        {
            "/r/.well-known/skill-registry.json": {
                "catalog_path": "catalog.json",
                "auth": {"token_endpoint": "/auth/token"},
            },
            "/r/catalog.json": [
                HttpRouteResponse(status=401),
                {"skills": [{"name": "retried_skill"}]},
            ],
            "/r/retried_skill/bundle.json": {
                "files": {
                    "SKILL.md": (
                        "---\nname: retried_skill\ndescription: r\nversion: 1.0.0\nauthor: t\nenabled: true\n---\n"
                    )
                }
            },
        },
        post_routes={
            "/r/auth/token": _make_token_response(),
        },
        request_log=request_log,
    ) as base_url:
        backend = HttpSkillBackend(
            client_id="cid",
            client_secret="csec",
            retry_attempts=3,
        )
        registry = SkillRegistry(
            skill_sources=[SkillSource(name="r", path=f"{base_url}/r", writable=False, backend=backend)]
        )
        assert "retried_skill" in registry.list_skills()

        catalog_reqs = [r for r in request_log if r["path"] == "/r/catalog.json"]
        assert len(catalog_reqs) >= 2
        assert catalog_reqs[1]["headers"]["Authorization"].startswith("Bearer token-gen-")


def test_http_backend_session_keepalive():
    """Session keepalive is sent when the session is past 75% of its TTL."""
    request_log: list[dict[str, object]] = []
    with serve_skill_http(
        {
            "/k/.well-known/skill-registry.json": {
                "catalog_path": "catalog.json",
                "auth": {"token_endpoint": "/auth/token"},
                "session": {
                    "initiate_path": "/session/init",
                    "session_header": "X-Sess",
                    "ttl_seconds": 2,
                    "keepalive_path": "/session/keepalive",
                },
            },
            "/k/catalog.json": {"skills": [{"name": "ka_skill"}]},
            "/k/ka_skill/bundle.json": {
                "files": {
                    "SKILL.md": ("---\nname: ka_skill\ndescription: k\nversion: 1.0.0\nauthor: t\nenabled: true\n---\n")
                }
            },
        },
        post_routes={
            "/k/auth/token": {
                "access_token": "tok-ka",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
            "/k/session/init": {
                "session_id": "sess-ka",
                "expires_in": 2,
            },
            "/k/session/keepalive": {
                "expires_in": 3600,
            },
        },
        request_log=request_log,
    ) as base_url:
        import time as _time

        backend = HttpSkillBackend(
            client_id="cid",
            client_secret="csec",
        )
        registry = SkillRegistry(
            skill_sources=[SkillSource(name="k", path=f"{base_url}/k", writable=False, backend=backend)]
        )
        assert "ka_skill" in registry.list_skills()

        _time.sleep(1.6)
        registry.refresh_sources()

        keepalive_reqs = [r for r in request_log if r["path"] == "/k/session/keepalive"]
        assert len(keepalive_reqs) >= 1
        assert keepalive_reqs[0]["headers"]["X-Sess"] == "sess-ka"


def test_composite_skill_backend_routes_by_prefix():
    """CompositeSkillBackend routes operations to child backends by longest prefix."""
    default_backend = InMemorySkillBackend(
        files={"/alpha/SKILL.md": "---\nname: alpha\ndescription: A\nversion: 1.0.0\nauthor: t\nenabled: true\n---\n"}
    )
    overlay_backend = InMemorySkillBackend(
        files={"/beta/SKILL.md": "---\nname: beta\ndescription: B\nversion: 1.0.0\nauthor: t\nenabled: true\n---\n"}
    )
    composite = CompositeSkillBackend(
        default=default_backend,
        routes={"/overlay": overlay_backend},
    )

    caps = composite.capabilities()
    assert caps.can_read_bundle is True
    assert caps.has_native_local_path is False
    assert composite.backend_name == "composite"

    info = composite.describe_root("/some/root")
    assert "composite/" in info.backend_name
    assert info.metadata["composite_route"] == "default"

    overlay_info = composite.describe_root("/overlay")
    assert overlay_info.metadata["composite_route"] == "/overlay"

    all_dirs = composite.list_skill_dirs("/")
    assert "alpha" in all_dirs

    assert composite.exists("/alpha/SKILL.md")
    text = composite.read_text("/alpha/SKILL.md")
    assert "alpha" in text

    assert composite.exists("/overlay/beta/SKILL.md")
    overlay_text = composite.read_text("/overlay/beta/SKILL.md")
    assert "beta" in overlay_text

    assert composite.local_path("/alpha") is None
    assert composite.get_refresh_report("/overlay") is None


def test_composite_skill_backend_write_routes():
    """Writes through composite are routed to the correct child backend."""
    primary = InMemorySkillBackend(files={})
    secondary = InMemorySkillBackend(files={})
    composite = CompositeSkillBackend(
        default=primary,
        routes={"/ext": secondary},
    )

    composite.write_text("/ext/new_skill/SKILL.md", "# New")
    assert secondary.exists("/new_skill/SKILL.md")
    assert not primary.exists("/ext/new_skill/SKILL.md")

    composite.write_text("/default_file.txt", "hello")
    assert primary.read_text("/default_file.txt") == "hello"


def _write_skill(root: Path, name: str, *, description: str, prompt: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: {description}
version: 1.0.0
author: tests
enabled: true
---

# {name}

## 系统提示
{prompt}
""",
        encoding="utf-8",
    )


# ── Section: Skill Runtime Extras ───────────────────

"""Tests for skill_runtime, skill_prompts, and skill_models edge cases."""





def test_build_tool_returns_none_when_name_missing():
    assert build_tool_from_definition({"code": "print(1)"}, "s") is None


def test_build_tool_returns_none_when_code_missing():
    assert build_tool_from_definition({"name": "t"}, "s") is None


def test_build_tool_with_empty_parameters():
    tool = build_tool_from_definition(
        {"name": "noop", "description": "d", "code": "pass", "parameters": []},
        "s",
    )
    assert tool is not None
    assert tool.name == "noop"


def test_build_tool_maps_parameter_types():
    params = [
        {"name": "a", "type": "int", "description": ""},
        {"name": "b", "type": "float", "description": ""},
        {"name": "c", "type": "bool", "description": ""},
        {"name": "d", "type": "list", "description": ""},
        {"name": "e", "type": "dict", "description": ""},
        {"name": "f", "type": "string", "description": ""},
    ]
    tool = build_tool_from_definition(
        {"name": "typed", "description": "d", "code": "pass", "parameters": params},
        "s",
    )
    assert tool is not None
    schema = tool.args_schema.model_json_schema()
    assert schema["properties"]["a"]["type"] == "integer"
    assert schema["properties"]["b"]["type"] == "number"
    assert schema["properties"]["c"]["type"] == "boolean"
    assert schema["properties"]["f"]["type"] == "string"


def test_build_tool_with_default_values():
    tool = build_tool_from_definition(
        {
            "name": "defaults",
            "description": "d",
            "code": "pass",
            "parameters": [{"name": "x", "type": "str", "description": "", "default": "hi"}],
        },
        "s",
    )
    assert tool is not None
    schema = tool.args_schema.model_json_schema()
    assert schema["properties"]["x"]["default"] == "hi"


def test_build_tool_passes_env_overrides_into_runtime(monkeypatch):
    monkeypatch.delenv("WEATHER_TOKEN", raising=False)
    tool = build_tool_from_definition(
        {
            "name": "show_env",
            "description": "d",
            "code": "import os\nresult = os.environ.get('WEATHER_TOKEN')",
            "parameters": [],
        },
        "weather",
        env_overrides={"WEATHER_TOKEN": "bridge-token"},
    )

    assert tool is not None
    result = tool._run()
    assert "bridge-token" in result


def test_load_python_module_tools_uses_env_overrides(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(TRUSTED_SKILLS_ENV, "*")
    monkeypatch.delenv("BRIDGED_TOKEN", raising=False)
    (tmp_path / "tools.py").write_text(
        """from langchain.tools import tool
import os

@tool
def read_bridged_env() -> str:
    \"\"\"Read an injected env var.\"\"\"
    return os.environ.get("BRIDGED_TOKEN", "")
""",
        encoding="utf-8",
    )

    tools = load_python_module_tools(
        tmp_path,
        "openclaw_skill",
        env_overrides={"BRIDGED_TOKEN": "from-openclaw"},
    )

    assert len(tools) == 1
    assert tools[0].invoke({}) == "from-openclaw"


def test_type_map_covers_all_common_types():
    expected = {
        "str",
        "string",
        "int",
        "integer",
        "float",
        "number",
        "bool",
        "boolean",
        "list",
        "array",
        "dict",
        "object",
    }
    assert set(TYPE_MAP.keys()) == expected


def test_render_active_skill_extensions_empty_skills():
    assert render_active_skill_extensions([]) == ""


def test_render_active_skill_extensions_excludes_disabled():
    disabled = SkillDefinition(name="off", description="d", enabled=False)
    assert render_active_skill_extensions([disabled]) == ""


def test_render_active_skill_extensions_progressive_true():
    skill = SkillDefinition(
        name="helper",
        description="A helper skill",
        version="2.0.0",
        capabilities=["cap1", "cap2"],
        tools=[{"name": "t1"}],
        source_name="user",
    )
    result = render_active_skill_extensions([skill], progressive=True)
    assert "**helper**" in result
    assert "(v2.0.0)" in result
    assert "1 tools" in result
    assert "@user" in result
    assert "cap1" in result


def test_render_active_skill_extensions_progressive_false():
    skill = SkillDefinition(
        name="full",
        description="d",
        system_prompt_extension="Use this skill to do things.",
    )
    result = render_active_skill_extensions([skill], progressive=False)
    assert "### 技能: full" in result
    assert "Use this skill to do things." in result


def test_render_active_skill_extensions_truncates_long_description():
    skill = SkillDefinition(name="long", description="x" * 200)
    result = render_active_skill_extensions([skill], progressive=True)
    assert len([line for line in result.split("\n") if "**long**" in line][0]) < 300


def test_skill_definition_to_dict_strips_tool_instance():
    tool_with_instance = {"name": "t", "code": "pass", "_tool_instance": object()}
    skill = SkillDefinition(name="s", description="d", tools=[tool_with_instance])
    serialized = skill.to_dict()
    assert "_tool_instance" not in serialized["tools"][0]
    assert serialized["tools"][0]["name"] == "t"


def test_skill_definition_to_dict_roundtrip():
    skill = SkillDefinition(
        name="rt",
        description="roundtrip",
        version="3.0.0",
        capabilities=["a", "b"],
        source_name="test",
    )
    d = skill.to_dict()
    assert d["name"] == "rt"
    assert d["capabilities"] == ["a", "b"]
    assert d["source_name"] == "test"
    assert isinstance(d["installed_at"], float)

# ── Section: Component Serialization ────────────────

"""Tests for core.component_serialization."""






class TestAgentSpec:
    def test_roundtrip(self):
        agent = AgentSpec(name="analyst", role="数据分析师", model="gpt-4", capabilities=["python"])
        d = agent.to_dict()
        restored = AgentSpec.from_dict(d)
        assert restored.name == "analyst"
        assert restored.role == "数据分析师"
        assert restored.model == "gpt-4"
        assert restored.capabilities == ["python"]

    def test_type_marker(self):
        agent = AgentSpec(name="x")
        d = agent.to_dict()
        assert d["_type"] == "agent"

    def test_component_type(self):
        assert AgentSpec(name="x").component_type == "agent"

    def test_defaults(self):
        agent = AgentSpec(name="min")
        assert agent.temperature == 0.7
        assert agent.tools == []
        assert agent.metadata == {}

    def test_extra_fields_ignored(self):
        d = {"name": "a", "role": "r", "unknown_field": 42, "_type": "agent"}
        agent = AgentSpec.from_dict(d)
        assert agent.name == "a"


class TestToolSpec:
    def test_roundtrip(self):
        tool = ToolSpec(name="search", description="搜索工具", cacheable=True, ttl=60.0)
        d = tool.to_dict()
        restored = ToolSpec.from_dict(d)
        assert restored.name == "search"
        assert restored.cacheable is True
        assert restored.ttl == 60.0

    def test_type_marker(self):
        assert ToolSpec(name="x").to_dict()["_type"] == "tool"


class TestTeamSpec:
    def test_roundtrip(self):
        team = TeamSpec(
            name="research",
            agents=[{"name": "a1", "role": "r1"}, {"name": "a2", "role": "r2"}],
            selector_type="llm",
            max_rounds=3,
            mode="society_of_mind",
        )
        d = team.to_dict()
        restored = TeamSpec.from_dict(d)
        assert restored.name == "research"
        assert len(restored.agents) == 2
        assert restored.mode == "society_of_mind"
        assert restored.max_rounds == 3

    def test_defaults(self):
        team = TeamSpec(name="t")
        assert team.selector_type == "round_robin"
        assert team.mode == "team"


class TestWorkflowSpec:
    def test_roundtrip(self):
        wf = WorkflowSpec(
            name="pipeline",
            nodes=[{"id": "n1", "type": "task"}, {"id": "n2", "type": "task"}],
            edges=[{"from": "n1", "to": "n2"}],
            variables={"input": "data"},
        )
        d = wf.to_dict()
        restored = WorkflowSpec.from_dict(d)
        assert restored.name == "pipeline"
        assert len(restored.nodes) == 2
        assert len(restored.edges) == 1
        assert restored.variables == {"input": "data"}


class TestSerializeDeserialize:
    def test_serialize(self):
        agent = AgentSpec(name="a", role="r")
        d = serialize_component(agent)
        assert d["_type"] == "agent"
        assert d["name"] == "a"

    def test_deserialize_agent(self):
        d = {"_type": "agent", "name": "test", "role": "tester"}
        comp = deserialize_component(d)
        assert isinstance(comp, AgentSpec)
        assert comp.name == "test"

    def test_deserialize_tool(self):
        d = {"_type": "tool", "name": "calc"}
        comp = deserialize_component(d)
        assert isinstance(comp, ToolSpec)

    def test_deserialize_team(self):
        d = {"_type": "team", "name": "squad"}
        comp = deserialize_component(d)
        assert isinstance(comp, TeamSpec)

    def test_deserialize_workflow(self):
        d = {"_type": "workflow", "name": "flow"}
        comp = deserialize_component(d)
        assert isinstance(comp, WorkflowSpec)

    def test_missing_type(self):
        with pytest.raises(ValueError, match="_type"):
            deserialize_component({"name": "x"})

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown"):
            deserialize_component({"_type": "alien", "name": "x"})


class TestJSON:
    def test_to_json(self):
        agent = AgentSpec(name="json_agent", role="coder")
        j = to_json(agent)
        data = json.loads(j)
        assert data["name"] == "json_agent"
        assert data["_type"] == "agent"

    def test_from_json(self):
        j = '{"_type": "agent", "name": "from_json", "role": "test"}'
        comp = from_json(j)
        assert isinstance(comp, AgentSpec)
        assert comp.name == "from_json"

    def test_roundtrip_json(self):
        original = ToolSpec(name="tool1", description="desc", ttl=30.0)
        j = to_json(original)
        restored = from_json(j)
        assert isinstance(restored, ToolSpec)
        assert restored.name == "tool1"
        assert restored.ttl == 30.0


class TestBatchOperations:
    def test_export_import(self):
        components = [
            AgentSpec(name="a1"),
            ToolSpec(name="t1"),
            TeamSpec(name="team1"),
        ]
        exported = export_components(components)
        assert len(exported) == 3
        imported = import_components(exported)
        assert len(imported) == 3
        assert isinstance(imported[0], AgentSpec)
        assert isinstance(imported[1], ToolSpec)
        assert isinstance(imported[2], TeamSpec)

    def test_empty(self):
        assert export_components([]) == []
        assert import_components([]) == []


class TestCustomRegistry:
    def test_register_custom(self):
        from dataclasses import dataclass as dc
        from dataclasses import fields as fs

        @dc
        class CustomSpec:
            name: str
            custom_field: str = ""

            @property
            def component_type(self):
                return "custom"

            def to_dict(self):
                return {"_type": "custom", "name": self.name, "custom_field": self.custom_field}

            @classmethod
            def from_dict(cls, data):
                data = {k: v for k, v in data.items() if k != "_type"}
                valid = {f.name for f in fs(cls)}
                return cls(**{k: v for k, v in data.items() if k in valid})

        register_component_type("custom", CustomSpec)
        d = {"_type": "custom", "name": "test", "custom_field": "value"}
        comp = deserialize_component(d)
        assert isinstance(comp, CustomSpec)
        assert comp.custom_field == "value"