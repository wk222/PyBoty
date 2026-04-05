"""URL extraction and SSRF protection.

Provides:
  - **extract_urls**: Extract URLs from message text (plain + Markdown).
  - **is_blocked_host**: SSRF protection — block localhost, private IPs,
    link-local, and configurable deny-list hosts.
  - **safe_urls**: Convenience that extracts and filters in one call.

Usage::

    from core.systems.integration.link_safety import safe_urls

    urls = safe_urls("Check https://example.com and http://169.254.1.1/admin")
    # urls == ["https://example.com"]   (169.254.x.x is blocked)
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(
    r"https?://[^\s\)\]>\"\',;]+",
    re.IGNORECASE,
)

_MARKDOWN_LINK = re.compile(
    r"\[(?:[^\]]*)\]\((https?://[^\s\)]+)\)",
    re.IGNORECASE,
)

_BLOCKED_HOSTNAMES: set[str] = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google",
    "169.254.169.254",
}

_BLOCKED_SUFFIXES: tuple[str, ...] = (
    ".internal",
    ".local",
    ".localhost",
)

_PUBLIC_HOST_LABEL = re.compile(r"^[a-z0-9-]{1,63}$", re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    """Extract all HTTP/HTTPS URLs from text (plain text + Markdown links)."""
    seen: set[str] = set()
    urls: list[str] = []

    for match in _MARKDOWN_LINK.finditer(text):
        url = _clean_url(match.group(1))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    for match in _URL_PATTERN.finditer(text):
        url = _clean_url(match.group(0))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


def _clean_url(url: str) -> str:
    """Strip trailing punctuation that may have been captured."""
    while url and url[-1] in (".", ",", ";", ")", "]", ">", "'", '"'):
        url = url[:-1]
    return url


def is_blocked_host(hostname: str) -> bool:
    """Return True if *hostname* should be blocked (SSRF protection).

    Blocks:
      - ``localhost`` and variants
      - Private IP ranges (10.x, 172.16-31.x, 192.168.x)
      - Link-local (169.254.x.x, fe80::)
      - Loopback (127.x.x.x, ::1)
      - Cloud metadata endpoints (169.254.169.254)
      - ``.internal`` / ``.local`` suffixes
    """
    hostname = hostname.lower().strip()

    if hostname in _BLOCKED_HOSTNAMES:
        return True

    if any(hostname.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        return True

    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return True
    except ValueError:
        pass

    labels = [label for label in hostname.split(".") if label]
    if len(labels) >= 2 and all(_PUBLIC_HOST_LABEL.fullmatch(label) for label in labels):
        return False

    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _, _, _, _, sockaddr in resolved:
            ip_str = sockaddr[0]
            try:
                addr = ipaddress.ip_address(ip_str)
                if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                    return True
            except ValueError:
                continue
    except (socket.gaierror, OSError):
        pass

    return False


def is_url_safe(url: str) -> bool:
    """Check if a URL is safe to fetch (not blocked by SSRF rules)."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if not hostname:
            return False
        return not is_blocked_host(hostname)
    except Exception:
        return False


def safe_urls(text: str, *, max_urls: int = 20) -> list[str]:
    """Extract URLs from text, filter out blocked hosts, return safe ones."""
    all_urls = extract_urls(text)[:max_urls * 2]
    safe: list[str] = []
    for url in all_urls:
        if is_url_safe(url):
            safe.append(url)
            if len(safe) >= max_urls:
                break
        else:
            logger.debug("Blocked URL (SSRF): %s", url)
    return safe
