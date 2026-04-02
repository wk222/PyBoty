"""Workflow node plugin protocol and registry.

Third-party or custom node types can be registered via ``register_node_plugin``
and will be automatically discovered by ``WorkflowNodeRuntime.dispatch_node``
through the ``extra_dispatch`` callback.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class WorkflowNodePlugin(Protocol):
    """Interface every workflow node plugin must satisfy."""

    node_type: str

    def execute(self, config: dict[str, Any], context: dict[str, Any]) -> Any:
        """Run the node with resolved *config* and a *context* dict.

        ``context`` contains at minimum:
        - ``variables``: workflow variable dict
        - ``workspace_dir``: absolute path to the workspace
        - ``resolve_var(value)``: callable to resolve variable references
        """
        ...

    def schema(self) -> dict[str, Any]:
        """Return a JSON-Schema-style descriptor for UI rendering."""
        ...


_PLUGIN_REGISTRY: dict[str, WorkflowNodePlugin] = {}


def register_node_plugin(plugin: WorkflowNodePlugin) -> None:
    """Register *plugin* globally.  Overwrites previous entries for the same node_type."""
    _PLUGIN_REGISTRY[plugin.node_type] = plugin
    logger.info("Registered workflow plugin: %s", plugin.node_type)


def unregister_node_plugin(node_type: str) -> bool:
    return _PLUGIN_REGISTRY.pop(node_type, None) is not None


def get_plugin(node_type: str) -> WorkflowNodePlugin | None:
    return _PLUGIN_REGISTRY.get(node_type)


def list_plugins() -> dict[str, WorkflowNodePlugin]:
    return dict(_PLUGIN_REGISTRY)


def dispatch_plugin(node_type: str, config: dict[str, Any], context: dict[str, Any]) -> Any:
    """Execute a registered plugin.  Raises ``KeyError`` if not found."""
    plugin = _PLUGIN_REGISTRY.get(node_type)
    if plugin is None:
        raise KeyError(f"No plugin registered for node type '{node_type}'")
    return plugin.execute(config, context)
