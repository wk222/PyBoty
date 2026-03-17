"""
统一配置加载 — 消除 config.json 读取散落在 4+ 个入口文件的重复代码。

用法:
    from core.config import get_config, get_llm_config, save_config
    cfg = get_config()               # 完整 dict
    llm = get_llm_config()           # {'api_key': ..., 'api_base': ..., 'model': ..., ...}
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config.json"

_DEFAULTS: dict[str, Any] = {
    "llm_config": {
        "provider": None,
        "api_key": None,
        "api_base": None,
        "model": "gpt-4",
        "temperature": 0.7,
    },
    "llm_fallback": [],
    "agent_config": {
        "thread_id": "default",
    },
    "observability": {
        "backend": "none",
        "langfuse_public_key": None,
        "langfuse_secret_key": None,
        "langfuse_host": None,
        "log_level": "INFO",
    },
    "rag_config": {
        "enabled": False,
        "backend": "chroma",
        "embedding_model": None,
        "chunk_size": 1000,
        "chunk_overlap": 200,
        "persist_dir": None,
    },
    "agent_control": {
        "mode": "balanced",
        "blocked_tools": [],
        "blocked_dynamic_tools": [],
        "risky_tools": [],
        "allow_dynamic_tools": True,
        "allow_tool_mutation": True,
        "allow_agent_mutation": True,
        "allow_agent_delegation": True,
        "max_recent_tool_calls": 20,
        "stuck_loop_warning_threshold": 3,
        "stuck_loop_kill_threshold": 6,
    },
}


def resolve_config_path(path: str | os.PathLike | None = None) -> Path:
    """Resolve the canonical config path."""
    return Path(path).resolve() if path else _CONFIG_PATH


def _merge_defaults(config: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = deepcopy(_DEFAULTS)
    if not isinstance(config, dict):
        return merged

    for key, value in config.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=16)
def get_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """读取并缓存 config.json，缺失时返回默认值。"""
    fp = resolve_config_path(path)
    if fp.exists():
        with fp.open("r", encoding="utf-8") as file:
            return _merge_defaults(json.load(file))
    return _merge_defaults()


def get_llm_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    cfg = get_config(path)
    defaults = deepcopy(_DEFAULTS["llm_config"])
    return {**defaults, **cfg.get("llm_config", {})}


def get_llm_fallback_config(path: str | os.PathLike | None = None) -> list[dict[str, Any]]:
    cfg = get_config(path)
    return cfg.get("llm_fallback", [])


def get_observability_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    cfg = get_config(path)
    defaults = deepcopy(_DEFAULTS["observability"])
    return {**defaults, **cfg.get("observability", {})}


def get_rag_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    cfg = get_config(path)
    defaults = deepcopy(_DEFAULTS["rag_config"])
    return {**defaults, **cfg.get("rag_config", {})}


def get_agent_control_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    cfg = get_config(path)
    defaults = deepcopy(_DEFAULTS["agent_control"])
    return {**defaults, **cfg.get("agent_control", {})}


def save_config(config: dict[str, Any], path: str | os.PathLike | None = None) -> Path:
    """Persist config.json using the merged default schema and clear the cache."""
    fp = resolve_config_path(path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    payload = _merge_defaults(config)
    with fp.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=4, ensure_ascii=False)
    get_config.cache_clear()
    return fp


def reload_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """强制重新读取（清除 lru_cache）。"""
    get_config.cache_clear()
    return get_config(path)
