"""Tests for structured retry policy."""

from __future__ import annotations

import pytest

from core.retry_policy import RetryAttemptInfo, RetryConfig, RetryPolicy, create_default_retry_policy


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
