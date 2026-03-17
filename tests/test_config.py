from __future__ import annotations

from pathlib import Path

from core.config import get_config, reload_config, save_config


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
