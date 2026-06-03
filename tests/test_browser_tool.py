"""Unit tests for BrowserTool governance and action routing (no Playwright required)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.assets.tools.browser.browser_tool import BrowserTool


class FakeBrowserService:
    def __init__(self):
        self.last_navigate_url = ""
        self.last_evaluate_script = ""

    def navigate(self, url: str):
        self.last_navigate_url = url
        return {"url": url, "title": "Example", "status": 200}

    def snapshot(self, max_chars: int = 20000):
        return f"snapshot(max={max_chars})"

    def click(self, ref=None, selector=None):
        return {"ok": True}

    def fill(self, text, ref=None, selector=None):
        return {"ok": True}

    def scroll(self, direction="down"):
        return {"scrollY": 100, "scrollHeight": 1000}

    def screenshot(self, full_page=False):
        return "/tmp/screenshot.png"

    def go_back(self):
        return {"url": "https://example.com/previous"}

    def press(self, key):
        return {"ok": True}

    def evaluate(self, script):
        self.last_evaluate_script = script
        return {"result": 42}

    def close(self):
        pass


@pytest.fixture
def browser_tool():
    tool = BrowserTool()
    tool._service = FakeBrowserService()
    return tool


class TestBrowserToolGovernance:
    def test_unknown_action_returns_helpful_message(self, browser_tool):
        result = browser_tool._run(action="fly")
        assert "Unknown action" in result
        assert "navigate" in result

    def test_blocked_localhost_host(self, browser_tool):
        result = browser_tool._run(action="navigate", url="http://localhost/admin")
        assert "Blocked host" in result

    def test_domain_blocklist_rejects_host(self):
        tool = BrowserTool(domain_blocklist={"evil.com"})
        tool._service = FakeBrowserService()
        result = tool._run(action="navigate", url="https://evil.com/page")
        assert "Domain blocked by policy" in result

    def test_domain_allowlist_rejects_unlisted_host(self):
        tool = BrowserTool(domain_allowlist={"allowed.com"})
        tool._service = FakeBrowserService()
        result = tool._run(action="navigate", url="https://other.com/page")
        assert "Domain not in allowlist" in result

    def test_focused_canvas_blocks_write_actions(self, browser_tool):
        browser_tool._canvas_mode = "focused"
        result = browser_tool._run(action="click", ref=1)
        assert "focused canvas mode" in result

    def test_evaluate_blocks_sensitive_apis_outside_deep_mode(self, browser_tool):
        result = browser_tool._run(action="evaluate", script="return document.cookie")
        assert "document.cookie" in result
        assert "blocked outside deep mode" in result

    def test_evaluate_allowed_in_deep_mode(self):
        tool = BrowserTool(canvas_mode="deep")
        tool._service = FakeBrowserService()
        result = tool._run(action="evaluate", script="return document.cookie")
        assert result == "42"


class TestBrowserToolActions:
    def test_navigate_prepends_https_and_snapshots(self, browser_tool):
        result = browser_tool._run(action="navigate", url="example.com")
        assert "Navigated to: https://example.com" in result
        assert "snapshot(max=20000)" in result
        assert browser_tool._service.last_navigate_url == "https://example.com"

    def test_focused_canvas_uses_smaller_snapshot_limit(self):
        tool = BrowserTool(canvas_mode="focused")
        tool._service = FakeBrowserService()
        result = tool._run(action="navigate", url="https://example.com")
        assert "snapshot(max=8000)" in result

    def test_snapshot_returns_service_output(self, browser_tool):
        assert browser_tool._run(action="snapshot") == "snapshot(max=20000)"

    def test_click_success_message(self, browser_tool):
        result = browser_tool._run(action="click", ref=3)
        assert result.startswith("Clicked.")

    def test_fill_requires_text_but_succeeds_with_mock(self, browser_tool):
        result = browser_tool._run(action="fill", ref=1, text="hello")
        assert result.startswith("Filled text.")

    def test_press_requires_key(self, browser_tool):
        assert "key' is required" in browser_tool._run(action="press")

    def test_event_callback_receives_browser_action(self):
        events: list[tuple[str, dict]] = []
        tool = BrowserTool(event_callback=lambda kind, payload: events.append((kind, payload)))
        tool._service = FakeBrowserService()
        tool._run(action="snapshot")
        assert events
        assert events[0][0] == "browser_action"
        assert events[0][1]["action"] == "snapshot"
        assert events[0][1]["canvas"] == "balanced"

    def test_service_error_surfaces_as_browser_error(self, browser_tool):
        browser_tool._service.navigate = MagicMock(side_effect=RuntimeError("boom"))
        result = browser_tool._run(action="navigate", url="https://example.com")
        assert "Browser error (navigate)" in result
        assert "boom" in result
