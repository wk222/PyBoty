"""Delivery and integration system entrypoints."""

from core.systems.integration.channels import (
    BaseChannel,
    ChannelConfig,
    ChannelManager,
    WeChatOfficialChannel,
    WeComChannel,
)
from core.systems.integration.gateway_runtime import (
    GatewayNodeCommand,
    GatewayNodeCommandRegistry,
    GatewayNodeRegistry,
    GatewayPairingRegistry,
    GatewayPresenceEntry,
    GatewayPresenceRegistry,
    GatewayRunRecord,
    GatewayRunRegistry,
    GatewayRuntime,
    GatewaySessionRegistry,
)
from core.systems.integration.mcp import MCPHub
from core.systems.integration.openresponses import (
    OpenResponsesRequest,
    build_gateway_models_catalog,
    build_openresponses_payload,
    prepare_gateway_request,
)
from core.systems.integration.plugin_manifest import (
    LoadedPlugin,
    PluginLoader,
    PluginManifest,
    PluginRegistry,
    discover_plugins,
    get_plugin_registry,
    parse_manifest,
    reset_plugin_registry,
)

__all__ = sorted([
    "BaseChannel",
    "ChannelConfig",
    "ChannelManager",
    "GatewayNodeCommand",
    "GatewayNodeCommandRegistry",
    "GatewayNodeRegistry",
    "GatewayPairingRegistry",
    "GatewayPresenceEntry",
    "GatewayPresenceRegistry",
    "GatewayRunRecord",
    "GatewayRunRegistry",
    "GatewayRuntime",
    "GatewaySessionRegistry",
    "LoadedPlugin",
    "MCPHub",
    "OpenResponsesRequest",
    "PluginLoader",
    "PluginManifest",
    "PluginRegistry",
    "WeChatOfficialChannel",
    "WeComChannel",
    "build_gateway_models_catalog",
    "build_openresponses_payload",
    "discover_plugins",
    "get_plugin_registry",
    "parse_manifest",
    "prepare_gateway_request",
    "reset_plugin_registry",
])
