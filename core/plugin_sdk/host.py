"""PluginHost — runtime that orchestrates lifecycle hooks for installed plugins.

What it does
~~~~~~~~~~~~

* Holds a list of registered plugin **instances** (caller-constructed).
* Walks each instance once at registration time, indexing methods carrying
  ``_pybot_lifecycle_hook`` markers (set by the decorators in
  :mod:`core.plugin_sdk.decorators`).
* Exposes four explicit invocation entry points the rest of PyBot calls:
  ``startup()``, ``shutdown()``, ``settings_changed(plugin_id, settings)``,
  and ``task_heartbeat(snapshot)``.
* Subscribes to ``EventType.SCHEDULE_RUN`` so heartbeat events emitted by
  :mod:`core.systems.tasks.task_registry` flow into ``task_heartbeat``
  hooks automatically.

What it deliberately does **not** do
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* It does not load plugin classes from disk — that is the installer's job.
* It does not enforce permission grants — :func:`core.plugin_sdk.installer.install`
  already gates installation on declared permissions; once a plugin is
  registered it is trusted.
* It does not catch import errors per-plugin — those are surfaced upstream
  (so the user sees them at install time, not silently at runtime).

The host stays in :mod:`core.plugin_sdk` (Layer 0) because all it touches
is the L0 ``EventBus``; no upper-layer imports are introduced.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.systems.runtime.event_bus import Event, EventType, event_bus

logger = logging.getLogger(__name__)


# Markers we recognise on plugin methods.
_HOOK_NAMES: tuple[str, ...] = (
    "on_startup",
    "on_shutdown",
    "on_settings_change",
    "on_task_heartbeat",
)


@dataclass
class _PluginEntry:
    plugin: Any
    plugin_id: str
    hooks: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)


class PluginHost:
    """Lifecycle dispatcher for registered plugins."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plugins: dict[str, _PluginEntry] = {}
        self._started = False
        self._task_subscription_attached = False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, plugin: Any, *, plugin_id: str | None = None) -> str:
        """Register a constructed plugin instance and index its hook methods.

        Returns the plugin's id (resolved from ``_pybot_plugin_meta`` set by
        :func:`~core.plugin_sdk.decorators.pybot_plugin` when no explicit
        ``plugin_id`` is given).
        """
        if plugin_id is None:
            meta = getattr(plugin, "_pybot_plugin_meta", None) or {}
            plugin_id = (meta.get("id") if isinstance(meta, dict) else None) or _fallback_id(plugin)

        entry = _PluginEntry(plugin=plugin, plugin_id=plugin_id)
        for attr_name in dir(plugin):
            if attr_name.startswith("__"):
                continue
            try:
                attr = getattr(plugin, attr_name)
            except Exception:
                continue
            marker = getattr(attr, "_pybot_lifecycle_hook", None)
            if marker in _HOOK_NAMES:
                entry.hooks.setdefault(marker, []).append(attr)

        with self._lock:
            existing = self._plugins.get(plugin_id)
            if existing is not None:
                logger.info("Replacing previously registered plugin %r", plugin_id)
            self._plugins[plugin_id] = entry

            if not self._task_subscription_attached:
                event_bus.subscribe(EventType.SCHEDULE_RUN, self._on_schedule_event)
                self._task_subscription_attached = True

        if self._started:
            self._invoke(entry, "on_startup", args=())
        return plugin_id

    def unregister(self, plugin_id: str) -> bool:
        with self._lock:
            entry = self._plugins.pop(plugin_id, None)
        if entry is None:
            return False
        self._invoke(entry, "on_shutdown", args=())
        return True

    def reset(self) -> None:
        """Drop every registered plugin (for tests)."""
        with self._lock:
            entries = list(self._plugins.values())
            self._plugins.clear()
        for entry in entries:
            self._invoke(entry, "on_shutdown", args=())
        with self._lock:
            if self._task_subscription_attached:
                event_bus.unsubscribe(EventType.SCHEDULE_RUN, self._on_schedule_event)
                self._task_subscription_attached = False
            self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def startup(self) -> None:
        with self._lock:
            entries = list(self._plugins.values())
            self._started = True
        for entry in entries:
            self._invoke(entry, "on_startup", args=())

    def shutdown(self) -> None:
        with self._lock:
            entries = list(self._plugins.values())
            self._started = False
        for entry in entries:
            self._invoke(entry, "on_shutdown", args=())

    def settings_changed(self, plugin_id: str, settings: dict[str, Any]) -> None:
        with self._lock:
            entry = self._plugins.get(plugin_id)
        if entry is None:
            return
        self._invoke(entry, "on_settings_change", args=(plugin_id, settings))

    def task_heartbeat(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            entries = list(self._plugins.values())
        for entry in entries:
            self._invoke(entry, "on_task_heartbeat", args=(snapshot,))

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_plugins(self) -> list[str]:
        with self._lock:
            return sorted(self._plugins)

    def get(self, plugin_id: str) -> Any | None:
        with self._lock:
            entry = self._plugins.get(plugin_id)
        return entry.plugin if entry else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _invoke(self, entry: _PluginEntry, hook_name: str, *, args: tuple) -> None:
        for fn in entry.hooks.get(hook_name, []):
            try:
                fn(*args)
            except Exception:
                logger.exception(
                    "%s hook failed in plugin %r", hook_name, entry.plugin_id
                )

    def _on_schedule_event(self, event: Event) -> None:
        payload = event.payload or {}
        if payload.get("task_event") != "task.heartbeat":
            return
        snapshot = payload.get("snapshot") or {}
        if not isinstance(snapshot, dict):
            return
        self.task_heartbeat(snapshot)


plugin_host = PluginHost()


__all__ = ["PluginHost", "plugin_host"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fallback_id(plugin: Any) -> str:
    cls = type(plugin)
    return f"{cls.__module__}.{cls.__name__}"
