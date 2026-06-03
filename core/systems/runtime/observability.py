"""Observability layer — tracing callbacks and integration.

Supports:
  - LangSmith (via LANGCHAIN_TRACING_V2 env var — zero-code)
  - Langfuse (via langfuse callback handler)
  - Console (structured logging to stdout)
  - None (disabled)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ObservabilityConfig:
    """Configuration for observability backends."""

    backend: str = "none"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None
    log_level: str = "INFO"


def _setup_langsmith() -> list[Any]:
    """LangSmith is configured entirely via env vars — just verify they're set."""
    api_key = os.environ.get("LANGCHAIN_API_KEY")
    tracing = os.environ.get("LANGCHAIN_TRACING_V2", "").lower()

    if tracing == "true" and api_key:
        logger.info("LangSmith tracing enabled (LANGCHAIN_TRACING_V2=true)")
        return []
    if tracing == "true" and not api_key:
        logger.warning("LANGCHAIN_TRACING_V2=true but LANGCHAIN_API_KEY not set")
    return []


def _setup_langfuse(config: ObservabilityConfig) -> list[Any]:
    """Initialize Langfuse callback handler."""
    try:
        from langfuse.callback import CallbackHandler as LangfuseCallbackHandler
    except ImportError:
        logger.warning("Langfuse not installed. Install with: pip install langfuse")
        return []

    kwargs: dict[str, Any] = {}
    pk = config.langfuse_public_key or os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = config.langfuse_secret_key or os.environ.get("LANGFUSE_SECRET_KEY")
    host = config.langfuse_host or os.environ.get("LANGFUSE_HOST")

    if pk:
        kwargs["public_key"] = pk
    if sk:
        kwargs["secret_key"] = sk
    if host:
        kwargs["host"] = host

    if not pk or not sk:
        logger.warning("Langfuse keys not configured — tracing disabled")
        return []

    handler = LangfuseCallbackHandler(**kwargs)
    logger.info("Langfuse tracing enabled")
    return [handler]


def _setup_console() -> list[Any]:
    """Console-based tracing via LangChain's verbose logging."""
    from langchain_core.callbacks import StdOutCallbackHandler

    logger.info("Console tracing enabled (stdout)")
    return [StdOutCallbackHandler()]


def setup_tracing(config: ObservabilityConfig | None = None) -> list[Any]:
    """Initialize tracing and return callback handlers to pass to LLM/Agent.

    Returns:
        List of LangChain-compatible callback handlers.
    """
    if config is None:
        config = ObservabilityConfig()

    backend = config.backend.lower()
    if backend == "none" or backend == "disabled":
        return []

    if backend == "langsmith":
        return _setup_langsmith()
    if backend == "langfuse":
        return _setup_langfuse(config)
    if backend == "console":
        return _setup_console()

    logger.warning("Unknown observability backend: %r, using none", backend)
    return []


def get_observability_config_from_dict(cfg: dict[str, Any]) -> ObservabilityConfig:
    """Parse observability config from a dict (e.g. from config.json)."""
    obs = cfg.get("observability", {})
    return ObservabilityConfig(
        backend=obs.get("backend", "none"),
        langfuse_public_key=obs.get("langfuse_public_key"),
        langfuse_secret_key=obs.get("langfuse_secret_key"),
        langfuse_host=obs.get("langfuse_host"),
        log_level=obs.get("log_level", "INFO"),
    )
