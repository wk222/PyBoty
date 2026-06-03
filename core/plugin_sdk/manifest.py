"""``pybot.manifest.json`` schema and loader.

Every installable extension — skill, agent, plugin, channel, or MCP server —
ships a single ``pybot.manifest.json`` describing how PyBot should fetch,
verify, configure and execute it.  This file gives a typed Python view of
that schema plus a hardened loader that

* refuses unknown extension kinds,
* normalises permission grants,
* checks SemVer compatibility against the running PyBot release,
* validates the optional ``cron`` field as a 5-field expression so that a
  bad manifest fails at install time rather than the first scheduler tick.

The schema is intentionally tiny — extra implementation-specific fields go
into the open ``settings`` block, which is bound to the extension's own
``settings.schema.json`` (see W5).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from core.systems.runtime.version import get_pybot_version


MANIFEST_FILENAME = "pybot.manifest.json"
MANIFEST_VERSION = "1.0"


class ManifestKind(str, Enum):
    SKILL = "skill"
    AGENT = "agent"
    PLUGIN = "plugin"
    CHANNEL = "channel"
    MCP = "mcp"


_SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.]+)?$")
_CRON = re.compile(r"^\S+\s+\S+\s+\S+\s+\S+\s+\S+$")


_KNOWN_PERMISSIONS: frozenset[str] = frozenset(
    {
        "fs:read",
        "fs:write",
        "network",
        "shell",
        "memory:read",
        "memory:write",
        "agent:spawn",
        "tool:invoke",
        "settings:write",
    }
)


class ManifestError(ValueError):
    """Raised when a manifest fails validation."""


@dataclass
class CompatRule:
    pybot: str = ">=0.1.0"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CompatRule":
        if not data:
            return cls()
        if not isinstance(data, dict):
            raise ManifestError("compat must be a JSON object")
        rule = data.get("pybot", ">=0.1.0")
        if not isinstance(rule, str):
            raise ManifestError("compat.pybot must be a string")
        return cls(pybot=rule.strip())


@dataclass
class PyBotManifest:
    """Typed view of ``pybot.manifest.json``."""

    id: str
    kind: ManifestKind
    version: str
    entrypoint: str
    name: str = ""
    description: str = ""
    author: str = ""
    permissions: tuple[str, ...] = ()
    settings_schema: str | None = None
    cron: str | None = None
    compat: CompatRule = field(default_factory=CompatRule)
    extra: dict[str, Any] = field(default_factory=dict)
    manifest_version: str = MANIFEST_VERSION

    # ------------------------------------------------------------------
    # Construction / serialisation
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PyBotManifest":
        if not isinstance(data, dict):
            raise ManifestError("manifest root must be a JSON object")

        try:
            kind_raw = str(data["kind"]).strip().lower()
            kind = ManifestKind(kind_raw)
        except KeyError as exc:
            raise ManifestError("missing required field 'kind'") from exc
        except ValueError as exc:
            raise ManifestError(
                f"unknown kind {data.get('kind')!r}; expected one of "
                f"{[k.value for k in ManifestKind]}"
            ) from exc

        identifier = str(data.get("id", "")).strip()
        if not _SAFE_ID.match(identifier):
            raise ManifestError(
                "field 'id' must match [a-z][a-z0-9_-]{1,63} (lower-case ascii)"
            )

        version = str(data.get("version", "")).strip()
        if not _SEMVER.match(version):
            raise ManifestError(
                f"field 'version' must be SemVer x.y.z, got {version!r}"
            )

        entrypoint = str(data.get("entrypoint", "")).strip()
        if not entrypoint:
            raise ManifestError("field 'entrypoint' is required")
        if "::" in entrypoint or "/" in entrypoint:
            raise ManifestError(
                "field 'entrypoint' must use Python dotted form, e.g. 'pkg.mod:fn'"
            )

        permissions_raw = data.get("permissions", []) or []
        if not isinstance(permissions_raw, list):
            raise ManifestError("field 'permissions' must be a list of strings")
        permissions: list[str] = []
        for perm in permissions_raw:
            if not isinstance(perm, str):
                raise ManifestError("permissions entries must be strings")
            normalised = perm.strip().lower()
            if normalised not in _KNOWN_PERMISSIONS:
                raise ManifestError(
                    f"unknown permission {perm!r}; allowed: {sorted(_KNOWN_PERMISSIONS)}"
                )
            permissions.append(normalised)

        cron = data.get("cron")
        if cron is not None:
            if not isinstance(cron, str) or not _CRON.match(cron.strip()):
                raise ManifestError(
                    "field 'cron' must be a 5-field cron expression"
                )
            cron = cron.strip()

        settings_schema = data.get("settings_schema")
        if settings_schema is not None and not isinstance(settings_schema, str):
            raise ManifestError("field 'settings_schema' must be a string path or URL")

        manifest_version = str(data.get("manifest_version", MANIFEST_VERSION)).strip()

        # Anything not explicitly modelled is preserved verbatim under ``extra``
        known = {
            "id",
            "kind",
            "version",
            "entrypoint",
            "name",
            "description",
            "author",
            "permissions",
            "settings_schema",
            "cron",
            "compat",
            "manifest_version",
        }
        extra = {k: v for k, v in data.items() if k not in known}

        return cls(
            id=identifier,
            kind=kind,
            version=version,
            entrypoint=entrypoint,
            name=str(data.get("name") or identifier),
            description=str(data.get("description") or ""),
            author=str(data.get("author") or ""),
            permissions=tuple(permissions),
            settings_schema=settings_schema,
            cron=cron,
            compat=CompatRule.from_dict(data.get("compat")),
            extra=extra,
            manifest_version=manifest_version,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["permissions"] = list(self.permissions)
        if not data.get("extra"):
            data.pop("extra", None)
        return data

    @classmethod
    def from_path(cls, path: str | Path) -> "PyBotManifest":
        p = Path(path)
        if p.is_dir():
            p = p / MANIFEST_FILENAME
        if not p.exists():
            raise ManifestError(f"manifest not found: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ManifestError(f"manifest is not valid JSON: {exc.msg}") from exc
        return cls.from_dict(data)

    # ------------------------------------------------------------------
    # Compatibility checks
    # ------------------------------------------------------------------

    def assert_compatible(self, pybot_version: str | None = None) -> None:
        """Raise :class:`ManifestError` if PyBot fails the SemVer rule."""
        version = pybot_version or get_pybot_version()
        if not _check_semver_rule(version, self.compat.pybot):
            raise ManifestError(
                f"manifest requires PyBot {self.compat.pybot}, current is {version}"
            )


# ---------------------------------------------------------------------------
# Tiny SemVer comparator (intentionally minimal — supports >=, >, <=, <, == and "*")
# ---------------------------------------------------------------------------


_COMPARATOR = re.compile(r"^\s*(>=|<=|==|>|<|=)?\s*(\d+(?:\.\d+){0,2}[\w.+-]*)\s*$")


def _parse_version(value: str) -> tuple[int, int, int]:
    parts = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", value.strip())
    if not parts:
        return (0, 0, 0)
    major = int(parts.group(1) or 0)
    minor = int(parts.group(2) or 0)
    patch = int(parts.group(3) or 0)
    return (major, minor, patch)


def _check_semver_rule(actual: str, rule: str) -> bool:
    rule = (rule or "*").strip()
    if rule in ("*", ""):
        return True
    actual_t = _parse_version(actual)
    # support comma-separated AND like ">=0.5.0,<1.0.0"
    for piece in rule.split(","):
        m = _COMPARATOR.match(piece.strip())
        if not m:
            return False
        op = (m.group(1) or "==").replace("=", "==", 1) if m.group(1) == "=" else (m.group(1) or "==")
        target = _parse_version(m.group(2))
        if op == ">=" and actual_t < target:
            return False
        if op == ">" and actual_t <= target:
            return False
        if op == "<=" and actual_t > target:
            return False
        if op == "<" and actual_t >= target:
            return False
        if op == "==" and actual_t != target:
            return False
    return True


__all__ = [
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "CompatRule",
    "ManifestError",
    "ManifestKind",
    "PyBotManifest",
]
