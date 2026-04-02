"""Compatibility alias for the canonical workflow_nodes_extended module."""

import sys

from core.assets.workflows import workflow_nodes_extended as _impl

sys.modules[__name__] = _impl
