"""LangChain tools for creating and maintaining sub-applications."""

from __future__ import annotations

import json

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from core.assets.apps.app_manager import AppManager
from core.assets.apps.app_manager_registry import get_shared_app_manager, set_shared_app_manager
from core.assets.apps.iterative_app_builder import IterativeAppBuilderTool


def _get_app_manager() -> AppManager:
    return get_shared_app_manager()


def set_app_manager(mgr: AppManager) -> None:
    """Backward-compatible wrapper around the shared AppManager registry."""
    set_shared_app_manager(mgr)


class CreateAppInput(BaseModel):
    app_name: str = Field(description="App identifier (lowercase, alphanumeric + underscore/hyphen)")
    display_name: str = Field(description="Human-readable app name")
    description: str = Field(description="What this app does")
    tags: str = Field(default="", description="Comma-separated tags")
    mode: str = Field(
        default="chat",
        description="App mode: 'chat', 'rag', 'workflow', 'assistant', 'static'",
    )
    workflow_binding: str = Field(default="", description="Workflow name to bind (required for mode=workflow)")
    system_prompt_override: str = Field(default="", description="Custom system prompt for this app's agent")
    isolated_knowledge: bool = Field(default=False, description="If true, gives the app its own isolated knowledge base namespace")
    isolated_storage: bool = Field(default=False, description="If true, gives the app its own isolated data storage directory")
    require_auth: bool = Field(default=False, description="If true, requires an app-specific API key to access")
    api_keys: str = Field(default="", description="Comma-separated list of API keys for this app (if require_auth is true)")
    exports: str = Field(default="", description="Comma-separated list of capabilities (workflows/agents) this app exports to the global marketplace")


class CreateAppTool(BaseTool):
    name: str = "create_app"
    description: str = """Create the managed scaffold for a sub-application. Served at /apps/<app_name>/.

Use this when you want the platform to create the app runtime correctly:
- app.json metadata
- mode-aware template scaffold
- static assets
- pybot-helpers.js injection

If you want a one-shot 'build + verify + auto-repair' loop, prefer `build_app_iteratively`.
If you need app-mode guidance or step-by-step strategy, read the `create_app_sop` skill."""
    args_schema: type[BaseModel] = CreateAppInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(
        self,
        app_name: str,
        display_name: str,
        description: str,
        tags: str = "",
        mode: str = "chat",
        workflow_binding: str = "",
        system_prompt_override: str = "",
        isolated_knowledge: bool = False,
        isolated_storage: bool = False,
        require_auth: bool = False,
        api_keys: str = "",
        exports: str = "",
    ) -> str:
        mgr = _get_app_manager()
        tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else []
        api_key_list = [k.strip() for k in api_keys.split(",") if k.strip()] if api_keys else []
        export_list = [e.strip() for e in exports.split(",") if e.strip()] if exports else []
        result = mgr.create_app(
            name=app_name,
            display_name=display_name,
            description=description,
            tags=tag_list,
            mode=mode,
            workflow_binding=workflow_binding,
            system_prompt_override=system_prompt_override,
            isolated_knowledge=isolated_knowledge,
            isolated_storage=isolated_storage,
            require_auth=require_auth,
            api_keys=api_key_list,
            exports=export_list,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)


class UpdateAppFileInput(BaseModel):
    app_name: str = Field(description="App identifier")
    file_path: str = Field(
        description="File path within the app (e.g., 'index.html', 'static/style.css', 'static/app.js', 'api.py')"
    )
    content: str = Field(description="Full file content to write")


class UpdateAppFileTool(BaseTool):
    name: str = "update_app_file"
    description: str = """Update or create a file in a sub-application.
Use this to write HTML, CSS, JS, or backend Python code.
Common files:
- index.html: Main page (HTML). MUST include <script src="static/pybot-helpers.js"></script>
  BEFORE <script src="static/app.js"></script>.
- static/style.css: Styles
- static/app.js: Frontend JavaScript (custom app code goes here)
- static/pybot-helpers.js: Auto-generated. DO NOT overwrite — contains all agent helpers.
- api.py: Backend API handler (receives 'action' and 'payload' variables, set 'result' to return data)

FRONTEND JS helpers (in pybot-helpers.js, available ONLY in app.js):
- agentChat(message, onChunk): Stream AI conversation. onChunk(chunk, full) for real-time display.
- agentRunWorkflow(name, vars): Trigger a workflow and get results.
- agentKnowledgeQuery(query, collection, topK): Search the knowledge base.
- agentSearch(query): Global search across tools, agents, workflows.
- agentCallTool(toolName, args): Call any registered tool directly. Returns the tool result DIRECTLY.
  If the tool returns a list, it returns an Array. Always check Array.isArray().
- apiCall(actionName, payloadObj): Call api.py backend (e.g. apiCall('fetch_hot', {count: 10})).
- dbQuery(sql): Run SELECT on shared DB (frontend shortcut).
- dbWrite(sql, params): Run INSERT/UPDATE/DELETE on shared DB (frontend shortcut).

BACKEND api.py environment (runs in isolated Python, NO frontend helpers available):
- Variables: action (str), payload (dict), DB_PATH (str), result (set this to return data)
- Built-in: db_query(sql) returns list of dicts, db_execute(sql, params) runs INSERT/UPDATE/DELETE
- Available: import json, sqlite3, os, re, datetime, requests, etc. (third-party packages are auto-installed)
- ⚠️ DO NOT use agentCallTool, agentChat, dbQuery, dbWrite in api.py — those are JS-only!

TESTING & VERIFICATION (CRITICAL):
When writing an app, ALWAYS leave testing interfaces and verify basic functions:
1. For backend (api.py): Write testable actions and use the `test_app_api` tool to verify they work without errors.
2. For frontend (app.js): Add a `window.runSelfTest = async () => { ... }` function that tests basic API calls or UI rendering logic and logs to console.
3. After meaningful changes, call `verify_app` to keep the app inside the managed repair loop.

IMPORTANT: When overwriting index.html, always keep the pybot-helpers.js script tag.
App.js MUST be wrapped in document.addEventListener('DOMContentLoaded', () => { ... });

⚠️ JSON ESCAPING — JUST USE \\n NORMALLY:
Write the JS code content naturally with \\n for line breaks. The system auto-repairs escaping issues.
Do NOT double-escape line breaks between statements (no \\\\n for newlines in code structure).

⚠️ AUTO-VALIDATION: After writing, the system automatically checks the file for errors.
If validation finds critical issues, you MUST call update_app_file again with fixed content.
Do NOT ignore validation warnings in the response.
"""
    args_schema: type[BaseModel] = UpdateAppFileInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, app_name: str, file_path: str, content: str) -> str:
        mgr = _get_app_manager()
        result = mgr.update_app_file(app_name, file_path, content)
        if not result.get("success"):
            return json.dumps(result, ensure_ascii=False, indent=2)

        validation = self._validate_file(mgr, app_name, file_path)
        if validation:
            result["validation"] = validation
            critical = [v for v in validation if v.get("severity") == "critical"]
            if critical:
                result["has_critical_issues"] = True
                result["action_required"] = (
                    f"发现 {len(critical)} 个严重问题，必须修复后重新调用 update_app_file。"
                )

        return json.dumps(result, ensure_ascii=False, indent=2)

    @staticmethod
    def _validate_file(mgr: AppManager, app_name: str, file_path: str) -> list[dict[str, str]]:
        from pathlib import Path

        from core.assets.apps.app_verifier_checks import check_api, check_html, check_javascript

        app_dir = Path(mgr.apps_dir) / app_name
        norm = file_path.replace("\\", "/")

        if norm == "index.html":
            html_path = app_dir / "index.html"
            if html_path.exists():
                return check_html(html_path.read_text(encoding="utf-8"))
        elif norm == "static/app.js":
            js_path = app_dir / "static" / "app.js"
            if js_path.exists():
                return check_javascript(js_path.read_text(encoding="utf-8"), js_path)
        elif norm == "api.py":
            api_path = app_dir / "api.py"
            if api_path.exists():
                return check_api(api_path.read_text(encoding="utf-8"))
        return []


class ListAppsInput(BaseModel):
    pass


class ListAppsTool(BaseTool):
    name: str = "list_apps"
    description: str = "List all sub-applications created by the agent. Shows name, description, status, and URL."
    args_schema: type[BaseModel] = ListAppsInput

    def _run(self) -> str:
        mgr = _get_app_manager()
        apps = mgr.list_apps()
        for app in apps:
            app["url"] = f"/apps/{app['name']}/"
        return json.dumps({"success": True, "apps": apps, "count": len(apps)}, ensure_ascii=False, indent=2)


class DeleteAppInput(BaseModel):
    app_name: str = Field(description="App identifier to delete")


class DeleteAppTool(BaseTool):
    name: str = "delete_app"
    description: str = "Delete a sub-application and all its files."
    args_schema: type[BaseModel] = DeleteAppInput

    def _run(self, app_name: str) -> str:
        mgr = _get_app_manager()
        result = mgr.delete_app(app_name)
        return json.dumps(result, ensure_ascii=False, indent=2)


class TestAppApiInput(BaseModel):
    app_name: str = Field(description="App identifier")
    action: str = Field(description="Action name to test in api.py")
    payload_json: str = Field(default="{}", description="JSON string of the payload to send")


class TestAppApiTool(BaseTool):
    name: str = "test_app_api"
    description: str = "Test the backend api.py of a sub-application by sending an action and payload. Use this to verify your api.py works correctly after creating or updating it."
    args_schema: type[BaseModel] = TestAppApiInput

    def _run(self, app_name: str, action: str, payload_json: str = "{}") -> str:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as e:
            return json.dumps({"success": False, "error": f"Invalid payload JSON: {e}"})
        
        mgr = _get_app_manager()
        result = mgr.execute_app_api(app_name, action, payload)
        return json.dumps(result, ensure_ascii=False, indent=2)


def get_app_creator_tools(llm=None) -> list[BaseTool]:
    tools = [
        CreateAppTool(),
        UpdateAppFileTool(),
        ListAppsTool(),
        DeleteAppTool(),
        TestAppApiTool(),
    ]
    if llm:
        tools.append(IterativeAppBuilderTool(llm=llm))
    return tools
