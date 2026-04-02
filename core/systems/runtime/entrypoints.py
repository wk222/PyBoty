"""Shared helpers for CLI and service entrypoints."""

from __future__ import annotations

import io
import os
import sys

DEFAULT_WEB_PORT = 5000
DEFAULT_API_PORT = 8000


def _wrap_text_stream(stream: object) -> object:
    if getattr(stream, "encoding", None) == "utf-8":
        return stream
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")
        return stream
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        return io.TextIOWrapper(buffer, encoding="utf-8")
    return stream


def ensure_utf8_stdio() -> None:
    """Force UTF-8 stdio on Windows consoles when needed."""
    sys.stdout = _wrap_text_stream(sys.stdout)
    sys.stderr = _wrap_text_stream(sys.stderr)


def resolve_port(*env_names: str, default: int) -> int:
    """Resolve the first valid integer port from the provided environment keys."""
    for env_name in env_names:
        raw_value = os.environ.get(env_name)
        if not raw_value:
            continue
        try:
            return int(raw_value)
        except ValueError:
            continue
    return default
