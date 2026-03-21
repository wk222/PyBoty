"""Tool argument auto-repair: fix common LLM type mismatches before Pydantic validation.

Inspired by LangChain's tool invocation error handling and DeepAgents' schema validation.
When an LLM calls a tool, it sometimes passes arguments in the wrong type:
  - A list where a str is expected (e.g. JSON array instead of serialized JSON string)
  - A dict where a str is expected
  - A str where an int/float is expected
  - A single value where a list is expected

Additionally, code-content strings are susceptible to JSON-deserialization damage:
LLM writes ``/\\n/g`` in a JS regex, but JSON decodes ``\\n`` into a real newline,
splitting the regex literal across two lines — a fatal syntax error.  The
``_repair_code_content`` pipeline restores broken escape sequences before the
tool ever sees them.

This module attempts to coerce arguments to match the tool's expected schema,
preventing ValidationError before it reaches the user.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain.tools import BaseTool

logger = logging.getLogger(__name__)

_BROKEN_JS_REGEX = re.compile(
    r"(?<![>)'\"\w<])"
    r"(/[^/\r\n]{0,40})\r?\n(\s{0,4}[^/\r\n]{0,40}/[gimsuy]{0,6}(?:\s*[,;)\].]|$))",
    re.MULTILINE,
)

_BROKEN_JS_STRING = re.compile(
    r"""(['\"])           # opening quote
    ([^'\"\\\r\n]{0,200}) # string content before the break
    \r?\n                 # the problematic real newline
    \s*                   # indentation (discarded)
    ([^'\"\\\r\n]{0,200}) # string content after the break
    \1                    # matching closing quote""",
    re.VERBOSE | re.MULTILINE,
)

_CODE_CONTENT_TOOLS: dict[str, tuple[str, str]] = {
    "update_app_file": ("file_path", "content"),
}


def _repair_js_regex(content: str) -> str:
    """Fix common JSON-deserialization damage in JavaScript source code.

    Handles three categories of damage:
    1. Under-escaped: ``/\\n/g`` deserialized into ``/`` + real newline + ``/g``
    2. Under-escaped: ``'\\n'`` deserialized into ``'`` + real newline + ``'``
    3. Over-escaped: code-structure newlines kept as literal ``\\n`` text
       (e.g. ``statement;\\nstatement`` on one line instead of two lines)
    """
    content = _BROKEN_JS_REGEX.sub(
        lambda m: m.group(1) + "\\n" + m.group(2),
        content,
    )
    content = _BROKEN_JS_STRING.sub(
        lambda m: m.group(1) + m.group(2) + "\\n" + m.group(3) + m.group(1),
        content,
    )
    content = _repair_over_escaped_newlines(content)
    return content


def _repair_over_escaped_newlines(js: str) -> str:
    """Convert literal ``\\n`` to real newlines when outside JS strings/regex/templates.

    Uses a lightweight state machine to track quoting context so that escape
    sequences inside string literals, template literals, and regex literals
    are preserved while bare ``\\n`` in code (e.g. between statements) is
    converted to a real newline.
    """
    if "\\n" not in js:
        return js

    out: list[str] = []
    i = 0
    n = len(js)

    while i < n:
        ch = js[i]

        if ch in ("'", '"'):
            j = i + 1
            while j < n and js[j] != ch:
                if js[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            j = min(j + 1, n)
            out.append(js[i:j])
            i = j
            continue

        if ch == "`":
            j = i + 1
            depth = 0
            while j < n:
                if js[j] == "`" and depth == 0:
                    j += 1
                    break
                if js[j] == "\\" and j + 1 < n:
                    j += 2
                elif js[j : j + 2] == "${":
                    j += 2
                    depth += 1
                elif js[j] == "}" and depth > 0:
                    j += 1
                    depth -= 1
                else:
                    j += 1
            out.append(js[i:j])
            i = j
            continue

        if ch == "/" and _can_start_regex(js, i):
            j = i + 1
            while j < n and js[j] != "/":
                if js[j] == "\\" and j + 1 < n:
                    j += 2
                elif js[j] in ("\r", "\n"):
                    break
                else:
                    j += 1
            if j < n and js[j] == "/":
                j += 1
                while j < n and js[j] in "gimsuy":
                    j += 1
                out.append(js[i:j])
                i = j
                continue

        if ch == "/" and i + 1 < n and js[i + 1] == "/":
            j = i + 2
            while j < n and js[j] not in ("\r", "\n"):
                j += 1
            out.append(js[i:j])
            i = j
            continue

        if ch == "/" and i + 1 < n and js[i + 1] == "*":
            j = js.find("*/", i + 2)
            j = j + 2 if j != -1 else n
            out.append(js[i:j])
            i = j
            continue

        if ch == "\\" and i + 1 < n and js[i + 1] == "n":
            out.append("\n")
            i += 2
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _can_start_regex(js: str, pos: int) -> bool:
    """Heuristic: can ``/`` at *pos* be the start of a regex literal?"""
    j = pos - 1
    while j >= 0 and js[j] in " \t":
        j -= 1
    if j < 0:
        return True
    ch = js[j]
    if ch in ")]}":
        return False
    if ch.isalnum() or ch in "_$":
        return False
    return True


def _repair_code_content(
    tool_name: str,
    tool_args: dict[str, Any],
) -> dict[str, Any] | None:
    """If *tool_name* writes code files, sanitise the content argument.

    Returns a new dict when a repair was made, or ``None`` if nothing changed.
    """
    spec = _CODE_CONTENT_TOOLS.get(tool_name)
    if spec is None:
        return None

    path_key, content_key = spec
    file_path = str(tool_args.get(path_key, ""))
    content = tool_args.get(content_key)
    if not isinstance(content, str):
        return None

    if file_path.endswith(".js"):
        fixed = _repair_js_regex(content)
        if fixed is not content and fixed != content:
            repaired = dict(tool_args)
            repaired[content_key] = fixed
            logger.info(
                "Auto-repaired broken JS regex in '%s' for tool '%s'",
                file_path, tool_name,
            )
            return repaired

    return None


def repair_tool_args(
    tool_name: str,
    tool_args: dict[str, Any],
    tools: list[BaseTool],
) -> dict[str, Any]:
    """Try to coerce tool_args to match the tool's args_schema.

    Returns the (potentially repaired) args dict. If no repair is needed
    or the tool is not found, returns the original args unchanged.
    """
    code_repaired = _repair_code_content(tool_name, tool_args)
    if code_repaired is not None:
        tool_args = code_repaired

    tool = _find_tool(tool_name, tools)
    if tool is None:
        return tool_args

    schema = _get_json_schema(tool)
    if not schema:
        return tool_args

    repaired = dict(tool_args)
    properties = schema.get("properties", {})
    changed = code_repaired is not None

    for param_name, param_schema in properties.items():
        if param_name not in repaired:
            continue

        value = repaired[param_name]
        expected_type = param_schema.get("type")
        if not expected_type:
            any_of = param_schema.get("anyOf", [])
            if any_of:
                expected_type = _pick_best_type(any_of, value)

        if not expected_type:
            continue

        coerced = _coerce_value(value, expected_type)
        if coerced is not value:
            repaired[param_name] = coerced
            changed = True
            logger.info(
                "Auto-repaired arg '%s' for tool '%s': %s -> %s",
                param_name, tool_name, type(value).__name__, expected_type,
            )

    return repaired if changed else tool_args


def _find_tool(tool_name: str, tools: list[BaseTool]) -> BaseTool | None:
    for tool in tools:
        if tool.name == tool_name:
            return tool
    return None


def _get_json_schema(tool: BaseTool) -> dict[str, Any] | None:
    schema_cls = getattr(tool, "args_schema", None)
    if schema_cls is None:
        return None
    try:
        if hasattr(schema_cls, "model_json_schema"):
            return schema_cls.model_json_schema()
        if hasattr(schema_cls, "schema"):
            return schema_cls.schema()
    except Exception:
        pass
    return None


def _pick_best_type(any_of: list[dict[str, Any]], value: Any) -> str | None:
    """From a Union/anyOf schema, pick the type that best fits a coercion target."""
    types = [s.get("type") for s in any_of if "type" in s]
    if isinstance(value, list) and "string" in types:
        return "string"
    if isinstance(value, dict) and "string" in types:
        return "string"
    if isinstance(value, str) and "array" in types:
        return "array"
    return types[0] if types else None


def _coerce_value(value: Any, expected_type: str) -> Any:
    """Attempt to coerce value to expected JSON Schema type.

    Returns the original value (same object identity) if no coercion is needed.
    """
    if expected_type == "string":
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        if not isinstance(value, str):
            return str(value)
        return value

    if expected_type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return value
        if isinstance(value, float) and value == int(value):
            return int(value)
        return value

    if expected_type == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return value
        return value

    if expected_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1", "yes"):
                return True
            if low in ("false", "0", "no"):
                return False
        return value

    if expected_type == "array":
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            return [value]
        return [value]

    if expected_type == "object":
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return value

    return value
