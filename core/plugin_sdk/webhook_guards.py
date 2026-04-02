"""Webhook Request Guards for PyBoty Plugin SDK.

Provides utilities to validate incoming webhook requests from external services
(like GitHub, Stripe, Discord) before they reach the plugin's business logic.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

logger = logging.getLogger(__name__)


class WebhookValidationError(Exception):
    """Raised when a webhook request fails validation."""
    pass


def verify_hmac_signature(
    payload: bytes,
    signature: str,
    secret: str,
    hash_alg: str = "sha256",
    header_prefix: str = "",
) -> bool:
    """Verify an HMAC signature commonly used in webhooks."""
    if not signature or not secret:
        return False

    if header_prefix and signature.startswith(header_prefix):
        signature = signature[len(header_prefix):]

    try:
        mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=getattr(hashlib, hash_alg))
        expected_signature = mac.hexdigest()
        return hmac.compare_digest(expected_signature, signature)
    except Exception as exc:
        logger.warning("HMAC verification failed: %s", exc)
        return False


def require_github_signature(secret: str, payload: bytes, signature_header: str) -> None:
    """Guard for GitHub Webhooks."""
    if not verify_hmac_signature(payload, signature_header, secret, hash_alg="sha256", header_prefix="sha256="):
        raise WebhookValidationError("Invalid GitHub webhook signature")


def require_discord_signature(public_key: str, signature: str, timestamp: str, body: bytes) -> None:
    """Guard for Discord Interactions (requires PyNaCl)."""
    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError
    except ImportError:
        logger.error("PyNaCl is required for Discord signature verification.")
        raise WebhookValidationError("Server missing crypto dependencies")

    try:
        verify_key = VerifyKey(bytes.fromhex(public_key))
        verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
    except (BadSignatureError, ValueError):
        raise WebhookValidationError("Invalid Discord interaction signature")
