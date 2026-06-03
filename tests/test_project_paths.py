from __future__ import annotations

from pathlib import Path

from core.systems.runtime import ProjectPaths


def test_project_paths_defaults_runtime_root_to_user_scope(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime-home"
    monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(runtime_root))

    paths = ProjectPaths.from_root()

    assert paths.root_dir != paths.runtime_root_dir
    assert paths.runtime_root_dir == runtime_root.resolve()
    assert paths.workspace_dir == (runtime_root / "workspace").resolve()
    assert paths.global_tools_dir == (runtime_root / "global_tools").resolve()
    assert paths.tools_workspace_dir == (runtime_root / ".tools_workspace").resolve()


def test_project_paths_keep_explicit_root_local_for_tests(tmp_path):
    paths = ProjectPaths.from_root(root_dir=tmp_path)

    assert paths.root_dir == tmp_path.resolve()
    assert paths.runtime_root_dir == tmp_path.resolve()
    assert paths.workspace_dir == (tmp_path / "workspace").resolve()


def test_project_paths_resolve_relative_runtime_root_against_source_root(tmp_path):
    paths = ProjectPaths.from_root(root_dir=tmp_path, runtime_root_dir=Path("runtime"))

    assert paths.runtime_root_dir == (tmp_path / "runtime").resolve()
    assert paths.global_tools_dir == (tmp_path / "runtime" / "global_tools").resolve()
