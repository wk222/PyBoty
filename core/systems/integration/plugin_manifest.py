"""Plugin manifest system, loader, lifecycle registry, and hook dispatch."""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from core.plugin_sdk.hooks import MessageHookContext, ToolCallHookContext

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "pybot.plugin.json"


class PluginLifecycle(Protocol):
    def on_load(self, manifest: PluginManifest) -> None: ...

    def on_enable(self, manifest: PluginManifest) -> None: ...

    def on_disable(self, manifest: PluginManifest) -> None: ...

    def on_unload(self, manifest: PluginManifest) -> None: ...


@dataclass
class PluginManifest:
    """Parsed plugin manifest."""

    id: str
    name: str
    description: str = ""
    version: str = "0.0.0"
    author: str = ""
    capabilities: list[str] = field(default_factory=list)
    config_schema: dict[str, Any] | None = None
    entry_point: str | None = None
    directory: str = ""
    routes: list[dict[str, str]] = field(default_factory=list)
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_capability(self, cap: str) -> bool:
        return cap in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "capabilities": self.capabilities,
            "entry_point": self.entry_point,
            "enabled": self.enabled,
            "directory": self.directory,
        }
        if self.config_schema:
            d["config_schema"] = self.config_schema
        if self.routes:
            d["routes"] = self.routes
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    module: ModuleType | None = None
    plugin: Any = None
    enabled: bool = False
    before_tool_call_handlers: list[Any] = field(default_factory=list)
    after_tool_call_handlers: list[Any] = field(default_factory=list)
    message_handlers: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.manifest.id,
            "name": self.manifest.name,
            "enabled": self.enabled,
            "loaded": self.module is not None or self.plugin is not None,
            "before_tool_call_handlers": len(self.before_tool_call_handlers),
            "after_tool_call_handlers": len(self.after_tool_call_handlers),
            "message_handlers": len(self.message_handlers),
        }


def parse_manifest(manifest_path: str) -> PluginManifest | None:
    """Parse a ``pybot.plugin.json`` file and return a PluginManifest."""
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict) or "id" not in data:
            logger.warning("Invalid manifest (missing 'id'): %s", manifest_path)
            return None

        return PluginManifest(
            id=data["id"],
            name=data.get("name", data["id"]),
            description=data.get("description", ""),
            version=data.get("version", "0.0.0"),
            author=data.get("author", ""),
            capabilities=data.get("capabilities", []),
            config_schema=data.get("config_schema") or data.get("configSchema"),
            entry_point=data.get("entry_point") or data.get("entryPoint"),
            directory=str(Path(manifest_path).parent),
            routes=data.get("routes", []),
            enabled=data.get("enabled", True),
            metadata=data.get("metadata", {}),
        )
    except Exception as exc:
        logger.warning("Failed to parse manifest %s: %s", manifest_path, exc)
        return None


class PluginLoader:
    """Load plugin modules, lifecycle callbacks, and decorated hooks."""

    def load(self, manifest: PluginManifest) -> LoadedPlugin:
        runtime = LoadedPlugin(manifest=manifest)
        if not manifest.entry_point:
            return runtime

        module = self._import_entrypoint(manifest)
        plugin_obj = self._resolve_entrypoint_target(manifest, module)
        before_handlers, after_handlers, message_handlers = self._collect_hooks(module, plugin_obj)
        runtime.module = module
        runtime.plugin = plugin_obj
        runtime.before_tool_call_handlers = before_handlers
        runtime.after_tool_call_handlers = after_handlers
        runtime.message_handlers = message_handlers
        self._call_lifecycle(runtime, "on_load")
        return runtime

    def enable(self, runtime: LoadedPlugin) -> LoadedPlugin:
        if not runtime.enabled:
            self._call_lifecycle(runtime, "on_enable")
            runtime.enabled = True
        return runtime

    def disable(self, runtime: LoadedPlugin) -> LoadedPlugin:
        if runtime.enabled:
            self._call_lifecycle(runtime, "on_disable")
            runtime.enabled = False
        return runtime

    def unload(self, runtime: LoadedPlugin) -> None:
        if runtime.enabled:
            self.disable(runtime)
        self._call_lifecycle(runtime, "on_unload")

    def _import_entrypoint(self, manifest: PluginManifest) -> ModuleType:
        package_root = str(Path(manifest.directory).resolve().parent)
        with _temp_sys_path(package_root):
            module_name, _sep, _attribute = (manifest.entry_point or "").partition(":")
            return importlib.import_module(module_name)

    @staticmethod
    def _resolve_plugin_object(module: ModuleType) -> Any:
        explicit_plugin = getattr(module, "plugin", None)
        if explicit_plugin is not None:
            return explicit_plugin

        entry_point = getattr(module, "__pybot_entry_point__", None)
        if entry_point is not None:
            return entry_point

        for _name, cls in inspect.getmembers(module, inspect.isclass):
            if getattr(cls, "_pybot_plugin_meta", None) is None:
                continue
            return cls()
        return None

    def _resolve_entrypoint_target(self, manifest: PluginManifest, module: ModuleType) -> Any:
        entry_point = manifest.entry_point or ""
        _module_name, sep, attribute = entry_point.partition(":")
        if not sep or not attribute:
            return self._resolve_plugin_object(module)

        target = getattr(module, attribute, None)
        if target is None:
            raise AttributeError(f"Plugin entry point attribute '{attribute}' not found in {entry_point!r}")
        if inspect.isclass(target):
            return target()
        return target

    def _collect_hooks(
        self,
        module: ModuleType,
        plugin_obj: Any,
    ) -> tuple[list[Any], list[Any], list[Any]]:
        before_handlers: list[Any] = []
        after_handlers: list[Any] = []
        message_handlers: list[Any] = []
        seen: set[int] = set()

        for handler in self._iter_hook_candidates(module, plugin_obj):
            handler_id = id(handler)
            if handler_id in seen:
                continue
            seen.add(handler_id)
            phase = getattr(handler, "_pybot_tool_hook", None)
            if phase == "before":
                before_handlers.append(handler)
            elif phase == "after":
                after_handlers.append(handler)
            if getattr(handler, "_pybot_message_hook", False):
                message_handlers.append(handler)

        return before_handlers, after_handlers, message_handlers

    @staticmethod
    def _iter_hook_candidates(module: ModuleType, plugin_obj: Any):
        for _name, value in inspect.getmembers(module):
            if callable(value):
                yield value
        if plugin_obj is None:
            return
        for _name, value in inspect.getmembers(plugin_obj):
            if callable(value):
                yield value

    @staticmethod
    def _call_lifecycle(runtime: LoadedPlugin, method_name: str) -> None:
        targets = [runtime.plugin, runtime.module]
        for target in targets:
            if target is None:
                continue
            method = getattr(target, method_name, None)
            if callable(method):
                try:
                    signature = inspect.signature(method)
                except (TypeError, ValueError):
                    signature = None
                if signature is not None and len(signature.parameters) == 0:
                    method()
                else:
                    method(runtime.manifest)


class PluginRegistry:
    """Registry of discovered plugins, loaded runtimes, and hook dispatch."""

    def __init__(self, loader: PluginLoader | None = None) -> None:
        self._plugins: dict[str, PluginManifest] = {}
        self._runtimes: dict[str, LoadedPlugin] = {}
        self._loader = loader or PluginLoader()

    def register(self, manifest: PluginManifest) -> None:
        self._plugins[manifest.id] = manifest
        logger.info("Registered plugin: %s v%s (%s)", manifest.id, manifest.version, manifest.capabilities)

    def unregister(self, plugin_id: str) -> bool:
        manifest = self._plugins.get(plugin_id)
        if manifest and manifest.metadata.get("protected", False):
            logger.warning("Attempted to unregister protected plugin: %s", plugin_id)
            raise ValueError(f"Plugin {plugin_id} is protected and cannot be uninstalled")
            
        if plugin_id in self._runtimes:
            self.unload_plugin(plugin_id)
        return self._plugins.pop(plugin_id, None) is not None

    def get(self, plugin_id: str) -> PluginManifest | None:
        return self._plugins.get(plugin_id)

    def all(self) -> list[PluginManifest]:
        return list(self._plugins.values())

    def by_capability(self, capability: str) -> list[PluginManifest]:
        return [p for p in self._plugins.values() if p.has_capability(capability)]

    def enabled(self) -> list[PluginManifest]:
        return [p for p in self._plugins.values() if p.enabled]

    def count(self) -> int:
        return len(self._plugins)

    def to_dict(self) -> list[dict[str, Any]]:
        return [self.describe_plugin(manifest.id) for manifest in self._plugins.values()]

    def describe_plugin(self, plugin_id: str) -> dict[str, Any]:
        manifest = self.get(plugin_id)
        if manifest is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")
        payload = manifest.to_dict()
        runtime = self._runtimes.get(plugin_id)
        if runtime is not None:
            payload["runtime"] = runtime.to_dict()
        else:
            payload["runtime"] = {
                "id": manifest.id,
                "name": manifest.name,
                "enabled": manifest.enabled,
                "loaded": False,
                "before_tool_call_handlers": 0,
                "after_tool_call_handlers": 0,
                "message_handlers": 0,
            }
        return payload

    def get_runtime(self, plugin_id: str) -> LoadedPlugin | None:
        return self._runtimes.get(plugin_id)

    def loaded_plugins(self) -> list[LoadedPlugin]:
        self._ensure_enabled_plugins_loaded()
        return list(self._runtimes.values())

    def load_plugin(self, plugin_id: str) -> LoadedPlugin:
        runtime = self._runtimes.get(plugin_id)
        if runtime is not None:
            return runtime
        manifest = self.get(plugin_id)
        if manifest is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")
            
        # Optional: Check for malicious dependencies here before loading
        self._scan_plugin_dependencies(manifest)
        
        runtime = self._loader.load(manifest)
        self._runtimes[plugin_id] = runtime
        if manifest.enabled:
            self._loader.enable(runtime)
        return runtime

    def _scan_plugin_dependencies(self, manifest: PluginManifest) -> None:
        """Scan plugin manifest for forbidden capabilities or dependencies."""
        forbidden_caps = {"system_exec", "raw_fs_access"}
        for cap in manifest.capabilities:
            if cap in forbidden_caps:
                logger.warning("Plugin %s requests high-risk capability: %s", manifest.id, cap)
                # In a strict environment, we could raise an exception here
                # raise ValueError(f"Plugin {manifest.id} uses forbidden capability {cap}")

    def enable_plugin(self, plugin_id: str) -> LoadedPlugin:
        manifest = self.get(plugin_id)
        if manifest is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")
        manifest.enabled = True
        runtime = self.load_plugin(plugin_id)
        return self._loader.enable(runtime)

    def disable_plugin(self, plugin_id: str) -> LoadedPlugin:
        manifest = self.get(plugin_id)
        if manifest is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")
        manifest.enabled = False
        runtime = self.load_plugin(plugin_id)
        return self._loader.disable(runtime)

    def unload_plugin(self, plugin_id: str) -> bool:
        runtime = self._runtimes.pop(plugin_id, None)
        if runtime is None:
            return False
        self._loader.unload(runtime)
        return True

    def run_before_tool_call(self, context: ToolCallHookContext) -> ToolCallHookContext:
        for runtime in self.loaded_plugins():
            if not runtime.enabled:
                continue
            for handler in runtime.before_tool_call_handlers:
                context.plugin_id = runtime.manifest.id
                try:
                    handler(context)
                except Exception:
                    logger.exception("Plugin hook failed: %s.before_tool_call", runtime.manifest.id)
                    continue
                if context.cancel:
                    return context
        return context

    def run_after_tool_call(self, context: ToolCallHookContext) -> ToolCallHookContext:
        for runtime in self.loaded_plugins():
            if not runtime.enabled:
                continue
            for handler in runtime.after_tool_call_handlers:
                context.plugin_id = runtime.manifest.id
                try:
                    handler(context)
                except Exception:
                    logger.exception("Plugin hook failed: %s.after_tool_call", runtime.manifest.id)
        return context

    def run_message_hooks(self, context: MessageHookContext) -> MessageHookContext:
        for runtime in self.loaded_plugins():
            if not runtime.enabled:
                continue
            for handler in runtime.message_handlers:
                context.plugin_id = runtime.manifest.id
                try:
                    handler(context)
                except Exception:
                    logger.exception("Plugin hook failed: %s.on_message", runtime.manifest.id)
                    continue
                if context.cancel:
                    return context
        return context

    def _ensure_enabled_plugins_loaded(self) -> None:
        for manifest in list(self._plugins.values()):
            if manifest.enabled and manifest.id not in self._runtimes:
                self.load_plugin(manifest.id)


_global_registry: PluginRegistry | None = None


def get_plugin_registry() -> PluginRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = PluginRegistry()
    return _global_registry


def reset_plugin_registry() -> PluginRegistry:
    global _global_registry
    _global_registry = PluginRegistry()
    return _global_registry


def discover_plugins(
    directories: list[str],
    *,
    registry: PluginRegistry | None = None,
) -> list[PluginManifest]:
    """Scan directories for ``pybot.plugin.json`` manifests and register them."""
    registry = registry or get_plugin_registry()
    discovered: list[PluginManifest] = []

    for dir_path in directories:
        if not os.path.isdir(dir_path):
            continue

        direct = os.path.join(dir_path, MANIFEST_FILENAME)
        if os.path.isfile(direct):
            manifest = parse_manifest(direct)
            if manifest:
                registry.register(manifest)
                discovered.append(manifest)
            continue

        for entry in os.listdir(dir_path):
            sub = os.path.join(dir_path, entry)
            if os.path.isdir(sub):
                manifest_path = os.path.join(sub, MANIFEST_FILENAME)
                if os.path.isfile(manifest_path):
                    manifest = parse_manifest(manifest_path)
                    if manifest:
                        registry.register(manifest)
                        discovered.append(manifest)

    logger.info("Discovered %d plugins from %d directories", len(discovered), len(directories))
    return discovered


@contextmanager
def _temp_sys_path(path: str):
    inserted = False
    if path and path not in sys.path:
        sys.path.insert(0, path)
        inserted = True
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(path)
            except ValueError:
                pass


__all__ = [
    "LoadedPlugin",
    "MANIFEST_FILENAME",
    "PluginLifecycle",
    "PluginLoader",
    "PluginManifest",
    "PluginRegistry",
    "discover_plugins",
    "get_plugin_registry",
    "parse_manifest",
    "reset_plugin_registry",
]
