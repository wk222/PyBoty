"""Tests for core.intervention."""

from __future__ import annotations

from core.intervention import (
    ContentFilterHandler,
    InterventionChain,
    InterventionResponse,
    InterventionResult,
    LoggingHandler,
    RateLimitHandler,
)


class TestInterventionResponse:
    def test_allow(self):
        r = InterventionResponse.allow()
        assert r.result == InterventionResult.PASS

    def test_modify(self):
        r = InterventionResponse.modify({"x": 1}, reason="cleaned")
        assert r.result == InterventionResult.MODIFY
        assert r.modified_content == {"x": 1}
        assert r.reason == "cleaned"

    def test_drop(self):
        r = InterventionResponse.drop("bad content")
        assert r.result == InterventionResult.DROP
        assert r.reason == "bad content"


class TestContentFilterHandler:
    def test_blocks_matching_pattern(self):
        h = ContentFilterHandler(["password", "secret"])
        resp = h.on_agent_message("agent1", "my password is 123")
        assert resp.result == InterventionResult.DROP

    def test_allows_clean_content(self):
        h = ContentFilterHandler(["password"])
        resp = h.on_agent_message("agent1", "hello world")
        assert resp.result == InterventionResult.PASS

    def test_tool_call_filter(self):
        h = ContentFilterHandler(["rm -rf"])
        resp = h.on_tool_call("shell", {"cmd": "rm -rf /"})
        assert resp.result == InterventionResult.DROP

    def test_delegation_filter(self):
        h = ContentFilterHandler(["hack"])
        resp = h.on_delegation("a", "b", "hack the system")
        assert resp.result == InterventionResult.DROP

    def test_case_insensitive(self):
        h = ContentFilterHandler(["SECRET"])
        resp = h.on_agent_message("a", "this is a Secret value")
        assert resp.result == InterventionResult.DROP

    def test_regex_pattern(self):
        h = ContentFilterHandler([r"\b\d{3}-\d{2}-\d{4}\b"])
        resp = h.on_agent_message("a", "SSN: 123-45-6789")
        assert resp.result == InterventionResult.DROP
        resp2 = h.on_agent_message("a", "no SSN here")
        assert resp2.result == InterventionResult.PASS


class TestRateLimitHandler:
    def test_allows_under_limit(self):
        h = RateLimitHandler(max_calls_per_minute=10)
        resp = h.on_tool_call("t", {})
        assert resp.result == InterventionResult.PASS

    def test_blocks_over_limit(self):
        h = RateLimitHandler(max_calls_per_minute=3)
        for _ in range(3):
            h.on_tool_call("t", {})
        resp = h.on_tool_call("t", {})
        assert resp.result == InterventionResult.DROP
        assert "Rate limit" in resp.reason

    def test_applies_to_all_methods(self):
        h = RateLimitHandler(max_calls_per_minute=2)
        h.on_tool_call("t", {})
        h.on_agent_message("a", "m")
        resp = h.on_delegation("a", "b", "task")
        assert resp.result == InterventionResult.DROP


class TestLoggingHandler:
    def test_logs_tool_call(self):
        h = LoggingHandler()
        resp = h.on_tool_call("search", {"q": "hello"})
        assert resp.result == InterventionResult.PASS
        assert len(h.log) == 1
        assert h.log[0]["type"] == "tool_call"

    def test_logs_agent_message(self):
        h = LoggingHandler()
        h.on_agent_message("bot", "response text")
        assert h.log[0]["type"] == "agent_message"

    def test_logs_delegation(self):
        h = LoggingHandler()
        h.on_delegation("a", "b", "do something")
        assert h.log[0]["type"] == "delegation"


class TestInterventionChain:
    def test_all_pass(self):
        chain = InterventionChain([LoggingHandler(), LoggingHandler()])
        resp = chain.on_tool_call("t", {"x": 1})
        assert resp.result == InterventionResult.PASS

    def test_drop_stops_chain(self):
        blocker = ContentFilterHandler(["blocked"])
        logger_h = LoggingHandler()
        chain = InterventionChain([blocker, logger_h])
        resp = chain.on_agent_message("a", "this is blocked content")
        assert resp.result == InterventionResult.DROP
        assert len(logger_h.log) == 0  # second handler not reached

    def test_modify_propagates(self):
        class Modifier:
            def on_tool_call(self, tool_name, args):
                new_args = {**args, "sanitized": True}
                return InterventionResponse.modify(new_args, "sanitized")

            def on_agent_message(self, agent_name, message):
                return InterventionResponse.allow()

            def on_delegation(self, from_agent, to_agent, task):
                return InterventionResponse.allow()

        logger_h = LoggingHandler()
        chain = InterventionChain([Modifier(), logger_h])
        resp = chain.on_tool_call("t", {"x": 1})
        assert resp.result == InterventionResult.MODIFY
        assert resp.modified_content["sanitized"] is True

    def test_add_handler(self):
        chain = InterventionChain()
        chain.add(LoggingHandler())
        resp = chain.on_tool_call("t", {})
        assert resp.result == InterventionResult.PASS

    def test_empty_chain(self):
        chain = InterventionChain()
        resp = chain.on_tool_call("t", {})
        assert resp.result == InterventionResult.PASS

    def test_delegation_chain(self):
        blocker = ContentFilterHandler(["forbidden"])
        chain = InterventionChain([blocker])
        resp = chain.on_delegation("a", "b", "do forbidden thing")
        assert resp.result == InterventionResult.DROP
        resp2 = chain.on_delegation("a", "b", "do normal thing")
        assert resp2.result == InterventionResult.PASS
