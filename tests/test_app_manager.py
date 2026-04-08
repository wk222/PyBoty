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

def test_switch_app_mode(temp_paths):
    manager = AppManager(str(temp_paths.apps_dir), project_paths=temp_paths)
    result = manager.create_app("test_switch", mode="static")
    assert result["success"]

    # Switch without rebuilding template
    switch_res = manager.switch_app_mode("test_switch", "chat", rebuild_template=False)
    assert switch_res["success"]
    assert manager.get_app("test_switch").mode == "chat"
    
    # Check that template hasn't changed to chat
    index_content = (temp_paths.apps_dir / "test_switch" / "index.html").read_text(encoding="utf-8")
    assert "class=\"chat-header\"" not in index_content

    # Switch and rebuild template
    switch_res2 = manager.switch_app_mode("test_switch", "rag", rebuild_template=True)
    assert switch_res2["success"]
    assert manager.get_app("test_switch").mode == "rag"
    
    # Check that template HAS changed to rag
    index_content2 = (temp_paths.apps_dir / "test_switch" / "index.html").read_text(encoding="utf-8")
    assert "class=\"rag-container\"" in index_content2

