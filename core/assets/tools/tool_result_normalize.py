"""Normalize tool outputs for HTTP proxies and agent ToolMessage content.

Prevents common AI-generated-tool failure modes:
- Double (or multi) JSON string encoding returned as a single string
- HTTP layers JSON-serializing an already-JSON string (escaped payload)
- Frontends expecting raw arrays/objects while tools return ``{success, result}`` envelopes
"""

from __future__ import annotations

import json
from typing import Any


def peel_json_wrapped_strings(value: Any, *, max_layers: int = 8) -> Any:
    """Repeatedly parse JSON if *value* is a string that looks like JSON.

    Stops when the value is not a str, or parsing fails, or max_layers is reached.
    """
    current: Any = value
    for _ in range(max_layers):
        if not isinstance(current, str):
            break
        stripped = current.strip()
        if len(stripped) < 2 or stripped[0] not in ("{", "[", '"'):
            break
        try:
            current = json.loads(stripped)
        except json.JSONDecodeError:
            break
    return current


def canonicalize_dynamic_tool_content_string(content: str) -> str:
    """Return a single-level JSON string for agent-facing ToolMessage.content.

    If *content* was multi-wrapped JSON strings, peel and re-serialize once so
    the model sees one valid JSON object/array instead of nested quotes.
    """
    peeled = peel_json_wrapped_strings(content)
    if isinstance(peeled, str):
        return peeled
    return json.dumps(peeled, ensure_ascii=False, default=str)


def normalize_for_app_tool_proxy(result: Any) -> Any:
    """Normalize raw tool return value for ``POST /api/apps/.../tool/.../run``.

    - Peels JSON string layers (fixes double JSON encoding / FastAPI double serialization).
    - If the payload is a success envelope ``{success: true, result: ...}``, returns
      *result* so generated apps using ``Array.isArray(videos)`` keep working.
    - On ``success: false`` leaves the full dict so the UI can show *error* / *traceback*.
    """
    peeled = peel_json_wrapped_strings(result)
    if isinstance(peeled, dict) and peeled.get("success") is True and "result" in peeled:
        return peeled["result"]
    return peeled
