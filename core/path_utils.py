"""
路径安全工具 — 集中处理路径遍历防御和规范化。

所有对外暴露文件路径的 API 都应通过 safe_resolve() 验证。
DeepAgents-inspired validate_path and sanitize_tool_call_id added for
backend-level path security and safe file naming.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath


def safe_resolve(base: str | Path, user_path: str | Path) -> Path:
    """将 user_path 解析到 base 目录下，确保不会逃逸。

    Raises:
        PermissionError: 当解析后路径不在 base 之下时。
    """
    base = Path(base).resolve()
    target = (base / Path(user_path)).resolve()
    if not (target == base or str(target).startswith(str(base) + os.sep)):
        raise PermissionError(f"Path traversal blocked: {user_path!r}")
    return target


def safe_join(base: str | Path, *parts: str) -> Path:
    """safe_resolve 的多段拼接快捷方式。"""
    return safe_resolve(base, os.path.join(*parts))


def validate_path(path: str, *, allowed_prefixes: list[str] | None = None) -> str:
    """Validate and normalize a file path for security.

    Inspired by DeepAgents ``backends/utils.py``:
    - Rejects path traversal (``..``, ``~``)
    - Rejects Windows absolute paths (``C:/``)
    - Optionally restricts to ``allowed_prefixes``
    - Returns a normalized POSIX-style path starting with ``/``

    Raises:
        ValueError: on unsafe or malformed paths.
    """
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized == "/":
        return "/"

    parts = PurePosixPath(normalized).parts
    if ".." in parts:
        raise ValueError(f"Path traversal not allowed: {path}")
    if normalized.startswith("~"):
        raise ValueError(f"Home-relative paths not allowed: {path}")
    if re.match(r"^[a-zA-Z]:", normalized):
        raise ValueError(f"Windows absolute paths are not supported: {path}. Use POSIX-style paths starting with /")

    result = "/" + normalized.lstrip("/")
    result = str(PurePosixPath(result))

    if allowed_prefixes is not None:
        if not any(result.startswith(prefix) for prefix in allowed_prefixes):
            raise ValueError(f"Path {result!r} is not under any allowed prefix: {allowed_prefixes}")

    return result


def sanitize_tool_call_id(tool_call_id: str) -> str:
    """Sanitize tool_call_id for safe use in filesystem paths.

    Inspired by DeepAgents ``backends/utils.py``: replaces dangerous
    characters (``.``, ``/``, ``\\``) with underscores to prevent
    path traversal and separator issues.
    """
    return tool_call_id.replace(".", "_").replace("/", "_").replace("\\", "_").replace(" ", "_")[:80]
