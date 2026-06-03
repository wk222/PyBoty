"""Tests for system doctor and team workspace bootstrap."""

from __future__ import annotations

from pathlib import Path

from core.systems.runtime.system_doctor import bootstrap_team_workspace, run_system_doctor
from core.systems.runtime.workspace_manager import WorkspaceManager


def test_bootstrap_team_workspace_creates_templates(tmp_path: Path):
    created = bootstrap_team_workspace(tmp_path)["created"]
    assert "SOUL.md" in created
    assert "TEAM.md" in created
    assert (tmp_path / "TEAM.md").exists()


def test_workspace_build_system_context_includes_team_and_memory(tmp_path: Path):
    bootstrap_team_workspace(tmp_path)
    manager = WorkspaceManager(str(tmp_path))
    manager.save_file("MEMORY.md", "# Memory\n- fact one\n- fact two\n")
    context = manager.build_system_context()
    assert "Team Context" in context
    assert "fact one" in context


def test_run_system_doctor_returns_structured_report(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PYBOT_API_KEYS", "test-key:*")
    bootstrap_team_workspace(tmp_path)
    report = run_system_doctor(workspace_dir=tmp_path)
    payload = report.to_dict()
    assert "checks" in payload
    assert payload["summary"]["pass"] >= 1
    ids = {item["id"] for item in payload["checks"]}
    assert "workspace" in ids
    assert "memory" in ids
