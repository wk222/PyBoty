"""Tests for centralized error types and utilities."""

from __future__ import annotations

from core.errors import (
    ToolAuthorizationError,
    ToolError,
    ToolInputError,
    ToolNotFoundError,
    ToolRateLimitError,
    ToolTimeoutError,
    extract_error_code,
    format_error,
    redact_sensitive_text,
)


class TestToolErrors:
    def test_tool_error_to_dict(self):
        err = ToolError("something broke", details={"key": "value"})
        d = err.to_dict()
        assert d["error"] is True
        assert d["code"] == "tool_error"
        assert d["status"] == 500
        assert d["message"] == "something broke"
        assert d["details"]["key"] == "value"

    def test_tool_input_error(self):
        err = ToolInputError("missing param 'name'")
        assert err.status == 400
        assert err.code == "invalid_input"

    def test_tool_authorization_error(self):
        err = ToolAuthorizationError("blocked by policy")
        assert err.status == 403
        assert err.code == "authorization_denied"

    def test_tool_not_found_error(self):
        err = ToolNotFoundError("tool 'xyz' not registered")
        assert err.status == 404
        assert err.code == "tool_not_found"

    def test_tool_timeout_error(self):
        err = ToolTimeoutError("execution timed out")
        assert err.status == 408
        assert err.code == "timeout"

    def test_tool_rate_limit_error(self):
        err = ToolRateLimitError("too many calls", retry_after_seconds=5.0)
        assert err.status == 429
        assert err.code == "rate_limited"
        assert err.retry_after_seconds == 5.0

    def test_to_dict_without_details(self):
        err = ToolInputError("bad input")
        d = err.to_dict()
        assert "details" not in d


class TestExtractErrorCode:
    def test_from_tool_error(self):
        assert extract_error_code(ToolInputError("x")) == "invalid_input"

    def test_from_dict_string(self):
        assert extract_error_code({"code": "ENOENT"}) == "ENOENT"

    def test_from_dict_int(self):
        assert extract_error_code({"code": 404}) == "404"

    def test_from_object_attribute(self):
        class Err:
            code = "custom_code"

        assert extract_error_code(Err()) == "custom_code"

    def test_none(self):
        assert extract_error_code(None) is None

    def test_no_code(self):
        assert extract_error_code("just a string") is None


class TestRedaction:
    def test_redacts_api_key(self):
        text = 'api_key: "sk-abc123def456ghijklmnopqrstuvwxyz"'
        result = redact_sensitive_text(text)
        assert "abc123" not in result
        assert "REDACTED" in result

    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUz.payload.signature"
        result = redact_sensitive_text(text)
        assert "eyJhbGciOiJIUz" not in result
        assert "REDACTED" in result

    def test_redacts_password(self):
        text = 'password: "hunter2"'
        result = redact_sensitive_text(text)
        assert "hunter2" not in result

    def test_preserves_safe_text(self):
        text = "This is a normal log message about user actions"
        assert redact_sensitive_text(text) == text


class TestFormatError:
    def test_format_tool_error(self):
        err = ToolInputError("bad param")
        result = format_error(err)
        assert "[invalid_input]" in result
        assert "bad param" in result

    def test_format_generic_error(self):
        err = ValueError("something wrong")
        result = format_error(err)
        assert "something wrong" in result

    def test_format_redacts_in_error(self):
        err = ValueError('Failed with token: "sk-abcdefghijklmnopqrstuvwxyz1234567890"')
        result = format_error(err)
        assert "abcdefghijklmnopqrstuvwxyz" not in result
