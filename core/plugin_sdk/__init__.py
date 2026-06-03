"""Public SDK for third-party PyBot plugins."""

from core.plugin_sdk.decorators import on_message, on_tool_call, pybot_plugin
from core.plugin_sdk.hooks import MessageHookContext, PluginHookContext, ToolCallHookContext

__all__ = [
    "MessageHookContext",
    "PluginHookContext",
    "ToolCallHookContext",
    "on_message",
    "on_tool_call",
    "pybot_plugin",
]
