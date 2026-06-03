"""Tests for workspace registry used by the IDE multi-workspace UI."""

from pathlib import Path

from core.systems.runtime.workspace_registry import WorkspaceRegistry


def test_registry_ensures_default_workspace(tmp_path: Path):
    default_ws = tmp_path / "workspace"
    default_ws.mkdir()
    registry = WorkspaceRegistry.for_paths(tmp_path, default_ws)

    entries = registry.list()
    assert len(entries) >= 1
    assert any(item["is_default"] for item in entries)

    default = registry.default()
    assert default.id == "default"
    assert Path(default.path).resolve() == default_ws.resolve()


def test_registry_create_and_resolve_manager(tmp_path: Path):
    default_ws = tmp_path / "workspace"
    default_ws.mkdir()
    registry = WorkspaceRegistry.for_paths(tmp_path, default_ws)

    created = registry.create("Paper Team")
    assert created.name == "Paper Team"
    assert Path(created.path).exists()

    mgr = registry.manager(created.id)
    created_files = mgr.ensure_team_templates()
    assert "SOUL.md" in created_files or (Path(created.path) / "SOUL.md").exists()

    assert registry.thread_prefix(created.id).startswith("ws:")
    assert registry.delete(created.id) is True
    assert registry.delete("default") is False
