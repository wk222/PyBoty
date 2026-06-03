"""PyBot Core package.

Canonical product model:
  Tools -> Skills -> Agents -> Workflows -> Apps

Subpackages:
  - ``core.assets``   user-creatable artifacts (tools / skills / workflows / agents / apps)
  - ``core.systems``  runtime systems (llm / session / memory / observability / agents / apps / ...)
  - ``core.modes``    operating modes (chat / admin / system_model / ...)

The package itself is a *thin* facade: it lazily exposes the three
subpackages plus the top-level :class:`PyBot` agent class so that
existing user code can still ``import core`` and reach the version
metadata. **All other symbols must be imported from their canonical
``core.<subpackage>....`` path.**
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from ._version import get_pybot_version

if TYPE_CHECKING:
    from . import assets, modes, systems
    from .agent import PyBot

__all__ = ["assets", "systems", "modes", "PyBot", "get_pybot_version"]

__version__ = get_pybot_version()
__author__ = "Patent Applicant"
__patent__ = "一种具有自主工具创建和智能体创建能力的智能体系统"


def __getattr__(name: str) -> Any:
    """Lazily expose subpackages and the :class:`PyBot` class only."""
    if name in {"assets", "systems", "modes"}:
        module = import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    if name == "PyBot":
        from agent import PyBot as _PyBot

        globals()[name] = _PyBot
        return _PyBot
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
