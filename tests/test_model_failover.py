"""Tests for core.model_failover — LLM failover wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from core.systems.runtime.model_failover import (
    ChatModelWithFailover,
    FailoverStats,
    _get_retry_after,
    _is_transient_error,
    create_failover_model,
)


def _make_chat_result(text: str = "hello") -> ChatResult:
    return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def _mock_model(name: str = "test") -> MagicMock:
    m = MagicMock(spec=BaseChatModel)
    m.model_name = name
    m._identifying_params = {"model": name}
    return m


class TestIsTransientError:
    def test_connection_error(self):
        assert _is_transient_error(ConnectionError("reset")) is True

    def test_timeout_error(self):
        assert _is_transient_error(TimeoutError("timed out")) is True

    def test_os_error(self):
        assert _is_transient_error(OSError("network")) is True

    def test_rate_limit_in_name(self):
        class RateLimitError(Exception):
            pass

        assert _is_transient_error(RateLimitError("slow down")) is True

    def test_status_code_429(self):
        exc = Exception("too many")
        exc.status_code = 429
        assert _is_transient_error(exc) is True

    def test_status_code_503(self):
        exc = Exception("unavailable")
        exc.status_code = 503
        assert _is_transient_error(exc) is True

    def test_non_transient(self):
        assert _is_transient_error(ValueError("bad input")) is False

    def test_overloaded_in_message(self):
        assert _is_transient_error(Exception("model is overloaded")) is True


class TestGetRetryAfter:
    def test_retry_after_attribute(self):
        exc = Exception("wait")
        exc.retry_after = 5.0
        assert _get_retry_after(exc) == 5.0

    def test_retry_after_header(self):
        exc = Exception("wait")
        exc.headers = {"Retry-After": "10"}
        assert _get_retry_after(exc) == 10.0

    def test_no_retry_info(self):
        assert _get_retry_after(ValueError("nope")) is None


class TestChatModelWithFailover:
    def test_primary_success(self):
        primary = _mock_model("primary")
        primary._generate.return_value = _make_chat_result("from primary")
        model = ChatModelWithFailover(primary=primary, fallbacks=[])
        msgs = [HumanMessage(content="hi")]
        result = model._generate(msgs)
        assert result.generations[0].message.content == "from primary"
        assert model.stats.primary_successes == 1
        assert model.stats.fallback_successes == 0

    def test_fallback_on_transient_error(self):
        primary = _mock_model("primary")
        primary._generate.side_effect = ConnectionError("down")

        fallback = _mock_model("fallback")
        fallback._generate.return_value = _make_chat_result("from fallback")

        model = ChatModelWithFailover(primary=primary, fallbacks=[fallback])
        msgs = [HumanMessage(content="hi")]
        result = model._generate(msgs)
        assert result.generations[0].message.content == "from fallback"
        assert model.stats.fallback_successes == 1

    def test_non_transient_skips_retries(self):
        primary = _mock_model("primary")
        primary._generate.side_effect = ValueError("bad format")

        fallback = _mock_model("fallback")
        fallback._generate.return_value = _make_chat_result("from fallback")

        model = ChatModelWithFailover(primary=primary, fallbacks=[fallback], max_retries_per_model=3)
        msgs = [HumanMessage(content="hi")]
        result = model._generate(msgs)
        assert result.generations[0].message.content == "from fallback"
        assert primary._generate.call_count == 1

    def test_all_exhausted_raises(self):
        primary = _mock_model("p")
        primary._generate.side_effect = ConnectionError("down")
        fallback = _mock_model("f")
        fallback._generate.side_effect = ConnectionError("also down")

        model = ChatModelWithFailover(primary=primary, fallbacks=[fallback], retry_delay_seconds=0)
        with pytest.raises(RuntimeError, match="All 2 models exhausted"):
            model._generate([HumanMessage(content="hi")])
        assert model.stats.total_failures == 1

    def test_bind_tools_propagates(self):
        primary = _mock_model("p")
        primary.bind_tools.return_value = _mock_model("p-bound")
        fallback = _mock_model("f")
        fallback.bind_tools.return_value = _mock_model("f-bound")

        model = ChatModelWithFailover(primary=primary, fallbacks=[fallback])
        bound = model.bind_tools([{"name": "test"}])
        assert isinstance(bound, ChatModelWithFailover)
        primary.bind_tools.assert_called_once()
        fallback.bind_tools.assert_called_once()

    def test_bind_tools_skips_unsupported_fallback(self):
        primary = _mock_model("p")
        primary.bind_tools.return_value = _mock_model("p-bound")
        fallback = _mock_model("f")
        fallback.bind_tools.side_effect = NotImplementedError("no tools")

        model = ChatModelWithFailover(primary=primary, fallbacks=[fallback])
        bound = model.bind_tools([{"name": "test"}])
        assert len(bound.fallbacks) == 1

    def test_get_stats(self):
        model = ChatModelWithFailover(primary=_mock_model(), stats=FailoverStats(total_calls=10, primary_successes=8))
        stats = model.get_stats()
        assert stats["total_calls"] == 10
        assert stats["primary_successes"] == 8

    @patch("core.model_failover.time.sleep")
    def test_retry_with_delay(self, mock_sleep):
        primary = _mock_model("p")
        call_count = 0

        def fail_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("transient")
            return _make_chat_result("ok")

        primary._generate.side_effect = fail_then_succeed

        model = ChatModelWithFailover(primary=primary, max_retries_per_model=2, retry_delay_seconds=0.5)
        result = model._generate([HumanMessage(content="hi")])
        assert result.generations[0].message.content == "ok"
        mock_sleep.assert_called_once()


class TestCreateFailoverModel:
    def test_no_fallbacks_returns_primary(self):
        primary = _mock_model()
        result = create_failover_model(primary)
        assert result is primary

    def test_empty_fallbacks_returns_primary(self):
        primary = _mock_model()
        result = create_failover_model(primary, [])
        assert result is primary

    def test_with_fallbacks_wraps(self):
        primary = _mock_model()
        fallback = _mock_model()
        result = create_failover_model(primary, [fallback])
        assert isinstance(result, ChatModelWithFailover)

    def test_custom_retries(self):
        primary = _mock_model()
        fallback = _mock_model()
        result = create_failover_model(primary, [fallback], max_retries=5, retry_delay=2.0)
        assert isinstance(result, ChatModelWithFailover)
        assert result.max_retries_per_model == 5
        assert result.retry_delay_seconds == 2.0
