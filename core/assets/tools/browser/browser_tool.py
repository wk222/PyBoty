"""
PyBot Browser Tool — governance-aware web automation.

Improvements over reference implementations:
- Domain allowlist/blocklist for security
- Event bus integration for tracing
- Canvas-aware capability gating (focused/balanced/deep)
- Token-efficient snapshot format
- Automatic risk classification per action
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional, Type
from urllib.parse import urlparse

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

BLOCKED_HOSTS = frozenset({
    "localhost", "127.0.0.1", "::1", "0.0.0.0",
    "169.254.169.254",  # cloud metadata
})

READ_ACTIONS = frozenset({"navigate", "snapshot", "screenshot", "scroll", "back", "get_text"})
WRITE_ACTIONS = frozenset({"click", "fill", "select", "press", "evaluate"})

ACTION_LIST = sorted(READ_ACTIONS | WRITE_ACTIONS)


class BrowserInput(BaseModel):
    action: str = Field(description=(
        f"Browser action: {', '.join(ACTION_LIST)}. "
        "Workflow: navigate → snapshot (get refs) → click/fill by ref → snapshot to verify."
    ))
    url: Optional[str] = Field(None, description="URL for 'navigate'")
    ref: Optional[int] = Field(None, description="Element ref from snapshot (for click/fill/select)")
    selector: Optional[str] = Field(None, description="CSS selector fallback")
    text: Optional[str] = Field(None, description="Text to type (for 'fill')")
    key: Optional[str] = Field(None, description="Key to press (for 'press')")
    direction: Optional[str] = Field(None, description="Scroll direction: up/down/left/right")
    script: Optional[str] = Field(None, description="JavaScript code (for 'evaluate')")
    full_page: bool = Field(False, description="Full page screenshot")


class BrowserTool(BaseTool):
    """Governance-integrated browser automation tool for PyBot."""

    name: str = "browser"
    description: str = (
        "Control a browser: navigate pages, interact with elements, extract content. "
        "Use navigate to open a URL (auto-snapshots the page), then click/fill/select "
        "by element ref numbers from the snapshot. Use screenshot to capture visual state."
    )
    args_schema: Type[BaseModel] = BrowserInput
    _service: Any = None
    _domain_allowlist: Optional[set] = None
    _domain_blocklist: Optional[set] = None
    _canvas_mode: str = "balanced"
    _event_callback: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        domain_allowlist: Optional[set] = None,
        domain_blocklist: Optional[set] = None,
        canvas_mode: str = "balanced",
        event_callback: Any = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._domain_allowlist = domain_allowlist
        self._domain_blocklist = domain_blocklist or set()
        self._canvas_mode = canvas_mode
        self._event_callback = event_callback

    def _get_service(self):
        if self._service is not None:
            return self._service
        from core.assets.tools.browser.browser_service import BrowserService, HAS_PLAYWRIGHT
        if not HAS_PLAYWRIGHT:
            raise RuntimeError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )
        self._service = BrowserService()
        return self._service

    def _emit_event(self, action: str, detail: str):
        if self._event_callback:
            try:
                self._event_callback("browser_action", {
                    "action": action,
                    "detail": detail[:200],
                    "canvas": self._canvas_mode,
                })
            except Exception:
                pass

    def _check_domain(self, url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
        except Exception:
            return "Invalid URL"
        if host in BLOCKED_HOSTS:
            return f"Blocked host: {host}"
        if self._domain_blocklist and host in self._domain_blocklist:
            return f"Domain blocked by policy: {host}"
        if self._domain_allowlist and host not in self._domain_allowlist:
            return f"Domain not in allowlist: {host}"
        return None

    def _check_canvas_permission(self, action: str) -> Optional[str]:
        if self._canvas_mode == "focused":
            if action in WRITE_ACTIONS:
                return f"Action '{action}' is not allowed in focused canvas mode (read-only browsing)"
            if action == "evaluate":
                return "JavaScript evaluation is not allowed in focused mode"
        return None

    def _run(self, **kwargs) -> str:
        action = kwargs.get("action", "").strip().lower()
        if action not in ACTION_LIST:
            return f"Unknown action '{action}'. Valid: {', '.join(ACTION_LIST)}"

        canvas_err = self._check_canvas_permission(action)
        if canvas_err:
            return canvas_err

        handler = {
            "navigate": self._navigate,
            "snapshot": self._snapshot,
            "click": self._click,
            "fill": self._fill,
            "select": self._select,
            "scroll": self._scroll,
            "screenshot": self._screenshot,
            "back": self._back,
            "get_text": self._get_text,
            "press": self._press,
            "evaluate": self._evaluate,
        }.get(action)

        try:
            result = handler(kwargs)
            self._emit_event(action, result[:200] if isinstance(result, str) else str(result)[:200])
            return result
        except Exception as e:
            logger.error("[Browser] %s error: %s", action, e)
            return f"Browser error ({action}): {e}"

    def _navigate(self, args: dict) -> str:
        url = (args.get("url") or "").strip()
        if not url:
            return "Error: 'url' is required"
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        domain_err = self._check_domain(url)
        if domain_err:
            return domain_err
        svc = self._get_service()
        result = svc.navigate(url)
        if "error" in result:
            return result["error"]
        snap = svc.snapshot(max_chars=self._snapshot_limit)
        return (
            f"Navigated to: {result['url']}\nTitle: {result['title']}\n"
            f"Status: {result['status']}\n\n{snap}"
        )

    @property
    def _snapshot_limit(self) -> int:
        return {"focused": 8000, "balanced": 20000, "deep": 40000}.get(self._canvas_mode, 20000)

    def _snapshot(self, args: dict) -> str:
        return self._get_service().snapshot(max_chars=self._snapshot_limit)

    def _click(self, args: dict) -> str:
        result = self._get_service().click(ref=args.get("ref"), selector=args.get("selector"))
        if "error" in result:
            return result["error"]
        return "Clicked. Use 'snapshot' to see updated page."

    def _fill(self, args: dict) -> str:
        text = args.get("text", "")
        result = self._get_service().fill(text, ref=args.get("ref"), selector=args.get("selector"))
        if "error" in result:
            return result["error"]
        return "Filled text. Use 'snapshot' to verify."

    def _select(self, args: dict) -> str:
        return "Select not yet implemented — use click on the option element instead."

    def _scroll(self, args: dict) -> str:
        direction = args.get("direction", "down")
        result = self._get_service().scroll(direction=direction)
        if "error" in result:
            return result["error"]
        return f"Scrolled {direction}. scrollY={result.get('scrollY')}/{result.get('scrollHeight')}"

    def _screenshot(self, args: dict) -> str:
        path = self._get_service().screenshot(full_page=args.get("full_page", False))
        return f"Screenshot saved: {path}"

    def _back(self, args: dict) -> str:
        result = self._get_service().go_back()
        if "error" in result:
            return result["error"]
        return f"Navigated back to: {result['url']}"

    def _get_text(self, args: dict) -> str:
        return self._get_service().snapshot(max_chars=self._snapshot_limit)

    def _press(self, args: dict) -> str:
        key = (args.get("key") or "").strip()
        if not key:
            return "Error: 'key' is required"
        result = self._get_service().press(key)
        if "error" in result:
            return result["error"]
        return f"Pressed: {key}"

    def _evaluate(self, args: dict) -> str:
        script = (args.get("script") or "").strip()
        if not script:
            return "Error: 'script' is required"
        if self._canvas_mode != "deep":
            forbidden = ["document.cookie", "localStorage", "sessionStorage", "fetch(", "XMLHttpRequest"]
            for f in forbidden:
                if f in script:
                    return f"Script contains '{f}' which is blocked outside deep mode"
        result = self._get_service().evaluate(script)
        if "error" in result:
            return result["error"]
        val = result.get("result")
        if isinstance(val, (dict, list)):
            import json
            return json.dumps(val, ensure_ascii=False, indent=2)
        return str(val) if val is not None else "(no return value)"

    def close(self):
        if self._service:
            self._service.close()
            self._service = None
