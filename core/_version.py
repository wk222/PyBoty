"""Self-contained version helper.

Lives at the package root (instead of ``core.systems.runtime``) so that
``import core`` does not have to load the heavyweight ``core.systems``
subpackage just to expose ``__version__``.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_FALLBACK_VERSION = "0.0.0"
_VERSION_PATTERN = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def _version_from_pyproject() -> str:
    pyproject = _PROJECT_ROOT / "pyproject.toml"
    try:
        content = pyproject.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK_VERSION

    match = _VERSION_PATTERN.search(content)
    if match:
        return match.group(1)
    return _FALLBACK_VERSION


def get_pybot_version() -> str:
    """Return the installed package version, or fall back to pyproject metadata."""
    try:
        return package_version("pybot")
    except PackageNotFoundError:
        return _version_from_pyproject()
