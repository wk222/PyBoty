"""Compatibility alias for the canonical runtime model_resolver module."""

import sys

from core.systems.runtime import model_resolver as _impl

sys.modules[__name__] = _impl
