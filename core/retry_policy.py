"""Structured retry policy for resilient operations.

Inspired by OpenClaw's per-integration retry runners with configurable
callbacks for shouldRetry, retryAfterMs, and onRetry.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True


@dataclass
class RetryAttemptInfo:
    """Context passed to retry callbacks."""

    attempt: int
    max_attempts: int
    delay_seconds: float
    error: Exception
    label: str


@dataclass
class RetryPolicy:
    """Configurable retry policy with callbacks.

    Usage::

        policy = RetryPolicy(
            config=RetryConfig(max_attempts=3),
            should_retry=lambda err: isinstance(err, ConnectionError),
            retry_after_seconds=lambda err: getattr(err, 'retry_after', None),
            on_retry=lambda info: logger.warning(f"Retrying {info.label}"),
        )
        result = policy.execute(some_function, label="fetch_data")
    """

    config: RetryConfig = field(default_factory=RetryConfig)
    should_retry: Callable[[Exception], bool] | None = None
    retry_after_seconds: Callable[[Exception], float | None] | None = None
    on_retry: Callable[[RetryAttemptInfo], None] | None = None

    def execute(self, fn: Callable[..., T], *args: Any, label: str = "operation", **kwargs: Any) -> T:
        """Execute a function with retry logic."""
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_error = exc
                if attempt >= self.config.max_attempts:
                    break
                if self.should_retry and not self.should_retry(exc):
                    break
                delay = self._compute_delay(attempt, exc)
                if self.on_retry:
                    self.on_retry(
                        RetryAttemptInfo(
                            attempt=attempt,
                            max_attempts=self.config.max_attempts,
                            delay_seconds=delay,
                            error=exc,
                            label=label,
                        )
                    )
                if delay > 0:
                    time.sleep(delay)
        raise last_error  # type: ignore[misc]

    def _compute_delay(self, attempt: int, exc: Exception) -> float:
        if self.retry_after_seconds:
            server_delay = self.retry_after_seconds(exc)
            if server_delay is not None and server_delay > 0:
                return min(server_delay, self.config.max_delay_seconds)

        delay = self.config.base_delay_seconds * (self.config.exponential_base ** (attempt - 1))
        delay = min(delay, self.config.max_delay_seconds)
        if self.config.jitter:
            import random

            delay *= 0.5 + random.random() * 0.5  # noqa: S311
        return delay


def create_default_retry_policy(
    *,
    max_attempts: int = 3,
    label: str = "default",
) -> RetryPolicy:
    """Create a retry policy suitable for general network operations."""
    return RetryPolicy(
        config=RetryConfig(max_attempts=max_attempts),
        should_retry=lambda exc: isinstance(exc, (ConnectionError, TimeoutError, OSError)),
        on_retry=lambda info: logger.warning(
            "%s: retry %d/%d in %.1fs — %s",
            info.label,
            info.attempt,
            info.max_attempts - 1,
            info.delay_seconds,
            type(info.error).__name__,
        ),
    )
