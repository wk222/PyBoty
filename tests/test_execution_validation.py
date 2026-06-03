from __future__ import annotations

from core.systems.execution.execution_validation import IterativeResourceValidator


def test_iterative_resource_validator_reports_missing_resource(temp_paths):
    validator = IterativeResourceValidator(workspace_dir=str(temp_paths.workspace_dir))

    result = validator.validate(resource_path="apps/missing-app")

    assert result["success"] is False
    assert "不存在" in result["error"]


def test_iterative_resource_validator_detects_html_and_api_failures(temp_paths):
    app_dir = temp_paths.workspace_dir / "apps" / "demo"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "index.html").write_text("", encoding="utf-8")
    (app_dir / "api.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

    validator = IterativeResourceValidator(workspace_dir=str(temp_paths.workspace_dir))
    result = validator.validate(resource_path="apps/demo")

    assert result["success"] is True
    assert result["verdict"] == "FAIL"
    checks = {item["check"]: item for item in result["results"]}
    assert checks["html_empty"]["status"] == "fail"
    assert checks["api_syntax"]["status"] == "fail"
