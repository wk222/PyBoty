"""API key loading for web and API entrypoints."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def load_api_keys_from_env() -> dict[str, list[str]]:
    """Parse ``PYBOT_API_KEYS`` or optionally allow dev-key for local development.

    Format: ``key1:scope1,scope2;key2:*``

    Set ``PYBOT_ALLOW_DEV_KEY=1`` to enable the legacy ``dev-key:*`` fallback when
    ``PYBOT_API_KEYS`` is unset (local development only — never in production).
    """
    raw = os.environ.get("PYBOT_API_KEYS", "").strip()
    keys: dict[str, list[str]] = {}
    if raw:
        for pair in raw.split(";"):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            key, scopes_raw = pair.split(":", 1)
            key = key.strip()
            if not key:
                continue
            scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()]
            keys[key] = scopes or ["*"]
        return keys

    if os.environ.get("PYBOT_ALLOW_DEV_KEY", "").strip().lower() in _TRUTHY:
        logger.warning(
            "PYBOT_ALLOW_DEV_KEY is enabled — using insecure default dev-key. "
            "Do not use in production."
        )
        return {"dev-key": ["*"]}

    logger.warning(
        "No PYBOT_API_KEYS configured; protected endpoints require authentication."
    )
    return keys


def debug_errors_enabled() -> bool:
    return os.environ.get("PYBOT_DEBUG_ERRORS", "").strip().lower() in _TRUTHY
