"""External content safety wrapper — boundary tags + injection detection.

When processing untrusted external content (webhook payloads, emails,
scraped web pages, user-uploaded files), this module:

  1. Wraps the content with **random boundary tags** so the LLM sees a clear
     delimiter between trusted system instructions and untrusted input.
  2. Detects **suspicious patterns** (prompt injection attempts, instruction
     overrides, role confusion) and flags or redacts them.
  3. Provides a ``sanitise`` convenience function for one-call usage.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_SUSPICIOUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system:\s*you\s+are", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"\bDAN\b.*\bmode\b", re.IGNORECASE),
    re.compile(r"pretend\s+you\s+are", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"new\s+instructions?:", re.IGNORECASE),
    re.compile(r"override\s+(system|instructions?)", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all\s+rules)", re.IGNORECASE),
    re.compile(r"\[INST\]|\[/INST\]", re.IGNORECASE),
    re.compile(r"<\|system\|>|<\|user\|>|<\|assistant\|>", re.IGNORECASE),
]


@dataclass
class ScanResult:
    """Result of scanning external content for suspicious patterns."""
    is_suspicious: bool = False
    detected_patterns: list[str] = field(default_factory=list)
    original_length: int = 0
    wrapped_content: str = ""


def _generate_boundary() -> str:
    """Generate a random boundary tag (16 hex chars)."""
    return secrets.token_hex(8)


def detect_suspicious_patterns(text: str) -> list[str]:
    """Return list of matched suspicious pattern descriptions."""
    found: list[str] = []
    for pattern in _SUSPICIOUS_PATTERNS:
        match = pattern.search(text)
        if match:
            found.append(f"pattern={pattern.pattern!r} match={match.group()!r}")
    return found


def wrap_external_content(
    content: str,
    *,
    source: str = "external",
    boundary: str | None = None,
    max_length: int = 100_000,
) -> str:
    """Wrap untrusted content with random boundary tags.

    The boundary tag is randomly generated per call (unless explicitly provided)
    to prevent adversaries from predicting and crafting content that spans
    the boundary.
    """
    if boundary is None:
        boundary = _generate_boundary()

    if len(content) > max_length:
        content = content[:max_length] + f"\n... [truncated at {max_length} chars]"

    return (
        f"<external-content source=\"{source}\" boundary=\"{boundary}\">\n"
        f"--- BEGIN UNTRUSTED CONTENT ({boundary}) ---\n"
        f"{content}\n"
        f"--- END UNTRUSTED CONTENT ({boundary}) ---\n"
        f"</external-content>"
    )


def redact_suspicious(text: str, replacement: str = "[REDACTED: suspicious pattern]") -> str:
    """Replace suspicious injection patterns with a redaction marker."""
    result = text
    for pattern in _SUSPICIOUS_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def sanitise(
    content: str,
    *,
    source: str = "external",
    max_length: int = 100_000,
    redact: bool = True,
    scan: bool = True,
) -> ScanResult:
    """Full pipeline: scan → optionally redact → wrap.

    Returns a ``ScanResult`` with the wrapped content and scan metadata.
    """
    result = ScanResult(original_length=len(content))

    if scan:
        patterns = detect_suspicious_patterns(content)
        if patterns:
            result.is_suspicious = True
            result.detected_patterns = patterns
            logger.warning(
                "Suspicious patterns in %s content (%d chars): %s",
                source, len(content), patterns,
            )

    processed = content
    if redact and result.is_suspicious:
        processed = redact_suspicious(processed)

    result.wrapped_content = wrap_external_content(processed, source=source, max_length=max_length)
    return result
