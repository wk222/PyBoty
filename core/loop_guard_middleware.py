"""Backward-compatible shim for ``core.loop_guard_middleware``."""

from __future__ import annotations

from .systems.middleware.loop_guard_middleware import *  # noqa: F401,F403
