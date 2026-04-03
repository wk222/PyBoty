from __future__ import annotations

import json

from core.assets.apps.app_manager import AppManager


def test_app_manager_persists_api_enablement_and_toggle(temp_paths):
    manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)

    created = manager.create_app("demo", "Demo", "test app")
    assert created["success"] is True

    updated = manager.update_app_file("demo", "api.py", "result = {'value': payload['value']}")
    assert updated["success"] is True
    assert manager.get_app("demo").api_enabled is True

    toggled = manager.toggle_app("demo", False)
    assert toggled == {"success": True, "app": "demo", "enabled": False}

    metadata = json.loads((temp_paths.apps_dir / "demo" / "app.json").read_text(encoding="utf-8"))
    assert metadata["api_enabled"] is True
    assert metadata["enabled"] is False


def test_app_manager_executes_app_api(temp_paths):
    manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
    manager.create_app("demo", "Demo", "test app")
    manager.update_app_file(
        "demo",
        "api.py",
        (
            "if action == 'echo':\n"
            "    result = {'echo': payload['value'], 'db_path': DB_PATH}\n"
            "else:\n"
            "    result = {'echo': None}\n"
        ),
    )

    result = manager.execute_app_api("demo", "echo", {"value": "hello"})

    assert result["success"] is True
    assert result["result"]["echo"] == "hello"
    assert result["result"]["db_path"].endswith("agent.db")


def test_app_manager_reloads_persisted_apps(temp_paths):
    manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
    manager.create_app("demo", "Demo", "test app")

    reloaded = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)

    assert reloaded.get_app("demo") is not None
    assert reloaded.get_app("demo").display_name == "Demo"
