from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from core.uv_env_manager import UvEnvManager


def test_sync_with_disk_discovers_env_and_persists_metadata(tmp_path: Path):
    envs_dir = tmp_path / "uv_envs"
    (envs_dir / "demo" / ".venv").mkdir(parents=True)

    manager = UvEnvManager(str(envs_dir))

    assert "demo" in manager.envs
    meta = json.loads((envs_dir / "envs.json").read_text(encoding="utf-8"))
    assert meta["demo"]["description"] == "(auto-discovered)"


def test_python_and_pip_paths_match_platform_layout(tmp_path: Path):
    manager = UvEnvManager(str(tmp_path / "uv_envs"))

    if os.name == "nt":
        assert manager._python_path("demo").endswith(r".venv\Scripts\python.exe")
        assert manager._pip_path("demo").endswith(r".venv\Scripts\pip.exe")
    else:
        assert manager._python_path("demo").endswith(".venv/bin/python")
        assert manager._pip_path("demo").endswith(".venv/bin/pip")


def test_get_disk_size_uses_portable_calculation(tmp_path: Path):
    envs_dir = tmp_path / "uv_envs"
    payload = envs_dir / "demo" / ".venv" / "Lib" / "site-packages" / "payload.bin"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"x" * 1536)

    manager = UvEnvManager(str(envs_dir))

    assert manager._get_disk_size("demo") == "1.5 KB"


def test_create_env_reads_python_version_from_stderr(monkeypatch, tmp_path: Path):
    manager = UvEnvManager(str(tmp_path / "uv_envs"))
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["uv", "venv"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[-1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="Python 3.12.7")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = manager.create_env("demo", python_version="3.12")

    assert result["success"] is True
    assert result["env"]["python_version"] == "Python 3.12.7"
    assert calls[0][:2] == ["uv", "venv"]


def test_get_env_returns_packages_and_disk_size(monkeypatch, tmp_path: Path):
    envs_dir = tmp_path / "uv_envs"
    (envs_dir / "demo" / ".venv").mkdir(parents=True)
    manager = UvEnvManager(str(envs_dir))

    monkeypatch.setattr(manager, "_list_packages", lambda name: ["pytest==8.4.2"])
    monkeypatch.setattr(manager, "_get_disk_size", lambda name: "12.0 KB")

    info = manager.get_env("demo")

    assert info is not None
    assert info["packages"] == ["pytest==8.4.2"]
    assert info["disk_size"] == "12.0 KB"
