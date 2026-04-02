"""Centralized error types and utilities for PyBot.

Tool error hierarchy and utilities:
- Tool-specific error types with status codes
- Error code extraction from nested exceptions
- Sensitive text redaction for safe logging
- Structured error formatting
"""

from __future__ import annotations

import re
from typing import Any


class ToolError(Exception):
    """Base class for all tool-related errors."""

    status: int = 500
    code: str = "tool_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error": True,
            "code": self.code,
            "status": self.status,
            "message": str(self),
        }
        if self.details:
            result["details"] = self.details
        return result


class ToolInputError(ToolError):
    """Invalid tool input parameters."""

    status = 400
    code = "invalid_input"


class ToolAuthorizationError(ToolError):
    """Tool call blocked by policy or permissions."""

    status = 403
    code = "authorization_denied"


class ToolNotFoundError(ToolError):
    """Requested tool does not exist."""

    status = 404
    code = "tool_not_found"


class ToolTimeoutError(ToolError):
    """Tool execution timed out."""

    status = 408
    code = "timeout"


class ToolRateLimitError(ToolError):
    """Tool call rate-limited."""

    status = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after_seconds: float = 0, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.retry_after_seconds = retry_after_seconds


def extract_error_code(err: Any) -> str | None:
    """Extract a string error code from an exception or dict.

    Handles
    ``.code`` attributes (string or int) and dict ``"code"`` keys.
    """
    if err is None:
        return None
    if isinstance(err, ToolError):
        return err.code
    if isinstance(err, dict):
        code = err.get("code")
        if isinstance(code, str):
            return code
        if isinstance(code, int):
            return str(code)
        return None
    code = getattr(err, "code", None)
    if isinstance(code, str):
        return code
    if isinstance(code, int):
        return str(code)
    return None


_SENSITIVE_PATTERNS = [
    (re.compile(r"(sk-[a-zA-Z0-9]{20,})", re.IGNORECASE), r"sk-***REDACTED***"),
    (re.compile(r"(Bearer\s+)[^\s]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(api[_-]?key[\"':\s=]+)[^\s\"',}]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(token[\"':\s=]+)[^\s\"',}]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(password[\"':\s=]+)[^\s\"',}]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(secret[\"':\s=]+)[^\s\"',}]+", re.IGNORECASE), r"\1***REDACTED***"),
]


def redact_sensitive_text(text: str) -> str:
    """Redact API keys, tokens, passwords, and secrets from text.

    Redact known sensitive patterns.
    """
    result = text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def format_error(err: Any) -> str:
    """Format an error for logging, redacting sensitive content.

    Format and redact for safe logging.
    """
    if isinstance(err, ToolError):
        return f"[{err.code}] {redact_sensitive_text(str(err))}"
    if isinstance(err, Exception):
        msg = str(err)
        return redact_sensitive_text(msg)
    return redact_sensitive_text(str(err))
