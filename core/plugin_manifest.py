"""Compatibility alias for the canonical plugin manifest module."""

import sys

from core.systems.integration import plugin_manifest as _impl

sys.modules[__name__] = _impl
