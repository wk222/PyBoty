from __future__ import annotations

import json

from core.assets.apps.app_manager import AppManager
from core.assets.apps.app_verifier import ReadAppFileTool, VerifyAppTool, set_verifier_app_manager


def test_verify_app_reports_runtime_and_api_issues(temp_paths):
    manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
    set_verifier_app_manager(manager)
    manager.create_app("demo", "Demo", "test app")
    manager.update_app_file(
        "demo",
        "index.html",
        """
<html>
<head>
    <link rel="stylesheet" href="static/style.css">
</head>
<body>
    <div id="app"></div>
    <script src="static/app.js"></script>
</body>
</html>
""".strip(),
    )
    manager.update_app_file(
        "demo",
        "static/app.js",
        """
async function loadData() {
    const response = await apiCall('/api/apps/demo/api');
    document.getElementById('app').innerHTML = response.value;
}

loadData();
""".strip(),
    )
    manager.update_app_file("demo", "static/style.css", "body { color: #222; }")
    manager.update_app_file("demo", "api.py", "value = payload.get('value')")

    result = json.loads(VerifyAppTool()._run("demo"))

    assert result["success"] is True
    assert result["verdict"] == "FAIL"
    assert result["summary"]["critical"] >= 2
    assert any(issue["category"] == "runtime" for issue in result["issues"])
    assert any(issue["category"] == "api" for issue in result["issues"])
    assert "必须修复" in result["fix_instructions"]


def test_verify_app_can_skip_fix_instructions(temp_paths):
    manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
    set_verifier_app_manager(manager)
    manager.create_app("demo", "Demo", "test app")

    result = json.loads(VerifyAppTool()._run("demo", auto_fix=False))

    assert result["success"] is True
    assert "fix_instructions" not in result


def test_read_app_file_rejects_path_escape(temp_paths):
    manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
    set_verifier_app_manager(manager)
    manager.create_app("demo", "Demo", "test app")

    result = json.loads(ReadAppFileTool()._run("demo", "../outside.txt"))

    assert result == {"success": False, "error": "路径越权"}
