from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.systems.integration import reset_plugin_registry
from core.systems.runtime import ProjectPaths
from web.app import create_app

logger = logging.getLogger(__name__)


@pytest.fixture
def tmp_path(request):
    """Windows-safe tmp_path without pytest's ``…/current`` symlink."""
    import platform
    import shutil
    import tempfile

    if platform.system() == "Windows":
        path = Path(tempfile.mkdtemp(prefix="pytest_tmp_"))
        yield path
        shutil.rmtree(path, ignore_errors=True)
        return

    factory = request.getfixturevalue("tmp_path_factory")
    path = factory.mktemp("tmp")
    yield path


@pytest.fixture(scope="session")
def isolated_runtime_home(request) -> Path:
    """Session-scoped runtime home that gracefully degrades on Windows.

    ``tmp_path_factory.mktemp`` invokes ``Path.symlink_to`` to create the
    ``…current`` convenience link.  Recent Windows builds raise a
    permission prompt for that operation when developer-mode is off, which
    can hang the whole test run.  We catch it once per session and fall
    back to a plain ``mkdtemp`` directory; tests get a real isolated dir
    either way.
    """
    import platform
    if platform.system() == "Windows":
        import tempfile
        return Path(tempfile.mkdtemp(prefix="pybot_runtime_home_"))
    try:
        tmp_path_factory = request.getfixturevalue("tmp_path_factory")
        return tmp_path_factory.mktemp("pybot_runtime_home")
    except (OSError, NotImplementedError, KeyError, ValueError):
        import tempfile

        return Path(tempfile.mkdtemp(prefix="pybot_runtime_home_"))


@pytest.fixture(autouse=True)
def _set_runtime_home(monkeypatch: pytest.MonkeyPatch, isolated_runtime_home: Path):
    monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(isolated_runtime_home))


@pytest.fixture(autouse=True)
def _reset_plugin_registry_fixture():
    reset_plugin_registry()
    yield
    reset_plugin_registry()


@pytest.fixture
def temp_paths(request) -> ProjectPaths:
    import platform
    if platform.system() == "Windows":
        import tempfile
        p = Path(tempfile.mkdtemp(prefix="pybot_tmp_path_"))
    else:
        p = request.getfixturevalue("tmp_path")
    return ProjectPaths.from_root(
        root_dir=p,
        workspace_dir=p / "workspace",
        runtime_root_dir=p,
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
def agent_workspace(request) -> Path:
    """Provide an isolated agent workspace directory."""
    import platform
    if platform.system() == "Windows":
        import tempfile
        p = Path(tempfile.mkdtemp(prefix="pybot_agent_ws_"))
    else:
        p = request.getfixturevalue("tmp_path")
    ws = p / "agents"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def tool_workspace(request) -> Path:
    """Provide an isolated tool workspace directory."""
    import platform
    if platform.system() == "Windows":
        import tempfile
        p = Path(tempfile.mkdtemp(prefix="pybot_tool_ws_"))
    else:
        p = request.getfixturevalue("tmp_path")
    ws = p / "tools"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def workflow_workspace(request) -> Path:
    """Provide an isolated workflow workspace directory."""
    import platform
    if platform.system() == "Windows":
        import tempfile
        p = Path(tempfile.mkdtemp(prefix="pybot_workflow_ws_"))
    else:
        p = request.getfixturevalue("tmp_path")
    ws = p / "workflows"
    ws.mkdir(parents=True, exist_ok=True)
    return ws
