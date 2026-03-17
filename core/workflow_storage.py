"""Workflow definition and runtime persistence helpers."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from typing import Any


class WorkflowStorage:
    """Filesystem-backed workflow store for definitions and runtime snapshots."""

    def __init__(self, workflows_dir: str, builder: Callable[[dict[str, Any]], Any]):
        self.workflows_dir = workflows_dir
        self._builder = builder
        os.makedirs(self.workflows_dir, exist_ok=True)

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
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump(strip_workflow_runtime(definition), file, indent=2, ensure_ascii=False)
        return name

    def update_workflow_definition(self, workflow_id: str, definition: dict[str, Any]) -> str:
        self._resolve_workflow_path(workflow_id)
        base = self._safe_basename(workflow_id)
        from core.workflow_spec import export_workflow_spec, strip_workflow_runtime

        yml_path = os.path.join(self.workflows_dir, f"{base}.yml")
        json_path = os.path.join(self.workflows_dir, f"{base}.json")
        with open(yml_path, "w", encoding="utf-8") as file:
            file.write(export_workflow_spec(definition))
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump(strip_workflow_runtime(definition), file, indent=2, ensure_ascii=False)
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
