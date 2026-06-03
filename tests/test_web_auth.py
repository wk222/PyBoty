"""Tests for API key loading."""

from __future__ import annotations

import pytest

from web.auth_config import load_api_keys_from_env


def test_load_api_keys_from_env_parses_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYBOT_API_KEYS", "alpha:admin,chat;beta:*")
    monkeypatch.delenv("PYBOT_ALLOW_DEV_KEY", raising=False)
    keys = load_api_keys_from_env()
    assert keys["alpha"] == ["admin", "chat"]
    assert keys["beta"] == ["*"]


def test_dev_key_only_when_explicitly_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYBOT_API_KEYS", raising=False)
    monkeypatch.delenv("PYBOT_ALLOW_DEV_KEY", raising=False)
    assert load_api_keys_from_env() == {}

    monkeypatch.setenv("PYBOT_ALLOW_DEV_KEY", "1")
    assert load_api_keys_from_env() == {"dev-key": ["*"]}
