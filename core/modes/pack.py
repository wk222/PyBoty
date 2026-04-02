"""ModePack protocol and global registry.

A *ModePack* encapsulates everything a root mode needs:
- capability profile
- initialisation / teardown hooks
- extra tools and prompt sections
- public API methods surfaced on the PyBot host

Register a new pack to add a fourth (or fifth …) mode – no changes
required in ``agent.py``, ``lifecycle.py`` or ``factories.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.modes.profile import ModeProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ModePack(Protocol):
    """Pluggable capability bundle for a root mode."""

    @property
    def name(self) -> str:
        """Canonical mode name, e.g. ``'assistant'``."""
        ...

    @property
    def profile(self) -> ModeProfile:
        """Capability flags for this mode."""
        ...

    def initialize(self, host: Any) -> None:
        """Called once when the mode is attached to a host PyBot."""
        ...

    def teardown(self, host: Any) -> None:
        """Called when the mode is detached or the host shuts down."""
        ...

    def get_tools(self, host: Any) -> list[Any]:
        """Return extra tools injected by this mode (may be empty)."""
        ...

    def get_prompt_section(self, host: Any) -> str:
        """Return a string appended to the system prompt for this mode."""
        ...

    def get_api_methods(self) -> dict[str, Callable[..., Any]]:
        """Return ``{method_name: callable}`` to expose on the host.

        Each callable receives ``(host, *args, **kwargs)`` as its first arg.
        """
        ...


# ---------------------------------------------------------------------------
# Base helper
# ---------------------------------------------------------------------------


@dataclass
class BaseModePack:
    """Convenience base that provides sensible defaults.

    Concrete packs only need to override the methods they care about.
    """

    _name: str
    _profile: ModeProfile

    @property
    def name(self) -> str:
        return self._name

    @property
    def profile(self) -> ModeProfile:
        return self._profile

    def initialize(self, host: Any) -> None:  # noqa: ARG002
        pass

    def teardown(self, host: Any) -> None:  # noqa: ARG002
        pass

    def get_tools(self, host: Any) -> list[Any]:  # noqa: ARG002
        return []

    def get_prompt_section(self, host: Any) -> str:  # noqa: ARG002
        return ""

    def get_api_methods(self) -> dict[str, Callable[..., Any]]:
        return {}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class ModePackRegistry:
    """Global store for all registered mode packs."""

    _packs: dict[str, ModePack] = field(default_factory=dict)

    # -- mutators -----------------------------------------------------------

    def register(self, pack: ModePack) -> None:
        """Register (or replace) a mode pack by its canonical name."""
        logger.info("ModePackRegistry: registered pack %r", pack.name)
        self._packs[pack.name] = pack

    def unregister(self, name: str) -> ModePack | None:
        """Remove a pack. Returns the removed pack or ``None``."""
        return self._packs.pop(name, None)

    # -- queries ------------------------------------------------------------

    def get(self, name: str) -> ModePack:
        """Resolve a pack by name.  Raises ``KeyError`` if missing."""
        try:
            return self._packs[name]
        except KeyError:
            available = ", ".join(sorted(self._packs)) or "(none)"
            raise KeyError(
                f"Unknown mode pack {name!r}. Available: {available}"
            ) from None

    def get_or_none(self, name: str) -> ModePack | None:
        return self._packs.get(name)

    def list_all(self) -> list[ModePack]:
        return list(self._packs.values())

    def names(self) -> list[str]:
        return list(self._packs.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._packs

    def __len__(self) -> int:
        return len(self._packs)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_global_registry = ModePackRegistry()


def get_global_registry() -> ModePackRegistry:
    """Return the process-wide mode-pack registry."""
    return _global_registry


def register_mode_pack(pack: ModePack) -> None:
    """Convenience shortcut to register on the global registry."""
    _global_registry.register(pack)


def get_mode_pack(name: str) -> ModePack:
    """Convenience shortcut to resolve from the global registry."""
    return _global_registry.get(name)
