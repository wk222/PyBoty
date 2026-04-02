"""OpenClaw compatibility helpers for config- and source-level bridging."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .skill_models import SkillDefinition

if TYPE_CHECKING:
    from .skill_registry import SkillRegistry

_DEFAULT_OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
_PYBOT_SUPPORTED_OPENCLAW_CHANNELS = {"webhook", "wechat", "wecom"}


def resolve_openclaw_config_path(
    path: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve an OpenClaw config path, falling back to the conventional location."""
    candidate = Path(path).expanduser() if path else _DEFAULT_OPENCLAW_CONFIG
    return candidate.resolve()


def try_load_openclaw_config(
    path: str | os.PathLike[str] | None = None,
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    """Best-effort OpenClaw config loader with permissive parsing."""
    config_path = resolve_openclaw_config_path(path)
    if not config_path.exists():
        return config_path, None, f"Config not found: {config_path}"

    raw = config_path.read_text(encoding="utf-8")
    errors: list[str] = []
    for parser_name, parser in (("json", json.loads), ("yaml", yaml.safe_load)):
        try:
            payload = parser(raw)
        except Exception as exc:  # pragma: no cover - error details only
            errors.append(f"{parser_name}: {exc}")
            continue
        if isinstance(payload, dict):
            return config_path, payload, None
        errors.append(f"{parser_name}: top-level payload was not an object")
    return config_path, None, "; ".join(errors) or "Unable to parse OpenClaw config"


def detect_openclaw_source(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Validate and summarize an OpenClaw-style local skill tree."""
    raw_root = Path(path).expanduser().resolve()
    skills_root = raw_root / "skills" if raw_root.name != "skills" else raw_root
    if not skills_root.exists() or not skills_root.is_dir():
        raise FileNotFoundError(f"OpenClaw skills directory not found under: {raw_root}")

    skill_dirs = sorted(
        entry.name for entry in skills_root.iterdir() if entry.is_dir() and (entry / "SKILL.md").exists()
    )
    if not skill_dirs:
        raise FileNotFoundError(f"No SKILL.md bundles found under: {skills_root}")

    return {
        "repo_root": str(raw_root if raw_root.name != "skills" else raw_root.parent),
        "skills_root": str(skills_root),
        "skill_count": len(skill_dirs),
        "sample_skills": skill_dirs[:10],
    }


def build_openclaw_source_specs(
    repo_path: str | os.PathLike[str],
    *,
    config_path: str | os.PathLike[str] | None = None,
    source_name: str = "openclaw",
    include_extra_dirs: bool = True,
) -> dict[str, Any]:
    """Build PyBot skill-source specs from an OpenClaw repo root plus config."""
    detected_repo = detect_openclaw_source(repo_path)
    parsed_config_path, openclaw_config, config_error = try_load_openclaw_config(config_path)

    source_specs: list[dict[str, str]] = [
        {
            "name": source_name,
            "path": detected_repo["repo_root"],
            "flavor": "openclaw",
        }
    ]
    imported_extra_dirs: list[dict[str, str]] = []

    if include_extra_dirs and openclaw_config is not None and parsed_config_path is not None:
        for index, extra_dir in enumerate(resolve_openclaw_extra_dirs(openclaw_config, parsed_config_path), start=1):
            if _dedupe_source_path(source_specs, extra_dir):
                continue
            extra_name = _build_extra_source_name(source_name, extra_dir, index)
            spec = {"name": extra_name, "path": str(extra_dir), "flavor": "openclaw"}
            source_specs.append(spec)
            imported_extra_dirs.append(spec)

    return {
        "repo": detected_repo,
        "config_path": str(parsed_config_path) if parsed_config_path else "",
        "config_loaded": openclaw_config is not None,
        "config_error": config_error or "",
        "source_specs": source_specs,
        "extra_sources": imported_extra_dirs,
        "config_summary": summarize_openclaw_config(openclaw_config, parsed_config_path),
    }


def build_openclaw_compat_report(
    skill_registry: SkillRegistry,
    *,
    repo_path: str | os.PathLike[str] | None = None,
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build a report describing the current OpenClaw bridge state."""
    repo_summary: dict[str, Any] = {}
    if repo_path:
        try:
            repo_summary = detect_openclaw_source(repo_path)
        except FileNotFoundError as exc:
            repo_summary = {"error": str(exc), "repo_root": str(Path(repo_path).expanduser())}

    parsed_config_path, openclaw_config, config_error = try_load_openclaw_config(config_path)
    openclaw_sources = [
        source.to_dict()
        for source in skill_registry.storage.sources
        if getattr(source, "flavor", "generic") == "openclaw"
    ]
    openclaw_skills = [
        build_openclaw_skill_bridge_report(skill, openclaw_config)
        for skill in skill_registry.skills.values()
        if skill.skill_format == "openclaw"
    ]

    return {
        "repo": repo_summary,
        "config": {
            "path": str(parsed_config_path) if parsed_config_path else "",
            "loaded": openclaw_config is not None,
            "error": config_error or "",
            "summary": summarize_openclaw_config(openclaw_config, parsed_config_path),
            "channel_compatibility": build_openclaw_channel_compatibility(openclaw_config),
        },
        "sources": openclaw_sources,
        "skills": openclaw_skills,
    }


def build_openclaw_skill_bridge_report(
    skill: SkillDefinition,
    openclaw_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe how an OpenClaw skill maps onto OpenClaw config conventions."""
    entry_key = get_openclaw_skill_key(skill)
    entries = _resolve_nested_path(openclaw_config or {}, "skills.entries")
    entry = entries.get(entry_key) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        entry = {}

    primary_env_checks: list[dict[str, Any]] = []
    if skill.primary_env:
        primary_env_checks.append(
            {
                "name": skill.primary_env,
                "available_via_entry_env": skill.primary_env in _coerce_dict(entry.get("env")),
                "available_via_api_key": bool(entry.get("apiKey")),
            }
        )

    global_config_checks = []
    for path in skill.requires_config:
        global_config_checks.append(
            {
                "path": path,
                "present": _resolve_nested_path(openclaw_config or {}, path) is not None,
            }
        )

    return {
        "name": skill.name,
        "entry_key": entry_key,
        "source_name": skill.source_name,
        "entry_present": bool(entry),
        "entry_enabled": entry.get("enabled") if entry else None,
        "entry_env_keys": sorted(_coerce_dict(entry.get("env")).keys()),
        "entry_config_keys": sorted(_coerce_dict(entry.get("config")).keys()),
        "entry_api_key_present": bool(entry.get("apiKey")),
        "primary_env_bridge": primary_env_checks,
        "global_config_bridge": global_config_checks,
        "runtime_env": build_openclaw_runtime_env(skill, openclaw_config),
        "metadata": skill.openclaw_metadata,
    }


def build_openclaw_runtime_env(
    skill: SkillDefinition,
    openclaw_config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build env vars that OpenClaw would make available for a skill run."""
    entry = _get_openclaw_skill_entry(skill, openclaw_config)
    if not entry:
        return {}
    if entry.get("enabled") is False:
        return {}

    resolved: dict[str, str] = {}
    entry_env = _coerce_dict(entry.get("env"))
    for key, value in entry_env.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(value, (str, int, float, bool)):
            continue
        if os.environ.get(key):
            continue
        resolved[key] = str(value)

    if skill.primary_env and not os.environ.get(skill.primary_env):
        if skill.primary_env not in resolved and isinstance(entry.get("apiKey"), str) and entry.get("apiKey"):
            resolved[skill.primary_env] = str(entry["apiKey"])

    return resolved


def import_openclaw_channels_for_pybot(
    openclaw_config: dict[str, Any] | None,
    current_pybot_channels: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import the subset of OpenClaw channels that PyBot can currently host."""
    pybot_channels = dict(current_pybot_channels or {})
    raw_channels = openclaw_config.get("channels", {}) if isinstance(openclaw_config, dict) else {}
    if not isinstance(raw_channels, dict):
        raw_channels = {}

    imported: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    for name, value in raw_channels.items():
        if name not in _PYBOT_SUPPORTED_OPENCLAW_CHANNELS:
            skipped.append({"name": str(name), "reason": "unsupported_by_pybot"})
            continue
        if not isinstance(value, dict):
            skipped.append({"name": str(name), "reason": "invalid_channel_config"})
            continue
        normalized = normalize_openclaw_channel_config(name, value)
        pybot_channels[name] = normalized
        imported[name] = normalized

    return {
        "channels": pybot_channels,
        "imported": imported,
        "skipped": skipped,
    }


def build_openclaw_channel_compatibility(openclaw_config: dict[str, Any] | None) -> dict[str, Any]:
    """Summarize which OpenClaw channels could map into PyBot today."""
    raw_channels = openclaw_config.get("channels", {}) if isinstance(openclaw_config, dict) else {}
    if not isinstance(raw_channels, dict):
        raw_channels = {}
    supported = sorted(name for name in raw_channels if name in _PYBOT_SUPPORTED_OPENCLAW_CHANNELS)
    unsupported = sorted(name for name in raw_channels if name not in _PYBOT_SUPPORTED_OPENCLAW_CHANNELS)
    return {
        "supported": supported,
        "unsupported": unsupported,
        "supported_count": len(supported),
        "unsupported_count": len(unsupported),
    }


def normalize_openclaw_channel_config(name: str, config: dict[str, Any]) -> dict[str, Any]:
    """Normalize a supported OpenClaw channel block into PyBot's simpler channel schema."""
    payload = dict(config)
    payload.setdefault("kind", name)
    return payload


def get_openclaw_skill_key(skill: SkillDefinition) -> str:
    """Resolve the canonical OpenClaw skill entry key for a skill."""
    raw_key = skill.openclaw_metadata.get("skillKey") if isinstance(skill.openclaw_metadata, dict) else None
    if isinstance(raw_key, str) and raw_key.strip():
        return raw_key.strip()
    return skill.name


def resolve_openclaw_extra_dirs(
    openclaw_config: dict[str, Any],
    config_path: Path,
) -> list[Path]:
    """Resolve `skills.load.extraDirs` entries relative to the OpenClaw config file."""
    load_config = _resolve_nested_path(openclaw_config, "skills.load")
    if not isinstance(load_config, dict):
        return []
    raw_entries = load_config.get("extraDirs", [])
    if isinstance(raw_entries, (str, Path)):
        raw_entries = [raw_entries]
    if not isinstance(raw_entries, list):
        return []

    resolved: list[Path] = []
    seen: set[str] = set()
    for value in raw_entries:
        if not isinstance(value, str) or not value.strip():
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (config_path.parent / candidate).resolve()
        else:
            candidate = candidate.resolve()
        normalized = str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(candidate)
    return resolved


def summarize_openclaw_config(
    openclaw_config: dict[str, Any] | None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Return a lightweight summary of an OpenClaw config object."""
    if not isinstance(openclaw_config, dict):
        return {
            "skill_entries": [],
            "extra_dirs": [],
            "channels": [],
            "channel_count": 0,
            "skill_entry_count": 0,
        }

    entries = _resolve_nested_path(openclaw_config, "skills.entries")
    channels = openclaw_config.get("channels", {})
    resolved_extra_dirs = resolve_openclaw_extra_dirs(openclaw_config, config_path) if config_path is not None else []

    return {
        "skill_entries": sorted(entries.keys()) if isinstance(entries, dict) else [],
        "extra_dirs": [str(item) for item in resolved_extra_dirs],
        "channels": sorted(channels.keys()) if isinstance(channels, dict) else [],
        "channel_count": len(channels) if isinstance(channels, dict) else 0,
        "skill_entry_count": len(entries) if isinstance(entries, dict) else 0,
    }


def resolve_openclaw_runtime_env_for_skill(
    skill: SkillDefinition,
    *,
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Load the configured OpenClaw bridge and resolve env vars for one skill."""
    _, openclaw_config, _ = try_load_openclaw_config(config_path)
    return build_openclaw_runtime_env(skill, openclaw_config)


def _resolve_nested_path(config: dict[str, Any], path: str) -> Any | None:
    current: Any = config
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _get_openclaw_skill_entry(
    skill: SkillDefinition,
    openclaw_config: dict[str, Any] | None,
) -> dict[str, Any]:
    entry_key = get_openclaw_skill_key(skill)
    entries = _resolve_nested_path(openclaw_config or {}, "skills.entries")
    entry = entries.get(entry_key) if isinstance(entries, dict) else None
    return entry if isinstance(entry, dict) else {}


def _build_extra_source_name(source_name: str, path: Path, index: int) -> str:
    base = path.name or f"extra_{index}"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", base).strip("_").lower() or f"extra_{index}"
    return f"{source_name}_extra_{index}_{slug}"


def _dedupe_source_path(specs: list[dict[str, str]], candidate: Path) -> bool:
    normalized = str(candidate.resolve())
    return any(str(Path(spec["path"]).expanduser().resolve()) == normalized for spec in specs)
