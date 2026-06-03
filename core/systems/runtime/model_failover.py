"""LLM model failover wrapper.

Wraps a primary BaseChatModel with a chain of fallback models.
On transient failures (rate limit, server error, timeout), automatically
switches to the next available model in the chain.

Uses LangChain's RunnableWithFallbacks pattern for automatic model rotation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass
class FailoverAttempt:
    """Record of a failover attempt for diagnostics."""

    provider: str
    model_name: str
    error: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class FailoverStats:
    """Accumulated failover statistics."""

    total_calls: int = 0
    primary_successes: int = 0
    fallback_successes: int = 0
    total_failures: int = 0
    attempts: list[FailoverAttempt] = field(default_factory=list)

    @property
    def fallback_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.fallback_successes / self.total_calls


def _is_transient_error(exc: Exception) -> bool:
    """Determine if an exception is transient and warrants failover."""
    exc_type = type(exc).__name__.lower()

    if "ratelimit" in exc_type or "rate_limit" in exc_type:
        return True
    if "timeout" in exc_type:
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True

    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status and int(status) in _TRANSIENT_STATUS_CODES:
        return True

    err_str = str(exc).lower()
    if any(kw in err_str for kw in ("rate limit", "429", "503", "502", "timeout", "overloaded")):
        return True

    return False


def _get_retry_after(exc: Exception) -> float | None:
    """Extract retry-after hint from exception if available."""
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        try:
            return float(retry_after)
        except (ValueError, TypeError):
            pass

    headers = getattr(exc, "headers", None) or getattr(exc, "response_headers", None)
    if isinstance(headers, dict):
        val = headers.get("retry-after") or headers.get("Retry-After")
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass

    return None


class ChatModelWithFailover(BaseChatModel):
    """A BaseChatModel wrapper that falls through to backup models on transient errors.

    Usage::

        from core.systems.runtime.model_resolver import resolve_model
        primary = resolve_model("openai:gpt-4o").model
        fallbacks = [
            resolve_model("anthropic:claude-sonnet-4-20250514").model,
            resolve_model("google:gemini-2.0-flash").model,
        ]
        model = ChatModelWithFailover(
            primary=primary,
            fallbacks=fallbacks,
        )
    """

    primary: Any
    fallbacks: list[Any] = []
    max_retries_per_model: int = 1
    retry_delay_seconds: float = 1.0
    stats: FailoverStats = FailoverStats()

    @property
    def _llm_type(self) -> str:
        return "chat-model-with-failover"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "primary": self.primary._identifying_params,
            "fallback_count": len(self.fallbacks),
        }

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.stats.total_calls += 1
        all_models = [self.primary, *self.fallbacks]

        for idx, model in enumerate(all_models):
            model_name = getattr(model, "model_name", getattr(model, "model", f"model_{idx}"))
            provider = type(model).__name__

            for attempt in range(1, self.max_retries_per_model + 1):
                try:
                    result = model._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
                    if idx == 0:
                        self.stats.primary_successes += 1
                    else:
                        self.stats.fallback_successes += 1
                        logger.info("Failover success: %s/%s (attempt %d)", provider, model_name, attempt)
                    return result
                except Exception as exc:
                    self.stats.attempts.append(
                        FailoverAttempt(
                            provider=provider,
                            model_name=str(model_name),
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )

                    if not _is_transient_error(exc):
                        logger.warning(
                            "Non-transient error from %s/%s, skipping remaining retries: %s",
                            provider,
                            model_name,
                            exc,
                        )
                        break

                    logger.warning(
                        "Transient error from %s/%s (attempt %d/%d): %s",
                        provider,
                        model_name,
                        attempt,
                        self.max_retries_per_model,
                        exc,
                    )

                    if attempt < self.max_retries_per_model:
                        delay = _get_retry_after(exc) or self.retry_delay_seconds
                        time.sleep(min(delay, 30.0))

        self.stats.total_failures += 1
        last = self.stats.attempts[-1] if self.stats.attempts else None
        error_msg = last.error if last else "unknown"
        raise RuntimeError(f"All {len(all_models)} models exhausted. Last error: {error_msg}")

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> ChatModelWithFailover:
        """Bind tools to all models in the failover chain."""
        bound_primary = self.primary.bind_tools(tools, **kwargs)
        bound_fallbacks = []
        for fb in self.fallbacks:
            try:
                bound_fallbacks.append(fb.bind_tools(tools, **kwargs))
            except Exception:
                logger.warning("Fallback model %s does not support bind_tools, skipping", type(fb).__name__)
                bound_fallbacks.append(fb)

        return ChatModelWithFailover(
            primary=bound_primary,
            fallbacks=bound_fallbacks,
            max_retries_per_model=self.max_retries_per_model,
            retry_delay_seconds=self.retry_delay_seconds,
        )

    def get_stats(self) -> dict[str, Any]:
        """Return failover statistics for observability."""
        return {
            "total_calls": self.stats.total_calls,
            "primary_successes": self.stats.primary_successes,
            "fallback_successes": self.stats.fallback_successes,
            "total_failures": self.stats.total_failures,
            "fallback_rate": self.stats.fallback_rate,
            "recent_errors": [
                {"provider": a.provider, "model": a.model_name, "error": a.error} for a in self.stats.attempts[-10:]
            ],
        }


def create_failover_model(
    primary: BaseChatModel,
    fallbacks: list[BaseChatModel] | None = None,
    *,
    max_retries: int = 1,
    retry_delay: float = 1.0,
) -> BaseChatModel:
    """Factory: wrap a primary model with optional fallbacks.

    If no fallbacks are provided, returns the primary model unchanged.
    """
    if not fallbacks:
        return primary
    return ChatModelWithFailover(
        primary=primary,
        fallbacks=fallbacks,
        max_retries_per_model=max_retries,
        retry_delay_seconds=retry_delay,
    )
