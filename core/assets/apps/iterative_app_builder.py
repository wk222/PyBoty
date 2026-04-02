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
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ConfigDict, Field

from core.assets.apps.app_manager_registry import get_shared_app_manager
from core.assets.apps.app_verifier import AppVerificationService

logger = logging.getLogger(__name__)


class IterativeAppBuilderInput(BaseModel):
    app_name: str = Field(description="App identifier (lowercase, alphanumeric + underscore/hyphen)")
    display_name: str = Field(description="Human-readable app name")
    description: str = Field(description="Detailed description of what the app should do and look like")
    mode: str = Field(default="chat", description="App mode: 'chat', 'rag', 'workflow', 'assistant', 'static'")
    max_iterations: int = Field(default=3, description="Maximum number of repair iterations")


class IterativeAppBuilderTool(BaseTool):
    name: str = "build_app_iteratively"
    description: str = """Build a complete web application iteratively.
This tool will create the app, generate its HTML/JS/CSS/API files based on your description, 
and then automatically run the AppVerifier. If the verifier finds critical errors or bugs, 
it will automatically prompt the LLM to fix them up to `max_iterations` times.
Use this instead of create_app + update_app_file when you want a guaranteed working app in one step."""
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

        # Step 1: Create the base app
        try:
            mgr.create_app(
                name=app_name,
                display_name=display_name,
                description=description,
                mode=mode,
            )
        except ValueError as e:
            # App might already exist, which is fine, we will overwrite
            logger.info("App %s might already exist: %s", app_name, e)

        # Step 2: Initial Generation
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert web developer. Generate the files for a PyBoty app. "
                       "The app mode is '{mode}'. Return a JSON object with keys: 'index.html', 'static/app.js', "
                       "'static/style.css', and optionally 'api.py'. Do not use markdown blocks, just raw JSON.\n\n"
                       "CRITICAL: Always leave testing interfaces in your code! "
                       "In `api.py`, include a `test` action to verify basic logic. "
                       "In `static/app.js`, include a `window.runSelfTest = async () => {...}` function to verify API/Tool calls."),
            ("human", "App Name: {app_name}\nDescription: {description}")
        ])

        logger.info("Starting initial generation for app: %s", app_name)
        response = self.llm.invoke(prompt.format_messages(
            mode=mode, app_name=app_name, description=description
        ))
        
        try:
            content = str(response.content)
            # Strip markdown JSON blocks if present
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            files = json.loads(content.strip())
            
            for file_path, file_content in files.items():
                if file_content:
                    mgr.write_file(app_name, file_path, file_content)
        except Exception as e:
            return json.dumps({"success": False, "error": f"Initial generation failed to parse JSON: {e}"})

        # Step 3: Iterative Verification and Repair
        for iteration in range(max_iterations):
            verification_result = verifier.verify_app(app_name)
            if not verification_result.get("success", False):
                return json.dumps(verification_result)

            score = verification_result.get("score", 0)
            issues = verification_result.get("issues", [])
            
            # Filter for critical or high issues
            critical_issues = [i for i in issues if i.get("severity") in ["critical", "high"]]
            
            if not critical_issues and score >= 80:
                logger.info("App %s passed verification on iteration %d with score %d", app_name, iteration, score)
                return json.dumps({
                    "success": True,
                    "message": f"App built successfully after {iteration} repairs.",
                    "score": score,
                    "app_url": f"/apps/{app_name}/"
                })

            logger.warning(
                "App %s failed verification. Iteration %d. Score: %d. Repairing...", 
                app_name, iteration, score
            )
            
            # Repair Prompt
            repair_prompt = ChatPromptTemplate.from_messages([
                ("system", "You are an expert web developer. The generated PyBoty app has verification errors. "
                           "Review the issues and return a JSON object with the FULL updated content for the "
                           "files that need fixing. Keys should be file paths (e.g., 'index.html', 'static/app.js'). "
                           "Return ONLY raw JSON."),
                ("human", "App Name: {app_name}\nIssues:\n{issues}\n\nPlease fix these issues.")
            ])

            repair_response = self.llm.invoke(repair_prompt.format_messages(
                app_name=app_name, issues=json.dumps(critical_issues, indent=2, ensure_ascii=False)
            ))

            try:
                repair_content = str(repair_response.content)
                if repair_content.startswith("```json"):
                    repair_content = repair_content[7:]
                if repair_content.endswith("```"):
                    repair_content = repair_content[:-3]
                fixed_files = json.loads(repair_content.strip())
                
                for file_path, file_content in fixed_files.items():
                    if file_content:
                        mgr.write_file(app_name, file_path, file_content)
            except Exception as e:
                logger.error("Repair generation failed to parse JSON: %s", e)
                # Continue to next iteration, maybe it will recover

        # Final check
        final_verification = verifier.verify_app(app_name)
        return json.dumps({
            "success": True,
            "message": "Reached max iterations.",
            "final_score": final_verification.get("score", 0),
            "remaining_issues": final_verification.get("issues", []),
            "app_url": f"/apps/{app_name}/"
        }, ensure_ascii=False, indent=2)
