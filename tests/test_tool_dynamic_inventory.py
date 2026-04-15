from __future__ import annotations

import json

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from core.assets.tools import DynamicToolInventory


@tool("alpha_tool")
def alpha_tool() -> str:
    """Return alpha."""
    return "alpha"


@tool("beta_tool")
def beta_tool() -> str:
    """Return beta."""
    return "beta"


class FakeRequest:
    def __init__(self, tools):
        self.tools = list(tools)

    def override(self, *, tools):
        return FakeRequest(tools)


def test_dynamic_tool_inventory_deduplicates_base_tools_during_injection():
    inventory = DynamicToolInventory()
    inventory.set_base_tools([alpha_tool, beta_tool])

    request, added_count = inventory.inject_tools(FakeRequest([alpha_tool]))

    assert added_count == 1
    assert [tool.name for tool in request.tools] == ["alpha_tool", "beta_tool"]


def test_dynamic_tool_inventory_tracks_known_dynamic_tool_names():
    inventory = DynamicToolInventory()

    inventory.set_known_dynamic_tools(["custom_lookup", " secondary_tool ", "", "custom_lookup"])

    assert inventory.is_dynamic_tool("custom_lookup") is True
    assert inventory.is_dynamic_tool("secondary_tool") is True
    assert inventory.is_dynamic_tool("missing_tool") is False


def test_dynamic_tool_inventory_records_successful_tool_creation_notice():
    inventory = DynamicToolInventory()
    result = ToolMessage(
        content=json.dumps({"success": True, "tool_name": "weather_lookup"}, ensure_ascii=False),
        tool_call_id="call_1",
        status="success",
    )

    inventory.note_tool_mutation(tool_name="create_custom_tool", result=result)

    assert inventory.last_created_tool == "weather_lookup"
    assert inventory.pop_mutation_notice() == "weather_lookup"
    assert inventory.pop_mutation_notice() is None
