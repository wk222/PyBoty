"""Auto-register the three built-in mode packs on first import.

Import this module early (e.g. from ``core/modes/__init__.py``) to ensure
all built-in packs are available in the global registry before any
mode resolution happens.
"""

from __future__ import annotations

from core.modes.app_matrix_pack import AppMatrixPack
from core.modes.assistant_pack import AssistantPack
from core.modes.admin.pack import AdminPack
from core.modes.pack import get_global_registry

_BOOTSTRAPPED = False


def ensure_builtin_packs() -> None:
    """Register built-in packs exactly once."""
    global _BOOTSTRAPPED  # noqa: PLW0603
    if _BOOTSTRAPPED:
        return
    registry = get_global_registry()
    for pack_cls in (AssistantPack, AdminPack, AppMatrixPack):
        pack = pack_cls()
        if pack.name not in registry:
            registry.register(pack)
    _BOOTSTRAPPED = True
