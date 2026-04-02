"""Public asset entrypoints for skill-related capabilities."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "SkillDefinition": (".skill_models", "SkillDefinition"),
    "SkillMarketplace": (".skill_marketplace", "SkillMarketplace"),
    "SkillRegistry": (".skill_registry", "SkillRegistry"),
    "SkillSource": (".skill_sources", "SkillSource"),
    "SkillSourceSpec": (".skill_sources", "SkillSourceSpec"),
    "build_skill_diagnostics": (".skill_diagnostics", "build_skill_diagnostics"),
    "build_openclaw_compat_report": (".openclaw_compat", "build_openclaw_compat_report"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
