"""Marketplace tools for App-to-App capability discovery and invocation."""

from __future__ import annotations

import json

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from core.assets.apps.app_manager_registry import get_shared_app_manager


class DiscoverCapabilitiesInput(BaseModel):
    query: str = Field(default="", description="Search query to find specific capabilities, workflows, or agents exported by other apps. Leave empty to list all.")


class DiscoverCapabilitiesTool(BaseTool):
    name: str = "discover_capabilities"
    description: str = "Search the global App Marketplace for capabilities (workflows, agents, tools) exported by other Apps. Use this to find services your app can consume."
    args_schema: type[BaseModel] = DiscoverCapabilitiesInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, query: str = "") -> str:
        mgr = get_shared_app_manager()
        if not mgr:
            return json.dumps({"error": "AppManager not initialized"}, ensure_ascii=False)
        
        results = mgr.discover_capabilities(query)
        if not results:
            return json.dumps({"message": "No matching capabilities found in the marketplace."}, ensure_ascii=False)
        
        return json.dumps({"capabilities": results}, ensure_ascii=False, indent=2)


class InvokeAppCapabilityInput(BaseModel):
    app_name: str = Field(description="The name of the target app providing the capability")
    capability_name: str = Field(description="The name of the exported capability (workflow, agent, or tool name)")
    payload: str = Field(description="JSON string representing the input payload/arguments for the capability")
    api_key: str = Field(default="", description="API Key for the target app, if it requires authentication")


class InvokeAppCapabilityTool(BaseTool):
    name: str = "invoke_app_capability"
    description: str = "Invoke a capability (workflow, agent, or tool) exported by another App in the App Matrix ecosystem. Requires the target app name, capability name, and a JSON payload."
    args_schema: type[BaseModel] = InvokeAppCapabilityInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, app_name: str, capability_name: str, payload: str, api_key: str = "") -> str:
        mgr = get_shared_app_manager()
        if not mgr:
            return json.dumps({"error": "AppManager not initialized"}, ensure_ascii=False)
        
        app_def = mgr.get_app(app_name)
        if not app_def:
            return json.dumps({"error": f"App '{app_name}' not found"}, ensure_ascii=False)
            
        if not app_def.enabled:
            return json.dumps({"error": f"App '{app_name}' is disabled"}, ensure_ascii=False)
            
        if capability_name not in app_def.exports:
            return json.dumps({"error": f"Capability '{capability_name}' is not exported by app '{app_name}'"}, ensure_ascii=False)
            
        if app_def.require_auth:
            if not api_key or api_key not in app_def.api_keys:
                return json.dumps({"error": "Unauthorized: Invalid or missing API key for target app"}, ensure_ascii=False)
        
        try:
            parsed_payload = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid JSON payload"}, ensure_ascii=False)
            
        # Execute the capability via the app's isolated API environment
        # We simulate a request to the app's api.py
        try:
            # We inject the capability_name into the payload so the app's api.py knows what to run
            # Or we pass capability_name as the 'action'
            result = mgr.execute_app_api(app_name, action=capability_name, payload=parsed_payload)
            return json.dumps({"success": True, "result": result}, ensure_ascii=False, indent=2)
        except Exception as exc:
            return json.dumps({"error": f"Execution failed: {str(exc)}"}, ensure_ascii=False)


def get_app_marketplace_tools() -> list[BaseTool]:
    return [DiscoverCapabilitiesTool(), InvokeAppCapabilityTool()]
