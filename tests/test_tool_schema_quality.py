"""Automated quality checks for all built-in tool schemas.

Ensures every tool parameter has ``type`` and ``description`` defined,
which is critical for LLM function-calling accuracy. Tools with missing
schema metadata cause the LLM to guess argument formats, leading to
ValidationError and poor reliability.

Inspired by DeepAgents' approach of validating tool schemas at test time.
"""

from __future__ import annotations

import pytest
from langchain.tools import BaseTool

from core.agent_creator import (
    AgentCreatorTool,
    DelegateToAgentTool,
    AskAgentTool,
    ListAgentsTool,
    RemoveAgentTool,
)
from core.app_creator import get_app_creator_tools
from core.app_verifier import get_app_verifier_tools
from core.capability_bus import CapBusTool
from core.clarification_tool import AskClarificationTool, AnalyzeRequirementTool
from core.eval_framework import EvalResponseTool, RunTestsTool
from core.execution_loop import ExecCodeTool, ScanProjectTool, IterativeFixTool
from core.skill_marketplace import (
    PackageSkillTool,
    InstallSkillTool,
    UninstallSkillTool,
    SearchSkillsTool,
    CreateSkillTool,
)
from core.tool_chain import RunChainTool, ToolStatsTool
from core.tool_creator import ListTemplatesTool, RemoveToolTool, ToolCreatorTool
from core.workflow_tools import (
    RunWorkflowTool,
    ResumeWorkflowTool,
    ListWorkflowsTool,
    GenerateWorkflowTool,
    TriggerWorkflowTool,
)


class _FakeEngine:
    """Minimal stub for workflow tools that require an engine."""
    def list_workflows(self): return []
    def parse_workflow(self, *a, **kw): return None


def _collect_builtin_tools() -> list[BaseTool]:
    tools: list[BaseTool] = []
    tools.extend(get_app_creator_tools())
    tools.extend(get_app_verifier_tools())
    tools.extend([AskClarificationTool(), AnalyzeRequirementTool()])
    tools.extend([ExecCodeTool(), ScanProjectTool(), IterativeFixTool()])
    tools.extend([ListTemplatesTool(), RemoveToolTool(), ToolCreatorTool()])

    engine = _FakeEngine()
    tools.extend([
        RunWorkflowTool(engine=engine),
        ResumeWorkflowTool(engine=engine),
        ListWorkflowsTool(engine=engine),
        GenerateWorkflowTool(engine=engine),
        TriggerWorkflowTool(engine=engine),
    ])

    tools.extend([
        AgentCreatorTool(),
        DelegateToAgentTool(),
        AskAgentTool(),
        ListAgentsTool(),
        RemoveAgentTool(),
    ])

    tools.extend([EvalResponseTool(), RunTestsTool()])
    tools.extend([RunChainTool(), ToolStatsTool()])
    tools.extend([
        PackageSkillTool(),
        InstallSkillTool(),
        UninstallSkillTool(),
        SearchSkillsTool(),
        CreateSkillTool(),
    ])
    tools.append(CapBusTool())
    return tools


ALL_TOOLS = _collect_builtin_tools()


def _get_schema(tool: BaseTool) -> dict | None:
    schema_cls = getattr(tool, "args_schema", None)
    if schema_cls is None:
        return None
    if hasattr(schema_cls, "model_json_schema"):
        return schema_cls.model_json_schema()
    if hasattr(schema_cls, "schema"):
        return schema_cls.schema()
    return None


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_tool_has_schema(tool: BaseTool):
    """Every tool must have an args_schema defined."""
    assert getattr(tool, "args_schema", None) is not None, (
        f"Tool '{tool.name}' has no args_schema — LLM cannot generate proper arguments"
    )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_tool_has_description(tool: BaseTool):
    """Every tool must have a non-empty description."""
    assert tool.description and len(tool.description.strip()) > 10, (
        f"Tool '{tool.name}' has missing or trivially short description"
    )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_all_params_have_type(tool: BaseTool):
    """Every parameter in the schema must have a 'type' or 'anyOf' field."""
    schema = _get_schema(tool)
    if schema is None:
        pytest.skip(f"Tool '{tool.name}' has no parseable schema")

    properties = schema.get("properties", {})
    for param_name, param_def in properties.items():
        has_type = "type" in param_def or "anyOf" in param_def or "$ref" in param_def or "allOf" in param_def
        assert has_type, (
            f"Tool '{tool.name}', param '{param_name}' has no type definition. "
            f"Schema: {param_def}"
        )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_all_params_have_description(tool: BaseTool):
    """Every parameter should have a description for LLM context."""
    schema = _get_schema(tool)
    if schema is None:
        pytest.skip(f"Tool '{tool.name}' has no parseable schema")

    properties = schema.get("properties", {})
    missing = []
    for param_name, param_def in properties.items():
        if "description" not in param_def or not param_def["description"].strip():
            missing.append(param_name)

    assert not missing, (
        f"Tool '{tool.name}' has parameters without descriptions: {missing}. "
        f"LLM relies on descriptions to understand parameter usage."
    )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_required_params_listed(tool: BaseTool):
    """If a schema has properties, required should be explicitly listed."""
    schema = _get_schema(tool)
    if schema is None:
        pytest.skip(f"Tool '{tool.name}' has no parseable schema")

    properties = schema.get("properties", {})
    if not properties:
        return

    required = schema.get("required", [])
    for req_field in required:
        assert req_field in properties, (
            f"Tool '{tool.name}': required field '{req_field}' is not in properties"
        )
