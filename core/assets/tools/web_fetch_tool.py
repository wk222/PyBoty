"""Atomic web fetch tool — download and extract text content from URLs."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from typing import Any, Callable, Optional, Type
from urllib.parse import urlparse

import requests
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15
_MAX_OUTPUT_BYTES = 50 * 1024
_CACHE_TTL_SECONDS = 15 * 60
_CACHE_MAX_ENTRIES = 64
_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024

_BLOCKED_SCHEMES = frozenset({"file", "ftp", "data", "javascript", "vbscript"})
_BLOCKED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

_USER_AGENT = (
    "Mozilla/5.0 (compatible; PyBot/1.0; +https://github.com/pybot)"
)

_STRIP_TAGS = frozenset({
    "script", "style", "noscript", "nav", "footer", "header",
    "aside", "iframe", "svg", "form", "button",
})


class _CacheEntry:
    __slots__ = ("content", "timestamp", "url")

    def __init__(self, url: str, content: str) -> None:
        self.url = url
        self.content = content
        self.timestamp = time.monotonic()

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.timestamp) > _CACHE_TTL_SECONDS


class _LRUCache:
    def __init__(self, max_entries: int = _CACHE_MAX_ENTRIES) -> None:
        self._entries: dict[str, _CacheEntry] = {}
        self._max = max_entries

    def get(self, url: str) -> str | None:
        entry = self._entries.get(url)
        if entry is None:
            return None
        if entry.expired:
            del self._entries[url]
            return None
        self._entries.pop(url)
        self._entries[url] = entry
        return entry.content

    def put(self, url: str, content: str) -> None:
        if url in self._entries:
            del self._entries[url]
        while len(self._entries) >= self._max:
            oldest = next(iter(self._entries))
            del self._entries[oldest]
        self._entries[url] = _CacheEntry(url, content)


_global_cache = _LRUCache()


def _is_private_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        )
    except ValueError:
        return False


def _resolve_and_check(hostname: str) -> str | None:
    if hostname in _BLOCKED_HOSTS:
        return f"❌ 安全拦截: 禁止访问主机 {hostname}"
    if _is_private_ip(hostname):
        return f"❌ 安全拦截: 禁止访问私有/保留 IP {hostname}"
    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _type, _proto, _canonname, sockaddr in resolved:
            ip_str = sockaddr[0]
            if _is_private_ip(ip_str):
                return f"❌ 安全拦截: {hostname} 解析到私有 IP {ip_str}"
    except socket.gaierror:
        return f"❌ DNS 解析失败: {hostname}"
    return None


def _validate_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return "❌ URL 格式无效"
    if parsed.scheme not in ("http", "https"):
        return f"❌ 不支持的协议: {parsed.scheme}（仅支持 http/https）"
    if parsed.scheme in _BLOCKED_SCHEMES:
        return f"❌ 安全拦截: 禁止使用协议 {parsed.scheme}"
    if not parsed.hostname:
        return "❌ URL 缺少主机名"
    return _resolve_and_check(parsed.hostname)


def _html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup, Comment
        soup = BeautifulSoup(html, "html.parser")
        for tag_name in _STRIP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()
        text = soup.get_text(separator="\n", strip=True)
    except ImportError:
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            cleaned.append(stripped)
    return "\n".join(cleaned)


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()[:200]
    return ""


def _truncate(text: str, max_bytes: int = _MAX_OUTPUT_BYTES) -> tuple[str, bool]:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    last_nl = truncated.rfind("\n")
    if last_nl > max_bytes // 2:
        truncated = truncated[:last_nl]
    return truncated, True


_MAX_REDIRECTS = 5


def _fetch_url(url: str, timeout: int = _DEFAULT_TIMEOUT) -> tuple[str | None, str | None]:
    current_url = url
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})

    for hop in range(_MAX_REDIRECTS + 1):
        if hop > 0:
            rejection = _validate_url(current_url)
            if rejection:
                return None, f"{rejection} (redirect hop {hop})"

        try:
            resp = session.get(
                current_url,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
        except requests.exceptions.Timeout:
            return None, f"❌ 请求超时（{timeout}s）"
        except requests.exceptions.ConnectionError as e:
            return None, f"❌ 连接失败: {e}"
        except Exception as e:
            return None, f"❌ 请求失败: {type(e).__name__}: {e}"

        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location")
            if not location:
                return None, "❌ 重定向缺少 Location 头"
            from urllib.parse import urljoin
            current_url = urljoin(current_url, location)
            resp.close()
            continue

        if resp.status_code >= 400:
            resp.close()
            return None, f"❌ HTTP 错误: {resp.status_code}"

        content_type = resp.headers.get("content-type", "")
        if not any(ct in content_type.lower() for ct in ("text/", "application/json", "application/xml", "+xml", "+json")):
            resp.close()
            return None, f"❌ 不支持的内容类型: {content_type}（仅支持文本/HTML/JSON/XML）"

        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192, decode_unicode=False):
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_DOWNLOAD_BYTES:
                break
        raw = b"".join(chunks)

        encoding = resp.encoding or "utf-8"
        try:
            body = raw.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            body = raw.decode("utf-8", errors="replace")

        resp.close()
        return body, None

    return None, f"❌ 重定向次数过多（超过 {_MAX_REDIRECTS} 次）"


class WebFetchInput(BaseModel):
    url: str = Field(description="要抓取的 URL（仅支持 http/https）")
    extract_text: bool = Field(
        default=True,
        description="是否将 HTML 转换为纯文本（默认 True）。设为 False 返回原始 HTML",
    )
    timeout: int = Field(
        default=_DEFAULT_TIMEOUT,
        ge=5,
        le=60,
        description=f"请求超时时间（秒），默认 {_DEFAULT_TIMEOUT}",
    )
    no_cache: bool = Field(
        default=False,
        description="是否跳过缓存直接请求（默认 False）",
    )


class WebFetchTool(BaseTool):
    name: str = "web_fetch"
    description: str = (
        "从 URL 下载网页或文本内容，自动将 HTML 转换为可读纯文本。"
        "支持 http/https 协议，适用于阅读文档、API 参考、博客文章等在线内容。"
        "内置 SSRF 防护（阻止访问内网/私有 IP）和 LRU 缓存（15 分钟 TTL）。"
        "输出自动截断到 50KB。"
    )
    args_schema: Type[BaseModel] = WebFetchInput
    risk_level: str = "medium"
    summarize_fn: Optional[Any] = None

    model_config = {"arbitrary_types_allowed": True}

    def _run(
        self,
        url: str,
        extract_text: bool = True,
        timeout: int = _DEFAULT_TIMEOUT,
        no_cache: bool = False,
    ) -> str:
        rejection = _validate_url(url)
        if rejection:
            return rejection

        cache_key = f"{url}|text={extract_text}"

        if not no_cache:
            cached = _global_cache.get(cache_key)
            if cached is not None:
                return f"[cached] {cached}"

        body, error = _fetch_url(url, timeout=timeout)
        if error:
            return error

        assert body is not None

        title = _extract_title(body)
        content_type_hint = "html" if "<html" in body[:500].lower() or "<body" in body[:1000].lower() else "text"

        if extract_text and content_type_hint == "html":
            text = _html_to_text(body)
        else:
            text = body

        text, was_truncated = _truncate(text)

        parts = []
        if title:
            parts.append(f"Title: {title}")
        parts.append(f"URL: {url}")
        if was_truncated:
            parts.append(f"[truncated to {_MAX_OUTPUT_BYTES // 1024}KB]")
        parts.append("")
        parts.append(text)
        result = "\n".join(parts)

        _global_cache.put(cache_key, result)
        return result
