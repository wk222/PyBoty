"""Iterative App Builder Tool for PyBoty.

Provides a high-level tool that generates an app, runs the AppVerifier,
and automatically feeds errors back to the LLM to fix them, ensuring
a higher success rate for generated applications.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.tools import BaseTool
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field
from langchain.agents import create_agent

from core.assets.apps.app_manager_registry import get_shared_app_manager
from core.assets.apps.app_verifier import AppVerificationService
from core.systems.governance.agent_control import AgentControlPolicy
from core.systems.governance.subagent_sandbox import build_subagent_sandbox
from core.assets.agents.agent_capability_profile import AgentCapabilityProfile
from core.assets.agents.agent_middleware_profile import AgentMiddlewareProfile
from core.systems.middleware.agent_middleware_factory import build_subagent_langchain_middleware
from core.assets.tools.tool_middleware import DynamicToolMiddleware

logger = logging.getLogger(__name__)


class IterativeAppBuilderInput(BaseModel):
    app_name: str = Field(description="App identifier (lowercase, alphanumeric + underscore/hyphen)")
    display_name: str = Field(description="Human-readable app name")
    description: str = Field(description="Detailed description of what the app should do and look like")
    mode: str = Field(default="chat", description="App mode: 'chat', 'rag', 'workflow', 'assistant', 'static'")
    max_iterations: int = Field(default=3, description="Maximum number of repair iterations")


class IterativeAppBuilderTool(BaseTool):
    name: str = "build_app_iteratively"
    description: str = """Build a complete web application iteratively using an isolated App Generator subagent.
This tool spawns a dedicated sandbox with strict managed policy and specialized tools to 
generate HTML/JS/CSS/API files, verify them, and repair them automatically.
Use this as the top-level App Runtime entrypoint when you want a working app in one step."""
    args_schema: type[BaseModel] = IterativeAppBuilderInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    llm: BaseChatModel | None = None

    def __init__(self, llm: BaseChatModel | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.llm = llm

    def _run(
        self,
        app_name: str,
        display_name: str,
        description: str,
        mode: str = "chat",
        max_iterations: int = 3,
    ) -> str:
        if not self.llm:
            return json.dumps({"success": False, "error": "LLM is required for iterative building."})

        mgr = get_shared_app_manager()
        verifier = AppVerificationService(app_manager=mgr)

        # Step 1: Create the managed base app if needed.
        create_result = mgr.create_app(
            name=app_name,
            display_name=display_name,
            description=description,
            mode=mode,
        )
        if not create_result.get("success") and "already exists" not in str(create_result.get("error", "")):
            return json.dumps(create_result, ensure_ascii=False)

        # Step 2: Set up the dedicated Subagent Sandbox and Tools
        from core.assets.apps.app_creator import UpdateAppFileTool, TestAppApiTool
        from core.assets.apps.app_verifier import VerifyAppTool, ReadAppFileTool
        from core.assets.agents.agent_storage import AgentDefinition

        # Create isolated capability profile for App Generation
        capability_profile = AgentCapabilityProfile.from_value({
            "preset": "builder",
            "allow_app_mutation": True,
            "allow_code_execution": False,  # Strict managed policy: block external code exec
        })
        policy = AgentControlPolicy(mode="balanced", blocked_tools=frozenset({"exec_code", "run_tests"}))
        sandbox = build_subagent_sandbox(agent_name="app_generator", capability_profile=capability_profile)
        
        # Tools specific to app generation
        app_tools = [
            UpdateAppFileTool(),
            TestAppApiTool(),
            VerifyAppTool(llm=self.llm),
            ReadAppFileTool(),
        ]

        tool_middleware = DynamicToolMiddleware(
            control_policy=policy,
            approval_scope="subagent:app_generator",
        )
        tool_middleware.set_base_tools(app_tools)

        agent_def = AgentDefinition(
            name="app_generator",
            role="builder",
            description="App Generator Sandbox",
            system_prompt=(
                "You are an expert web developer operating in a strict sandbox. "
                "Your goal is to build, verify, and fix the requested application. "
                f"The app mode is '{mode}'. "
                "CRITICAL: Always leave testing interfaces in your code! "
                "In `api.py`, include a `test` action to verify basic logic. "
                "In `static/app.js`, include a `window.runSelfTest = async () => {...}` function. "
                "Use `update_app_file` to write code, then IMMEDIATELY use `verify_app` to check it. "
                "If `verify_app` reports errors, you MUST read the broken files, fix them with `update_app_file`, and re-verify. "
                "Do NOT stop until `verify_app` passes with a high score and no critical errors."
            )
        )

        middleware_stack = build_subagent_langchain_middleware(
            definition=agent_def,
            sandbox=sandbox,
            capability_profile=capability_profile,
            middleware_profile=AgentMiddlewareProfile.from_value("default"),
            effective_policy=policy,
            tool_middleware=tool_middleware,
        )

        graph = create_agent(
            model=self.llm,
            tools=app_tools,
            system_prompt=agent_def.system_prompt,
            middleware=middleware_stack,
        )

        # Step 3: Run the generation loop within the isolated subagent
        logger.info("Starting subagent generation loop for app: %s", app_name)
        state = {
            "messages": [
                {"role": "user", "content": f"Please build the app '{app_name}'. Description:\n{description}"}
            ]
        }
        
        config = {"recursion_limit": max(20, max_iterations * 5)}
        try:
            result = graph.invoke(state, config=config)
            final_messages = result.get("messages", [])
            response_text = final_messages[-1].content if final_messages else "App building complete."
        except Exception as e:
            logger.error("App generator subagent failed: %s", e)
            return json.dumps({"success": False, "error": f"Subagent failed: {e}"})

        # Final verification to return result
        final_verification = verifier.verify_app(app_name)
        return json.dumps({
            "success": True,
            "message": "App generation complete via subagent.",
            "final_score": final_verification.get("score", 0),
            "remaining_issues": final_verification.get("issues", []),
            "app_url": f"/apps/{app_name}/",
            "agent_response": response_text
        }, ensure_ascii=False, indent=2)
