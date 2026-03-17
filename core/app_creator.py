"""LangChain tools for creating and maintaining sub-applications."""

from __future__ import annotations

import json

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from core.app_manager import AppManager
from core.app_manager_registry import get_shared_app_manager, set_shared_app_manager


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
        description="App mode: 'chat' (AI chat interface), 'rag' (knowledge Q&A), 'workflow' (run a workflow), 'assistant' (full agent), 'static' (plain HTML)",
    )
    workflow_binding: str = Field(default="", description="Workflow name to bind (required for mode=workflow)")
    system_prompt_override: str = Field(default="", description="Custom system prompt for this app's agent")


class CreateAppTool(BaseTool):
    name: str = "create_app"
    description: str = """Create an agent-driven sub-application. Served at /apps/<app_name>/.

App modes (choose the best one for the user's request):
- "chat": Full AI chat interface with streaming responses. Best for: chatbots, customer support, tutors.
- "rag": Knowledge base Q&A with semantic search + AI answers. Best for: documentation, FAQ, research tools.
- "workflow": Run a specific workflow with form inputs. Best for: automation, data processing, batch operations.
- "assistant": Full agent with tool access. Best for: AI assistants, task automation.
- "static": Plain HTML/CSS/JS. Best for: dashboards, simple tools, landing pages.

PREFER agent-driven modes (chat/rag/workflow/assistant) over static when the user wants something intelligent.

Built-in JS helpers for agent-driven apps:
- agentChat(message, onChunk): Stream a conversation with the AI agent
- agentRunWorkflow(name, vars): Trigger a workflow
- agentKnowledgeQuery(query, collection, topK): Search knowledge base
- agentSearch(query): Global search
- agentCallTool(toolName, args): Call a registered tool
- dbQuery(sql), dbWrite(sql, params): Database access

After creating, use update_app_file to customize the HTML/CSS/JS further."""
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
    ) -> str:
        mgr = _get_app_manager()
        tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else []
        result = mgr.create_app(
            name=app_name,
            display_name=display_name,
            description=description,
            tags=tag_list,
            mode=mode,
            workflow_binding=workflow_binding,
            system_prompt_override=system_prompt_override,
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

Agent-driven JS helpers (all in pybot-helpers.js):
- agentChat(message, onChunk): Stream AI conversation. onChunk(chunk, full) for real-time display.
- agentRunWorkflow(name, vars): Trigger a workflow and get results.
- agentKnowledgeQuery(query, collection, topK): Search the knowledge base.
- agentSearch(query): Global search across tools, agents, workflows.
- agentCallTool(toolName, args): Call any registered tool directly.
- apiCall(endpoint, options): Call any API endpoint.
- dbQuery(sql): Run SELECT on shared DB.
- dbWrite(sql, params): Run INSERT/UPDATE/DELETE on shared DB.

IMPORTANT: When overwriting index.html, always keep the pybot-helpers.js script tag.
For api.py backend, you have access to: action (str), payload (dict), DB_PATH (str), result (set this to return).
"""
    args_schema: type[BaseModel] = UpdateAppFileInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, app_name: str, file_path: str, content: str) -> str:
        mgr = _get_app_manager()
        result = mgr.update_app_file(app_name, file_path, content)
        return json.dumps(result, ensure_ascii=False, indent=2)


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


def get_app_creator_tools() -> list[BaseTool]:
    return [
        CreateAppTool(),
        UpdateAppFileTool(),
        ListAppsTool(),
        DeleteAppTool(),
    ]
