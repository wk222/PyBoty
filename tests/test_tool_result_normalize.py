"""Tests for tool result normalization (double JSON, success envelope unwrapping)."""

import json

import pytest

from core.assets.tools.tool_result_normalize import (
    canonicalize_dynamic_tool_content_string,
    normalize_for_app_tool_proxy,
    peel_json_wrapped_strings,
)


def test_peel_json_wrapped_strings_nested():
    inner = {"items": [1, 2]}
    s = json.dumps(json.dumps(json.dumps(inner)))
    out = peel_json_wrapped_strings(s, max_layers=8)
    assert out == inner


def test_peel_json_wrapped_strings_respects_max_layers():
    inner = {"a": 1}
    s = json.dumps(json.dumps(inner))
    out_full = peel_json_wrapped_strings(s, max_layers=8)
    assert out_full == inner
    out_one = peel_json_wrapped_strings(s, max_layers=1)
    assert isinstance(out_one, str)
    assert json.loads(out_one) == inner


def test_normalize_for_app_tool_proxy_unwrap_success():
    payload = {"success": True, "result": [1, 2, 3]}
    assert normalize_for_app_tool_proxy(payload) == [1, 2, 3]


def test_normalize_for_app_tool_proxy_keep_error_envelope():
    payload = {"success": False, "error": "boom"}
    assert normalize_for_app_tool_proxy(payload) == payload


def test_canonicalize_dynamic_tool_content_string_double_json():
    inner = {"a": 1}
    doubled = json.dumps(json.dumps(inner))
    out = canonicalize_dynamic_tool_content_string(doubled)
    assert json.loads(out) == inner


@pytest.mark.parametrize(
    "raw",
    [
        None,
        42,
        {"x": 1},
        ["a"],
    ],
)
def test_normalize_for_app_tool_proxy_passthrough_non_str(raw):
    assert normalize_for_app_tool_proxy(raw) is raw
