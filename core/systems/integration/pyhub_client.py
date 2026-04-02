"""PyHub registry client — wraps PyHub REST API for PyBot integration."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx


class PyHubClient:
    """HTTP client for interacting with a PyHub marketplace server."""

    def __init__(self, registry_url: str = "http://localhost:8000", api_key: str | None = None):
        self.registry_url = registry_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _url(self, path: str) -> str:
        return f"{self.registry_url}/api/v1{path}"

    def search(self, query: str, pkg_type: str | None = None, page: int = 1) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"q": query, "page": page}
        if pkg_type:
            params["type"] = pkg_type
        resp = httpx.get(self._url("/search"), params=params, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json().get("items", [])

    def get_package(self, slug: str) -> dict[str, Any]:
        resp = httpx.get(self._url(f"/packages/{slug}"), headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def list_packages(
        self,
        pkg_type: str | None = None,
        sort: str = "updated",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"sort": sort, "page": page, "page_size": page_size}
        if pkg_type:
            params["type"] = pkg_type
        resp = httpx.get(self._url("/packages"), params=params, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def install(self, slug: str, version: str = "latest", target_dir: str = ".") -> Path:
        params = {}
        if version and version != "latest":
            params["version"] = version

        resp = httpx.get(
            self._url(f"/packages/{slug}/download"),
            params=params,
            headers=self._headers(),
            timeout=120,
            follow_redirects=True,
        )
        resp.raise_for_status()

        dest = Path(target_dir).resolve() / slug
        dest.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            zf.extractall(dest)

        return dest

    def publish(self, path: str, pkg_type: str, version: str = "0.1.0", changelog: str = "") -> dict[str, Any]:
        directory = Path(path).resolve()
        if not directory.is_dir():
            raise ValueError(f"'{path}' is not a directory")

        slug = directory.name

        try:
            resp = httpx.post(
                self._url("/packages"),
                json={"slug": slug, "type": pkg_type, "display_name": slug, "summary": ""},
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 409:
                raise

        files = self._collect_files(directory)
        upload_files = [("files", (str(fp.relative_to(directory)), fp.read_bytes())) for fp in files]

        resp = httpx.post(
            self._url(f"/packages/{slug}/versions"),
            data={"version": version, "changelog": changelog},
            files=upload_files,
            headers=self._headers(),
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def star(self, slug: str) -> dict[str, Any]:
        resp = httpx.post(self._url(f"/stars/{slug}"), headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def unstar(self, slug: str) -> dict[str, Any]:
        resp = httpx.delete(self._url(f"/stars/{slug}"), headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _collect_files(directory: Path, max_files: int = 200) -> list[Path]:
        skip = {".git", "__pycache__", "node_modules", ".venv", ".ruff_cache"}
        files = []
        for p in sorted(directory.rglob("*")):
            if p.is_file() and not any(part in skip for part in p.parts):
                files.append(p)
                if len(files) >= max_files:
                    break
        return files
