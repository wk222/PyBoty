from __future__ import annotations

from pathlib import Path

from core.systems.runtime import (
    config_impl,
    get_config,
    reload_config,
    save_config,
    save_project_config,
)


def test_missing_config_uses_fresh_defaults(tmp_path: Path):
    config_path = tmp_path / "missing.json"
    get_config.cache_clear()
    config = get_config(config_path)
    config["llm_config"]["model"] = "custom-model"

    get_config.cache_clear()
    fresh = get_config(config_path)
    assert fresh["llm_config"]["model"] == "gpt-4"


def test_save_config_merges_defaults_and_round_trips(tmp_path: Path):
    config_path = tmp_path / "config.json"
    saved_path = save_config({"llm_config": {"api_key": "test-key"}}, config_path)

    assert saved_path == config_path.resolve()
    loaded = reload_config(config_path)
    assert loaded["llm_config"]["api_key"] == "test-key"
    assert loaded["llm_config"]["model"] == "gpt-4"
    assert loaded["agent_config"]["thread_id"] == "default"
    assert loaded["rag_config"]["search_strategy"] == "vector"
    assert loaded["rag_config"]["embedding_batch_size"] == 32


def test_resolve_config_path_prefers_runtime_home(monkeypatch, tmp_path: Path):
    runtime_home = tmp_path / "runtime-home"
    monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(runtime_home))
    monkeypatch.delenv("PYBOT_CONFIG_PATH", raising=False)
    monkeypatch.setattr(config_impl, "_LEGACY_CONFIG_PATH", tmp_path / "legacy-config.json")

    resolved = config_impl.resolve_config_path()

    assert resolved == (runtime_home / "config.json").resolve()


def test_resolve_config_path_falls_back_to_legacy_repo_config(monkeypatch, tmp_path: Path):
    runtime_home = tmp_path / "runtime-home"
    legacy_path = tmp_path / "legacy-config.json"
    legacy_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(runtime_home))
    monkeypatch.delenv("PYBOT_CONFIG_PATH", raising=False)
    monkeypatch.setattr(config_impl, "_LEGACY_CONFIG_PATH", legacy_path)

    resolved = config_impl.resolve_config_path()

    assert resolved == legacy_path.resolve()


def test_save_config_defaults_to_runtime_home(monkeypatch, tmp_path: Path):
    runtime_home = tmp_path / "runtime-home"
    monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(runtime_home))
    monkeypatch.delenv("PYBOT_CONFIG_PATH", raising=False)
    monkeypatch.setattr(config_impl, "_LEGACY_CONFIG_PATH", tmp_path / "legacy-config.json")

    saved_path = save_config({"llm_config": {"model": "gpt-4.1-mini"}})

    assert saved_path == (runtime_home / "config.json").resolve()
    assert saved_path.exists()


def test_project_settings_override_user_settings(tmp_path: Path):
    user_config = tmp_path / "config.json"
    project_config = tmp_path / ".pybot" / "project.config.json"

    save_config({"permission": {"mode": "plan"}}, user_config)
    save_project_config({"permission": {"mode": "bypass"}}, project_config)

    loaded = reload_config(user_config, project_path=project_config)

    assert loaded["permission"]["mode"] == "bypass"


def test_trusted_settings_projection_includes_layer_paths(tmp_path: Path):
    user_config = tmp_path / "config.json"
    project_config = tmp_path / ".pybot" / "project.config.json"
    system_config = tmp_path / "settings.system.json"

    save_config({"permission": {"mode": "default"}}, user_config)
    save_project_config({"permission": {"mode": "plan"}}, project_config)
    config_impl.save_system_config({"permission": {"rules": {"web_fetch": "allow"}}}, system_config)

    projection = config_impl.get_settings_projection(
        user_config,
        project_path=project_config,
        system_path=system_config,
        session_overrides={"permission": {"mode": "bypass"}},
    )

    assert projection["permission_mode"] == "bypass"
    assert projection["paths"]["user"] == str(user_config.resolve())
    assert projection["paths"]["project"] == str(project_config.resolve())
    assert projection["paths"]["system"] == str(system_config.resolve())
    assert projection["active_sources"] == ["system", "user", "project", "session"]
