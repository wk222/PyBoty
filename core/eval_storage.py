"""Filesystem persistence for evaluation suites and reports."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .eval_models import TestCase


class EvalStorage:
    """Persist evaluation suites and reports under the workspace."""

    def __init__(self, workspace_dir: str | Path = "workspace") -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.tests_dir = self.workspace_dir / "tests"
        self.results_dir = self.workspace_dir / "test_results"
        self.tests_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def save_report(self, report: dict[str, Any]) -> Path:
        target = self.results_dir / f"eval_{int(time.time())}.json"
        self._write_json(target, report)
        return target

    def load_test_suite(self, name: str) -> list[TestCase]:
        path = self._suite_path(name)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [TestCase(**item) for item in data.get("test_cases", [])]

    def save_test_suite(
        self,
        name: str,
        test_cases: list[TestCase],
        *,
        created_at: float | None = None,
    ) -> Path:
        payload = {
            "name": name,
            "created_at": created_at if created_at is not None else time.time(),
            "test_cases": [test_case.to_dict() for test_case in test_cases],
        }
        path = self._suite_path(name)
        self._write_json(path, payload)
        return path

    def _suite_path(self, name: str) -> Path:
        safe_name = Path(name).name
        if not safe_name or safe_name != name or safe_name in {".", ".."}:
            raise ValueError(f"Invalid suite name: {name!r}")
        return self.tests_dir / f"{safe_name}.json"

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
