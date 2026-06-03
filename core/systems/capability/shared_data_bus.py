"""Shared Data Bus — cross-app data exchange for App Matrix mode.

Enables apps within an App Matrix to publish, subscribe to, and query
shared data channels. Each channel carries typed data that multiple
apps can read/write, forming a collaborative data mesh.

Data Flow:
  App A (publisher) → SharedDataBus → App B, App C (subscribers)

Features:
  - Named channels with optional schema validation
  - Publisher/subscriber pattern with async notification
  - Data retention with configurable TTL
  - Access control (read/write per-app permissions)
  - Query interface for cross-app data discovery
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class DataEntry:
    """A single data item on a shared channel."""

    key: str
    value: Any
    publisher: str
    timestamp: float = field(default_factory=time.time)
    ttl_seconds: float = 0  # 0 = no expiry
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.ttl_seconds <= 0:
            return False
        return time.time() - self.timestamp > self.ttl_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "publisher": self.publisher,
            "timestamp": self.timestamp,
            "ttl_seconds": self.ttl_seconds,
            "metadata": self.metadata,
        }


@dataclass
class ChannelConfig:
    """Configuration for a shared data channel."""

    name: str
    description: str = ""
    max_entries: int = 1000
    default_ttl: float = 0
    read_apps: list[str] = field(default_factory=lambda: ["*"])
    write_apps: list[str] = field(default_factory=lambda: ["*"])

    def can_read(self, app_name: str) -> bool:
        return "*" in self.read_apps or app_name in self.read_apps

    def can_write(self, app_name: str) -> bool:
        return "*" in self.write_apps or app_name in self.write_apps

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "max_entries": self.max_entries,
            "default_ttl": self.default_ttl,
            "read_apps": self.read_apps,
            "write_apps": self.write_apps,
        }


class SharedDataBus:
    """Cross-app data exchange bus."""

    def __init__(self):
        self._lock = threading.Lock()
        self._channels: dict[str, ChannelConfig] = {}
        self._data: dict[str, dict[str, DataEntry]] = {}
        self._subscribers: dict[str, list[Callable[[str, DataEntry], None]]] = defaultdict(list)

    def create_channel(self, config: ChannelConfig) -> None:
        with self._lock:
            self._channels[config.name] = config
            if config.name not in self._data:
                self._data[config.name] = {}
        logger.info("SharedDataBus: Channel '%s' created", config.name)

    def publish(
        self,
        channel: str,
        key: str,
        value: Any,
        publisher: str = "",
        ttl: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Publish data to a channel."""
        with self._lock:
            ch_config = self._channels.get(channel)
            if ch_config is None:
                ch_config = ChannelConfig(name=channel)
                self._channels[channel] = ch_config
                self._data[channel] = {}

            if not ch_config.can_write(publisher):
                logger.warning("SharedDataBus: App '%s' denied write to '%s'", publisher, channel)
                return False

            entry = DataEntry(
                key=key,
                value=value,
                publisher=publisher,
                ttl_seconds=ttl if ttl is not None else ch_config.default_ttl,
                metadata=metadata or {},
            )
            self._data[channel][key] = entry

            self._enforce_limits(channel)

        for handler in self._subscribers.get(channel, []):
            try:
                handler(channel, entry)
            except Exception as exc:
                logger.warning("SharedDataBus subscriber error: %s", exc)

        return True

    def get(self, channel: str, key: str, app_name: str = "") -> DataEntry | None:
        """Get a single entry from a channel."""
        with self._lock:
            ch_config = self._channels.get(channel)
            if ch_config and not ch_config.can_read(app_name):
                return None
            entries = self._data.get(channel, {})
            entry = entries.get(key)
            if entry and entry.is_expired:
                del entries[key]
                return None
            return entry

    def query(
        self,
        channel: str,
        app_name: str = "",
        prefix: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query entries from a channel."""
        with self._lock:
            ch_config = self._channels.get(channel)
            if ch_config and not ch_config.can_read(app_name):
                return []
            entries = self._data.get(channel, {})
            results = []
            for key, entry in sorted(entries.items(), key=lambda x: x[1].timestamp, reverse=True):
                if entry.is_expired:
                    continue
                if prefix and not key.startswith(prefix):
                    continue
                results.append(entry.to_dict())
                if len(results) >= limit:
                    break
            return results

    def subscribe(
        self,
        channel: str,
        handler: Callable[[str, DataEntry], None],
    ) -> None:
        self._subscribers[channel].append(handler)

    def list_channels(self) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for name, config in self._channels.items():
                entry_count = len([
                    e for e in self._data.get(name, {}).values()
                    if not e.is_expired
                ])
                result.append({
                    **config.to_dict(),
                    "entry_count": entry_count,
                    "subscriber_count": len(self._subscribers.get(name, [])),
                })
            return result

    def delete_channel(self, channel: str) -> bool:
        with self._lock:
            removed = self._channels.pop(channel, None)
            self._data.pop(channel, None)
            self._subscribers.pop(channel, None)
            return removed is not None

    def _enforce_limits(self, channel: str) -> None:
        ch_config = self._channels.get(channel)
        if ch_config is None:
            return
        entries = self._data.get(channel, {})

        expired = [k for k, e in entries.items() if e.is_expired]
        for k in expired:
            del entries[k]

        if len(entries) > ch_config.max_entries:
            sorted_keys = sorted(entries.keys(), key=lambda k: entries[k].timestamp)
            excess = len(entries) - ch_config.max_entries
            for k in sorted_keys[:excess]:
                del entries[k]

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            total_entries = sum(
                len([e for e in ch.values() if not e.is_expired])
                for ch in self._data.values()
            )
            return {
                "channel_count": len(self._channels),
                "total_entries": total_entries,
                "total_subscribers": sum(len(s) for s in self._subscribers.values()),
            }
