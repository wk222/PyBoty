"""Diagnostics helpers for skill compatibility and dependency checks."""

from __future__ import annotations

import os
import shutil
from typing import Any

from .openclaw_compat import build_openclaw_skill_bridge_report
from .skill_models import SkillDefinition


def build_skill_diagnostics(
    skill: SkillDefinition,
    *,
    config: dict[str, Any] | None = None,
    openclaw_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build runtime diagnostics for a skill's declared requirements."""
    resolved_config = config or {}
    bin_checks = [_check_bin_requirement(name) for name in skill.requires_bins]
    env_checks = [_check_env_requirement(skill.primary_env)] if skill.primary_env else []
    config_checks = [_check_config_requirement(path, resolved_config) for path in skill.requires_config]
    openclaw_bridge = build_openclaw_skill_bridge_report(skill, openclaw_config)

    missing_bins = [item["name"] for item in bin_checks if not item["present"]]
    missing_env = [item["name"] for item in env_checks if not item["present"]]
    missing_config = [item["path"] for item in config_checks if not item["present"]]

    return {
        "skill": skill.name,
        "skill_format": skill.skill_format,
        "source_name": skill.source_name,
        "source_backend": skill.source_backend,
        "source_path": skill.source_path,
        "writable": skill.writable,
        "requires": {
            "bins": bin_checks,
            "env": env_checks,
            "config": config_checks,
        },
        "summary": {
            "healthy": not (missing_bins or missing_env or missing_config),
            "missing_bins": missing_bins,
            "missing_env": missing_env,
            "missing_config": missing_config,
            "total_missing": len(missing_bins) + len(missing_env) + len(missing_config),
        },
        "openclaw": {
            "enabled": skill.skill_format == "openclaw",
            "metadata": skill.openclaw_metadata,
            "compatibility": openclaw_bridge,
        },
    }


def _check_bin_requirement(name: str) -> dict[str, Any]:
    resolved = shutil.which(name)
    return {
        "name": name,
        "present": resolved is not None,
        "resolved_path": resolved or "",
    }


def _check_env_requirement(name: str) -> dict[str, Any]:
    value = os.environ.get(name)
    return {
        "name": name,
        "present": bool(value),
        "masked_value": _mask_value(value) if value else "",
    }


def _check_config_requirement(path: str, config: dict[str, Any]) -> dict[str, Any]:
    value = _resolve_config_path(config, path)
    return {
        "path": path,
        "present": value is not None,
        "value_preview": _preview_config_value(value),
    }


def _resolve_config_path(config: dict[str, Any], path: str) -> Any | None:
    current: Any = config
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _mask_value(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return value[:3] + "..." + value[-2:]


def _preview_config_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return f"{type(value).__name__}({len(value)})"
    text = str(value)
    return text if len(text) <= 64 else text[:61] + "..."
