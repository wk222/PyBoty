from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.project_paths import ProjectPaths
from web.app import create_app


@pytest.fixture
def temp_paths(tmp_path: Path) -> ProjectPaths:
    return ProjectPaths.from_root(root_dir=tmp_path, workspace_dir=tmp_path / "workspace")


@pytest.fixture
def app(temp_paths: ProjectPaths):
    return create_app(
        paths=temp_paths,
        llm_config={"model": "gpt-4o-mini", "api_key": "test", "api_base": "https://example.com/v1"},
        control_config={"mode": "balanced"},
    )


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
