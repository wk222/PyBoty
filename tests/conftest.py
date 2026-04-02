from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.systems.integration import reset_plugin_registry
from core.systems.runtime import ProjectPaths
from web.app import create_app

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def isolated_runtime_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("pybot_runtime_home")


@pytest.fixture(autouse=True)
def _set_runtime_home(monkeypatch: pytest.MonkeyPatch, isolated_runtime_home: Path):
    monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(isolated_runtime_home))


@pytest.fixture(autouse=True)
def _reset_plugin_registry_fixture():
    reset_plugin_registry()
    yield
    reset_plugin_registry()


@pytest.fixture
def temp_paths(tmp_path: Path) -> ProjectPaths:
    return ProjectPaths.from_root(
        root_dir=tmp_path,
        workspace_dir=tmp_path / "workspace",
        runtime_root_dir=tmp_path,
    )


@pytest.fixture
def app(temp_paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYBOT_API_KEYS", "dev-key:*;secret-token:*")
    return create_app(
        paths=temp_paths,
        llm_config={"model": "gpt-4o-mini", "api_key": "test", "api_base": "https://example.com/v1"},
        control_config={"mode": "balanced"},
    )


@pytest.fixture
def client(app):
    with TestClient(app, headers={"Authorization": "Bearer dev-key"}) as test_client:
        yield test_client


@pytest.fixture
def agent_workspace(tmp_path: Path) -> Path:
    """Provide an isolated agent workspace directory."""
    ws = tmp_path / "agents"
    ws.mkdir()
    return ws


@pytest.fixture
def tool_workspace(tmp_path: Path) -> Path:
    """Provide an isolated tool workspace directory."""
    ws = tmp_path / "tools"
    ws.mkdir()
    return ws


@pytest.fixture
def workflow_workspace(tmp_path: Path) -> Path:
    """Provide an isolated workflow workspace directory."""
    ws = tmp_path / "workflows"
    ws.mkdir()
    return ws
