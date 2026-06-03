"""Backwards-compatible re-export of :func:`core.get_pybot_version`.

The implementation now lives at the package root (``core/_version.py``)
so that ``import core`` does not need to load the heavyweight
``core.systems`` subpackage just for the version string.
"""

from __future__ import annotations

from core._version import _FALLBACK_VERSION, _VERSION_PATTERN, _version_from_pyproject, get_pybot_version

__all__ = [
    "_FALLBACK_VERSION",
    "_VERSION_PATTERN",
    "_version_from_pyproject",
    "get_pybot_version",
]
