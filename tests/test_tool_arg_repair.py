"""Unit tests for core.tool_arg_repair — type coercion, JS regex repair, code content repair."""

from __future__ import annotations

import json

import pytest
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from core.assets.tools.tool_arg_repair import (
    _coerce_value,
    _pick_best_type,
    _repair_code_content,
    _repair_js_regex,
    _repair_over_escaped_newlines,
    repair_tool_args,
)


class DummyInput(BaseModel):
    name: str = Field(description="A name")
    count: int = Field(default=0, description="A count")
    tags: list[str] = Field(default_factory=list, description="Tag list")
    enabled: bool = Field(default=True, description="Toggle")
    score: float = Field(default=0.0, description="A score")
    metadata: dict = Field(default_factory=dict, description="Extra metadata")


class DummyTool(BaseTool):
    name: str = "dummy_tool"
    description: str = "A test tool"
    args_schema: type[BaseModel] = DummyInput

    def _run(self, **kwargs):
        return "ok"


class FileInput(BaseModel):
    app_name: str = Field(description="App id")
    file_path: str = Field(description="File path")
    content: str = Field(description="File content")


class UpdateAppFileTool(BaseTool):
    name: str = "update_app_file"
    description: str = "Update app file"
    args_schema: type[BaseModel] = FileInput

    def _run(self, **kwargs):
        return "ok"


TOOLS: list[BaseTool] = [DummyTool(), UpdateAppFileTool()]


# ── _coerce_value ──


class TestCoerceValue:
    def test_list_to_string(self):
        result = _coerce_value([1, 2, 3], "string")
        assert result == "[1, 2, 3]"

    def test_dict_to_string(self):
        result = _coerce_value({"a": 1}, "string")
        assert result == json.dumps({"a": 1}, ensure_ascii=False)

    def test_int_to_string(self):
        result = _coerce_value(42, "string")
        assert result == "42"

    def test_string_stays_string(self):
        val = "hello"
        result = _coerce_value(val, "string")
        assert result is val

    def test_str_to_int(self):
        assert _coerce_value("10", "integer") == 10

    def test_float_to_int(self):
        assert _coerce_value(3.0, "integer") == 3

    def test_invalid_str_to_int_stays(self):
        val = "abc"
        assert _coerce_value(val, "integer") is val

    def test_str_to_float(self):
        assert _coerce_value("3.14", "number") == pytest.approx(3.14)

    def test_str_true_to_bool(self):
        assert _coerce_value("true", "boolean") is True
        assert _coerce_value("YES", "boolean") is True
        assert _coerce_value("1", "boolean") is True

    def test_str_false_to_bool(self):
        assert _coerce_value("false", "boolean") is False
        assert _coerce_value("NO", "boolean") is False
        assert _coerce_value("0", "boolean") is False

    def test_bool_stays_bool(self):
        assert _coerce_value(True, "boolean") is True

    def test_json_str_to_array(self):
        assert _coerce_value('[1, 2]', "array") == [1, 2]

    def test_non_array_str_wraps(self):
        assert _coerce_value("hello", "array") == ["hello"]

    def test_list_stays_list(self):
        val = [1, 2]
        assert _coerce_value(val, "array") is val

    def test_scalar_wraps_to_array(self):
        assert _coerce_value(42, "array") == [42]

    def test_json_str_to_object(self):
        assert _coerce_value('{"a": 1}', "object") == {"a": 1}

    def test_dict_stays_dict(self):
        val = {"a": 1}
        assert _coerce_value(val, "object") is val

    def test_invalid_json_str_stays_for_object(self):
        val = "not-json"
        assert _coerce_value(val, "object") is val

    def test_unknown_type_returns_unchanged(self):
        val = "test"
        assert _coerce_value(val, "null") is val

    def test_bool_not_treated_as_int(self):
        assert _coerce_value(True, "integer") is True


# ── _pick_best_type ──


class TestPickBestType:
    def test_list_prefers_string(self):
        assert _pick_best_type([{"type": "array"}, {"type": "string"}], [1, 2]) == "string"

    def test_dict_prefers_string(self):
        assert _pick_best_type([{"type": "object"}, {"type": "string"}], {"a": 1}) == "string"

    def test_str_prefers_array(self):
        assert _pick_best_type([{"type": "string"}, {"type": "array"}], "[1,2]") == "array"

    def test_defaults_to_first(self):
        assert _pick_best_type([{"type": "integer"}, {"type": "string"}], 42) == "integer"

    def test_empty_returns_none(self):
        assert _pick_best_type([], "x") is None


# ── repair_tool_args ──


class TestRepairToolArgs:
    def test_list_to_str_repair(self):
        args = {"name": ["a", "b"], "count": 0}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result["name"] == '["a", "b"]'

    def test_str_to_int_repair(self):
        args = {"name": "test", "count": "5"}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result["count"] == 5

    def test_str_to_bool_repair(self):
        args = {"name": "test", "enabled": "false"}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result["enabled"] is False

    def test_str_to_list_repair(self):
        args = {"name": "test", "tags": '["a", "b"]'}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result["tags"] == ["a", "b"]

    def test_str_to_dict_repair(self):
        args = {"name": "test", "metadata": '{"key": "val"}'}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result["metadata"] == {"key": "val"}

    def test_no_repair_needed_returns_same(self):
        args = {"name": "test", "count": 3}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result is args

    def test_unknown_tool_returns_unchanged(self):
        args = {"name": [1, 2]}
        result = repair_tool_args("nonexistent_tool", args, TOOLS)
        assert result is args

    def test_missing_param_ignored(self):
        args = {"name": "test"}
        result = repair_tool_args("dummy_tool", args, TOOLS)
        assert result is args


# ── _repair_js_regex ──


class TestRepairJsRegex:
    def test_fixes_broken_regex_newline(self):
        broken = "var x = text.replace(/\n/g, '<br>');"
        fixed = _repair_js_regex(broken)
        assert "/\\n/g" in fixed
        assert "\n" not in fixed.split("/g")[0].split("replace(")[1]

    def test_fixes_broken_string_newline(self):
        broken = "var s = 'hello\nworld';"
        fixed = _repair_js_regex(broken)
        assert "\\n" in fixed
        assert fixed.count("\n") < broken.count("\n")

    def test_preserves_correct_code(self):
        correct = "var x = text.replace(/\\n/g, '<br>');\nvar y = 1;"
        assert _repair_js_regex(correct) == correct

    def test_handles_empty_string(self):
        assert _repair_js_regex("") == ""


# ── _repair_over_escaped_newlines ──


class TestRepairOverEscapedNewlines:
    def test_converts_bare_escaped_newline(self):
        code = "var a = 1;\\nvar b = 2;"
        result = _repair_over_escaped_newlines(code)
        assert result == "var a = 1;\nvar b = 2;"

    def test_preserves_escaped_in_string(self):
        code = "var s = 'hello\\nworld';"
        result = _repair_over_escaped_newlines(code)
        assert result == code

    def test_preserves_escaped_in_template(self):
        code = "var s = `hello\\nworld`;"
        result = _repair_over_escaped_newlines(code)
        assert result == code

    def test_no_change_when_no_escaped_n(self):
        code = "var a = 1;\nvar b = 2;"
        assert _repair_over_escaped_newlines(code) is code


# ── _repair_code_content ──


class TestRepairCodeContent:
    def test_repairs_js_file_content(self):
        args = {
            "app_name": "test",
            "file_path": "static/app.js",
            "content": "text.replace(/\n/g, '')",
        }
        result = _repair_code_content("update_app_file", args)
        assert result is not None
        assert "/\\n/g" in result["content"]

    def test_skips_non_js_file(self):
        args = {
            "app_name": "test",
            "file_path": "index.html",
            "content": "<div>\n</div>",
        }
        result = _repair_code_content("update_app_file", args)
        assert result is None

    def test_skips_unknown_tool(self):
        args = {"file_path": "app.js", "content": "/\n/g"}
        result = _repair_code_content("some_other_tool", args)
        assert result is None

    def test_skips_non_string_content(self):
        args = {"app_name": "x", "file_path": "app.js", "content": 123}
        result = _repair_code_content("update_app_file", args)
        assert result is None
