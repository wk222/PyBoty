"""Public SDK for third-party PyBot plugins."""

from core.plugin_sdk.decorators import (
    on_message,
    on_shutdown,
    on_settings_change,
    on_startup,
    on_task_heartbeat,
    on_tool_call,
    pybot_plugin,
)
from core.plugin_sdk.hooks import MessageHookContext, PluginHookContext, ToolCallHookContext
from core.plugin_sdk.host import PluginHost, plugin_host
from core.plugin_sdk.manifest import (
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    CompatRule,
    ManifestError,
    ManifestKind,
    PyBotManifest,
)
from core.plugin_sdk.marketplace import (
    MARKETPLACE_SCHEMA_VERSION,
    MarketplaceEntry,
    MarketplaceError,
    MarketplaceIndex,
)
from core.plugin_sdk.settings import (
    DEFAULT_SCHEMA_FILENAME,
    SETTINGS_FILENAME,
    SchemaProperty,
    SettingsError,
    SettingsSchema,
    SettingsStore,
)

__all__ = [
    "CompatRule",
    "DEFAULT_SCHEMA_FILENAME",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "MARKETPLACE_SCHEMA_VERSION",
    "ManifestError",
    "ManifestKind",
    "MarketplaceEntry",
    "MarketplaceError",
    "MarketplaceIndex",
    "MessageHookContext",
    "PluginHookContext",
    "PluginHost",
    "PyBotManifest",
    "SETTINGS_FILENAME",
    "SchemaProperty",
    "SettingsError",
    "SettingsSchema",
    "SettingsStore",
    "ToolCallHookContext",
    "on_message",
    "on_shutdown",
    "on_settings_change",
    "on_startup",
    "on_task_heartbeat",
    "on_tool_call",
    "plugin_host",
    "pybot_plugin",
]
