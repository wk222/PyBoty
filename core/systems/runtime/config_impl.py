"""Runtime config facade backed by the canonical trusted settings stack."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.systems.runtime.project_paths import ProjectPaths
from core.systems.runtime.trusted_settings import (
    DEFAULT_SETTINGS,
    TrustedSettingsBundle,
    build_trusted_settings_bundle,
    load_settings_file,
    merge_settings,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_LEGACY_CONFIG_PATH = _PROJECT_ROOT / "config.json"
_LEGACY_CONFIG_PATH = _DEFAULT_LEGACY_CONFIG_PATH
_CONFIG_PATH = _DEFAULT_LEGACY_CONFIG_PATH
_CONFIG_PATH_ENV = "PYBOT_CONFIG_PATH"


def resolve_config_path(path: str | os.PathLike | None = None) -> Path:
    """Resolve the user settings path."""
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
        return runtime_path.resolve()

    legacy_path = Path(_LEGACY_CONFIG_PATH)
    if legacy_path.exists():
        return legacy_path.resolve()

    return runtime_path.resolve()


def resolve_project_config_path(path: str | os.PathLike | None = None) -> Path:
    """Resolve the project-scoped settings path."""
    if path:
        return Path(path).resolve()
    return (_PROJECT_ROOT / ".pybot" / "project.config.json").resolve()


def resolve_system_config_path(path: str | os.PathLike | None = None) -> Path:
    """Resolve the optional system policy/settings path."""
    if path:
        return Path(path).resolve()
    return (ProjectPaths.from_root().runtime_root_dir / "settings.system.json").resolve()


def resolve_managed_policy_path(path: str | os.PathLike | None = None) -> Path:
    """Resolve the managed policy path."""
    if path:
        return Path(path).resolve()
    return (ProjectPaths.from_root().runtime_root_dir / "policy.managed.json").resolve()


def _coerce_section(config: dict[str, Any] | None, key: str) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    value = config.get(key, {})
    return dict(value) if isinstance(value, dict) else {}


@lru_cache(maxsize=16)
def _get_trusted_settings_cached(
    user_path: str,
    project_path: str,
    system_path: str,
    managed_policy_path: str,
) -> TrustedSettingsBundle:
    return build_trusted_settings_bundle(
        system_values=load_settings_file(system_path),
        user_values=load_settings_file(user_path),
        project_values=load_settings_file(project_path),
        managed_policy_values=load_settings_file(managed_policy_path),
        system_path=system_path,
        user_path=user_path,
        project_path=project_path,
        managed_policy_path=managed_policy_path,
    )


def _clear_settings_cache() -> None:
    _get_trusted_settings_cached.cache_clear()


def get_trusted_settings(
    path: str | os.PathLike | None = None,
    *,
    project_path: str | os.PathLike | None = None,
    system_path: str | os.PathLike | None = None,
    managed_policy_path: str | os.PathLike | None = None,
    session_overrides: dict[str, Any] | None = None,
) -> TrustedSettingsBundle:
    """Return the trusted settings bundle with layered provenance."""
    user_fp = str(resolve_config_path(path))
    project_fp = str(resolve_project_config_path(project_path))
    system_fp = str(resolve_system_config_path(system_path))
    managed_policy_fp = str(resolve_managed_policy_path(managed_policy_path))
    bundle = _get_trusted_settings_cached(user_fp, project_fp, system_fp, managed_policy_fp)
    if session_overrides:
        return bundle.with_session(session_overrides)
    return bundle


def get_config(
    path: str | os.PathLike | None = None,
    *,
    project_path: str | os.PathLike | None = None,
    system_path: str | os.PathLike | None = None,
    managed_policy_path: str | os.PathLike | None = None,
    session_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the effective merged settings dict."""
    return get_trusted_settings(
        path,
        project_path=project_path,
        system_path=system_path,
        managed_policy_path=managed_policy_path,
        session_overrides=session_overrides,
    ).effective


def get_settings_projection(
    path: str | os.PathLike | None = None,
    *,
    project_path: str | os.PathLike | None = None,
    system_path: str | os.PathLike | None = None,
    managed_policy_path: str | os.PathLike | None = None,
    session_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return get_trusted_settings(
        path,
        project_path=project_path,
        system_path=system_path,
        managed_policy_path=managed_policy_path,
        session_overrides=session_overrides,
    ).build_projection()


def get_llm_config(path: str | os.PathLike | None = None, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else get_config(path)
    return merge_settings(DEFAULT_SETTINGS["llm_config"], _coerce_section(cfg, "llm_config"))


def get_llm_fallback_config(
    path: str | os.PathLike | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = config if isinstance(config, dict) else get_config(path)
    raw = cfg.get("llm_fallback", []) if isinstance(cfg, dict) else []
    return [dict(item) for item in raw if isinstance(item, dict)]


def get_observability_config(
    path: str | os.PathLike | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else get_config(path)
    return merge_settings(DEFAULT_SETTINGS["observability"], _coerce_section(cfg, "observability"))


def get_rag_config(path: str | os.PathLike | None = None, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else get_config(path)
    return merge_settings(DEFAULT_SETTINGS["rag_config"], _coerce_section(cfg, "rag_config"))


def get_agent_control_config(
    path: str | os.PathLike | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else get_config(path)
    return merge_settings(DEFAULT_SETTINGS["agent_control"], _coerce_section(cfg, "agent_control"))


def get_permission_config(
    path: str | os.PathLike | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else get_config(path)
    return merge_settings(DEFAULT_SETTINGS["permission"], _coerce_section(cfg, "permission"))


def get_extra_skill_sources(
    path: str | os.PathLike | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = config if isinstance(config, dict) else get_config(path)
    raw = cfg.get("extra_skill_sources", []) if isinstance(cfg, dict) else []
    return [dict(item) for item in raw if isinstance(item, dict) and item.get("path")]


def get_channels_config(
    path: str | os.PathLike | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    cfg = config if isinstance(config, dict) else get_config(path)
    raw = cfg.get("channels", {}) if isinstance(cfg, dict) else {}
    if not isinstance(raw, dict):
        return {}
    return {name: dict(value) for name, value in raw.items() if isinstance(value, dict)}


def get_channel_routes_config(
    path: str | os.PathLike | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = config if isinstance(config, dict) else get_config(path)
    raw = cfg.get("channel_routes", []) if isinstance(cfg, dict) else []
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def get_gateway_config(
    path: str | os.PathLike | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else get_config(path)
    return merge_settings(DEFAULT_SETTINGS["gateway"], _coerce_section(cfg, "gateway"))


def get_openclaw_compat_config(
    path: str | os.PathLike | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else get_config(path)
    return merge_settings(DEFAULT_SETTINGS["openclaw_compat"], _coerce_section(cfg, "openclaw_compat"))


def _resolve_save_path(
    *,
    path: str | os.PathLike | None,
    source: str,
) -> Path:
    normalized_source = str(source or "user").strip().lower()
    if normalized_source == "project":
        return resolve_project_config_path(path)
    if normalized_source == "system":
        return resolve_system_config_path(path)
    if normalized_source == "session":
        raise ValueError("Session settings are runtime-only and cannot be persisted")
    return resolve_config_path(path)


def save_config(
    config: dict[str, Any],
    path: str | os.PathLike | None = None,
    *,
    source: str = "user",
) -> Path:
    """Persist one settings layer and clear the cached bundle."""
    from core.systems.runtime.trusted_settings import _flatten_settings

    fp = _resolve_save_path(path=path, source=source)
    
    # Enforce managed policy trust boundary
    try:
        bundle = get_trusted_settings(path)
        managed_layer = bundle.get_layer("managed_policy")
        if managed_layer and managed_layer.values:
            incoming = _flatten_settings(config)
            managed = _flatten_settings(managed_layer.values)
            for key, val in incoming.items():
                if key in managed and val != managed[key]:
                    raise ValueError(f"Cannot overwrite managed policy key: {key}")
    except Exception as exc:
        if isinstance(exc, ValueError) and "Cannot overwrite managed policy key" in str(exc):
            raise

    fp.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(config or {})
    with fp.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=4, ensure_ascii=False)
    _clear_settings_cache()
    return fp


def save_project_config(config: dict[str, Any], path: str | os.PathLike | None = None) -> Path:
    return save_config(config, path, source="project")


def save_system_config(config: dict[str, Any], path: str | os.PathLike | None = None) -> Path:
    return save_config(config, path, source="system")


def reload_config(
    path: str | os.PathLike | None = None,
    *,
    project_path: str | os.PathLike | None = None,
    system_path: str | os.PathLike | None = None,
    managed_policy_path: str | os.PathLike | None = None,
    session_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Clear the cache and reload the effective config."""
    _clear_settings_cache()
    return get_config(
        path,
        project_path=project_path,
        system_path=system_path,
        managed_policy_path=managed_policy_path,
        session_overrides=session_overrides,
    )


get_config.cache_clear = _clear_settings_cache  # type: ignore[attr-defined]
get_trusted_settings.cache_clear = _clear_settings_cache  # type: ignore[attr-defined]


__all__ = [
    "DEFAULT_SETTINGS",
    "get_agent_control_config",
    "get_channel_routes_config",
    "get_channels_config",
    "get_config",
    "get_extra_skill_sources",
    "get_gateway_config",
    "get_llm_config",
    "get_llm_fallback_config",
    "get_observability_config",
    "get_openclaw_compat_config",
    "get_permission_config",
    "get_rag_config",
    "get_settings_projection",
    "get_trusted_settings",
    "reload_config",
    "resolve_config_path",
    "resolve_project_config_path",
    "resolve_system_config_path",
    "save_config",
    "save_project_config",
    "save_system_config",
]
