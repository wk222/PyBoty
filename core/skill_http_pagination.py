"""Descriptor loading, pagination, and catalog URL helpers for HttpSkillBackend."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


class HttpSkillPaginationMixin:
    """Descriptor, pagination, path splitting, and refresh report methods.

    Expects the host class to provide:
    - self.enable_descriptor_lookup, self.registry_descriptor_path, self.catalog_path
    - self._descriptor_cache, self._descriptor_etag_cache, self._catalog_cache
    - self._fetch_json(url, *, if_none_match, root, allow_missing)
    """

    def _load_descriptor(
        self: Any,
        root: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[dict[str, object] | None, dict[str, object]]:
        if not self.enable_descriptor_lookup or not self.registry_descriptor_path.strip():
            return None, {"request_count": 0, "backpressure_events": 0, "retry_after_seconds": 0.0}

        if root in self._descriptor_cache and not force_refresh:
            return self._descriptor_cache[root], {
                "request_count": 0,
                "backpressure_events": 0,
                "retry_after_seconds": 0.0,
            }
        cached = self._descriptor_cache.get(root)

        descriptor_url = f"{root}/{self.registry_descriptor_path.lstrip('/')}"
        old_etag = self._descriptor_etag_cache.get(root, "")
        payload, etag, status_code, _, fetch_meta = self._fetch_json(
            descriptor_url,
            if_none_match=old_etag if force_refresh and cached is not None else "",
            root=root,
            allow_missing=True,
        )
        if status_code == 304 and cached is not None:
            return cached, fetch_meta
        if status_code == 404 or payload is None:
            self._descriptor_cache[root] = None
            return None, fetch_meta
        descriptor = dict(payload)
        descriptor.setdefault("catalog_path", self.catalog_path)
        descriptor["descriptor_path"] = self.registry_descriptor_path
        descriptor["descriptor_url"] = descriptor_url
        descriptor["descriptor_etag"] = etag
        self._descriptor_cache[root] = descriptor
        if etag:
            self._descriptor_etag_cache[root] = etag
        return descriptor, fetch_meta

    def _catalog_path_for_descriptor(self: Any, descriptor: dict[str, object] | None) -> str:
        if descriptor is None:
            return self.catalog_path
        configured = str(descriptor.get("catalog_path", self.catalog_path)).strip()
        return configured or self.catalog_path

    def _default_bundle_url(self: Any, root: str, skill_name: str) -> str:
        descriptor = self._descriptor_cache.get(root)
        if isinstance(descriptor, dict):
            template = str(descriptor.get("bundle_path_template", "")).strip()
            if template:
                return urljoin(
                    f"{root.rstrip('/')}/",
                    template.format(root=root.rstrip("/"), skill=skill_name, skill_name=skill_name),
                )
        return f"{root}/{skill_name}/bundle.json"

    @staticmethod
    def _descriptor_registry_metadata(descriptor: dict[str, object]) -> dict[str, object]:
        registry_meta = descriptor.get("registry")
        if isinstance(registry_meta, dict):
            return dict(registry_meta)
        return {}

    @staticmethod
    def _pagination_config(descriptor: dict[str, object] | None) -> dict[str, object]:
        if not isinstance(descriptor, dict):
            return {}
        pagination = descriptor.get("pagination")
        return dict(pagination) if isinstance(pagination, dict) else {}

    def _catalog_page_url(
        self: Any,
        root: str,
        descriptor: dict[str, object] | None,
        *,
        cursor: str = "",
    ) -> str:
        catalog_path = self._catalog_path_for_descriptor(descriptor)
        base_url = f"{root}/{catalog_path.lstrip('/')}"
        params: dict[str, str] = {}
        if isinstance(descriptor, dict):
            raw_catalog_query = descriptor.get("catalog_query")
            if isinstance(raw_catalog_query, dict):
                for key, value in raw_catalog_query.items():
                    if value is None:
                        continue
                    params[str(key)] = str(value)
        pagination = self._pagination_config(descriptor)
        page_size_param = str(pagination.get("page_size_param", "")).strip()
        page_size = pagination.get("page_size")
        if page_size_param and page_size not in {None, ""}:
            params[page_size_param] = str(page_size)
        cursor_param = str(pagination.get("cursor_param", "cursor")).strip()
        if cursor and cursor_param:
            params[cursor_param] = cursor
        return self._merge_query_params(base_url, params)

    def _next_catalog_page_url(
        self: Any,
        *,
        root: str,
        descriptor: dict[str, object] | None,
        current_url: str,
        payload: dict[str, object],
    ) -> str:
        pagination = self._pagination_config(descriptor)
        next_field = str(pagination.get("next_field", "next")).strip() or "next"
        next_value = str(payload.get(next_field, "")).strip()
        if next_value:
            return urljoin(current_url, next_value)
        mode = str(pagination.get("mode", "")).strip().lower()
        if mode != "cursor":
            return ""
        cursor_field = str(pagination.get("cursor_field", "next_cursor")).strip() or "next_cursor"
        cursor_value = str(payload.get(cursor_field, "")).strip()
        if not cursor_value:
            return ""
        return self._catalog_page_url(root, descriptor, cursor=cursor_value)

    @staticmethod
    def _pagination_metadata(pagination: dict[str, object]) -> dict[str, object]:
        if not pagination:
            return {}
        return {
            "pagination_mode": str(pagination.get("mode", "next")).strip() or "next",
            "pagination_next_field": str(pagination.get("next_field", "next")).strip() or "next",
            "pagination_cursor_field": str(pagination.get("cursor_field", "next_cursor")).strip() or "next_cursor",
            "pagination_cursor_param": str(pagination.get("cursor_param", "cursor")).strip() or "cursor",
            "pagination_page_size_param": str(pagination.get("page_size_param", "")).strip(),
            "pagination_page_size": int(pagination.get("page_size", 0) or 0),
        }

    @staticmethod
    def _merge_query_params(url: str, params: dict[str, str]) -> str:
        if not params:
            return url
        split = urlsplit(url)
        existing = dict(parse_qsl(split.query, keep_blank_values=True))
        existing.update(params)
        query = urlencode(existing)
        return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))

    def _split_path(self: Any, path: str) -> tuple[str, str | None, str]:
        normalized = str(path).rstrip("/")
        roots = sorted(self._catalog_cache, key=len, reverse=True)
        for root in roots:
            if normalized == root:
                return root, None, ""
            if normalized.startswith(root + "/"):
                remainder = normalized[len(root) + 1 :]
                skill_name, _, relative_path = remainder.partition("/")
                return root, skill_name or None, relative_path
        root = self.normalize_root(path)
        return root, None, ""

    @staticmethod
    def _build_refresh_report(metadata: dict[str, object]) -> dict[str, object]:
        return {
            "status": metadata.get("refresh_status", "loaded"),
            "changed": bool(metadata.get("catalog_changed", True)),
            "etag": str(metadata.get("etag", "")).strip(),
            "checked_at": float(metadata.get("last_checked_at", 0.0) or 0.0),
            "request_count": int(metadata.get("request_count", 0) or 0),
            "page_count": int(metadata.get("page_count", 0) or 0),
            "skill_count": int(metadata.get("skill_count", 0) or 0),
            "auth_configured": bool(metadata.get("auth_configured", False)),
            "auth_mode": str(metadata.get("auth_mode", "none")).strip(),
            "auth_negotiated": bool(metadata.get("auth_negotiated", False)),
            "session_active": bool(metadata.get("session_active", False)),
            "conditional_request": bool(metadata.get("conditional_request", False)),
            "descriptor_loaded": bool(metadata.get("descriptor_loaded", False)),
            "backpressure_events": int(metadata.get("backpressure_events", 0) or 0),
            "retry_after_seconds": float(metadata.get("retry_after_seconds", 0.0) or 0.0),
            "stale_cache": False,
            "registry_version": str(metadata.get("registry_version", "")).strip(),
        }
