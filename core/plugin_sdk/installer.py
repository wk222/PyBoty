"""``pybot install`` implementation.

Resolves an installation source (local path, git URL, or marketplace name),
verifies the bundled :class:`PyBotManifest`, copies the payload into
``workspace/extensions/<id>/`` and emits a ``CAPABILITY_INSTALLED`` event so
the rest of the runtime (Skills registry, Agent loader, MCP hub, …) can
pick it up without the installer needing direct dependencies on each.

The installer is deliberately decoupled from the marketplace lookup — the
caller hands it a resolved ``InstallSource`` so that:

* unit tests can run end-to-end without git or HTTP,
* the same code path serves "from local checkout", "from git" and
  "from marketplace cache".

This module sits in :mod:`core.plugin_sdk` (Layer 0), therefore it cannot
import from L1+ subsystems.  EventBus emission goes through the existing
``core.systems.runtime.event_bus`` module which is L0 (the runtime
foundation), so that import is legal.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.plugin_sdk.manifest import (
    MANIFEST_FILENAME,
    ManifestError,
    PyBotManifest,
)
from core.systems.runtime.event_bus import Event, EventType, event_bus

logger = logging.getLogger(__name__)


_DEFAULT_EXTENSIONS_DIR = Path("workspace/extensions")


class InstallSourceKind(str, Enum):
    LOCAL = "local"
    GIT = "git"
    MARKETPLACE = "marketplace"


@dataclass
class InstallSource:
    """Where the manifest payload comes from."""

    kind: InstallSourceKind
    location: str

    @classmethod
    def autodetect(cls, raw: str) -> "InstallSource":
        text = raw.strip()
        if not text:
            raise ValueError("install source is empty")
        if text.startswith("git+") or text.endswith(".git") or _looks_like_url(text):
            return cls(InstallSourceKind.GIT, text.removeprefix("git+"))
        path = Path(text).expanduser()
        if path.exists():
            return cls(InstallSourceKind.LOCAL, str(path.resolve()))
        return cls(InstallSourceKind.MARKETPLACE, text)


def _looks_like_url(text: str) -> bool:
    parsed = urlparse(text)
    return bool(parsed.scheme and parsed.netloc)


@dataclass
class InstallResult:
    """What :func:`install` returns."""

    manifest: PyBotManifest
    install_dir: Path
    source: InstallSource
    installed_at: float = field(default_factory=time.time)
    upgraded_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "install_dir": str(self.install_dir),
            "source": dataclasses.asdict(self.source),
            "installed_at": self.installed_at,
            "upgraded_from": self.upgraded_from,
        }


# Marketplace resolver type: (name) -> InstallSource (e.g. local cache path)
MarketplaceResolver = Callable[[str], InstallSource]


def install(
    source: str | InstallSource,
    *,
    extensions_root: Path | str | None = None,
    marketplace_resolver: MarketplaceResolver | None = None,
    grant_permissions: set[str] | None = None,
    dry_run: bool = False,
) -> InstallResult:
    """Install or upgrade an extension.

    Parameters
    ----------
    source:
        A raw string (auto-detected) or an explicit :class:`InstallSource`.
    extensions_root:
        Where to copy the extension; defaults to ``workspace/extensions``.
    marketplace_resolver:
        Function mapping a marketplace name to an :class:`InstallSource`
        (typically pointing at a downloaded tarball or git clone).  Required
        if any installs use ``InstallSourceKind.MARKETPLACE``.
    grant_permissions:
        Caller-pre-granted permissions.  Manifest permissions not in this
        set raise :class:`PermissionError` so callers (CLI, web admin) can
        prompt before granting.  Pass ``None`` to grant everything declared.
    dry_run:
        If ``True``, validate and resolve everything but do not copy files
        or emit events.

    Returns
    -------
    :class:`InstallResult` summarising what was installed.
    """
    src = source if isinstance(source, InstallSource) else InstallSource.autodetect(str(source))
    root = Path(extensions_root) if extensions_root else _DEFAULT_EXTENSIONS_DIR
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    with _stage(src, marketplace_resolver) as staged:
        manifest_path = staged / MANIFEST_FILENAME
        if not manifest_path.exists():
            raise ManifestError(
                f"source {src.location!r} does not contain {MANIFEST_FILENAME}"
            )

        manifest = PyBotManifest.from_path(manifest_path)
        manifest.assert_compatible()

        if grant_permissions is not None:
            missing = set(manifest.permissions) - set(grant_permissions)
            if missing:
                raise PermissionError(
                    f"manifest requests ungranted permissions: {sorted(missing)}"
                )

        install_dir = root / manifest.id
        upgraded_from: str | None = None

        if dry_run:
            return InstallResult(
                manifest=manifest, install_dir=install_dir, source=src
            )

        if install_dir.exists():
            existing_manifest_path = install_dir / MANIFEST_FILENAME
            if existing_manifest_path.exists():
                try:
                    existing = PyBotManifest.from_path(existing_manifest_path)
                    upgraded_from = existing.version
                except ManifestError:
                    upgraded_from = "unparseable"
            shutil.rmtree(install_dir)

        shutil.copytree(staged, install_dir)
        _write_install_record(
            install_dir,
            source=src,
            installed_at=time.time(),
            upgraded_from=upgraded_from,
        )

    event_bus.emit(
        Event(
            type=EventType.CAPABILITY_INSTALLED,
            payload={
                "id": manifest.id,
                "kind": manifest.kind.value,
                "version": manifest.version,
                "install_dir": str(install_dir),
                "source": dataclasses.asdict(src),
                "upgraded_from": upgraded_from,
            },
            source="plugin_sdk.installer",
        )
    )
    logger.info(
        "Installed %s %s@%s -> %s%s",
        manifest.kind.value,
        manifest.id,
        manifest.version,
        install_dir,
        f" (upgraded from {upgraded_from})" if upgraded_from else "",
    )
    return InstallResult(
        manifest=manifest,
        install_dir=install_dir,
        source=src,
        upgraded_from=upgraded_from,
    )


def uninstall(
    extension_id: str,
    *,
    extensions_root: Path | str | None = None,
) -> bool:
    """Remove an installed extension by id.  Returns ``True`` if anything was deleted."""
    root = Path(extensions_root) if extensions_root else _DEFAULT_EXTENSIONS_DIR
    install_dir = root / extension_id
    if not install_dir.exists():
        return False
    manifest = None
    manifest_path = install_dir / MANIFEST_FILENAME
    if manifest_path.exists():
        try:
            manifest = PyBotManifest.from_path(manifest_path)
        except ManifestError:
            manifest = None
    shutil.rmtree(install_dir)
    event_bus.emit(
        Event(
            type=EventType.CAPABILITY_INSTALLED,  # use the same event family with a flag
            payload={
                "id": extension_id,
                "kind": manifest.kind.value if manifest else "unknown",
                "version": manifest.version if manifest else "unknown",
                "uninstalled": True,
            },
            source="plugin_sdk.installer",
        )
    )
    return True


def list_installed(
    extensions_root: Path | str | None = None,
) -> list[PyBotManifest]:
    """Enumerate installed extensions found under *extensions_root*."""
    root = Path(extensions_root) if extensions_root else _DEFAULT_EXTENSIONS_DIR
    if not root.exists():
        return []
    manifests: list[PyBotManifest] = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest_path = sub / MANIFEST_FILENAME
        if not manifest_path.exists():
            continue
        try:
            manifests.append(PyBotManifest.from_path(manifest_path))
        except ManifestError as exc:
            logger.warning("ignoring broken manifest at %s: %s", manifest_path, exc)
    return manifests


# ---------------------------------------------------------------------------
# Internal staging helpers
# ---------------------------------------------------------------------------


class _Staged:
    """Context manager yielding a Path that contains the manifest payload."""

    def __init__(self, root: Path, *, cleanup: bool) -> None:
        self.root = root
        self._cleanup = cleanup

    def __enter__(self) -> Path:
        return self.root

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._cleanup and self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)


def _stage(
    source: InstallSource,
    marketplace_resolver: MarketplaceResolver | None,
) -> _Staged:
    if source.kind is InstallSourceKind.LOCAL:
        return _Staged(Path(source.location), cleanup=False)

    if source.kind is InstallSourceKind.MARKETPLACE:
        if marketplace_resolver is None:
            raise RuntimeError(
                "marketplace install requires a marketplace_resolver callback"
            )
        resolved = marketplace_resolver(source.location)
        return _stage(resolved, marketplace_resolver=None)

    if source.kind is InstallSourceKind.GIT:
        tmp = Path(tempfile.mkdtemp(prefix="pybot-install-"))
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", source.location, str(tmp)],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError("git is not installed; cannot fetch git sources") from exc
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise RuntimeError(f"git clone failed: {stderr.strip()}") from exc
        return _Staged(tmp, cleanup=True)

    raise RuntimeError(f"unknown install source kind: {source.kind}")


def _write_install_record(
    install_dir: Path,
    *,
    source: InstallSource,
    installed_at: float,
    upgraded_from: str | None,
) -> None:
    record = {
        "source": dataclasses.asdict(source),
        "installed_at": installed_at,
        "upgraded_from": upgraded_from,
    }
    (install_dir / ".install.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


__all__ = [
    "InstallResult",
    "InstallSource",
    "InstallSourceKind",
    "MarketplaceResolver",
    "install",
    "list_installed",
    "uninstall",
]
