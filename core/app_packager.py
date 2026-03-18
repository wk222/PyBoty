"""APP bundle packaging, publishing, and installation for PyHub integration."""

from __future__ import annotations

import json
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any


_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", ".ruff_cache"}
_MAX_BUNDLE_FILES = 300
_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB per file


class AppPackager:
    """Handles export, import, and PyHub sync for PyBoty apps."""

    def __init__(self, apps_dir: str | Path):
        self.apps_dir = Path(apps_dir).resolve()

    def _app_dir(self, name: str) -> Path:
        return self.apps_dir / name

    def _collect_files(self, app_dir: Path) -> list[Path]:
        files: list[Path] = []
        for p in sorted(app_dir.rglob("*")):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if p.stat().st_size > _MAX_FILE_SIZE:
                continue
            files.append(p)
            if len(files) >= _MAX_BUNDLE_FILES:
                break
        return files

    def export_bundle(self, app_name: str) -> dict[str, Any]:
        """Export an app as a bundle dict: {manifest, files: {relpath: content}}."""
        app_dir = self._app_dir(app_name)
        if not app_dir.is_dir():
            return {"success": False, "error": f"App '{app_name}' not found"}

        meta_path = app_dir / "app.json"
        if not meta_path.exists():
            return {"success": False, "error": "Missing app.json"}

        with meta_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)

        file_entries: dict[str, str] = {}
        for fp in self._collect_files(app_dir):
            rel = fp.relative_to(app_dir).as_posix()
            try:
                file_entries[rel] = fp.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                pass  # skip binary files in text bundle

        return {
            "success": True,
            "app_name": app_name,
            "manifest": manifest,
            "files": file_entries,
            "file_count": len(file_entries),
        }

    def export_zip(self, app_name: str) -> bytes | None:
        """Export an app as a ZIP archive in memory."""
        app_dir = self._app_dir(app_name)
        if not app_dir.is_dir():
            return None

        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in self._collect_files(app_dir):
                rel = fp.relative_to(app_dir).as_posix()
                zf.write(fp, rel)
        return buf.getvalue()

    def import_bundle(
        self,
        bundle: dict[str, Any],
        *,
        overwrite: bool = False,
        target_name: str | None = None,
    ) -> dict[str, Any]:
        """Import an app from a bundle dict."""
        manifest = bundle.get("manifest", {})
        files = bundle.get("files", {})
        app_name = target_name or manifest.get("name", "")
        if not app_name:
            return {"success": False, "error": "No app name in bundle"}

        app_dir = self._app_dir(app_name)
        if app_dir.exists() and not overwrite:
            return {"success": False, "error": f"App '{app_name}' already exists. Use overwrite=true."}

        app_dir.mkdir(parents=True, exist_ok=True)

        manifest["name"] = app_name
        manifest["updated_at"] = time.time()
        meta_path = app_dir / "app.json"
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        written = 0
        for rel_path, content in files.items():
            if rel_path == "app.json":
                continue
            fp = app_dir / rel_path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            written += 1

        return {
            "success": True,
            "app_name": app_name,
            "files_written": written + 1,
        }

    def import_zip(
        self,
        zip_data: bytes,
        app_name: str,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Import an app from ZIP archive bytes."""
        app_dir = self._app_dir(app_name)
        if app_dir.exists() and not overwrite:
            return {"success": False, "error": f"App '{app_name}' already exists. Use overwrite=true."}

        app_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(BytesIO(zip_data)) as zf:
            zf.extractall(app_dir)

        meta_path = app_dir / "app.json"
        if meta_path.exists():
            with meta_path.open("r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["name"] = app_name
            manifest["updated_at"] = time.time()
            with meta_path.open("w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

        file_count = sum(1 for _ in app_dir.rglob("*") if _.is_file())
        return {"success": True, "app_name": app_name, "files_written": file_count}

    def publish_to_hub(
        self,
        app_name: str,
        hub_client: Any,
        *,
        version: str = "0.1.0",
        changelog: str = "",
    ) -> dict[str, Any]:
        """Package and publish an app to PyHub."""
        app_dir = self._app_dir(app_name)
        if not app_dir.is_dir():
            return {"success": False, "error": f"App '{app_name}' not found"}

        try:
            result = hub_client.publish(
                path=str(app_dir),
                pkg_type="app",
                version=version,
                changelog=changelog,
            )
            return {"success": True, "app_name": app_name, "hub_result": result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def install_from_hub(
        self,
        slug: str,
        hub_client: Any,
        *,
        version: str = "latest",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Download and install an app from PyHub."""
        app_dir = self._app_dir(slug)
        if app_dir.exists() and not overwrite:
            return {"success": False, "error": f"App '{slug}' already exists. Use overwrite=true."}

        try:
            installed_path = hub_client.install(
                slug=slug,
                version=version,
                target_dir=str(self.apps_dir),
            )
            return {
                "success": True,
                "app_name": slug,
                "path": str(installed_path),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def get_dependency_info(self, app_name: str) -> dict[str, Any]:
        """Analyze an app's bindings and dependencies."""
        app_dir = self._app_dir(app_name)
        meta_path = app_dir / "app.json"
        if not meta_path.exists():
            return {"success": False, "error": "Missing app.json"}

        with meta_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)

        deps: dict[str, Any] = {
            "agent_binding": manifest.get("agent_binding", ""),
            "workflow_binding": manifest.get("workflow_binding", ""),
            "knowledge_collections": manifest.get("knowledge_collections", []),
            "allowed_tools": manifest.get("allowed_tools", []),
            "mode": manifest.get("mode", "static"),
            "api_enabled": manifest.get("api_enabled", False),
        }
        deps["has_api_py"] = (app_dir / "api.py").exists()

        file_count = sum(1 for _ in app_dir.rglob("*") if _.is_file())
        total_size = sum(_.stat().st_size for _ in app_dir.rglob("*") if _.is_file())
        deps["file_count"] = file_count
        deps["total_size_bytes"] = total_size

        return {"success": True, "app_name": app_name, "dependencies": deps}
