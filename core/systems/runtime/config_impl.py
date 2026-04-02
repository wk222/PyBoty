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

from core.systems.runtime.project_paths import ProjectPaths

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_LEGACY_CONFIG_PATH = _PROJECT_ROOT / "config.json"
_LEGACY_CONFIG_PATH = _DEFAULT_LEGACY_CONFIG_PATH
_CONFIG_PATH = _DEFAULT_LEGACY_CONFIG_PATH
_CONFIG_PATH_ENV = "PYBOT_CONFIG_PATH"

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
        "embedding_batch_size": 32,
        "search_strategy": "vector",
        "hybrid_keyword_weight": 0.35,
        "hybrid_vector_weight": 0.65,
        "mmr_enabled": False,
        "mmr_lambda": 0.7,
        "temporal_decay_enabled": False,
        "temporal_half_life_days": 30.0,
        "chunk_size": 1000,
        "chunk_overlap": 200,
        "persist_dir": None,
    },
    "agent_control": {
        "mode": "balanced",
        "blocked_tools": [],
        "blocked_dynamic_tools": [],
        "risky_tools": [],
        "approval_required_tools": [],
        "approval_required_dynamic_tools": False,
        "allow_dynamic_tools": True,
        "allow_tool_mutation": True,
        "allow_agent_mutation": True,
        "allow_agent_delegation": True,
        "max_subagent_depth": 3,
        "max_concurrent_subagents": 5,
        "subagent_timeout_seconds": 300,
        "max_recent_tool_calls": 20,
        "stuck_loop_warning_threshold": 3,
        "stuck_loop_kill_threshold": 6,
    },
    "extra_skill_sources": [],
    "channels": {},
    "channel_routes": [],
    "gateway": {
        "auth": {
            "mode": "none",
            "token": None,
            "password": None,
        },
        "ws": {
            "enabled": True,
            "protocol_version": 3,
            "tick_interval_ms": 15000,
            "require_device_id": True,
            "require_paired_device_token": False,
        },
        "pairing": {
            "enabled": True,
            "auto_approve_local": True,
        },
        "http": {
            "endpoints": {
                "responses": {
                    "enabled": True,
                    "stream_enabled": True,
                },
                "models": {
                    "enabled": True,
                },
            },
        },
    },
    "openclaw_compat": {
        "repo_path": None,
        "config_path": None,
    },
}


def resolve_config_path(path: str | os.PathLike | None = None) -> Path:
    """Resolve the canonical config path.

    Resolution order:
    1. Explicit ``path`` argument
    2. ``PYBOT_CONFIG_PATH`` environment override
    3. Runtime-home ``config.json``
    4. Legacy repo-root ``config.json`` as a read-compat fallback
    """
    if path:
        return Path(path).resolve()

    configured = os.environ.get(_CONFIG_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    compat_override_path = Path(_CONFIG_PATH).resolve()
    if compat_override_path != _DEFAULT_LEGACY_CONFIG_PATH.resolve():
        return compat_override_path

    runtime_path = ProjectPaths.from_root().runtime_root_dir / "config.json"
    if runtime_path.exists():
        return runtime_path
    legacy_path = Path(_LEGACY_CONFIG_PATH)
    if legacy_path.exists():
        return legacy_path
    return runtime_path


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


def get_extra_skill_sources(path: str | os.PathLike | None = None) -> list[dict[str, Any]]:
    """Return external skill source entries from config.

    Each entry should be a dict with at least ``path`` and optionally ``name``.
    Example config.json::

        "extra_skill_sources": [
            {"name": "openclaw", "path": "C:/path/to/openclaw-main", "flavor": "openclaw"}
        ]
    """
    cfg = get_config(path)
    raw = cfg.get("extra_skill_sources", [])
    return [e for e in raw if isinstance(e, dict) and e.get("path")]


def get_channels_config(path: str | os.PathLike | None = None) -> dict[str, dict[str, Any]]:
    """Return configured external channel adapters."""
    cfg = get_config(path)
    raw = cfg.get("channels", {})
    if not isinstance(raw, dict):
        return {}
    return {name: value for name, value in raw.items() if isinstance(value, dict)}


def get_channel_routes_config(path: str | os.PathLike | None = None) -> list[dict[str, Any]]:
    """Return configured channel routing rules."""
    cfg = get_config(path)
    raw = cfg.get("channel_routes", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def get_gateway_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Return gateway compatibility settings."""
    cfg = get_config(path)
    defaults = deepcopy(_DEFAULTS["gateway"])
    raw = cfg.get("gateway", {})
    if not isinstance(raw, dict):
        return defaults

    auth = raw.get("auth", {})
    http_cfg = raw.get("http", {})
    if isinstance(auth, dict):
        defaults["auth"] = {**defaults["auth"], **auth}
    ws_cfg = raw.get("ws", {})
    pairing_cfg = raw.get("pairing", {})
    if isinstance(ws_cfg, dict):
        defaults["ws"] = {**defaults["ws"], **ws_cfg}
    if isinstance(pairing_cfg, dict):
        defaults["pairing"] = {**defaults["pairing"], **pairing_cfg}
    if isinstance(http_cfg, dict):
        endpoint_defaults = defaults["http"].get("endpoints", {})
        raw_endpoints = http_cfg.get("endpoints", {})
        merged_endpoints = dict(endpoint_defaults)
        if isinstance(raw_endpoints, dict):
            for endpoint_name, endpoint_defaults_value in endpoint_defaults.items():
                raw_endpoint_cfg = raw_endpoints.get(endpoint_name, {})
                if isinstance(endpoint_defaults_value, dict) and isinstance(raw_endpoint_cfg, dict):
                    merged_endpoints[endpoint_name] = {**endpoint_defaults_value, **raw_endpoint_cfg}
        defaults["http"] = {**defaults["http"], **http_cfg, "endpoints": merged_endpoints}
    return defaults


def get_openclaw_compat_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Return persisted OpenClaw compatibility bridge settings."""
    cfg = get_config(path)
    raw = cfg.get("openclaw_compat", {})
    return raw if isinstance(raw, dict) else {}


def save_config(config: dict[str, Any], path: str | os.PathLike | None = None) -> Path:
    """Persist config.json using the merged default schema and clear the cache."""
    if path:
        fp = Path(path).resolve()
    else:
        configured = os.environ.get(_CONFIG_PATH_ENV, "").strip()
        if configured:
            fp = Path(configured).expanduser().resolve()
        else:
            compat_override_path = Path(_CONFIG_PATH).resolve()
            if compat_override_path != _DEFAULT_LEGACY_CONFIG_PATH.resolve():
                fp = compat_override_path
            else:
                fp = ProjectPaths.from_root().runtime_root_dir / "config.json"
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
