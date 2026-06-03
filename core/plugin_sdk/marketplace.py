"""Marketplace index — JSON file mapping ``id`` to ``manifest_url`` / ``git`` / ``path``.

The marketplace is intentionally a single static JSON file (``marketplace.json``)
either local or fetched over HTTP at install time.  This keeps PyBot
distribution-agnostic: a hosted marketplace is just a public URL pointing at
a file that follows this schema.

Format
~~~~~~

.. code-block:: json

   {
     "schema_version": "1.0",
     "updated_at": 1717720000,
     "entries": [
       {
         "id": "weather-skill",
         "kind": "skill",
         "version": "0.3.1",
         "name": "Weather skill",
         "description": "Calls the OpenWeatherMap API.",
         "git": "https://github.com/example/weather-skill.git",
         "tags": ["weather", "api"]
       }
     ]
   }

``git`` may be replaced by ``path`` (local filesystem) or ``archive``
(future).  ``id`` must be a valid manifest id (lower-case, hyphen/underscore
only).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


MARKETPLACE_SCHEMA_VERSION = "1.0"


class MarketplaceError(ValueError):
    """Raised when a marketplace index is malformed."""


@dataclass
class MarketplaceEntry:
    id: str
    kind: str
    version: str
    name: str = ""
    description: str = ""
    git: str | None = None
    path: str | None = None
    archive: str | None = None
    tags: tuple[str, ...] = ()

    def install_url(self) -> str:
        for value in (self.git, self.path, self.archive):
            if value:
                return value
        raise MarketplaceError(
            f"marketplace entry {self.id!r} has no source (git/path/archive)"
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        return data


@dataclass
class MarketplaceIndex:
    entries: list[MarketplaceEntry] = field(default_factory=list)
    schema_version: str = MARKETPLACE_SCHEMA_VERSION
    updated_at: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketplaceIndex":
        if not isinstance(data, dict):
            raise MarketplaceError("marketplace must be a JSON object")
        raw_entries = data.get("entries") or []
        if not isinstance(raw_entries, list):
            raise MarketplaceError("'entries' must be a list")
        entries: list[MarketplaceEntry] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise MarketplaceError("each entry must be an object")
            try:
                entries.append(_entry_from_dict(raw))
            except KeyError as exc:
                raise MarketplaceError(
                    f"entry missing required field {exc.args[0]!r}"
                ) from exc
        return cls(
            entries=entries,
            schema_version=str(data.get("schema_version", MARKETPLACE_SCHEMA_VERSION)),
            updated_at=_safe_float(data.get("updated_at")),
        )

    @classmethod
    def from_path(cls, path: str | Path) -> "MarketplaceIndex":
        p = Path(path)
        if not p.exists():
            raise MarketplaceError(f"marketplace index not found: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MarketplaceError(f"marketplace JSON invalid: {exc.msg}") from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "updated_at": self.updated_at,
            "entries": [e.to_dict() for e in self.entries],
        }

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def find(self, identifier: str) -> MarketplaceEntry | None:
        for entry in self.entries:
            if entry.id == identifier:
                return entry
        return None

    def search(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        tag: str | None = None,
    ) -> list[MarketplaceEntry]:
        q = query.strip().lower()
        results: list[MarketplaceEntry] = []
        for entry in self.entries:
            if kind and entry.kind != kind:
                continue
            if tag and tag not in entry.tags:
                continue
            if q and q not in entry.id.lower() \
                and q not in entry.name.lower() \
                and q not in entry.description.lower() \
                and not any(q in t.lower() for t in entry.tags):
                continue
            results.append(entry)
        return results


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _entry_from_dict(data: dict[str, Any]) -> MarketplaceEntry:
    return MarketplaceEntry(
        id=data["id"],
        kind=str(data.get("kind") or "skill"),
        version=str(data.get("version") or "0.0.0"),
        name=str(data.get("name") or data["id"]),
        description=str(data.get("description") or ""),
        git=data.get("git"),
        path=data.get("path"),
        archive=data.get("archive"),
        tags=tuple(data.get("tags") or ()),
    )


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "MARKETPLACE_SCHEMA_VERSION",
    "MarketplaceEntry",
    "MarketplaceError",
    "MarketplaceIndex",
]
