"""Tests for validate_path and sanitize_tool_call_id utilities."""

from __future__ import annotations

import pytest

from core.systems.runtime.path_utils import sanitize_tool_call_id, validate_path


class TestValidatePath:
    def test_normalizes_posix_path(self):
        assert validate_path("foo/bar.txt") == "/foo/bar.txt"

    def test_strips_leading_slashes(self):
        assert validate_path("///a/b") == "/a/b"

    def test_empty_or_root(self):
        assert validate_path("") == "/"
        assert validate_path("/") == "/"

    def test_rejects_traversal(self):
        with pytest.raises(ValueError, match="traversal"):
            validate_path("foo/../etc/passwd")

    def test_rejects_home_relative(self):
        with pytest.raises(ValueError, match="Home-relative"):
            validate_path("~/.ssh/id_rsa")

    def test_rejects_windows_absolute(self):
        with pytest.raises(ValueError, match="Windows"):
            validate_path("C:/Users/admin/file.txt")

    def test_backslash_normalized(self):
        assert validate_path("foo\\bar\\baz.txt") == "/foo/bar/baz.txt"

    def test_allowed_prefixes_pass(self):
        result = validate_path("workspace/src/file.py", allowed_prefixes=["/workspace"])
        assert result == "/workspace/src/file.py"

    def test_allowed_prefixes_fail(self):
        with pytest.raises(ValueError, match="not under any allowed prefix"):
            validate_path("etc/passwd", allowed_prefixes=["/workspace", "/home"])


class TestSanitizeToolCallId:
    def test_replaces_dots(self):
        assert "." not in sanitize_tool_call_id("call.123.abc")

    def test_replaces_slashes(self):
        result = sanitize_tool_call_id("call/123\\abc")
        assert "/" not in result
        assert "\\" not in result

    def test_replaces_spaces(self):
        assert " " not in sanitize_tool_call_id("call 123 abc")

    def test_truncates_long_ids(self):
        long_id = "a" * 200
        assert len(sanitize_tool_call_id(long_id)) <= 80

    def test_preserves_safe_chars(self):
        assert sanitize_tool_call_id("call_abc-123") == "call_abc-123"

    def test_empty_string(self):
        assert sanitize_tool_call_id("") == ""

    def test_combined_dangerous_chars(self):
        result = sanitize_tool_call_id("../../etc/passwd.txt")
        assert result == "______etc_passwd_txt"
