"""Web Search Tool — multi-engine web search with structured results.

Supports DuckDuckGo (no API key), Bing, and SerpAPI.
Falls back gracefully: SerpAPI → Bing → DuckDuckGo HTML scraping.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Optional, Type
from urllib.parse import quote_plus, urljoin

import requests
from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; PyBot-Search/1.0)"
_TIMEOUT = 12
_MAX_RESULTS = 8
_CACHE_TTL = 300
_cache: dict[str, tuple[float, str]] = {}


class WebSearchInput(BaseModel):
    query: str = Field(description="Search query keywords")
    num_results: int = Field(
        default=5,
        ge=1,
        le=_MAX_RESULTS,
        description=f"Number of results to return (1-{_MAX_RESULTS})",
    )
    engine: str = Field(
        default="auto",
        description="Search engine: 'auto', 'duckduckgo', 'bing', or 'serpapi'",
    )


def _cache_get(key: str) -> str | None:
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
        return entry[1]
    _cache.pop(key, None)
    return None


def _cache_set(key: str, value: str):
    if len(_cache) > 128:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[key] = (time.monotonic(), value)


def _search_duckduckgo(query: str, num: int) -> list[dict]:
    """Search via DuckDuckGo HTML — no API key needed."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("[WebSearch] DuckDuckGo request failed: %s", e)
        return []

    results = []
    snippets = re.findall(
        r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.+?)</a>.*?'
        r'<a class="result__snippet"[^>]*>(.*?)</a>',
        resp.text,
        re.DOTALL,
    )
    for href, title, snippet in snippets[:num]:
        clean_title = re.sub(r"<[^>]+>", "", title).strip()
        clean_snippet = re.sub(r"<[^>]+>", "", snippet).strip()
        results.append({
            "title": clean_title,
            "url": href,
            "snippet": clean_snippet,
        })
    return results


def _search_bing(query: str, num: int) -> list[dict]:
    """Search via Bing Web Search API v7."""
    api_key = os.environ.get("BING_SEARCH_KEY", "")
    if not api_key:
        return []
    try:
        resp = requests.get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": api_key},
            params={"q": query, "count": num, "mkt": "zh-CN"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("[WebSearch] Bing API failed: %s", e)
        return []

    results = []
    for item in data.get("webPages", {}).get("value", [])[:num]:
        results.append({
            "title": item.get("name", ""),
            "url": item.get("url", ""),
            "snippet": item.get("snippet", ""),
        })
    return results


def _search_serpapi(query: str, num: int) -> list[dict]:
    """Search via SerpAPI (Google results)."""
    api_key = os.environ.get("SERPAPI_KEY", "")
    if not api_key:
        return []
    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={
                "q": query,
                "api_key": api_key,
                "num": num,
                "engine": "google",
                "hl": "zh-CN",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("[WebSearch] SerpAPI failed: %s", e)
        return []

    results = []
    for item in data.get("organic_results", [])[:num]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": item.get("snippet", ""),
        })
    return results


def _format_results(results: list[dict], query: str, engine: str) -> str:
    if not results:
        return f"No results found for: {query}"
    lines = [f"Search results for \"{query}\" (via {engine}, {len(results)} results):\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   {r['url']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        lines.append("")
    return "\n".join(lines)


class WebSearchTool(BaseTool):
    """Multi-engine web search tool for PyBot."""

    name: str = "web_search"
    description: str = (
        "Search the web for information. Returns titles, URLs, and snippets. "
        "Use this to find current information, documentation, news, etc. "
        "Supports DuckDuckGo (free, no key), Bing (needs BING_SEARCH_KEY), "
        "and SerpAPI/Google (needs SERPAPI_KEY)."
    )
    args_schema: Type[BaseModel] = WebSearchInput
    risk_level: str = "low"

    _event_callback: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, event_callback: Any = None, **kwargs):
        super().__init__(**kwargs)
        self._event_callback = event_callback

    def _emit_event(self, detail: str):
        if self._event_callback:
            try:
                self._event_callback("web_search", {"detail": detail[:200]})
            except Exception:
                pass

    def _run(self, query: str, num_results: int = 5, engine: str = "auto") -> str:
        if not query or not query.strip():
            return "Error: search query is required"

        query = query.strip()
        cache_key = f"{engine}:{query}:{num_results}"
        cached = _cache_get(cache_key)
        if cached:
            return f"[cached] {cached}"

        self._emit_event(f"Searching: {query}")

        results: list[dict] = []
        used_engine = "unknown"

        if engine == "auto":
            for try_engine, try_fn in [
                ("serpapi", _search_serpapi),
                ("bing", _search_bing),
                ("duckduckgo", _search_duckduckgo),
            ]:
                results = try_fn(query, num_results)
                if results:
                    used_engine = try_engine
                    break
        elif engine == "serpapi":
            results = _search_serpapi(query, num_results)
            used_engine = "serpapi"
        elif engine == "bing":
            results = _search_bing(query, num_results)
            used_engine = "bing"
        else:
            results = _search_duckduckgo(query, num_results)
            used_engine = "duckduckgo"

        formatted = _format_results(results, query, used_engine)
        _cache_set(cache_key, formatted)
        return formatted
