from __future__ import annotations

import json

import pytest

from core.modes.agents import AgentDefinition, AgentStorage
from core.assets.tools import (
    TemplateToolCreator,
    ToolCreationError,
    ToolCreatorTool,
    ToolStorage,
    build_tool_definition,
    create_dynamic_tool,
    persist_validated_tool_definition,
)


def test_tool_creator_persists_and_executes_tool(temp_paths):
    storage = ToolStorage(str(temp_paths.global_tools_dir))
    creator = ToolCreatorTool(storage=storage)

    result = json.loads(
        creator._run(
            tool_name="adder",
            description="Add two integers",
            parameters='[{"name":"a","type":"int","description":"left"},{"name":"b","type":"int","description":"right"}]',
            code="result = a + b",
            dependencies=[],
            usage_guide="用于简单加法",
        )
    )

    assert result["success"] is True
    definition = storage.get_tool("adder")
    assert definition is not None

    dynamic_tool = create_dynamic_tool(definition, project_paths=temp_paths)
    execution = json.loads(dynamic_tool._run(a=2, b=3))

    assert execution["success"] is True
    assert execution["result"] == 5


def test_template_tool_creator_targets_agent_storage(temp_paths):
    storage = ToolStorage(str(temp_paths.global_tools_dir))
    agent_storage = AgentStorage(str(temp_paths.agents_dir))
    agent_storage.add_agent(
        AgentDefinition(
            name="helper",
            role="support",
            description="Shared helper agent",
            system_prompt="You help with utility tasks.",
        )
    )
    creator = TemplateToolCreator(storage=storage, agent_storage=agent_storage)

    result = json.loads(creator._run("calculator", custom_name="agent_calc", target_agent="helper"))

    assert result["success"] is True
    assert storage.get_tool("agent_calc") is None

    agent_tools = ToolStorage(str(temp_paths.agents_dir / "helper" / "tools"))
    assert agent_tools.get_tool("agent_calc") is not None


def test_failed_tool_update_restores_previous_definition(temp_paths):
    storage = ToolStorage(str(temp_paths.global_tools_dir))
    original = build_tool_definition(
        tool_name="demo_tool",
        description="Original",
        parameters=[],
        code="result = 'ok'",
        dependencies=[],
        usage_guide="original",
    )
    storage.add_tool("demo_tool", original)

    updated = build_tool_definition(
        tool_name="demo_tool",
        description="Broken",
        parameters=[],
        code="result = 'broken'",
        dependencies=[],
        usage_guide="broken",
    )

    def broken_validator(_: dict[str, object]) -> None:
        raise RuntimeError("boom")

    with pytest.raises(ToolCreationError):
        persist_validated_tool_definition(storage, updated, validator=broken_validator)

    restored = storage.get_tool("demo_tool")
    assert restored is not None
    assert restored["description"] == "Original"
    assert restored["code"] == "result = 'ok'"
