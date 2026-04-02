"""Workflow definition and runtime persistence helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from typing import Any


class WorkflowVersionStore:
    """Manages workflow version history (draft/published/history)."""

    def __init__(self, versions_dir: str):
        self._dir = versions_dir
        os.makedirs(self._dir, exist_ok=True)

    def _wf_dir(self, name: str) -> str:
        safe = re.sub(r"[^\w\-]", "_", name)
        d = os.path.join(self._dir, safe)
        os.makedirs(d, exist_ok=True)
        return d

    def _content_hash(self, data: dict[str, Any]) -> str:
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def save_version(self, name: str, definition: dict[str, Any], *, message: str = "") -> dict[str, Any]:
        wf_dir = self._wf_dir(name)
        commit_id = self._content_hash(definition)
        ts = time.time()

        commit = {
            "commit_id": commit_id,
            "timestamp": ts,
            "message": message or f"Save at {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "definition": definition,
        }
        commit_path = os.path.join(wf_dir, f"{commit_id}.json")
        with open(commit_path, "w", encoding="utf-8") as f:
            json.dump(commit, f, ensure_ascii=False, indent=2, default=str)

        meta = self._load_meta(name)
        meta.setdefault("commits", [])
        if not any(c["commit_id"] == commit_id for c in meta["commits"]):
            meta["commits"].insert(0, {
                "commit_id": commit_id,
                "timestamp": ts,
                "message": commit["message"],
            })
        meta["draft_commit_id"] = commit_id
        meta["updated_at"] = ts
        meta.setdefault("created_at", ts)
        meta["name"] = name
        self._save_meta(name, meta)
        return {"commit_id": commit_id, "timestamp": ts}

    def publish(self, name: str, commit_id: str | None = None) -> dict[str, Any]:
        meta = self._load_meta(name)
        target = commit_id or meta.get("draft_commit_id")
        if not target:
            raise ValueError(f"No draft to publish for '{name}'")
        meta["published_commit_id"] = target
        meta["published_at"] = time.time()
        self._save_meta(name, meta)
        return {"published_commit_id": target}

    def get_version(self, name: str, commit_id: str) -> dict[str, Any] | None:
        wf_dir = self._wf_dir(name)
        path = os.path.join(wf_dir, f"{commit_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def get_draft(self, name: str) -> dict[str, Any] | None:
        meta = self._load_meta(name)
        cid = meta.get("draft_commit_id")
        return self.get_version(name, cid) if cid else None

    def get_published(self, name: str) -> dict[str, Any] | None:
        meta = self._load_meta(name)
        cid = meta.get("published_commit_id")
        return self.get_version(name, cid) if cid else None

    def list_history(self, name: str, limit: int = 20) -> list[dict[str, Any]]:
        meta = self._load_meta(name)
        commits = meta.get("commits", [])[:limit]
        draft_id = meta.get("draft_commit_id")
        pub_id = meta.get("published_commit_id")
        for c in commits:
            c["is_draft"] = c["commit_id"] == draft_id
            c["is_published"] = c["commit_id"] == pub_id
        return commits

    def rollback(self, name: str, commit_id: str) -> dict[str, Any]:
        version = self.get_version(name, commit_id)
        if not version:
            raise ValueError(f"Commit '{commit_id}' not found for '{name}'")
        meta = self._load_meta(name)
        meta["draft_commit_id"] = commit_id
        meta["updated_at"] = time.time()
        self._save_meta(name, meta)
        return {"draft_commit_id": commit_id, "definition": version.get("definition")}

    def get_meta(self, name: str) -> dict[str, Any]:
        return self._load_meta(name)

    def _meta_path(self, name: str) -> str:
        return os.path.join(self._wf_dir(name), "_meta.json")

    def _load_meta(self, name: str) -> dict[str, Any]:
        path = self._meta_path(name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {"name": name, "commits": []}

    def _save_meta(self, name: str, meta: dict[str, Any]) -> None:
        path = self._meta_path(name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)


class WorkflowStorage:
    """Filesystem-backed workflow store for definitions and runtime snapshots."""

    def __init__(self, workflows_dir: str, builder: Callable[[dict[str, Any]], Any]):
        self.workflows_dir = workflows_dir
        self._builder = builder
        os.makedirs(self.workflows_dir, exist_ok=True)
        self.versions = WorkflowVersionStore(os.path.join(workflows_dir, ".versions"))

    def save_workflow_file(self, workflow: Any) -> str:
        safe_name = re.sub(r"[^\w\-]", "_", workflow.name)
        yml_path = os.path.join(self.workflows_dir, f"{safe_name}.yml")
        json_path = os.path.join(self.workflows_dir, f"{safe_name}.json")
        yaml_text = workflow.to_workflow_spec()
        with open(yml_path, "w", encoding="utf-8") as file:
            file.write(yaml_text)
        clean = workflow.to_dict(runtime=False)
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump(clean, file, ensure_ascii=False, indent=2)
        return yml_path

    def load_workflow(self, name_or_file: str) -> Any | None:
        safe_name = re.sub(r"[^\w\-]", "_", name_or_file)
        for ext in (".yml", ".yaml", ".json"):
            if name_or_file.endswith(ext):
                filepath = os.path.join(self.workflows_dir, name_or_file)
                break
        else:
            filepath = None
            for ext in (".yml", ".yaml", ".json"):
                candidate = os.path.join(self.workflows_dir, f"{safe_name}{ext}")
                if os.path.exists(candidate):
                    filepath = candidate
                    break

        if filepath and os.path.exists(filepath):
            return self._builder(self._load_file(filepath))

        if os.path.exists(self.workflows_dir):
            for filename in os.listdir(self.workflows_dir):
                if not filename.endswith((".json", ".yml", ".yaml")):
                    continue
                try:
                    data = self._load_file(os.path.join(self.workflows_dir, filename))
                    if data.get("name") == name_or_file:
                        return self._builder(data)
                except Exception:
                    continue
        return None

    def list_workflow_files(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        if not os.path.exists(self.workflows_dir):
            return results
        for filename in sorted(os.listdir(self.workflows_dir)):
            filepath = os.path.join(self.workflows_dir, filename)
            try:
                if filename.endswith((".yml", ".yaml")):
                    import yaml

                    with open(filepath, encoding="utf-8") as file:
                        data = yaml.safe_load(file) or {}
                elif filename.endswith(".json"):
                    with open(filepath, encoding="utf-8") as file:
                        data = json.load(file)
                else:
                    continue
                name = data.get("name", filename.rsplit(".", 1)[0])
                if name in seen_names:
                    continue
                seen_names.add(name)
                raw_nodes = data.get("nodes", [])
                node_count = len(raw_nodes) if isinstance(raw_nodes, list) else len(raw_nodes.keys())
                results.append(
                    {
                        "file": filename,
                        "name": name,
                        "description": data.get("description", ""),
                        "version": data.get("version", "1.0.0"),
                        "nodes_count": node_count,
                        "tags": data.get("tags", []),
                        "schedule": data.get("schedule"),
                        "format": "yaml" if filename.endswith((".yml", ".yaml")) else "json",
                    }
                )
            except Exception:
                continue
        return results

    def save_runtime(self, workflow: Any) -> None:
        workflow.updated_at = time.time()
        runtime_dir = os.path.join(self.workflows_dir, ".runs")
        os.makedirs(runtime_dir, exist_ok=True)
        filepath = os.path.join(runtime_dir, f"{workflow.id}.json")
        try:
            with open(filepath, "w", encoding="utf-8") as file:
                json.dump(workflow.to_dict(), file, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_runtime(self, workflow_id: str) -> Any | None:
        runtime_dir = os.path.join(self.workflows_dir, ".runs")
        filepath = os.path.join(runtime_dir, f"{workflow_id}.json")
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as file:
                data = json.load(file)
            return self._builder(data)
        return None

    def create_workflow_definition(self, name: str, definition: dict[str, Any]) -> str:
        base = self._safe_basename(name)
        from core.workflow_spec import export_workflow_spec, strip_workflow_runtime

        yml_path = os.path.join(self.workflows_dir, f"{base}.yml")
        json_path = os.path.join(self.workflows_dir, f"{base}.json")
        with open(yml_path, "w", encoding="utf-8") as file:
            file.write(export_workflow_spec(definition))
        clean = strip_workflow_runtime(definition)
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump(clean, file, indent=2, ensure_ascii=False)
        self.versions.save_version(name, clean, message="Initial creation")
        return name

    def update_workflow_definition(self, workflow_id: str, definition: dict[str, Any]) -> str:
        self._resolve_workflow_path(workflow_id)
        base = self._safe_basename(workflow_id)
        from core.workflow_spec import export_workflow_spec, strip_workflow_runtime

        yml_path = os.path.join(self.workflows_dir, f"{base}.yml")
        json_path = os.path.join(self.workflows_dir, f"{base}.json")
        with open(yml_path, "w", encoding="utf-8") as file:
            file.write(export_workflow_spec(definition))
        clean = strip_workflow_runtime(definition)
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump(clean, file, indent=2, ensure_ascii=False)
        self.versions.save_version(workflow_id, clean, message="Update")
        return workflow_id

    def delete_workflow_definition(self, workflow_id: str) -> bool:
        base = self._safe_basename(workflow_id)
        found = False
        for ext in (".yml", ".yaml", ".json"):
            path = os.path.join(self.workflows_dir, base + ext)
            if os.path.exists(path):
                os.remove(path)
                found = True
        if not found:
            raise FileNotFoundError(f"Workflow '{workflow_id}' not found")
        return True

    def get_workflow_definition(self, workflow_id: str) -> dict[str, Any]:
        return self._load_file(self._resolve_workflow_path(workflow_id))

    def _load_file(self, filepath: str) -> dict[str, Any]:
        with open(filepath, encoding="utf-8") as file:
            raw = file.read()
        if filepath.endswith((".yml", ".yaml")):
            from core.workflow_spec import parse_workflow_spec

            return parse_workflow_spec(raw)
        return json.loads(raw)

    def _safe_basename(self, name: str) -> str:
        return re.sub(r"[^\w\-]", "_", name)

    def _resolve_workflow_path(self, workflow_id: str) -> str:
        base = self._safe_basename(workflow_id)
        for ext in (".yml", ".yaml", ".json"):
            path = os.path.join(self.workflows_dir, base + ext)
            if os.path.exists(path):
                return path
        raise FileNotFoundError(f"Workflow '{workflow_id}' not found")
