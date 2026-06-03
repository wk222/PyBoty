"""Tests for Tool Extensions, Path Validations, File System Tools, Web Fetching, and Unified Inventory.

Consolidates:
1. test_structured_output.py
2. test_path_validation.py
3. test_file_system_tools.py
4. test_web_fetch_tool.py
5. test_unified_tool_inventory.py
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

# Core runtime structured output imports
from core.systems.runtime.structured_output import (
    CodeReview,
    StructuredOutputError,
    TaskAnalysis,
    _extract_json_from_text,
    _schema_to_instruction,
    invoke_structured,
)

# Core path validation imports
from core.systems.runtime.path_utils import sanitize_tool_call_id, validate_path

# Atomic file-system tools imports
from core.assets.tools.file_system_tools import (
    GlobFilesTool,
    GrepFilesTool,
    ListDirectoryTool,
    ReadFileTool,
    StrReplaceTool,
    WriteFileTool,
    _check_path,
    _resolve_root,
)
from core.systems.context import WorkspaceViewService

# Web fetching tool imports
from core.assets.tools import web_fetch_tool
from core.assets.tools.web_fetch_tool import (
    WebFetchTool,
    _LRUCache,
    _extract_title,
    _global_cache,
    _html_to_text,
    _is_private_ip,
    _truncate,
    _validate_url,
)

# Unified tool inventory imports
from core.assets.tools.unified_tool_info import (
    LAYER_SKILL_TOOL,
    LAYER_TOOL,
    UnifiedToolInfo,
)
from core.assets.tools.unified_tool_inventory import UnifiedAssetInventory


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def root(tmp_path: Path) -> str:
    return str(tmp_path.resolve())


@pytest.fixture
def view_service() -> WorkspaceViewService:
    return WorkspaceViewService()


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each web fetch test starts with an empty cache."""
    _global_cache._entries.clear()
    yield
    _global_cache._entries.clear()


# ── Section 1: Structured Pydantic Response Parsing ───────────────────

class SimpleSchema(BaseModel):
    name: str
    value: int


class DetailedSchema(BaseModel):
    title: str = Field(description="标题")
    items: list[str] = Field(default_factory=list, description="项目列表")
    score: float = Field(default=0.0)


class TestExtractJson:
    def test_pure_json(self):
        result = _extract_json_from_text('{"name": "test", "value": 42}')
        assert result == '{"name": "test", "value": 42}'

    def test_json_in_code_fence(self):
        text = 'Here is the result:\n```json\n{"name": "test", "value": 42}\n```'
        result = _extract_json_from_text(text)
        assert '"test"' in result

    def test_json_in_plain_fence(self):
        text = 'Result:\n```\n{"name": "x", "value": 1}\n```'
        result = _extract_json_from_text(text)
        assert '"x"' in result

    def test_json_embedded_in_text(self):
        text = 'The answer is {"name": "embedded", "value": 99} and that is it.'
        result = _extract_json_from_text(text)
        assert result is not None
        assert "embedded" in result

    def test_no_json(self):
        result = _extract_json_from_text("No JSON here at all")
        assert result is None

    def test_empty_string(self):
        result = _extract_json_from_text("")
        assert result is None


class TestSchemaToInstruction:
    def test_produces_json_schema(self):
        result = _schema_to_instruction(SimpleSchema)
        assert "name" in result
        assert "value" in result

    def test_detailed_schema(self):
        result = _schema_to_instruction(DetailedSchema)
        assert "title" in result


class TestInvokeStructuredNative:
    def test_native_success(self):
        mock_structured = MagicMock()
        mock_structured.invoke.return_value = SimpleSchema(name="test", value=42)

        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.with_structured_output.return_value = mock_structured

        messages = [HumanMessage(content="hi")]
        result = invoke_structured(mock_llm, messages, SimpleSchema, method="native")
        assert result.name == "test"
        assert result.value == 42

    def test_native_not_supported(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.with_structured_output.side_effect = NotImplementedError("nope")

        messages = [HumanMessage(content="hi")]
        with pytest.raises(StructuredOutputError):
            invoke_structured(mock_llm, messages, SimpleSchema, method="native", max_retries=0)


class TestInvokeStructuredJsonMode:
    def test_json_mode_success(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.invoke.return_value = AIMessage(content='{"name": "parsed", "value": 99}')

        messages = [HumanMessage(content="test")]
        result = invoke_structured(mock_llm, messages, SimpleSchema, method="json_mode")
        assert result.name == "parsed"
        assert result.value == 99

    def test_json_mode_with_code_fence(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.invoke.return_value = AIMessage(content='Sure:\n```json\n{"name": "fenced", "value": 7}\n```')

        messages = [HumanMessage(content="test")]
        result = invoke_structured(mock_llm, messages, SimpleSchema, method="json_mode")
        assert result.name == "fenced"

    def test_json_mode_invalid_json(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.invoke.return_value = AIMessage(content="not json at all")

        with pytest.raises(StructuredOutputError):
            invoke_structured(
                mock_llm,
                [HumanMessage(content="test")],
                SimpleSchema,
                method="json_mode",
                max_retries=0,
            )


class TestInvokeStructuredManual:
    def test_manual_extracts_json(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.invoke.return_value = AIMessage(content='Here is your data: {"name": "manual", "value": 5}')

        result = invoke_structured(
            mock_llm,
            [HumanMessage(content="test")],
            SimpleSchema,
            method="manual",
        )
        assert result.name == "manual"

    def test_manual_fails_no_json(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.invoke.return_value = AIMessage(content="Just text, no JSON.")

        with pytest.raises(StructuredOutputError):
            invoke_structured(
                mock_llm,
                [HumanMessage(content="test")],
                SimpleSchema,
                method="manual",
                max_retries=0,
            )


class TestInvokeStructuredAuto:
    def test_auto_tries_strategies(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.with_structured_output.side_effect = NotImplementedError("nope")
        mock_llm.invoke.return_value = AIMessage(content='{"name": "auto", "value": 1}')

        result = invoke_structured(mock_llm, [HumanMessage(content="test")], SimpleSchema)
        assert result.name == "auto"

    def test_auto_all_fail(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        mock_llm.with_structured_output.side_effect = NotImplementedError("nope")
        mock_llm.invoke.return_value = AIMessage(content="no json here")

        with pytest.raises(StructuredOutputError, match="All strategies exhausted"):
            invoke_structured(
                mock_llm,
                [HumanMessage(content="test")],
                SimpleSchema,
                max_retries=0,
            )


class TestInvokeUnknownMethod:
    def test_unknown_method(self):
        mock_llm = MagicMock(spec=BaseChatModel)
        with pytest.raises(StructuredOutputError, match="Unknown method"):
            invoke_structured(
                mock_llm,
                [HumanMessage(content="test")],
                SimpleSchema,
                method="nonexistent",
            )


class TestBuiltinSchemas:
    def test_task_analysis_schema(self):
        ta = TaskAnalysis(
            summary="Build a feature",
            steps=["Step 1", "Step 2"],
            complexity="medium",
            estimated_minutes=30,
        )
        assert ta.complexity == "medium"

    def test_code_review_schema(self):
        cr = CodeReview(
            issues=["Bug in line 10"],
            suggestions=["Add tests"],
            quality_score=7,
            summary="Good overall",
        )
        assert cr.quality_score == 7


# ── Section 2: Path Sanitization and Verification ────────────────────

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


# ── Section 3: Atomic File System Tools ───────────────────────────────

class TestResolveRoot:
    def test_uses_explicit_allowed_root(self, tmp_path: Path):
        resolved = _resolve_root(str(tmp_path))
        assert resolved == os.path.realpath(str(tmp_path))

    def test_falls_back_to_initial_cwd(self):
        resolved = _resolve_root(None)
        assert os.path.isabs(resolved)


class TestCheckPath:
    def test_accepts_inside_root(self, root: str):
        ok, resolved = _check_path("foo.txt", root)
        assert ok is True
        assert resolved.startswith(root)

    def test_rejects_outside_root(self, root: str):
        ok, message = _check_path("../escape.txt", root)
        assert ok is False
        assert "越界" in message

    def test_accepts_absolute_inside_root(self, root: str):
        target = os.path.join(root, "data", "x.json")
        ok, resolved = _check_path(target, root)
        assert ok is True
        assert resolved == os.path.realpath(target)

    def test_rejects_absolute_outside_root(self, tmp_path: Path):
        other = tmp_path.parent / "siblings" / "leak.txt"
        ok, message = _check_path(str(other), str(tmp_path))
        assert ok is False
        assert "越界" in message


class TestWriteFileTool:
    def test_creates_new_file(self, tmp_path: Path, root: str):
        tool = WriteFileTool(allowed_root=root)
        result = tool._run("hello.txt", "world")
        assert "成功" in result
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "world"

    def test_creates_intermediate_directories(self, tmp_path: Path, root: str):
        tool = WriteFileTool(allowed_root=root)
        result = tool._run("nested/deep/file.txt", "data")
        assert "成功" in result
        assert (tmp_path / "nested" / "deep" / "file.txt").exists()

    def test_invalidates_cached_view(
        self, tmp_path: Path, root: str, view_service: WorkspaceViewService,
    ):
        target = tmp_path / "a.txt"
        target.write_text("v1", encoding="utf-8")
        stat = target.stat()
        view_service.record_view(
            resolved_path=str(target),
            content="v1",
            mtime=stat.st_mtime,
            file_size=stat.st_size,
            offset=0,
            limit=0,
            is_partial=False,
            total_lines=0,
        )
        assert str(target) in view_service._full_views

        tool = WriteFileTool(allowed_root=root)
        tool.read_file_state = view_service
        tool._run("a.txt", "v2")
        assert str(target) not in view_service._full_views

    def test_rejects_path_escape(self, root: str):
        tool = WriteFileTool(allowed_root=root)
        result = tool._run("../escape.txt", "x")
        assert "越界" in result


class TestReadFileTool:
    def test_reads_full_file(self, tmp_path: Path, root: str):
        (tmp_path / "data.txt").write_text("line1\nline2\nline3", encoding="utf-8")
        tool = ReadFileTool(allowed_root=root)
        result = tool._run("data.txt")
        assert "line1" in result
        assert "line3" in result

    def test_reads_partial_file(self, tmp_path: Path, root: str):
        content = "\n".join(f"line{i}" for i in range(1, 11))
        (tmp_path / "big.txt").write_text(content, encoding="utf-8")
        tool = ReadFileTool(allowed_root=root)
        result = tool._run("big.txt", offset=2, limit=3)
        assert "[partial view]" in result
        assert "line3" in result
        assert "line5" in result
        assert "line6" not in result

    def test_returns_unchanged_stub(
        self, tmp_path: Path, root: str, view_service: WorkspaceViewService,
    ):
        target = tmp_path / "stable.txt"
        target.write_text("hi", encoding="utf-8")
        tool = ReadFileTool(allowed_root=root)
        tool.read_file_state = view_service
        first = tool._run("stable.txt")
        assert "hi" in first
        second = tool._run("stable.txt")
        assert "FILE_UNCHANGED" in second

    def test_force_bypasses_cache(
        self, tmp_path: Path, root: str, view_service: WorkspaceViewService,
    ):
        target = tmp_path / "stable.txt"
        target.write_text("hi", encoding="utf-8")
        tool = ReadFileTool(allowed_root=root)
        tool.read_file_state = view_service
        tool._run("stable.txt")
        forced = tool._run("stable.txt", force=True)
        assert "FILE_UNCHANGED" not in forced
        assert "hi" in forced

    def test_handles_missing_file(self, root: str):
        tool = ReadFileTool(allowed_root=root)
        assert "不存在" in tool._run("absent.txt")

    def test_offset_beyond_eof(self, tmp_path: Path, root: str):
        (tmp_path / "short.txt").write_text("only one line", encoding="utf-8")
        tool = ReadFileTool(allowed_root=root)
        result = tool._run("short.txt", offset=10, limit=5)
        assert "[partial view]" in result


class TestStrReplaceTool:
    def test_replaces_unique_occurrence(self, tmp_path: Path, root: str):
        target = tmp_path / "doc.txt"
        target.write_text("hello world", encoding="utf-8")
        tool = StrReplaceTool(allowed_root=root)
        result = tool._run("doc.txt", "world", "earth")
        assert "成功" in result
        assert target.read_text(encoding="utf-8") == "hello earth"

    def test_rejects_missing_target_string(self, tmp_path: Path, root: str):
        target = tmp_path / "doc.txt"
        target.write_text("hello world", encoding="utf-8")
        tool = StrReplaceTool(allowed_root=root)
        result = tool._run("doc.txt", "missing", "x")
        assert "未找到" in result

    def test_rejects_ambiguous_target_string(self, tmp_path: Path, root: str):
        target = tmp_path / "doc.txt"
        target.write_text("dup dup", encoding="utf-8")
        tool = StrReplaceTool(allowed_root=root)
        result = tool._run("doc.txt", "dup", "x")
        assert "出现了" in result
        assert "2" in result

    def test_invalidates_cache(
        self, tmp_path: Path, root: str, view_service: WorkspaceViewService,
    ):
        target = tmp_path / "doc.txt"
        target.write_text("hello world", encoding="utf-8")
        stat = target.stat()
        view_service.record_view(
            resolved_path=str(target),
            content="hello world",
            mtime=stat.st_mtime,
            file_size=stat.st_size,
            offset=0,
            limit=0,
            is_partial=False,
            total_lines=0,
        )
        tool = StrReplaceTool(allowed_root=root)
        tool.read_file_state = view_service
        tool._run("doc.txt", "world", "earth")
        assert str(target) not in view_service._full_views


class TestListDirectoryTool:
    def test_lists_basic_entries(self, tmp_path: Path, root: str):
        (tmp_path / "a.txt").write_text("", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("", encoding="utf-8")
        tool = ListDirectoryTool(allowed_root=root)
        result = tool._run(".")
        assert "a.txt" in result
        assert "sub" in result

    def test_respects_depth_limit(self, tmp_path: Path, root: str):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "deep.txt").write_text("", encoding="utf-8")
        tool = ListDirectoryTool(allowed_root=root)
        result = tool._run(".", depth=1)
        assert "sub" in result
        assert "deep.txt" not in result

    def test_skips_hidden_entries(self, tmp_path: Path, root: str):
        (tmp_path / ".hidden").write_text("", encoding="utf-8")
        (tmp_path / "shown.txt").write_text("", encoding="utf-8")
        tool = ListDirectoryTool(allowed_root=root)
        result = tool._run(".")
        assert ".hidden" not in result
        assert "shown.txt" in result


class TestGrepFilesTool:
    def test_finds_matches(self, tmp_path: Path, root: str):
        (tmp_path / "a.py").write_text("import os\nimport re\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("from typing import Any\n", encoding="utf-8")
        tool = GrepFilesTool(allowed_root=root)
        result = tool._run("import", path=".", include="*.py")
        assert "a.py" in result
        assert "b.py" in result

    def test_respects_include_filter(self, tmp_path: Path, root: str):
        (tmp_path / "a.py").write_text("token", encoding="utf-8")
        (tmp_path / "a.txt").write_text("token", encoding="utf-8")
        tool = GrepFilesTool(allowed_root=root)
        result = tool._run("token", path=".", include="*.py")
        assert "a.py" in result
        assert "a.txt" not in result

    def test_no_matches_returns_message(self, tmp_path: Path, root: str):
        (tmp_path / "a.py").write_text("nothing here", encoding="utf-8")
        tool = GrepFilesTool(allowed_root=root)
        result = tool._run("xyz", path=".")
        assert "无匹配结果" in result

    def test_invalid_regex(self, root: str):
        tool = GrepFilesTool(allowed_root=root)
        result = tool._run("(unbalanced", path=".")
        assert "无效的正则表达式" in result

    def test_truncation_message_when_capped(self, tmp_path: Path, root: str):
        target = tmp_path / "big.txt"
        target.write_text("\n".join("hit" for _ in range(20)), encoding="utf-8")
        tool = GrepFilesTool(allowed_root=root)
        result = tool._run("hit", path=".", max_results=5)
        assert "结果已截断" in result


class TestGlobFilesTool:
    def test_matches_simple_pattern(self, tmp_path: Path, root: str):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "b.py").write_text("", encoding="utf-8")
        (tmp_path / "c.txt").write_text("", encoding="utf-8")
        tool = GlobFilesTool(allowed_root=root)
        result = tool._run("*.py")
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_double_star_recursive(self, tmp_path: Path, root: str):
        (tmp_path / "src" / "deep").mkdir(parents=True)
        (tmp_path / "src" / "deep" / "x.py").write_text("", encoding="utf-8")
        tool = GlobFilesTool(allowed_root=root)
        result = tool._run("**/*.py")
        assert "x.py" in result

    def test_exclude_pattern(self, tmp_path: Path, root: str):
        (tmp_path / "keep.py").write_text("", encoding="utf-8")
        (tmp_path / "ignore.py").write_text("", encoding="utf-8")
        tool = GlobFilesTool(allowed_root=root)
        result = tool._run("*.py", exclude="ignore.py")
        assert "keep.py" in result
        assert "ignore.py" not in result

    def test_no_matches_returns_message(self, tmp_path: Path, root: str):
        tool = GlobFilesTool(allowed_root=root)
        result = tool._run("*.nope")
        assert "无匹配文件" in result


# ── Section 4: Web Fetching Tool and LRU Caching ──────────────────────

class TestUrlValidation:
    def test_blocks_unsupported_scheme(self):
        assert "ftp" in (_validate_url("ftp://example.com/data") or "")
        assert "file" in (_validate_url("file:///etc/passwd") or "")

    def test_blocks_localhost(self):
        result = _validate_url("http://localhost/admin")
        assert result is not None
        assert "localhost" in result

    def test_blocks_private_ip_literal(self):
        result = _validate_url("http://127.0.0.1/")
        assert result is not None
        assert "127.0.0.1" in result

    def test_blocks_rfc1918_literal(self):
        result = _validate_url("http://10.0.0.1/")
        assert result is not None
        assert "10.0.0.1" in result

    def test_missing_host(self):
        result = _validate_url("http:///")
        assert result is not None
        assert "主机名" in result

    def test_invalid_url(self):
        result = _validate_url("not-a-url")
        assert result is not None


class TestPrivateIpDetection:
    def test_loopback(self):
        assert _is_private_ip("127.0.0.1") is True
        assert _is_private_ip("::1") is True

    def test_private(self):
        assert _is_private_ip("10.0.0.5") is True
        assert _is_private_ip("192.168.1.1") is True
        assert _is_private_ip("172.16.0.1") is True

    def test_public(self):
        assert _is_private_ip("8.8.8.8") is False
        assert _is_private_ip("1.1.1.1") is False

    def test_non_ip(self):
        assert _is_private_ip("example.com") is False


class TestHtmlToText:
    def test_strips_scripts_and_styles(self):
        html = "<html><script>alert(1)</script><style>body{}</style><p>Hello</p></html>"
        assert "alert" not in _html_to_text(html)
        assert "Hello" in _html_to_text(html)

    def test_extracts_visible_text(self):
        html = "<html><body><h1>Title</h1><p>Para</p></body></html>"
        text = _html_to_text(html)
        assert "Title" in text
        assert "Para" in text

    def test_drops_blank_lines(self):
        html = "<html><body><p>One</p>\n\n\n<p>Two</p></body></html>"
        text = _html_to_text(html)
        assert "\n\n\n" not in text


class TestExtractTitle:
    def test_finds_title(self):
        assert _extract_title("<html><title>Hello</title></html>") == "Hello"

    def test_caps_length(self):
        title = "x" * 500
        assert len(_extract_title(f"<title>{title}</title>")) <= 200

    def test_missing_title(self):
        assert _extract_title("<html></html>") == ""


class TestTruncate:
    def test_short_text_unchanged(self):
        text, truncated = _truncate("hello", max_bytes=100)
        assert text == "hello"
        assert truncated is False

    def test_long_text_truncated(self):
        text = "abc\n" * 1000
        result, truncated = _truncate(text, max_bytes=100)
        assert truncated is True
        assert len(result.encode("utf-8")) <= 100


class TestLRUCache:
    def test_get_missing_returns_none(self):
        cache = _LRUCache(max_entries=3)
        assert cache.get("http://example.com") is None

    def test_put_and_get(self):
        cache = _LRUCache(max_entries=3)
        cache.put("http://example.com", "body")
        assert cache.get("http://example.com") == "body"

    def test_eviction(self):
        cache = _LRUCache(max_entries=2)
        cache.put("a", "A")
        cache.put("b", "B")
        cache.put("c", "C")
        assert cache.get("a") is None
        assert cache.get("b") == "B"
        assert cache.get("c") == "C"

    def test_get_refreshes_lru_order(self):
        cache = _LRUCache(max_entries=2)
        cache.put("a", "A")
        cache.put("b", "B")
        cache.get("a")
        cache.put("c", "C")
        assert cache.get("a") == "A"
        assert cache.get("b") is None


def _mock_response(*, status_code=200, headers=None, body=b"<html><body>OK</body></html>",
                   is_redirect=False, is_permanent_redirect=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {"content-type": "text/html"}
    resp.is_redirect = is_redirect
    resp.is_permanent_redirect = is_permanent_redirect
    resp.encoding = "utf-8"
    resp.iter_content = MagicMock(return_value=iter([body]))
    resp.close = MagicMock()
    return resp


class TestFetchUrlSession:
    def test_session_closed_on_success(self):
        resp = _mock_response()
        session = MagicMock()
        session.get.return_value = resp
        session.headers = {}
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        with patch.object(web_fetch_tool.requests, "Session", return_value=session):
            body, error = web_fetch_tool._fetch_url("http://example.com/path")

        assert error is None
        assert body is not None
        assert "OK" in body
        session.__exit__.assert_called_once()
        resp.close.assert_called()

    def test_session_closed_on_http_error(self):
        resp = _mock_response(status_code=500)
        session = MagicMock()
        session.get.return_value = resp
        session.headers = {}
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        with patch.object(web_fetch_tool.requests, "Session", return_value=session):
            body, error = web_fetch_tool._fetch_url("http://example.com/path")

        assert body is None
        assert error is not None
        assert "500" in error
        session.__exit__.assert_called_once()
        resp.close.assert_called()

    def test_session_closed_on_unexpected_content_type(self):
        resp = _mock_response(headers={"content-type": "application/octet-stream"})
        session = MagicMock()
        session.get.return_value = resp
        session.headers = {}
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        with patch.object(web_fetch_tool.requests, "Session", return_value=session):
            body, error = web_fetch_tool._fetch_url("http://example.com/path")

        assert body is None
        assert error is not None
        session.__exit__.assert_called_once()

    def test_redirect_uses_urljoin_for_relative(self):
        first = _mock_response(
            status_code=302,
            headers={"Location": "/landing"},
            is_redirect=True,
        )
        second = _mock_response(body=b"<html><body>Landing</body></html>")
        session = MagicMock()
        session.get.side_effect = [first, second]
        session.headers = {}
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        with patch.object(web_fetch_tool.requests, "Session", return_value=session):
            body, error = web_fetch_tool._fetch_url("http://example.com/start")

        assert error is None
        assert body is not None and "Landing" in body
        called_urls = [call.args[0] for call in session.get.call_args_list]
        assert called_urls[1] == "http://example.com/landing"


class TestWebFetchTool:
    def test_validate_blocks_localhost(self):
        tool = WebFetchTool()
        result = tool._run("http://localhost/admin")
        assert "localhost" in result

    def test_returns_cached_result(self):
        tool = WebFetchTool()
        _global_cache.put("http://example.com|text=True", "Title: Cached\nbody")
        result = tool._run("http://example.com")
        assert result.startswith("[cached]")

    def test_run_with_mocked_fetch(self):
        tool = WebFetchTool()
        with patch.object(
            web_fetch_tool,
            "_fetch_url",
            return_value=("<html><title>T</title><body><p>Hello</p></body></html>", None),
        ):
            result = tool._run("http://example.com")
        assert "Title: T" in result
        assert "Hello" in result


# ── Section 5: Unified Tool Info & Unified Asset Inventory ───────────

def _make_tool_storage(tools: dict[str, dict]) -> MagicMock:
    storage = MagicMock()
    storage.tools = {
        name: {"name": name, **defn} for name, defn in tools.items()
    }
    storage.list_tools.return_value = {name: defn.get("description", "") for name, defn in tools.items()}
    storage.get_tool.side_effect = lambda n: storage.tools.get(n)
    return storage


def _make_skill_definition(name: str, tools: list[dict], enabled: bool = True) -> MagicMock:
    skill = MagicMock()
    skill.name = name
    skill.description = f"Skill {name}"
    skill.enabled = enabled
    skill.tools = [{"name": t["name"], "description": t.get("description", ""), **t} for t in tools]
    skill.capabilities = [f"tag:{name}"]
    skill.system_prompt_extension = f"# {name} context"
    return skill


def _make_skill_registry(skills: dict[str, MagicMock]) -> MagicMock:
    registry = MagicMock()
    registry.skills = skills
    registry.get_active_tools.return_value = []
    registry.get_skill.side_effect = lambda n: skills.get(n)
    return registry


class TestUnifiedToolInfoFromToolDef:
    def test_basic_fields(self):
        info = UnifiedToolInfo.from_tool_def("my_tool", {
            "description": "does stuff",
            "parameters": [{"name": "x", "type": "str"}],
            "dependencies": ["requests"],
            "usage_guide": "call it",
            "usage_count": 5,
            "tags": ["io", "http"],
        })
        assert info.name == "my_tool"
        assert info.description == "does stuff"
        assert info.layer == LAYER_TOOL
        assert info.source == "global"
        assert info.skill_name is None
        assert info.enabled is True
        assert info.usage_count == 5
        assert "io" in info.tags

    def test_missing_optional_fields_default(self):
        info = UnifiedToolInfo.from_tool_def("t", {"description": "x"})
        assert info.parameters == []
        assert info.dependencies == []
        assert info.tags == []
        assert info.usage_guide == ""

    def test_extra_fields_in_metadata(self):
        info = UnifiedToolInfo.from_tool_def("t", {"description": "x", "custom_key": "val"})
        assert info.metadata.get("custom_key") == "val"


class TestUnifiedToolInfoFromSkillToolDef:
    def test_layer_and_source(self):
        info = UnifiedToolInfo.from_skill_tool_def(
            {"name": "db_query", "description": "query db"},
            skill_name="database",
            skill_enabled=True,
            skill_tags=["sql"],
            system_prompt_extension="use SQL",
        )
        assert info.name == "db_query"
        assert info.layer == LAYER_SKILL_TOOL
        assert info.source == "skill:database"
        assert info.skill_name == "database"
        assert info.tags == ["sql"]
        assert info.system_prompt_extension == "use SQL"
        assert info.enabled is True

    def test_disabled_skill(self):
        info = UnifiedToolInfo.from_skill_tool_def(
            {"name": "x"},
            skill_name="mypkg",
            skill_enabled=False,
        )
        assert info.enabled is False

    def test_to_summary(self):
        info = UnifiedToolInfo.from_tool_def("t", {"description": "d"})
        s = info.to_summary()
        assert s["name"] == "t"
        assert "layer" in s
        assert "source" in s


class TestUnifiedAssetInventoryEmpty:
    def setup_method(self):
        self.inv = UnifiedAssetInventory()

    def test_list_all_empty(self):
        assert self.inv.list_all() == []

    def test_get_missing(self):
        assert self.inv.get("anything") is None

    def test_find_empty(self):
        assert self.inv.find() == []

    def test_enabled_names_empty(self):
        assert self.inv.enabled_names() == []

    def test_build_langchain_tools_empty(self):
        with patch("core.assets.tools.unified_tool_inventory.create_dynamic_tool") as mock_create:
            tools = self.inv.build_langchain_tools()
            assert tools == []
            mock_create.assert_not_called()

    def test_summary_zeros(self):
        s = self.inv.summary()
        assert s["total"] == 0
        assert s["direct_tools"] == 0
        assert s["skill_tools"] == 0


class TestUnifiedAssetInventoryToolsOnly:
    def setup_method(self):
        self.storage = _make_tool_storage({
            "alpha": {"description": "alpha tool", "tags": ["io"]},
            "beta": {"description": "beta tool", "tags": []},
        })
        self.inv = UnifiedAssetInventory(tool_storage=self.storage)

    def test_list_all_includes_all_tools(self):
        items = self.inv.list_all()
        names = {i.name for i in items}
        assert {"alpha", "beta"} == names

    def test_all_are_layer_tool(self):
        for item in self.inv.list_all():
            assert item.layer == LAYER_TOOL
            assert item.source == "global"

    def test_get_existing(self):
        info = self.inv.get("alpha")
        assert info is not None
        assert info.name == "alpha"

    def test_get_missing(self):
        assert self.inv.get("nope") is None

    def test_find_by_query(self):
        results = self.inv.find(query="alpha")
        assert len(results) == 1
        assert results[0].name == "alpha"

    def test_find_by_query_case_insensitive(self):
        results = self.inv.find(query="ALPHA")
        assert len(results) == 1

    def test_find_by_layer_tool(self):
        results = self.inv.find(layer=LAYER_TOOL)
        assert len(results) == 2

    def test_find_by_layer_skill_tool_empty(self):
        results = self.inv.find(layer=LAYER_SKILL_TOOL)
        assert results == []

    def test_find_by_tags(self):
        results = self.inv.find(tags=["io"])
        assert len(results) == 1
        assert results[0].name == "alpha"

    def test_find_by_nonexistent_tag_empty(self):
        assert self.inv.find(tags=["nonexistent"]) == []

    def test_enabled_names(self):
        names = self.inv.enabled_names()
        assert set(names) == {"alpha", "beta"}

    def test_list_by_source(self):
        items = self.inv.list_by_source("global")
        assert len(items) == 2

    def test_summary_counts(self):
        s = self.inv.summary()
        assert s["total"] == 2
        assert s["direct_tools"] == 2
        assert s["skill_tools"] == 0

    def test_build_langchain_tools(self):
        with patch("core.assets.tools.unified_tool_inventory.create_dynamic_tool", side_effect=lambda t: MagicMock(name=t["name"])) as mock_create:
            tools = self.inv.build_langchain_tools()
        assert len(tools) == 2

    def test_build_named_subset(self):
        fake_tool = MagicMock()
        fake_tool.name = "alpha"
        with patch("core.assets.tools.unified_tool_inventory.create_dynamic_tool", return_value=fake_tool):
            tools = self.inv.build_langchain_tools(names=["alpha"])
        assert len(tools) == 1


class TestUnifiedAssetInventorySkillsOnly:
    def setup_method(self):
        skill_a = _make_skill_definition("web_search", [
            {"name": "search_web", "description": "search the web"},
            {"name": "fetch_page", "description": "fetch a URL"},
        ])
        skill_b = _make_skill_definition("database", [
            {"name": "run_query", "description": "run SQL"},
        ], enabled=False)
        self.registry = _make_skill_registry({"web_search": skill_a, "database": skill_b})
        self.inv = UnifiedAssetInventory(skill_registry=self.registry)

    def test_list_all_includes_all_skill_tools(self):
        items = self.inv.list_all()
        names = {i.name for i in items}
        assert names == {"search_web", "fetch_page", "run_query"}

    def test_skill_tool_layer(self):
        for item in self.inv.list_all():
            assert item.layer == LAYER_SKILL_TOOL

    def test_skill_tool_source_prefix(self):
        info = self.inv.get("search_web")
        assert info is not None
        assert info.source == "skill:web_search"
        assert info.skill_name == "web_search"

    def test_disabled_skill_tool_is_disabled(self):
        info = self.inv.get("run_query")
        assert info is not None
        assert info.enabled is False

    def test_enabled_names_excludes_disabled(self):
        names = self.inv.enabled_names()
        assert "run_query" not in names
        assert "search_web" in names

    def test_find_by_layer_skill_tool(self):
        results = self.inv.find(layer=LAYER_SKILL_TOOL)
        assert len(results) == 3

    def test_summary_skill_groups(self):
        s = self.inv.summary()
        assert s["skill_tools"] == 3
        assert set(s["skill_groups"]) == {"web_search", "database"}

    def test_list_by_skill_source(self):
        items = self.inv.list_by_source("skill:web_search")
        assert len(items) == 2


class TestUnifiedAssetInventoryMerged:
    def setup_method(self):
        self.storage = _make_tool_storage({
            "alpha": {"description": "direct alpha"},
            "shared_name": {"description": "direct version"},
        })
        skill = _make_skill_definition("mypkg", [
            {"name": "skill_tool_a", "description": "skill a"},
            {"name": "shared_name", "description": "skill version (should be shadowed)"},
        ])
        self.registry = _make_skill_registry({"mypkg": skill})
        self.inv = UnifiedAssetInventory(
            tool_storage=self.storage, skill_registry=self.registry
        )

    def test_direct_tools_win_on_collision(self):
        info = self.inv.get("shared_name")
        assert info is not None
        assert info.layer == LAYER_TOOL
        assert info.description == "direct version"

    def test_total_count_deduped(self):
        items = self.inv.list_all()
        names = [i.name for i in items]
        assert names.count("shared_name") == 1
        assert len(items) == 3

    def test_summary_totals(self):
        s = self.inv.summary()
        assert s["total"] == 3
        assert s["direct_tools"] == 2
        assert s["skill_tools"] == 1
