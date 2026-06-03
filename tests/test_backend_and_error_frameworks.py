"""Tests for Backends, Error handling frameworks, Evaluations, and Retry policies.

Consolidates:
1. test_backend_errors.py
2. test_backend_factory.py
3. test_errors.py
4. test_eval_framework.py
5. test_retry_policy.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

# Core system backend protocol imports
from core.systems.runtime.backend_protocol import (
    CompositeBackend,
    EditResult,
    LocalFilesystemBackend,
    WriteResult,
    BackendFactory,
    resolve_backend,
)

# Core system centralized error imports
from core.systems.runtime.errors import (
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

# Core evaluation framework imports
from core.systems.eval.eval_framework import EvalFramework
from core.systems.eval.eval_models import TestCase as EvalTestCase

# Core retry policies
from core.systems.runtime.retry_policy import (
    RetryAttemptInfo,
    RetryConfig,
    RetryPolicy,
    create_default_retry_policy,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def backend(tmp_path):
    return LocalFilesystemBackend(root_dir=str(tmp_path))


# ── Section 1: Backend Protocols & File Operation Results ─────────────

class TestWriteResult:
    def test_write_success(self, backend, tmp_path):
        result = backend.write("hello.txt", "world")
        assert isinstance(result, WriteResult)
        assert result.error is None
        assert result.path is not None
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "world"

    def test_write_creates_subdirs(self, backend, tmp_path):
        result = backend.write("sub/dir/file.txt", "nested")
        assert result.error is None
        assert (tmp_path / "sub" / "dir" / "file.txt").exists()

    def test_write_permission_denied_on_traversal(self, backend):
        result = backend.write("../../escape.txt", "bad")
        assert result.error == "permission_denied"

    def test_write_overwrites_existing(self, backend, tmp_path):
        (tmp_path / "f.txt").write_text("old", encoding="utf-8")
        result = backend.write("f.txt", "new")
        assert result.error is None
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "new"


class TestEditResult:
    def test_edit_success(self, backend, tmp_path):
        (tmp_path / "f.txt").write_text("hello world", encoding="utf-8")
        result = backend.edit("f.txt", "hello", "goodbye")
        assert isinstance(result, EditResult)
        assert result.error is None
        assert result.old_found is True
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "goodbye world"

    def test_edit_file_not_found(self, backend):
        result = backend.edit("nope.txt", "a", "b")
        assert result.error == "file_not_found"
        assert result.old_found is False

    def test_edit_is_directory(self, backend, tmp_path):
        (tmp_path / "adir").mkdir()
        result = backend.edit("adir", "a", "b")
        assert result.error == "is_directory"

    def test_edit_old_not_found(self, backend, tmp_path):
        (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
        result = backend.edit("f.txt", "missing", "replacement")
        assert result.error is None
        assert result.old_found is False

    def test_edit_permission_denied_on_traversal(self, backend):
        result = backend.edit("../../escape.txt", "a", "b")
        assert result.error == "permission_denied"


class TestCompositeBackendStructuredResults:
    def test_composite_write_returns_write_result(self, tmp_path):
        backend = LocalFilesystemBackend(root_dir=str(tmp_path))
        composite = CompositeBackend(default=backend)
        result = composite.write("test.txt", "hello")
        assert isinstance(result, WriteResult)
        assert result.error is None

    def test_composite_edit_returns_edit_result(self, tmp_path):
        backend = LocalFilesystemBackend(root_dir=str(tmp_path))
        (tmp_path / "test.txt").write_text("abc", encoding="utf-8")
        composite = CompositeBackend(default=backend)
        result = composite.edit("test.txt", "abc", "xyz")
        assert isinstance(result, EditResult)
        assert result.old_found is True


# ── Section 2: Backend Factory and Resolvers ──────────────────────────

class TestResolveBackend:
    def test_instance_returned_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = LocalFilesystemBackend(root_dir=tmp)
            resolved = resolve_backend(backend)
            assert resolved is backend

    def test_factory_called_with_args(self):
        with tempfile.TemporaryDirectory() as tmp:

            def factory(root: str) -> LocalFilesystemBackend:
                return LocalFilesystemBackend(root_dir=root)

            resolved = resolve_backend(factory, tmp)
            assert isinstance(resolved, LocalFilesystemBackend)
            assert resolved.root_dir == tmp

    def test_factory_called_with_kwargs(self):
        with tempfile.TemporaryDirectory() as tmp:

            def factory(*, root_dir: str) -> LocalFilesystemBackend:
                return LocalFilesystemBackend(root_dir=root_dir)

            resolved = resolve_backend(factory, root_dir=tmp)
            assert isinstance(resolved, LocalFilesystemBackend)

    def test_backend_factory_type_is_callable(self):
        assert callable(BackendFactory)


# ── Section 3: Centralized Error Framework ────────────────────────────

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


# ── Section 4: Evaluation Framework ───────────────────────────────────

def test_eval_framework_saves_and_loads_suite(temp_paths):
    framework = EvalFramework(str(temp_paths.workspace_dir))
    cases = [
        EvalTestCase(
            name="math",
            input_prompt="1+1等于几？",
            expected_contains=["2"],
            min_score=0.4,
        )
    ]

    framework.save_test_suite("smoke", cases)
    loaded = framework.load_test_suite("smoke")

    assert len(loaded) == 1
    assert loaded[0].name == "math"
    assert loaded[0].expected_contains == ["2"]
    assert (temp_paths.workspace_dir / "tests" / "smoke.json").exists()


def test_eval_framework_blocks_suite_path_escape(temp_paths):
    framework = EvalFramework(str(temp_paths.workspace_dir))

    with pytest.raises(ValueError, match="Invalid suite name"):
        framework.save_test_suite("../escape", [])


def test_eval_framework_runs_suite_and_persists_report(temp_paths):
    framework = EvalFramework(str(temp_paths.workspace_dir))
    framework.set_agent_callback(lambda prompt: "答案是 2，而且代码是 print('hi')")

    result = framework.run_test_suite(
        [
            EvalTestCase(
                name="code",
                input_prompt="写代码回答 1+1 等于几",
                expected_contains=["2", "print"],
                min_score=0.4,
            )
        ]
    )

    assert result["passed"] == 1
    report_files = list((temp_paths.workspace_dir / "test_results").glob("eval_*.json"))
    assert report_files


def test_eval_framework_falls_back_to_heuristics_when_llm_eval_is_invalid(temp_paths):
    framework = EvalFramework(str(temp_paths.workspace_dir))
    framework.set_agent_callback(lambda prompt: "not-json")

    result = framework.eval_response("写一个函数", "def hello():\n    return 'hi'")

    assert result.test_name == "heuristic_eval"
    assert result.score > 0


# ── Section 5: Retry Policies ─────────────────────────────────────────

class TestRetryPolicy:
    def test_succeeds_first_try(self):
        policy = RetryPolicy(config=RetryConfig(max_attempts=3))
        result = policy.execute(lambda: 42, label="test")
        assert result == 42

    def test_retries_on_failure(self):
        call_count = 0

        def failing_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("down")
            return "ok"

        policy = RetryPolicy(
            config=RetryConfig(max_attempts=3, base_delay_seconds=0, jitter=False),
        )
        result = policy.execute(failing_then_success, label="test")
        assert result == "ok"
        assert call_count == 3

    def test_exhausts_attempts(self):
        policy = RetryPolicy(
            config=RetryConfig(max_attempts=2, base_delay_seconds=0, jitter=False),
        )
        with pytest.raises(ValueError, match="always fails"):
            policy.execute(lambda: (_ for _ in ()).throw(ValueError("always fails")), label="test")

    def test_should_retry_callback(self):
        policy = RetryPolicy(
            config=RetryConfig(max_attempts=3, base_delay_seconds=0),
            should_retry=lambda exc: isinstance(exc, ConnectionError),
        )
        with pytest.raises(ValueError):
            policy.execute(
                lambda: (_ for _ in ()).throw(ValueError("not retryable")),
                label="test",
            )

    def test_on_retry_callback(self):
        infos: list[RetryAttemptInfo] = []
        call_count = 0

        def fail_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("temporary")
            return "ok"

        policy = RetryPolicy(
            config=RetryConfig(max_attempts=3, base_delay_seconds=0, jitter=False),
            on_retry=lambda info: infos.append(info),
        )
        policy.execute(fail_once, label="my_op")
        assert len(infos) == 1
        assert infos[0].label == "my_op"
        assert infos[0].attempt == 1

    def test_retry_after_seconds_callback(self):
        call_count = 0

        def fail_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("retry me")
            return "ok"

        policy = RetryPolicy(
            config=RetryConfig(max_attempts=2, base_delay_seconds=0, jitter=False),
            retry_after_seconds=lambda exc: 0.001,
        )
        result = policy.execute(fail_once, label="test")
        assert result == "ok"


class TestCreateDefaultPolicy:
    def test_creates_policy(self):
        policy = create_default_retry_policy(max_attempts=2)
        assert policy.config.max_attempts == 2
        assert policy.should_retry is not None

    def test_retries_connection_errors(self):
        assert policy_retries(ConnectionError("down"))
        assert policy_retries(TimeoutError("slow"))
        assert policy_retries(OSError("network"))
        assert not policy_retries(ValueError("bad"))


def policy_retries(exc: Exception) -> bool:
    policy = create_default_retry_policy()
    return policy.should_retry(exc) if policy.should_retry else False
