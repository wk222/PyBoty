"""Tests for skill_runtime, skill_prompts, and skill_models edge cases."""

from __future__ import annotations

from core.skill_models import SkillDefinition
from core.skill_prompts import render_active_skill_extensions
from core.skill_runtime import TYPE_MAP, build_tool_from_definition


def test_build_tool_returns_none_when_name_missing():
    assert build_tool_from_definition({"code": "print(1)"}, "s") is None


def test_build_tool_returns_none_when_code_missing():
    assert build_tool_from_definition({"name": "t"}, "s") is None


def test_build_tool_with_empty_parameters():
    tool = build_tool_from_definition(
        {"name": "noop", "description": "d", "code": "pass", "parameters": []},
        "s",
    )
    assert tool is not None
    assert tool.name == "noop"


def test_build_tool_maps_parameter_types():
    params = [
        {"name": "a", "type": "int", "description": ""},
        {"name": "b", "type": "float", "description": ""},
        {"name": "c", "type": "bool", "description": ""},
        {"name": "d", "type": "list", "description": ""},
        {"name": "e", "type": "dict", "description": ""},
        {"name": "f", "type": "string", "description": ""},
    ]
    tool = build_tool_from_definition(
        {"name": "typed", "description": "d", "code": "pass", "parameters": params},
        "s",
    )
    assert tool is not None
    schema = tool.args_schema.model_json_schema()
    assert schema["properties"]["a"]["type"] == "integer"
    assert schema["properties"]["b"]["type"] == "number"
    assert schema["properties"]["c"]["type"] == "boolean"
    assert schema["properties"]["f"]["type"] == "string"


def test_build_tool_with_default_values():
    tool = build_tool_from_definition(
        {
            "name": "defaults",
            "description": "d",
            "code": "pass",
            "parameters": [{"name": "x", "type": "str", "description": "", "default": "hi"}],
        },
        "s",
    )
    assert tool is not None
    schema = tool.args_schema.model_json_schema()
    assert schema["properties"]["x"]["default"] == "hi"


def test_type_map_covers_all_common_types():
    expected = {
        "str",
        "string",
        "int",
        "integer",
        "float",
        "number",
        "bool",
        "boolean",
        "list",
        "array",
        "dict",
        "object",
    }
    assert set(TYPE_MAP.keys()) == expected


def test_render_active_skill_extensions_empty_skills():
    assert render_active_skill_extensions([]) == ""


def test_render_active_skill_extensions_excludes_disabled():
    disabled = SkillDefinition(name="off", description="d", enabled=False)
    assert render_active_skill_extensions([disabled]) == ""


def test_render_active_skill_extensions_progressive_true():
    skill = SkillDefinition(
        name="helper",
        description="A helper skill",
        version="2.0.0",
        capabilities=["cap1", "cap2"],
        tools=[{"name": "t1"}],
        source_name="user",
    )
    result = render_active_skill_extensions([skill], progressive=True)
    assert "**helper**" in result
    assert "(v2.0.0)" in result
    assert "1 tools" in result
    assert "@user" in result
    assert "cap1" in result


def test_render_active_skill_extensions_progressive_false():
    skill = SkillDefinition(
        name="full",
        description="d",
        system_prompt_extension="Use this skill to do things.",
    )
    result = render_active_skill_extensions([skill], progressive=False)
    assert "### 技能: full" in result
    assert "Use this skill to do things." in result


def test_render_active_skill_extensions_truncates_long_description():
    skill = SkillDefinition(name="long", description="x" * 200)
    result = render_active_skill_extensions([skill], progressive=True)
    assert len([line for line in result.split("\n") if "**long**" in line][0]) < 300


def test_skill_definition_to_dict_strips_tool_instance():
    tool_with_instance = {"name": "t", "code": "pass", "_tool_instance": object()}
    skill = SkillDefinition(name="s", description="d", tools=[tool_with_instance])
    serialized = skill.to_dict()
    assert "_tool_instance" not in serialized["tools"][0]
    assert serialized["tools"][0]["name"] == "t"


def test_skill_definition_to_dict_roundtrip():
    skill = SkillDefinition(
        name="rt",
        description="roundtrip",
        version="3.0.0",
        capabilities=["a", "b"],
        source_name="test",
    )
    d = skill.to_dict()
    assert d["name"] == "rt"
    assert d["capabilities"] == ["a", "b"]
    assert d["source_name"] == "test"
    assert isinstance(d["installed_at"], float)
