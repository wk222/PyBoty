"""HTTP-backed read-only skill backend with auth/session negotiation.

Split into mixins for maintainability:
- ``skill_http_auth.py``       — auth models + auth/session negotiation
- ``skill_http_transport.py``  — HTTP fetch/post, throttle, retry, async
- ``skill_http_pagination.py`` — descriptor, pagination, path splitting
"""

from __future__ import annotations

import asyncio
import posixpath
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .skill_backends import (
    SkillBackendBase,
    SkillBackendCapabilities,
    SkillBackendRootInfo,
    SkillFileEntry,
    validate_skill_bundle,
)
from .skill_http_auth import HttpSkillAuthMixin, NegotiatedToken, RegistrySession
from .skill_http_pagination import HttpSkillPaginationMixin
from .skill_http_transport import HttpSkillTransportMixin


@dataclass
class HttpSkillBackend(
    HttpSkillAuthMixin,
    HttpSkillTransportMixin,
    HttpSkillPaginationMixin,
    SkillBackendBase,
):
    """HTTP-backed read-only skill backend using catalog + bundle fetch semantics."""

    headers: dict[str, str] = field(default_factory=dict)
    bearer_token: str = ""
    token_env_var: str = ""
    basic_auth_username: str = ""
    basic_auth_password: str = ""
    basic_auth_password_env: str = ""
    auth_header_name: str = "Authorization"
    client_id: str = ""
    client_secret: str = ""
    client_secret_env: str = ""
    timeout: float = 10.0
    catalog_path: str = "index.json"
    registry_descriptor_path: str = ".well-known/skill-registry.json"
    enable_descriptor_lookup: bool = True
    page_limit: int = 8
    max_concurrency: int = 4
    min_request_interval: float = 0.0
    retry_attempts: int = 2
    retry_backoff_seconds: float = 0.25
    backend_name: str = "http"
    _catalog_cache: dict[str, dict[str, dict[str, object]]] = field(default_factory=dict, init=False, repr=False)
    _bundle_cache: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict, init=False, repr=False)
    _catalog_metadata_cache: dict[str, dict[str, object]] = field(default_factory=dict, init=False, repr=False)
    _catalog_etag_cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _refresh_report_cache: dict[str, dict[str, object]] = field(default_factory=dict, init=False, repr=False)
    _descriptor_cache: dict[str, dict[str, object] | None] = field(default_factory=dict, init=False, repr=False)
    _descriptor_etag_cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _negotiated_token_cache: dict[str, NegotiatedToken] = field(default_factory=dict, init=False, repr=False)
    _session_cache: dict[str, RegistrySession] = field(default_factory=dict, init=False, repr=False)
    _throttle_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _last_request_at: float = field(default=0.0, init=False, repr=False)
    _async_semaphore: asyncio.Semaphore | None = field(default=None, init=False, repr=False)

    # ── Capabilities / root management ─────────────────────────────

    def capabilities(self) -> SkillBackendCapabilities:
        return SkillBackendCapabilities(
            can_write_bundle=False,
            can_write_files=False,
            can_remove_tree=False,
            supports_python_tools=True,
            has_native_local_path=False,
            remote=True,
            supports_catalog_pagination=True,
            supports_request_backpressure=True,
            supports_conditional_requests=True,
            supports_authenticated_access=True,
            supports_registry_descriptor=True,
            supports_auth_negotiation=True,
            supports_session_negotiation=True,
        )

    def normalize_root(self, root: str | Path) -> str:
        raw = str(root).strip().rstrip("/")
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid HTTP skill root: {root!r}")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

    def describe_root(self, root: str) -> SkillBackendRootInfo:
        normalized = self.normalize_root(root)
        exists = True
        metadata: dict[str, object] = {}
        try:
            self._load_catalog(normalized)
            metadata = dict(self._catalog_metadata_cache.get(normalized, {}))
        except Exception:
            exists = False
        descriptor = self._descriptor_cache.get(normalized)
        if descriptor is not None:
            metadata["descriptor_loaded"] = True
            metadata["descriptor_path"] = str(descriptor.get("descriptor_path", "")).strip()
            metadata["descriptor_etag"] = str(descriptor.get("descriptor_etag", "")).strip()
        refresh_report = self._refresh_report_cache.get(normalized)
        if refresh_report is not None:
            metadata["refresh_status"] = refresh_report.get("status", "")
            metadata["last_checked_at"] = refresh_report.get("checked_at", 0.0)
            metadata["stale_cache"] = refresh_report.get("stale_cache", False)
        return SkillBackendRootInfo(
            root=normalized,
            backend_name=self.backend_name,
            exists=exists,
            display_path=normalized,
            local_path="",
            remote=True,
            metadata=metadata,
        )

    def refresh_root(self, root: str) -> None:
        normalized = self.normalize_root(root)
        checked_at = time.time()
        had_cache = normalized in self._catalog_cache
        try:
            self._load_catalog(normalized, force_refresh=True)
            report = dict(self._refresh_report_cache.get(normalized, {}))
            report.setdefault("checked_at", checked_at)
            report.setdefault("stale_cache", False)
            self._refresh_report_cache[normalized] = report
        except Exception as exc:
            self._refresh_report_cache[normalized] = {
                "status": "error",
                "changed": False,
                "checked_at": checked_at,
                "stale_cache": had_cache,
                "error": str(exc),
                "etag": self._catalog_etag_cache.get(normalized, ""),
                "auth_configured": self._auth_configured(),
            }
            if not had_cache:
                self._catalog_cache.pop(normalized, None)
                self._catalog_metadata_cache.pop(normalized, None)

    def get_refresh_report(self, root: str) -> dict[str, object] | None:
        normalized = self.normalize_root(root)
        report = self._refresh_report_cache.get(normalized)
        return dict(report) if report is not None else None

    def get_source_descriptor(self, root: str) -> dict[str, object] | None:
        normalized = self.normalize_root(root)
        descriptor = self._descriptor_cache.get(normalized)
        return dict(descriptor) if descriptor is not None else None

    # ── Async wrappers ─────────────────────────────────────────────

    async def adescribe_root(self, root: str) -> SkillBackendRootInfo:
        return await self._run_remote_call(self.describe_root, root)

    async def aexists(self, path: str) -> bool:
        return await self._run_remote_call(self.exists, path)

    async def alist_skill_dirs(self, root: str) -> list[str]:
        return await self._run_remote_call(self.list_skill_dirs, root)

    async def alist_files(self, root: str) -> list[SkillFileEntry]:
        return await self._run_remote_call(self.list_files, root)

    async def aread_bundle(self, root: str) -> dict[str, str]:
        return await self._run_remote_call(self.read_bundle, root)

    async def aread_text(self, path: str) -> str:
        return await self._run_remote_call(self.read_text, path)

    # ── Read-only operations ───────────────────────────────────────

    def ensure_root(self, root: str) -> None:
        raise PermissionError("HTTP skill sources are read-only")

    def exists(self, path: str) -> bool:
        try:
            root, skill_name, relative_path = self._split_path(path)
            if skill_name is None:
                self._load_catalog(root)
                return True
            bundle = self._load_bundle(root, skill_name)
            if not relative_path:
                return True
            return relative_path in bundle
        except Exception:
            return False

    def join(self, root: str, *parts: str) -> str:
        base = self.normalize_root(root)
        if not parts:
            return base
        normalized_parts = posixpath.normpath(posixpath.join("/", *[str(part).replace("\\", "/") for part in parts]))
        if normalized_parts in {"/", ""}:
            return base
        if normalized_parts.startswith("/../") or normalized_parts == "/..":
            raise PermissionError(f"Path traversal blocked: {parts!r}")
        return f"{base.rstrip('/')}/{normalized_parts.lstrip('/')}"

    def list_skill_dirs(self, root: str) -> list[str]:
        return sorted(self._load_catalog(self.normalize_root(root)))

    def list_files(self, root: str) -> list[SkillFileEntry]:
        source_root, skill_name, relative_path = self._split_path(root)
        if skill_name is None or relative_path:
            raise FileNotFoundError(root)
        bundle = self._load_bundle(source_root, skill_name)
        return [
            SkillFileEntry(path=path, size=len(content.encode("utf-8"))) for path, content in sorted(bundle.items())
        ]

    def read_bundle(self, root: str) -> dict[str, str]:
        source_root, skill_name, relative_path = self._split_path(root)
        if skill_name is None or relative_path:
            raise FileNotFoundError(root)
        return dict(self._load_bundle(source_root, skill_name))

    def read_text(self, path: str) -> str:
        source_root, skill_name, relative_path = self._split_path(path)
        if skill_name is None or not relative_path:
            raise FileNotFoundError(path)
        return self._load_bundle(source_root, skill_name)[relative_path]

    def write_bundle(self, root: str, files: dict[str, str], *, replace: bool = False) -> None:
        raise PermissionError("HTTP skill sources are read-only")

    def write_text(self, path: str, content: str) -> None:
        raise PermissionError("HTTP skill sources are read-only")

    def remove_tree(self, path: str) -> bool:
        raise PermissionError("HTTP skill sources are read-only")

    def local_path(self, path: str) -> Path | None:
        return None

    # ── Catalog loading ────────────────────────────────────────────

    def _load_catalog(self, root: str, *, force_refresh: bool = False) -> dict[str, dict[str, object]]:
        cached = self._catalog_cache.get(root)
        if cached is not None and not force_refresh:
            return cached

        descriptor, descriptor_fetch = self._load_descriptor(root, force_refresh=force_refresh)
        self._negotiate_auth(root, descriptor)
        self._negotiate_session(root, descriptor)
        catalog_path = self._catalog_path_for_descriptor(descriptor)
        pagination = self._pagination_config(descriptor)
        old_catalog = dict(cached) if cached is not None else None
        old_etag = self._catalog_etag_cache.get(root, "")
        catalog: dict[str, dict[str, object]] = {}
        negotiated = self._negotiated_token_cache.get(root)
        session = self._session_cache.get(root)
        metadata: dict[str, object] = {
            "catalog_path": catalog_path,
            "auth_configured": self._auth_configured(),
            "auth_mode": self._auth_mode(),
            "auth_negotiated": negotiated is not None and not negotiated.expired,
            "session_active": session is not None and not session.expired,
            "descriptor_loaded": descriptor is not None,
            "descriptor_path": self.registry_descriptor_path,
            "descriptor_request_count": int(descriptor_fetch.get("request_count", 0) or 0),
        }
        if session is not None and not session.expired:
            metadata["session_cursor_mode"] = session.cursor_mode
            metadata["session_header"] = session.session_header
        if descriptor is not None:
            metadata["descriptor_etag"] = str(descriptor.get("descriptor_etag", "")).strip()
            metadata["descriptor_url"] = str(descriptor.get("descriptor_url", "")).strip()
            metadata.update(self._descriptor_registry_metadata(descriptor))
        metadata.update(self._pagination_metadata(pagination))
        next_url = self._catalog_page_url(root, descriptor, cursor="")
        page_count = 0
        request_count = int(descriptor_fetch.get("request_count", 0) or 0)
        backpressure_events = int(descriptor_fetch.get("backpressure_events", 0) or 0)
        retry_after_seconds = float(descriptor_fetch.get("retry_after_seconds", 0.0) or 0.0)
        first_page: tuple[dict[str, object] | None, str, int, dict[str, str], dict[str, object]] | None = None

        if force_refresh:
            first_page = self._fetch_json(
                next_url,
                if_none_match=old_etag if cached is not None else "",
                root=root,
            )
            request_count += int(first_page[4].get("request_count", 0) or 0)
            backpressure_events += int(first_page[4].get("backpressure_events", 0) or 0)
            retry_after_seconds = max(
                retry_after_seconds,
                float(first_page[4].get("retry_after_seconds", 0.0) or 0.0),
            )
            first_payload, first_etag, first_status, first_headers, _ = first_page
            if first_status == 304 and cached is not None:
                refreshed = dict(self._catalog_metadata_cache.get(root, {}))
                refreshed.update(
                    {
                        "catalog_path": catalog_path,
                        "auth_configured": self._auth_configured(),
                        "auth_mode": self._auth_mode(),
                        "etag": first_etag or old_etag,
                        "refresh_status": "not_modified",
                        "catalog_changed": False,
                        "request_count": request_count,
                        "last_checked_at": time.time(),
                        "conditional_request": True,
                        "backpressure_events": backpressure_events,
                        "retry_after_seconds": retry_after_seconds,
                        "descriptor_loaded": descriptor is not None,
                        "descriptor_path": self.registry_descriptor_path,
                    }
                )
                registry_version = first_headers.get("x-registry-version", "").strip()
                if registry_version:
                    refreshed["registry_version"] = registry_version
                self._catalog_metadata_cache[root] = refreshed
                self._catalog_etag_cache[root] = first_etag or old_etag
                self._refresh_report_cache[root] = self._build_refresh_report(refreshed)
                return cached
            if first_headers.get("x-registry-version", "").strip():
                metadata["registry_version"] = first_headers["x-registry-version"].strip()
            if first_etag:
                metadata["etag"] = first_etag
                self._catalog_etag_cache[root] = first_etag

        while next_url and page_count < self.page_limit:
            self._keepalive_session(root)
            if page_count == 0 and first_page is not None:
                payload, page_etag, status_code, page_headers, fetch_meta = first_page
            else:
                payload, page_etag, status_code, page_headers, fetch_meta = self._fetch_json(next_url, root=root)
                request_count += int(fetch_meta.get("request_count", 0) or 0)
                backpressure_events += int(fetch_meta.get("backpressure_events", 0) or 0)
                retry_after_seconds = max(
                    retry_after_seconds,
                    float(fetch_meta.get("retry_after_seconds", 0.0) or 0.0),
                )
            if status_code == 304 or payload is None:
                break
            page_count += 1
            if page_count == 1:
                registry_version = page_headers.get("x-registry-version", "").strip()
                if registry_version:
                    metadata["registry_version"] = registry_version
                if page_etag:
                    metadata["etag"] = page_etag
                    self._catalog_etag_cache[root] = page_etag
            raw_skills = payload.get("skills", payload)

            if isinstance(raw_skills, list):
                for item in raw_skills:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "")).strip()
                    if name:
                        catalog[name] = dict(item)
            elif isinstance(raw_skills, dict):
                for name, item in raw_skills.items():
                    if isinstance(item, dict):
                        record = dict(item)
                    else:
                        record = {"bundle_url": item}
                    record.setdefault("name", name)
                    catalog[str(name)] = record

            registry_meta = payload.get("registry")
            if isinstance(registry_meta, dict):
                metadata.update(registry_meta)

            next_url = self._next_catalog_page_url(
                root=root,
                descriptor=descriptor,
                current_url=next_url,
                payload=payload,
            )

        metadata["page_count"] = page_count
        metadata["skill_count"] = len(catalog)
        metadata["paginated"] = page_count > 1
        metadata["request_count"] = request_count or page_count
        metadata["backpressure_events"] = backpressure_events
        metadata["retry_after_seconds"] = retry_after_seconds
        metadata["last_checked_at"] = time.time()
        metadata["refresh_status"] = "updated" if force_refresh else "loaded"
        metadata["catalog_changed"] = old_catalog != catalog if old_catalog is not None else True
        metadata["conditional_request"] = bool(force_refresh and old_etag)

        if force_refresh and metadata["refresh_status"] != "not_modified":
            stale_keys = [key for key in self._bundle_cache if key[0] == root]
            for key in stale_keys:
                self._bundle_cache.pop(key, None)
        self._catalog_cache[root] = catalog
        self._catalog_metadata_cache[root] = metadata
        self._refresh_report_cache[root] = self._build_refresh_report(metadata)
        return catalog

    def _load_bundle(self, root: str, skill_name: str) -> dict[str, str]:
        cache_key = (root, skill_name)
        cached = self._bundle_cache.get(cache_key)
        if cached is not None:
            return cached

        catalog = self._load_catalog(root)
        record = catalog.get(skill_name)
        if record is None:
            raise FileNotFoundError(skill_name)

        if isinstance(record.get("files"), dict):
            bundle = validate_skill_bundle(record["files"])
        else:
            bundle_url = str(record.get("bundle_url") or self._default_bundle_url(root, skill_name))
            payload, _, status_code, _, _ = self._fetch_json(bundle_url, root=root)
            if status_code == 304 or payload is None:
                raise ValueError(f"Invalid remote skill bundle payload for {skill_name!r}")
            if isinstance(payload, dict) and isinstance(payload.get("files"), dict):
                bundle = validate_skill_bundle(payload["files"])
            elif isinstance(payload, dict):
                bundle = validate_skill_bundle(
                    {path: content for path, content in payload.items() if isinstance(content, str)}
                )
            else:
                raise ValueError(f"Invalid remote skill bundle payload for {skill_name!r}")

        self._bundle_cache[cache_key] = bundle
        return bundle
