from __future__ import annotations

from core.systems.runtime import (
    RetryConfig,
    RetryPolicy,
    ToolInputError,
    extract_error_code,
    get_pybot_version,
    interpolate_placeholders,
    validate_path,
)
from core.systems.runtime.version import get_pybot_version as legacy_get_pybot_version


def test_structured_runtime_exports_work_after_batch0_move():
    assert validate_path("workspace/demo.txt") == "/workspace/demo.txt"
    assert interpolate_placeholders("hello {name}", {"name": "pybot"}) == "hello pybot"
    assert extract_error_code(ToolInputError("bad input")) == "invalid_input"
    policy = RetryPolicy(config=RetryConfig(max_attempts=1))
    assert policy.config.max_attempts == 1


def test_legacy_stub_and_new_runtime_export_resolve_same_version():
    assert get_pybot_version() == legacy_get_pybot_version()
