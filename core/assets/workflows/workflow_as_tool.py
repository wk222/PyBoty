"""Wrap saved workflows as individual agent tools.

Unlike the generic ``TriggerWorkflowTool`` which requires the agent to know
workflow names, this module scans saved workflows and creates one
``StructuredTool`` per workflow with its own name, description, and input
schema derived from the workflow's declared input variables.

Inspired by Coze's ``WorkflowAsModelTool`` pattern.

Usage::

    from core.assets.workflows.workflow_as_tool import discover_workflow_tools

    tools = discover_workflow_tools(engine)
    # Each tool is named after the workflow: "wf_data_analysis", "wf_report_gen", etc.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)


def _build_input_model(workflow_name: str, variables: dict[str, Any]) -> type[BaseModel]:
    """Dynamically create a Pydantic model from workflow input variable declarations."""
    fields: dict[str, Any] = {}
    for key, default in variables.items():
        if key.startswith("_") or "." in key:
            continue
        if isinstance(default, str):
            fields[key] = (str, Field(default=default, description=f"Input: {key}"))
        elif isinstance(default, (int, float)):
            fields[key] = (type(default), Field(default=default, description=f"Input: {key}"))
        elif isinstance(default, bool):
            fields[key] = (bool, Field(default=default, description=f"Input: {key}"))
        else:
            fields[key] = (str, Field(default="", description=f"Input: {key} (as JSON string)"))

    if not fields:
        fields["input_data"] = (str, Field(
            default="{}",
            description="Input data as JSON string",
        ))

    model_name = f"WF_{workflow_name.replace('-', '_').replace(' ', '_')}_Input"
    return create_model(model_name, **fields)


def _make_workflow_runner(engine: Any, workflow_name: str):
    """Create a closure that runs the named workflow."""
    def run_workflow(**kwargs: Any) -> str:
        try:
            workflow = engine.load_workflow(workflow_name)
            if not workflow:
                return json.dumps(
                    {"success": False, "error": f"Workflow '{workflow_name}' not found"},
                    ensure_ascii=False,
                )

            input_data = kwargs.get("input_data")
            if input_data and isinstance(input_data, str):
                try:
                    parsed = json.loads(input_data)
                    kwargs.update(parsed)
                    del kwargs["input_data"]
                except (json.JSONDecodeError, KeyError):
                    pass

            workflow.variables.update(kwargs)
            workflow.variables["input"] = kwargs
            result = engine.run_workflow(workflow)
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            return json.dumps(
                {"success": False, "error": str(exc)},
                ensure_ascii=False,
            )
    return run_workflow


def discover_workflow_tools(engine: Any) -> list[StructuredTool]:
    """Scan saved workflows and create a StructuredTool for each.

    Returns a list of tools, each named ``wf_{workflow_name}`` to avoid
    collision with regular tools.
    """
    tools: list[StructuredTool] = []
    try:
        saved = engine.list_workflow_files()
    except Exception as exc:
        logger.debug("workflow_as_tool: cannot list workflows: %s", exc)
        return tools

    for entry in saved:
        name = entry.get("name", "")
        if not name:
            continue

        try:
            workflow = engine.load_workflow(name)
            if not workflow:
                continue
        except Exception:
            continue

        description = workflow.description or f"Execute the '{name}' workflow."
        declared_vars = {
            k: v for k, v in workflow.variables.items()
            if not k.startswith("_") and "." not in k and k != "input"
        }

        tool_name = f"wf_{name.replace('-', '_').replace(' ', '_')}"
        if len(tool_name) > 60:
            tool_name = tool_name[:60]

        input_model = _build_input_model(name, declared_vars)
        runner = _make_workflow_runner(engine, name)

        tool = StructuredTool.from_function(
            name=tool_name,
            description=f"[Workflow] {description}",
            func=runner,
            args_schema=input_model,
        )
        tools.append(tool)
        logger.debug("workflow_as_tool: registered '%s' with %d input fields",
                      tool_name, len(input_model.model_fields))

    return tools
