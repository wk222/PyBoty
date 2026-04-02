"""Backend adapters for skill discovery and file access."""

from __future__ import annotations

import asyncio
import posixpath
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from core.path_utils import safe_resolve


@dataclass(frozen=True)
class SkillFileEntry:
    """A file entry within a skill source."""

    path: str
    size: int


@dataclass(frozen=True)
class SkillBackendCapabilities:
    """Capability flags surfaced by a skill backend."""

    can_read_bundle: bool = True
    can_write_bundle: bool = True
    can_read_files: bool = True
    can_write_files: bool = True
    can_remove_tree: bool = True
    supports_python_tools: bool = True
    has_native_local_path: bool = False
    remote: bool = False
    supports_catalog_pagination: bool = False
    supports_request_backpressure: bool = False
    supports_conditional_requests: bool = False
    supports_authenticated_access: bool = False
    supports_registry_descriptor: bool = False
    supports_auth_negotiation: bool = False
    supports_session_negotiation: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "can_read_bundle": self.can_read_bundle,
            "can_write_bundle": self.can_write_bundle,
            "can_read_files": self.can_read_files,
            "can_write_files": self.can_write_files,
            "can_remove_tree": self.can_remove_tree,
            "supports_python_tools": self.supports_python_tools,
            "has_native_local_path": self.has_native_local_path,
            "remote": self.remote,
            "supports_catalog_pagination": self.supports_catalog_pagination,
            "supports_request_backpressure": self.supports_request_backpressure,
            "supports_conditional_requests": self.supports_conditional_requests,
            "supports_authenticated_access": self.supports_authenticated_access,
            "supports_registry_descriptor": self.supports_registry_descriptor,
            "supports_auth_negotiation": self.supports_auth_negotiation,
            "supports_session_negotiation": self.supports_session_negotiation,
        }


@dataclass(frozen=True)
class SkillBackendRootInfo:
    """Structured description of a backend root/source."""

    root: str
    backend_name: str
    exists: bool
    display_path: str
    local_path: str = ""
    remote: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "root": self.root,
            "backend_name": self.backend_name,
            "exists": self.exists,
            "display_path": self.display_path,
            "local_path": self.local_path,
            "remote": self.remote,
            "metadata": self.metadata,
        }


def validate_skill_bundle(files: dict[str, str]) -> dict[str, str]:
    """Normalize and validate a text-file skill bundle."""
    normalized: dict[str, str] = {}
    for relative_path, content in files.items():
        normalized_path = posixpath.normpath(str(relative_path).replace("\\", "/")).lstrip("/")
        if not normalized_path or normalized_path.startswith("..") or normalized_path == ".":
            raise ValueError(f"Invalid skill bundle path: {relative_path!r}")
        normalized[normalized_path] = str(content)
    return normalized


class SkillBackend(Protocol):
    """Minimal protocol for pluggable skill sources."""

    backend_name: str

    def capabilities(self) -> SkillBackendCapabilities:
        """Return backend capability flags."""

    def normalize_root(self, root: str | Path) -> str:
        """Normalize a source root into a backend-specific path token."""

    def describe_root(self, root: str) -> SkillBackendRootInfo:
        """Return source/root metadata for UI and orchestration layers."""

    def refresh_root(self, root: str) -> None:
        """Invalidate any cached state for a backend root/source."""

    def ensure_root(self, root: str) -> None:
        """Ensure a writable source root exists."""

    def exists(self, path: str) -> bool:
        """Return whether a backend path exists."""

    def join(self, root: str, *parts: str) -> str:
        """Safely resolve child paths within a root."""

    def list_skill_dirs(self, root: str) -> list[str]:
        """List immediate child directories that may contain skills."""

    def list_files(self, root: str) -> list[SkillFileEntry]:
        """List files under a skill root with relative paths."""

    def read_bundle(self, root: str) -> dict[str, str]:
        """Read all text files beneath a skill root."""

    def read_text(self, path: str) -> str:
        """Read a UTF-8 text file."""

    def write_bundle(self, root: str, files: dict[str, str], *, replace: bool = False) -> None:
        """Write a set of text files beneath a skill root."""

    def write_text(self, path: str, content: str) -> None:
        """Write a UTF-8 text file."""

    def remove_tree(self, path: str) -> bool:
        """Remove a skill root."""

    def local_path(self, path: str) -> Path | None:
        """Return a local filesystem path when the backend exposes one."""

    def get_refresh_report(self, root: str) -> dict[str, object] | None:
        """Return the most recent refresh/revalidation report for a source root."""

    def get_source_descriptor(self, root: str) -> dict[str, object] | None:
        """Return a normalized source/registry descriptor when the backend exposes one."""

    async def adescribe_root(self, root: str) -> SkillBackendRootInfo:
        """Async source/root metadata lookup."""

    async def arefresh_root(self, root: str) -> None:
        """Async cache invalidation for a backend root/source."""

    async def aensure_root(self, root: str) -> None:
        """Async writable root initialization."""

    async def aexists(self, path: str) -> bool:
        """Async existence check."""

    async def alist_skill_dirs(self, root: str) -> list[str]:
        """Async skill directory listing."""

    async def alist_files(self, root: str) -> list[SkillFileEntry]:
        """Async file listing."""

    async def aread_bundle(self, root: str) -> dict[str, str]:
        """Async skill bundle fetch."""

    async def aread_text(self, path: str) -> str:
        """Async text-file fetch."""

    async def awrite_bundle(self, root: str, files: dict[str, str], *, replace: bool = False) -> None:
        """Async bundle write."""

    async def awrite_text(self, path: str, content: str) -> None:
        """Async text-file write."""

    async def aremove_tree(self, path: str) -> bool:
        """Async tree removal."""

    async def aget_refresh_report(self, root: str) -> dict[str, object] | None:
        """Async source refresh/revalidation report lookup."""

    async def aget_source_descriptor(self, root: str) -> dict[str, object] | None:
        """Async source/registry descriptor lookup."""


class SkillBackendBase:
    """Convenience base class that provides async wrappers for sync backends."""

    def refresh_root(self, root: str) -> None:
        return None

    def get_refresh_report(self, root: str) -> dict[str, object] | None:
        return None

    def get_source_descriptor(self, root: str) -> dict[str, object] | None:
        return None

    async def adescribe_root(self, root: str) -> SkillBackendRootInfo:
        return await asyncio.to_thread(self.describe_root, root)

    async def arefresh_root(self, root: str) -> None:
        await asyncio.to_thread(self.refresh_root, root)

    async def aensure_root(self, root: str) -> None:
        await asyncio.to_thread(self.ensure_root, root)

    async def aexists(self, path: str) -> bool:
        return await asyncio.to_thread(self.exists, path)

    async def alist_skill_dirs(self, root: str) -> list[str]:
        return await asyncio.to_thread(self.list_skill_dirs, root)

    async def alist_files(self, root: str) -> list[SkillFileEntry]:
        return await asyncio.to_thread(self.list_files, root)

    async def aread_bundle(self, root: str) -> dict[str, str]:
        return await asyncio.to_thread(self.read_bundle, root)

    async def aread_text(self, path: str) -> str:
        return await asyncio.to_thread(self.read_text, path)

    async def awrite_bundle(self, root: str, files: dict[str, str], *, replace: bool = False) -> None:
        await asyncio.to_thread(self.write_bundle, root, files, replace=replace)

    async def awrite_text(self, path: str, content: str) -> None:
        await asyncio.to_thread(self.write_text, path, content)

    async def aremove_tree(self, path: str) -> bool:
        return await asyncio.to_thread(self.remove_tree, path)

    async def aget_refresh_report(self, root: str) -> dict[str, object] | None:
        return await asyncio.to_thread(self.get_refresh_report, root)

    async def aget_source_descriptor(self, root: str) -> dict[str, object] | None:
        return await asyncio.to_thread(self.get_source_descriptor, root)


@dataclass(frozen=True)
class FilesystemSkillBackend(SkillBackendBase):
    """Local filesystem-backed skill storage."""

    backend_name: str = "filesystem"

    def capabilities(self) -> SkillBackendCapabilities:
        return SkillBackendCapabilities(
            has_native_local_path=True,
            supports_python_tools=True,
            remote=False,
        )

    def normalize_root(self, root: str | Path) -> str:
        return str(Path(root).resolve())

    def describe_root(self, root: str) -> SkillBackendRootInfo:
        normalized = self.normalize_root(root)
        local_path = Path(normalized)
        return SkillBackendRootInfo(
            root=normalized,
            backend_name=self.backend_name,
            exists=local_path.exists(),
            display_path=normalized,
            local_path=str(local_path),
            remote=False,
        )

    def ensure_root(self, root: str) -> None:
        Path(root).mkdir(parents=True, exist_ok=True)

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def join(self, root: str, *parts: str) -> str:
        if not parts:
            return str(Path(root).resolve())
        return str(safe_resolve(root, Path(*parts)))

    def list_skill_dirs(self, root: str) -> list[str]:
        root_path = Path(root)
        if not root_path.exists():
            return []
        return sorted(entry.name for entry in root_path.iterdir() if entry.is_dir())

    def list_files(self, root: str) -> list[SkillFileEntry]:
        root_path = Path(root)
        if not root_path.is_dir():
            return []
        files: list[SkillFileEntry] = []
        for file_path in sorted(path for path in root_path.rglob("*") if path.is_file()):
            files.append(
                SkillFileEntry(
                    path=file_path.relative_to(root_path).as_posix(),
                    size=file_path.stat().st_size,
                )
            )
        return files

    def read_bundle(self, root: str) -> dict[str, str]:
        bundle: dict[str, str] = {}
        for entry in self.list_files(root):
            bundle[entry.path] = self.read_text(self.join(root, entry.path))
        return bundle

    def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    def write_bundle(self, root: str, files: dict[str, str], *, replace: bool = False) -> None:
        normalized = validate_skill_bundle(files)
        root_path = Path(root)
        if replace and root_path.exists():
            shutil.rmtree(root_path)
        root_path.mkdir(parents=True, exist_ok=True)
        for relative_path, content in normalized.items():
            self.write_text(self.join(root, relative_path), content)

    def write_text(self, path: str, content: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(target)

    def remove_tree(self, path: str) -> bool:
        target = Path(path)
        if not target.exists():
            return False
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True

    def local_path(self, path: str) -> Path | None:
        return Path(path)


@dataclass
class InMemorySkillBackend(SkillBackendBase):
    """Simple in-memory backend for backend-agnostic skill sources and tests."""

    files: dict[str, str] = field(default_factory=dict)
    backend_name: str = "memory"

    def __post_init__(self) -> None:
        normalized: dict[str, str] = {}
        for path, content in self.files.items():
            normalized[self._normalize_path(path)] = content
        self.files = normalized

    def capabilities(self) -> SkillBackendCapabilities:
        return SkillBackendCapabilities(
            has_native_local_path=False,
            supports_python_tools=True,
            remote=True,
        )

    def normalize_root(self, root: str | Path) -> str:
        normalized = self._normalize_path(root)
        return "/" if normalized == "." else normalized

    def describe_root(self, root: str) -> SkillBackendRootInfo:
        normalized = self.normalize_root(root)
        return SkillBackendRootInfo(
            root=normalized,
            backend_name=self.backend_name,
            exists=self.exists(normalized),
            display_path=normalized,
            local_path="",
            remote=True,
        )

    def ensure_root(self, root: str) -> None:
        self.normalize_root(root)

    def exists(self, path: str) -> bool:
        normalized = self.normalize_root(path)
        if normalized in self.files:
            return True
        prefix = f"{normalized.rstrip('/')}/"
        return any(candidate.startswith(prefix) for candidate in self.files)

    def join(self, root: str, *parts: str) -> str:
        base = self.normalize_root(root)
        if not parts:
            return base
        candidate = self._normalize_path(posixpath.join(base, *parts))
        prefix = f"{base.rstrip('/')}/"
        if candidate != base and not candidate.startswith(prefix):
            raise PermissionError(f"Path traversal blocked: {parts!r}")
        return candidate

    def list_skill_dirs(self, root: str) -> list[str]:
        base = self.normalize_root(root).rstrip("/")
        if not base:
            base = "/"
        prefix = "/" if base == "/" else f"{base}/"
        seen: set[str] = set()
        for path in self.files:
            if not path.startswith(prefix):
                continue
            remainder = path[len(prefix) :]
            first = remainder.split("/", 1)[0]
            if first:
                seen.add(first)
        return sorted(seen)

    def list_files(self, root: str) -> list[SkillFileEntry]:
        base = self.normalize_root(root).rstrip("/")
        if not base:
            base = "/"
        prefix = "/" if base == "/" else f"{base}/"
        entries: list[SkillFileEntry] = []
        for path, content in sorted(self.files.items()):
            if path.startswith(prefix):
                relative = path[len(prefix) :]
                if relative:
                    entries.append(SkillFileEntry(path=relative, size=len(content.encode("utf-8"))))
        return entries

    def read_bundle(self, root: str) -> dict[str, str]:
        bundle: dict[str, str] = {}
        for entry in self.list_files(root):
            bundle[entry.path] = self.read_text(self.join(root, entry.path))
        return bundle

    def read_text(self, path: str) -> str:
        normalized = self.normalize_root(path)
        return self.files[normalized]

    def write_bundle(self, root: str, files: dict[str, str], *, replace: bool = False) -> None:
        root_path = self.normalize_root(root)
        normalized = validate_skill_bundle(files)
        if replace:
            self.remove_tree(root_path)
        self.ensure_root(root_path)
        for relative_path, content in normalized.items():
            self.write_text(self.join(root_path, relative_path), content)

    def write_text(self, path: str, content: str) -> None:
        self.files[self.normalize_root(path)] = content

    def remove_tree(self, path: str) -> bool:
        normalized = self.normalize_root(path).rstrip("/")
        targets = [
            candidate for candidate in self.files if candidate == normalized or candidate.startswith(f"{normalized}/")
        ]
        if not targets:
            return False
        for candidate in targets:
            self.files.pop(candidate, None)
        return True

    def local_path(self, path: str) -> Path | None:
        return None

    @staticmethod
    def _normalize_path(path: str | Path) -> str:
        normalized = posixpath.normpath(str(path).replace("\\", "/"))
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized


@dataclass
class CompositeSkillBackend(SkillBackendBase):
    """Routes skill operations to child backends by path prefix (longest-prefix-wins).

    Inspired by deepagents' CompositeBackend pattern. Allows a single skill source
    to aggregate multiple backends under different path prefixes.
    """

    default: SkillBackend = field(default_factory=lambda: InMemorySkillBackend())
    routes: dict[str, SkillBackend] = field(default_factory=dict)
    backend_name: str = "composite"
    _sorted_routes: list[tuple[str, SkillBackend]] = field(init=False, repr=False, default_factory=list)

    def __post_init__(self) -> None:
        self._sorted_routes = sorted(self.routes.items(), key=lambda r: len(r[0]), reverse=True)

    def _route(self, path: str) -> tuple[SkillBackend, str, str | None]:
        """Find the best matching backend, strip the prefix, return (backend, stripped_path, route_key)."""
        normalized = str(path).rstrip("/")
        for prefix, backend in self._sorted_routes:
            prefix_clean = prefix.rstrip("/")
            if normalized == prefix_clean:
                return backend, "/", prefix
            if normalized.startswith(prefix_clean + "/"):
                return backend, normalized[len(prefix_clean) :], prefix
        return self.default, path, None

    def capabilities(self) -> SkillBackendCapabilities:
        caps = [self.default.capabilities()]
        for _, backend in self._sorted_routes:
            caps.append(backend.capabilities())
        return SkillBackendCapabilities(
            can_read_bundle=any(c.can_read_bundle for c in caps),
            can_write_bundle=any(c.can_write_bundle for c in caps),
            can_write_files=any(c.can_write_files for c in caps),
            can_remove_tree=any(c.can_remove_tree for c in caps),
            supports_python_tools=any(c.supports_python_tools for c in caps),
            has_native_local_path=any(c.has_native_local_path for c in caps),
            remote=any(c.remote for c in caps),
            supports_catalog_pagination=any(c.supports_catalog_pagination for c in caps),
            supports_request_backpressure=any(c.supports_request_backpressure for c in caps),
            supports_conditional_requests=any(c.supports_conditional_requests for c in caps),
            supports_authenticated_access=any(c.supports_authenticated_access for c in caps),
            supports_registry_descriptor=any(c.supports_registry_descriptor for c in caps),
            supports_auth_negotiation=any(c.supports_auth_negotiation for c in caps),
            supports_session_negotiation=any(c.supports_session_negotiation for c in caps),
        )

    def normalize_root(self, root: str | Path) -> str:
        backend, stripped, _ = self._route(str(root))
        return backend.normalize_root(stripped)

    def describe_root(self, root: str) -> SkillBackendRootInfo:
        backend, stripped, route_key = self._route(root)
        info = backend.describe_root(stripped)
        return SkillBackendRootInfo(
            root=root,
            backend_name=f"{self.backend_name}/{info.backend_name}",
            exists=info.exists,
            display_path=info.display_path,
            local_path=info.local_path,
            remote=info.remote,
            metadata={**info.metadata, "composite_route": route_key or "default"},
        )

    def refresh_root(self, root: str) -> None:
        backend, stripped, _ = self._route(root)
        backend.refresh_root(stripped)

    def ensure_root(self, root: str) -> None:
        backend, stripped, _ = self._route(root)
        backend.ensure_root(stripped)

    def exists(self, path: str) -> bool:
        backend, stripped, _ = self._route(path)
        return backend.exists(stripped)

    def join(self, root: str, *parts: str) -> str:
        backend, stripped, route_key = self._route(root)
        inner = backend.join(stripped, *parts)
        if route_key is not None:
            return f"{route_key.rstrip('/')}/{inner.lstrip('/')}"
        return inner

    def list_skill_dirs(self, root: str) -> list[str]:
        dirs: set[str] = set()
        try:
            dirs.update(self.default.list_skill_dirs(root))
        except Exception:
            pass
        for prefix, backend in self._sorted_routes:
            try:
                dirs.update(backend.list_skill_dirs(prefix))
            except Exception:
                pass
        return sorted(dirs)

    def list_files(self, root: str) -> list[SkillFileEntry]:
        backend, stripped, _ = self._route(root)
        return backend.list_files(stripped)

    def read_bundle(self, root: str) -> dict[str, str]:
        backend, stripped, _ = self._route(root)
        return backend.read_bundle(stripped)

    def read_text(self, path: str) -> str:
        backend, stripped, _ = self._route(path)
        return backend.read_text(stripped)

    def write_bundle(self, root: str, files: dict[str, str], *, replace: bool = False) -> None:
        backend, stripped, _ = self._route(root)
        backend.write_bundle(stripped, files, replace=replace)

    def write_text(self, path: str, content: str) -> None:
        backend, stripped, _ = self._route(path)
        backend.write_text(stripped, content)

    def remove_tree(self, path: str) -> bool:
        backend, stripped, _ = self._route(path)
        return backend.remove_tree(stripped)

    def local_path(self, path: str) -> Path | None:
        backend, stripped, _ = self._route(path)
        return backend.local_path(stripped)

    def get_refresh_report(self, root: str) -> dict[str, object] | None:
        backend, stripped, _ = self._route(root)
        return backend.get_refresh_report(stripped)

    def get_source_descriptor(self, root: str) -> dict[str, object] | None:
        backend, stripped, _ = self._route(root)
        return backend.get_source_descriptor(stripped)
