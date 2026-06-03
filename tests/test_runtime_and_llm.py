"""Unified tests for core Runtime base, context management, and LLM resolution/failover/routing (Eighth Round).

Consolidated and merged from 8 individual test files:
* test_project_paths.py
* test_context_budget.py
* test_context_hygiene_runtime.py
* test_private_state.py
* test_model_resolver.py
* test_model_failover.py
* test_model_router.py
* test_observability.py
"""

from __future__ import annotations

import importlib
import os
import tempfile
import time
from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

# Core Systems / Runtime / Context Imports
from core.systems.runtime.pause_resume import PauseContext, PauseManager, PauseState, SimplePausableAgent
from core.systems.runtime.a2a_protocol import A2ARegistry, A2ATask, AgentCard, TaskState
from core.systems.runtime.cli_support import CliConfigError, InteractiveCliApp, load_required_config
from tests.mock_llm import AIMessageCompat, MockLLM, MockLLMFactory, ToolCallCompat, mock_llm_caller

# Core Systems / Runtime / Context Imports
from core.systems.context.context_budget import ContextBudgetManager
from core.systems.context.context_hygiene_runtime import ContextHygieneRuntime
from core.systems.context.private_state import (
    BUILTIN_PRIVATE_KEYS,
    get_private_keys,
    get_private_keys_by_owner,
    register_private_keys,
)
from core.systems.context.projected_runtime_view import build_projected_runtime_view
from core.systems.agents.subagent_runtime import EXCLUDED_SUBAGENT_STATE_KEYS, filter_subagent_state
from core.systems.llm import (
    ModelProviderError,
    ModelRouter,
    ModelTier,
    ResolvedModel,
    RoutingDecision,
    TierConfig,
    create_model_router,
    list_all_providers,
    list_available_providers,
    resolve_model,
)
from core.systems.llm.model_failover import (
    ChatModelWithFailover,
    FailoverStats,
    _get_retry_after,
    _is_transient_error,
    create_failover_model,
)
from core.systems.llm.model_resolver import _parse_spec
from core.systems.middleware.summarization_middleware import SummarizationConfig
from core.systems.observability.cost_tracker import (
    CostTracker,
    CostTrackerCallback,
    _estimate_cost,
)
from core.systems.observability.setup import (
    ObservabilityConfig,
    get_observability_config_from_dict,
    setup_tracing,
)
from core.systems.runtime import ProjectPaths
from core.systems.runtime.hooks_runtime import HookPhase, HooksRuntime


# ---------------------------------------------------------------------------
# 1. Project Paths Tests (formerly test_project_paths.py)
# ---------------------------------------------------------------------------

class TestProjectPathsClass:
    def test_project_paths_defaults_runtime_root_to_user_scope(self, monkeypatch, tmp_path):
        runtime_root = tmp_path / "runtime-home"
        monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(runtime_root))

        paths = ProjectPaths.from_root()

        assert paths.root_dir != paths.runtime_root_dir
        assert paths.runtime_root_dir == runtime_root.resolve()
        assert paths.workspace_dir == (runtime_root / "workspace").resolve()
        assert paths.global_tools_dir == (runtime_root / "global_tools").resolve()
        assert paths.tools_workspace_dir == (runtime_root / ".tools_workspace").resolve()

    def test_project_paths_keep_explicit_root_local_for_tests(self, tmp_path):
        paths = ProjectPaths.from_root(root_dir=tmp_path)

        assert paths.root_dir == tmp_path.resolve()
        assert paths.runtime_root_dir == tmp_path.resolve()
        assert paths.workspace_dir == (tmp_path / "workspace").resolve()

    def test_project_paths_resolve_relative_runtime_root_against_source_root(self, tmp_path):
        paths = ProjectPaths.from_root(root_dir=tmp_path, runtime_root_dir=Path("runtime"))

        assert paths.runtime_root_dir == (tmp_path / "runtime").resolve()
        assert paths.global_tools_dir == (tmp_path / "runtime" / "global_tools").resolve()


# ---------------------------------------------------------------------------
# 2. Context Budget Tests (formerly test_context_budget.py)
# ---------------------------------------------------------------------------

class TestContextBudget:
    def test_context_budget_prefers_canonical_runtime_view(self):
        manager = ContextBudgetManager(context_limit=200_000)
        canonical = build_projected_runtime_view(
            thread_id="thread-budget",
            root_mode="assistant",
            system_context={"working_summary": "Canonical summary from projected runtime view."},
            session={"session_notebook_summary": "note one\nnote two"},
            tasks={
                "activities": [
                    {"activity_id": "a1", "kind": "tool_run", "title": "read_file", "timestamp": 1},
                    {"activity_id": "a2", "kind": "governance", "title": "permission:ask", "timestamp": 2},
                ]
            },
        )

        estimated = manager.estimate_session_tokens(
            {
                "message_count": 2,
                "working_summary": "",
                "timeline": [],
                "runtime_view": canonical.to_payload(),
            }
        )

        assert estimated > 300

    def test_context_budget_micro_trim_tool_output_head_tail_behavior(self):
        manager = ContextBudgetManager(context_limit=128000)
        
        long_text = "A" * 1000 + "B" * 1000 + "C" * 1000  # 3000 chars
        budget = 400
        
        trimmed, removed = manager.micro_trim_tool_output(
            long_text, 
            pressure_level="high", # default is 500
            max_chars=budget
        )
        
        assert removed == 3000 - budget
        assert "A" * (budget // 2) in trimmed
        assert "C" * (budget // 4) in trimmed
        assert "B" not in trimmed # B is in the middle, should be trimmed
        assert f"[{removed} chars trimmed]" in trimmed

    def test_context_budget_apply_micro_trim_based_on_pressure(self):
        manager = ContextBudgetManager(context_limit=1000) # small limit
        
        # Fake session record to simulate high token usage
        session_record = {
            "message_count": 100, # 100 * 150 = 15000 tokens > 1000 limit -> critical pressure
        }
        
        outputs = {
            "tool_1": "X" * 500, # 500 chars. critical limit is 200
            "tool_2": "Y" * 100  # 100 chars, < 200, shouldn't be trimmed
        }
        
        trimmed_outputs, stats = manager.apply_micro_trim(outputs, session_record=session_record)
        
        assert "tool_1" in stats
        assert stats["tool_1"] == 300 # 500 - 200 = 300 removed
        assert len(trimmed_outputs["tool_1"]) > 100
        assert "trimmed" in trimmed_outputs["tool_1"]
        
        assert "tool_2" not in stats
        assert trimmed_outputs["tool_2"] == "Y" * 100


# ---------------------------------------------------------------------------
# 3. Context Hygiene Runtime Tests (formerly test_context_hygiene_runtime.py)
# ---------------------------------------------------------------------------

class TestContextHygieneRuntimeClass:
    @staticmethod
    def _human(text: str) -> HumanMessage:
        return HumanMessage(content=text)

    @staticmethod
    def _ai(text: str, tool_calls: list | None = None) -> AIMessage:
        return AIMessage(content=text, tool_calls=tool_calls or [])

    def test_context_hygiene_runtime_records_history_snip_and_projection(self, tmp_path):
        runtime = ContextHygieneRuntime(
            config=SummarizationConfig(keep_recent_messages=2, offload_dir=str(tmp_path), thread_id="thread-1")
        )
        messages = [self._human(f"msg {index}") for index in range(6)]
        payloads: list[dict] = []

        count = runtime.summarize(
            messages,
            summarize_fn=lambda prompt: "summary text",
            resume_bundle_text="## Task Runtime\n- t1: stabilize",
            compaction_callback=payloads.append,
        )

        assert count == 4
        assert runtime.summary_message is not None
        assert "### Post-compact rebuild" in runtime.summary_message.content
        assert payloads[-1]["summary"] == "### Post-compact rebuild\n### Resume bundle\n## Task Runtime\n- t1: stabilize\n\nsummary text"
        assert payloads[-1]["history_snip_count"] == 1 or payloads[-1]["metadata"]["history_snip_count"] == 1

        projection = runtime.build_projection()
        assert projection["summary_active"] is True
        assert projection["history_snip_count"] == 1
        assert projection["latest_boundary"]["summary"].startswith("### Post-compact rebuild")

    def test_context_hygiene_runtime_microcompact_preserves_tool_preview(self):
        runtime = ContextHygieneRuntime(config=SummarizationConfig(keep_recent_messages=2, microcompact_age=2))
        messages = [
            self._ai("", tool_calls=[{"id": "tc-read", "name": "read_file", "args": {"path": "src/app.py"}}]),
            ToolMessage(content=("line\n" * 300), tool_call_id="tc-read", name="read_file"),
            self._human("step 1"),
            self._human("step 2"),
            self._human("step 3"),
        ]

        compacted = runtime.microcompact(messages)

        assert compacted[1].content.startswith("[microcompact]")
        assert "read_file(path=src/app.py)" in compacted[1].content

    def test_context_hygiene_runtime_runs_fixed_rebuild_and_writeback_hooks(self, tmp_path):
        hooks = HooksRuntime()
        hooks.register(
            HookPhase.CONTEXT_HYGIENE_REBUILD,
            "rebuild_guard",
            lambda payload: {
                "prepend_sections": ["### Runtime Guardrail\nRebuild strictly from the projected runtime view."],
            },
        )
        hooks.register(
            HookPhase.CONTEXT_HYGIENE_WRITEBACK,
            "writeback_tags",
            lambda payload: {
                "notes": ["compaction checkpoint captured"],
                "session_tags": ["compact:recorded"],
                "boundary_annotations": {"owner": "context_hygiene"},
            },
        )
        runtime = ContextHygieneRuntime(
            config=SummarizationConfig(keep_recent_messages=2, offload_dir=str(tmp_path), thread_id="thread-hooks"),
            hooks_runtime=hooks,
        )
        payloads: list[dict] = []

        runtime.summarize(
            [self._human(f"msg {index}") for index in range(5)],
            summarize_fn=lambda prompt: "summary text",
            resume_bundle_text="## Task Runtime\n- t1: stabilize",
            projected_runtime_view={"context_hygiene": {"summary_active": True}},
            compaction_callback=payloads.append,
        )

        assert payloads
        assert "Rebuild strictly from the projected runtime view." in payloads[-1]["summary"]
        assert payloads[-1]["metadata"]["hook_notes"] == ["compaction checkpoint captured"]
        assert payloads[-1]["metadata"]["hook_session_tags"] == ["compact:recorded"]
        assert payloads[-1]["metadata"]["boundary_annotations"]["owner"] == "context_hygiene"


# ---------------------------------------------------------------------------
# 4. Private State Isolation Tests (formerly test_private_state.py)
# ---------------------------------------------------------------------------

class TestPrivateStateRegistry:
    def test_builtin_keys_include_messages(self):
        assert "messages" in BUILTIN_PRIVATE_KEYS

    def test_builtin_keys_include_todos(self):
        assert "todos" in BUILTIN_PRIVATE_KEYS

    def test_builtin_keys_include_skills_metadata(self):
        assert "skills_metadata" in BUILTIN_PRIVATE_KEYS

    def test_builtin_keys_include_memory_contents(self):
        assert "memory_contents" in BUILTIN_PRIVATE_KEYS

    def test_get_private_keys_includes_builtins(self):
        keys = get_private_keys()
        for k in BUILTIN_PRIVATE_KEYS:
            assert k in keys

    def test_register_custom_keys(self):
        register_private_keys("TestOwner", {"_test_key_abc"})
        keys = get_private_keys()
        assert "_test_key_abc" in keys

    def test_get_private_keys_by_owner(self):
        owners = get_private_keys_by_owner()
        assert "builtin" in owners
        assert "TodoListMiddleware" in owners
        assert "SummarizationMiddleware" in owners
        assert "MemoryMiddleware" in owners

    def test_middleware_registered_keys(self):
        owners = get_private_keys_by_owner()
        assert "_todo_state" in owners["TodoListMiddleware"]
        assert "_summarization_event" in owners["SummarizationMiddleware"]
        assert "skills_metadata" in owners["SkillsMiddleware"]


class TestSubagentStateFiltering:
    def test_excluded_keys_derived_from_registry(self):
        assert "messages" in EXCLUDED_SUBAGENT_STATE_KEYS
        assert "todos" in EXCLUDED_SUBAGENT_STATE_KEYS

    def test_filter_removes_private_keys(self):
        state = {
            "messages": [{"role": "user", "content": "hi"}],
            "todos": [{"id": "1"}],
            "skills_metadata": [],
            "memory_contents": {"a": "b"},
            "custom_key": "keep_me",
            "workspace_data": {"x": 1},
        }
        filtered = filter_subagent_state(state)
        assert "messages" not in filtered
        assert "todos" not in filtered
        assert "skills_metadata" not in filtered
        assert "memory_contents" not in filtered
        assert filtered["custom_key"] == "keep_me"
        assert filtered["workspace_data"] == {"x": 1}

    def test_filter_empty_state(self):
        assert filter_subagent_state({}) == {}
        assert filter_subagent_state(None) == {}

    def test_filter_uses_dynamic_registry(self):
        register_private_keys("TestFilter", {"_dynamic_test_key"})
        state = {"_dynamic_test_key": "secret", "public": "visible"}
        filtered = filter_subagent_state(state)
        assert "_dynamic_test_key" not in filtered
        assert filtered["public"] == "visible"


# ---------------------------------------------------------------------------
# 5. Model Resolver Tests (formerly test_model_resolver.py)
# ---------------------------------------------------------------------------

def _fake_chat_model(**attrs) -> MagicMock:
    m = MagicMock(spec=BaseChatModel)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class TestParseSpec:
    def test_provider_model_format(self):
        provider, model = _parse_spec("anthropic:claude-sonnet-4-20250514")
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-20250514"

    def test_plain_model_defaults_to_openai(self):
        provider, model = _parse_spec("gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"

    def test_provider_with_spaces(self):
        provider, model = _parse_spec("  google : gemini-2.0-flash  ")
        assert provider == "google"
        assert model == "gemini-2.0-flash"

    def test_empty_model_raises(self):
        with pytest.raises(ModelProviderError, match="Empty model name"):
            _parse_spec("openai:")

    def test_multiple_colons(self):
        provider, model = _parse_spec("openai:ft:gpt-4o:org:custom")
        assert provider == "openai"
        assert model == "ft:gpt-4o:org:custom"


class TestResolveModelPrebuilt:
    def test_passthrough_prebuilt_model(self):
        mock_model = _fake_chat_model(model_name="test-model")
        result = resolve_model(mock_model)
        assert isinstance(result, ResolvedModel)
        assert result.model is mock_model
        assert result.provider == "prebuilt"
        assert result.model_name == "test-model"

    def test_prebuilt_model_without_model_name(self):
        mock_model = _fake_chat_model(model="fallback-name")
        if hasattr(mock_model, "model_name"):
            del mock_model.model_name
        result = resolve_model(mock_model)
        assert result.provider == "prebuilt"


class TestResolveModelString:
    @patch("core.systems.llm.model_resolver._check_provider_available")
    @patch("core.systems.llm.model_resolver._build_model_from_provider")
    def test_openai_string(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        result = resolve_model("gpt-4o", temperature=0.5)
        mock_check.assert_called_once_with("openai")
        mock_build.assert_called_once_with("openai", "gpt-4o", temperature=0.5, api_key=None, base_url=None)
        assert result.provider == "openai"
        assert result.model_name == "gpt-4o"

    @patch("core.systems.llm.model_resolver._check_provider_available")
    @patch("core.systems.llm.model_resolver._build_model_from_provider")
    def test_anthropic_string(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        result = resolve_model("anthropic:claude-sonnet-4-20250514", api_key="sk-test")
        mock_check.assert_called_once_with("anthropic")
        assert result.provider == "anthropic"
        assert result.model_name == "claude-sonnet-4-20250514"


class TestResolveModelDict:
    @patch("core.systems.llm.model_resolver._check_provider_available")
    @patch("core.systems.llm.model_resolver._build_model_from_provider")
    def test_dict_with_provider(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        spec = {"provider": "google", "model": "gemini-2.0-flash", "temperature": 0.3}
        result = resolve_model(spec)
        mock_check.assert_called_once_with("google")
        assert result.provider == "google"
        assert result.model_name == "gemini-2.0-flash"

    @patch("core.systems.llm.model_resolver._check_provider_available")
    @patch("core.systems.llm.model_resolver._build_model_from_provider")
    def test_dict_without_provider_uses_base_url(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        spec = {"model": "custom-model", "api_base": "http://localhost:8080/v1"}
        result = resolve_model(spec)
        mock_check.assert_called_once_with("openai")
        assert result.provider == "openai"

    def test_dict_missing_model_raises(self):
        with pytest.raises(ModelProviderError, match="must include 'model'"):
            resolve_model({"provider": "openai"})

    @patch("core.systems.llm.model_resolver._check_provider_available")
    @patch("core.systems.llm.model_resolver._build_model_from_provider")
    def test_dict_provider_model_format(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        spec = {"model": "anthropic:claude-sonnet-4-20250514"}
        result = resolve_model(spec)
        mock_check.assert_called_once_with("anthropic")
        assert result.model_name == "claude-sonnet-4-20250514"

    @patch("core.systems.llm.model_resolver._check_provider_available")
    @patch("core.systems.llm.model_resolver._build_model_from_provider")
    def test_dict_api_base_alias(self, mock_build, mock_check):
        mock_build.return_value = MagicMock()
        spec = {"model": "gpt-4o", "api_base": "http://proxy/v1"}
        resolve_model(spec)
        call_kwargs = mock_build.call_args
        assert call_kwargs.kwargs.get("base_url") == "http://proxy/v1"


class TestResolveModelErrors:
    def test_unknown_provider(self):
        with pytest.raises(ModelProviderError, match="Unknown provider"):
            resolve_model("nonexistent:model-x")

    def test_unsupported_spec_type(self):
        with pytest.raises(ModelProviderError, match="Unsupported spec type"):
            resolve_model(42)


class TestProviderListing:
    @patch("core.systems.llm.model_resolver.importlib.import_module")
    def test_list_available(self, mock_import):
        mock_import.side_effect = lambda pkg: None if pkg == "langchain_openai" else (_ for _ in ()).throw(ImportError)
        result = list_available_providers()
        assert "openai" in result

    @patch("core.systems.llm.model_resolver.importlib.import_module")
    def test_list_all(self, mock_import):
        mock_import.side_effect = lambda pkg: None if pkg == "langchain_openai" else (_ for _ in ()).throw(ImportError)
        result = list_all_providers()
        assert result["openai"] is True
        assert result["anthropic"] is False


class TestCheckProviderAvailable:
    def test_missing_package_gives_install_hint(self):
        with patch("core.systems.llm.model_resolver.importlib.import_module", side_effect=ImportError):
            with pytest.raises(ModelProviderError, match="pip install langchain-anthropic"):
                resolve_model("anthropic:claude-sonnet-4-20250514")


# ---------------------------------------------------------------------------
# 6. Model Failover Tests (formerly test_model_failover.py)
# ---------------------------------------------------------------------------

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

    @patch("core.systems.llm.model_failover.time.sleep")
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


# ---------------------------------------------------------------------------
# 7. Model Router Tests (formerly test_model_router.py)
# ---------------------------------------------------------------------------

class TestModelRouterClassification:
    def setup_method(self):
        self.router = ModelRouter()

    def test_simple_greeting_routes_light(self):
        d = self.router.classify("你好")
        assert d.tier == ModelTier.LIGHT

    def test_hello_routes_light(self):
        d = self.router.classify("hello")
        assert d.tier == ModelTier.LIGHT

    def test_complex_task_routes_heavy(self):
        d = self.router.classify("请写一个完整的用户认证系统，包括注册、登录、JWT token管理")
        assert d.tier == ModelTier.HEAVY

    def test_refactor_routes_heavy(self):
        d = self.router.classify("帮我重构这段代码，优化性能")
        assert d.tier == ModelTier.HEAVY

    def test_debug_routes_heavy(self):
        d = self.router.classify("debug this error: TypeError at line 42")
        assert d.tier == ModelTier.HEAVY

    def test_medium_default(self):
        d = self.router.classify("帮我解释一下这个函数的作用")
        assert d.tier == ModelTier.MEDIUM

    def test_long_prompt_routes_heavy(self):
        long_prompt = "请分析以下代码：\n" + "x = 1\n" * 500
        d = self.router.classify(long_prompt)
        assert d.tier == ModelTier.HEAVY

    def test_short_factual_routes_light(self):
        d = self.router.classify("什么是Python")
        assert d.tier == ModelTier.LIGHT


class TestCanvasBias:
    def test_focused_biases_light(self):
        router = ModelRouter(canvas="focused")
        d = router.classify("解释下这个概念")
        assert d.tier in (ModelTier.LIGHT, ModelTier.MEDIUM)

    def test_deep_biases_heavy(self):
        router = ModelRouter(canvas="deep")
        d = router.classify("解释下这个概念")
        assert d.tier == ModelTier.HEAVY


class TestExplicitHint:
    def test_hint_overrides_classification(self):
        router = ModelRouter()
        d = router.classify("你好", hint=ModelTier.HEAVY)
        assert d.tier == ModelTier.HEAVY
        assert d.reason == "explicit_hint"

    def test_string_hint(self):
        router = ModelRouter()
        d = router.classify("你好", hint="light")
        assert d.tier == ModelTier.LIGHT


class TestToolBinding:
    def test_tools_upgrade_light_to_medium(self):
        router = ModelRouter()
        d = router.classify("hello", has_tools=True)
        assert d.tier == ModelTier.MEDIUM


class TestRouterDisabled:
    def test_disabled_always_medium(self):
        router = ModelRouter(enabled=False)
        d = router.classify("写一个完整的系统")
        assert d.tier == ModelTier.MEDIUM
        assert d.reason == "router_disabled"


class TestRouterStats:
    def test_stats_accumulate(self):
        router = ModelRouter()
        router.classify("你好")
        router.classify("写一个完整的系统")
        router.classify("解释一下")
        stats = router.stats.to_dict()
        assert stats["total"] == 3
        assert stats["light"] + stats["medium"] + stats["heavy"] == 3


class TestTierUpdate:
    def test_update_tier_model(self):
        router = ModelRouter()
        router.update_tier(ModelTier.LIGHT, "gpt-3.5-turbo")
        d = router.classify("hello")
        assert d.model_spec == "gpt-3.5-turbo"


class TestFactory:
    def test_create_from_config(self):
        config = {
            "enabled": True,
            "light": {"model": "gpt-4o-mini", "max_tokens": 1024},
            "heavy": {"model": "claude-sonnet-4-20250514", "max_tokens": 32768},
        }
        router = create_model_router(config, default_model="gpt-4o")
        assert router._tiers[ModelTier.LIGHT].model_spec == "gpt-4o-mini"
        assert router._tiers[ModelTier.HEAVY].model_spec == "claude-sonnet-4-20250514"
        assert router._tiers[ModelTier.MEDIUM].model_spec == "gpt-4o"

    def test_create_default(self):
        router = create_model_router()
        assert router._enabled is True
        d = router.classify("你好")
        assert d.model_spec == "gpt-4o-mini"


class TestRoutingDecision:
    def test_decision_fields(self):
        router = ModelRouter()
        d = router.classify("implement a REST API")
        assert isinstance(d, RoutingDecision)
        assert isinstance(d.tier, ModelTier)
        assert isinstance(d.model_spec, str)
        assert "max_tokens" in d.overrides
        assert "temperature" in d.overrides


# ---------------------------------------------------------------------------
# 8. Observability and Cost Tracker Tests (formerly test_observability.py)
# ---------------------------------------------------------------------------

class TestObservabilityConfig:
    def test_defaults(self):
        cfg = ObservabilityConfig()
        assert cfg.backend == "none"
        assert cfg.langfuse_public_key is None

    def test_from_dict(self):
        d = {"observability": {"backend": "langsmith", "log_level": "DEBUG"}}
        cfg = get_observability_config_from_dict(d)
        assert cfg.backend == "langsmith"
        assert cfg.log_level == "DEBUG"

    def test_from_empty_dict(self):
        cfg = get_observability_config_from_dict({})
        assert cfg.backend == "none"


class TestSetupTracing:
    def test_none_backend(self):
        callbacks = setup_tracing(ObservabilityConfig(backend="none"))
        assert callbacks == []

    def test_disabled_backend(self):
        callbacks = setup_tracing(ObservabilityConfig(backend="disabled"))
        assert callbacks == []

    def test_console_backend(self):
        callbacks = setup_tracing(ObservabilityConfig(backend="console"))
        assert len(callbacks) == 1

    def test_unknown_backend(self):
        callbacks = setup_tracing(ObservabilityConfig(backend="unknown_thing"))
        assert callbacks == []

    def test_langsmith_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            callbacks = setup_tracing(ObservabilityConfig(backend="langsmith"))
            assert callbacks == []

    def test_langfuse_without_keys(self):
        callbacks = setup_tracing(ObservabilityConfig(backend="langfuse"))
        assert callbacks == []


class TestCostEstimate:
    def test_known_model(self):
        cost = _estimate_cost("gpt-4o", 1000, 500)
        assert cost > 0

    def test_unknown_model(self):
        cost = _estimate_cost("totally-unknown-model", 1000, 500)
        assert cost == 0.0

    def test_zero_tokens(self):
        cost = _estimate_cost("gpt-4o", 0, 0)
        assert cost == 0.0


class TestCostTrackerClass:
    def test_record_llm_call(self):
        tracker = CostTracker()
        record = tracker.record_llm_call("gpt-4o", 100, 50, duration_ms=200)
        assert record.total_tokens == 150
        assert record.cost_usd > 0

    def test_record_tool_call(self):
        tracker = CostTracker()
        record = tracker.record_tool_call("search", duration_ms=100, success=True)
        assert record.tool_name == "search"
        assert record.success is True

    def test_summary_aggregation(self):
        tracker = CostTracker()
        tracker.record_llm_call("gpt-4o", 100, 50)
        tracker.record_llm_call("gpt-4o", 200, 100)
        tracker.record_tool_call("search", duration_ms=50)
        tracker.record_tool_call("search", duration_ms=30, success=False)

        summary = tracker.get_summary()
        assert summary.total_llm_calls == 2
        assert summary.total_tool_calls == 2
        assert summary.total_input_tokens == 300
        assert summary.total_output_tokens == 150
        assert summary.total_cost_usd > 0
        assert "gpt-4o" in summary.model_breakdown
        assert summary.model_breakdown["gpt-4o"]["calls"] == 2
        assert "search" in summary.tool_breakdown
        assert summary.tool_breakdown["search"]["failures"] == 1

    def test_summary_to_dict(self):
        tracker = CostTracker()
        tracker.record_llm_call("gpt-4o", 100, 50)
        d = tracker.get_summary().to_dict()
        assert isinstance(d, dict)
        assert "total_cost_usd" in d

    def test_persist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "costs.json")
            tracker = CostTracker(persist_path=path)
            tracker.record_llm_call("gpt-4o", 100, 50)
            assert os.path.exists(path)

    def test_empty_summary(self):
        tracker = CostTracker()
        summary = tracker.get_summary()
        assert summary.total_llm_calls == 0
        assert summary.total_cost_usd == 0.0


class TestCostTrackerCallbackClass:
    def test_callback_records_tool_events(self):
        tracker = CostTracker()
        callback = CostTrackerCallback(tracker)

        callback.on_tool_start({}, "input", run_id="t1")
        callback.on_tool_end("output", run_id="t1", name="my_tool")

        summary = tracker.get_summary()
        assert summary.total_tool_calls == 1

    def test_callback_records_tool_error(self):
        tracker = CostTracker()
        callback = CostTrackerCallback(tracker)

        callback.on_tool_start({}, "input", run_id="t2")
        callback.on_tool_error(Exception("fail"), run_id="t2", name="bad_tool")

        summary = tracker.get_summary()
        assert summary.total_tool_calls == 1
        assert summary.tool_breakdown["bad_tool"]["failures"] == 1


# ── Pause and Resume agent runtime ────────────────────────────────────

class TestSimplePausableAgent:
    def test_initial_state(self):
        a = SimplePausableAgent("agent1")
        assert a.agent_name == "agent1"
        assert a.pause_state == PauseState.RUNNING
        assert a.saved_context is None

    def test_pause(self):
        a = SimplePausableAgent("a")
        ctx = PauseContext(reason="test", paused_by="user")
        a.on_pause(ctx)
        assert a.pause_state == PauseState.PAUSED
        assert a.saved_context is not None
        assert a.saved_context.reason == "test"

    def test_resume(self):
        a = SimplePausableAgent("a")
        a.on_pause(PauseContext(reason="pause"))
        a.on_resume(PauseContext(reason="resume"))
        assert a.pause_state == PauseState.RUNNING
        assert a.saved_context is None

    def test_wait_if_paused_immediate(self):
        a = SimplePausableAgent("a")
        assert a.wait_if_paused(timeout=0.01)

    def test_wait_if_paused_blocks(self):
        a = SimplePausableAgent("a")
        a.on_pause(PauseContext(reason="block"))
        result = a.wait_if_paused(timeout=0.05)
        assert not result

    def test_wait_then_resume(self):
        a = SimplePausableAgent("a")
        a.on_pause(PauseContext(reason="wait"))

        def resume_later():
            time.sleep(0.05)
            a.on_resume(PauseContext())

        t = threading.Thread(target=resume_later)
        t.start()
        result = a.wait_if_paused(timeout=1.0)
        t.join()
        assert result
        assert a.pause_state == PauseState.RUNNING


class TestPauseManager:
    def setup_method(self):
        self.mgr = PauseManager()
        self.a1 = SimplePausableAgent("agent1")
        self.a2 = SimplePausableAgent("agent2")
        self.mgr.register(self.a1)
        self.mgr.register(self.a2)

    def test_pause_all(self):
        count = self.mgr.pause_all("maintenance")
        assert count == 2
        assert self.a1.pause_state == PauseState.PAUSED
        assert self.a2.pause_state == PauseState.PAUSED
        assert self.mgr.global_state == PauseState.PAUSED

    def test_resume_all(self):
        self.mgr.pause_all("test")
        count = self.mgr.resume_all()
        assert count == 2
        assert self.a1.pause_state == PauseState.RUNNING
        assert self.a2.pause_state == PauseState.RUNNING
        assert self.mgr.global_state == PauseState.RUNNING

    def test_resume_not_paused(self):
        count = self.mgr.resume_all()
        assert count == 0

    def test_pause_single(self):
        assert self.mgr.pause_agent("agent1", "individual")
        assert self.a1.pause_state == PauseState.PAUSED
        assert self.a2.pause_state == PauseState.RUNNING

    def test_resume_single(self):
        self.mgr.pause_all()
        assert self.mgr.resume_agent("agent1")
        assert self.a1.pause_state == PauseState.RUNNING
        assert self.a2.pause_state == PauseState.PAUSED

    def test_pause_nonexistent(self):
        assert not self.mgr.pause_agent("ghost")

    def test_resume_nonexistent(self):
        assert not self.mgr.resume_agent("ghost")

    def test_resume_already_running(self):
        assert not self.mgr.resume_agent("agent1")

    def test_unregister(self):
        assert self.mgr.unregister("agent1")
        assert not self.mgr.unregister("agent1")
        status = self.mgr.status()
        assert status["agent_count"] == 1

    def test_status(self):
        self.mgr.pause_agent("agent1")
        status = self.mgr.status()
        assert status["agent_count"] == 2
        assert status["paused_count"] == 1
        assert status["agents"]["agent1"] == "paused"
        assert status["agents"]["agent2"] == "running"

    def test_resume_with_data(self):
        self.mgr.pause_all()
        self.mgr.resume_all(data={"approval": True})
        assert self.a1.pause_state == PauseState.RUNNING

    def test_concurrent_pause_resume(self):
        agents = [SimplePausableAgent(f"a{i}") for i in range(10)]
        for a in agents:
            self.mgr.register(a)
        errors = []

        def cycle():
            try:
                for _ in range(5):
                    self.mgr.pause_all("concurrent")
                    self.mgr.resume_all()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=cycle) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ── Agent-to-Agent Communication Protocol ─────────────────────────────

@pytest.fixture
def local_card():
    return AgentCard(
        agent_id="pybot-1",
        name="PyBot Instance 1",
        description="Primary instance",
        endpoint="http://localhost:8000",
        capabilities=["code_gen", "rag", "workflow"],
        skills=["python", "data_analysis"],
    )


@pytest.fixture
def peer_card():
    return AgentCard(
        agent_id="pybot-2",
        name="PyBot Instance 2",
        description="Secondary instance",
        endpoint="http://remote:8000",
        capabilities=["image_gen", "translation"],
        skills=["design", "multilingual"],
    )


class TestAgentCard:
    def test_to_dict_roundtrip(self, local_card):
        data = local_card.to_dict()
        restored = AgentCard.from_dict(data)
        assert restored.agent_id == local_card.agent_id
        assert restored.capabilities == local_card.capabilities

    def test_default_protocols(self, local_card):
        assert "a2a/1.0" in local_card.protocols


class TestA2ARegistry:
    def test_register_and_list_peers(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)
        peers = registry.list_peers()
        assert len(peers) == 1
        assert peers[0]["agent_id"] == "pybot-2"

    def test_unregister_peer(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)
        assert registry.unregister_peer("pybot-2") is True
        assert registry.unregister_peer("nonexistent") is False
        assert len(registry.list_peers()) == 0

    def test_find_capable_peers(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)

        result = registry.find_capable_peers("translation")
        assert len(result) == 1
        assert result[0].agent_id == "pybot-2"

        result = registry.find_capable_peers("nonexistent")
        assert len(result) == 0

    def test_find_by_skill(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)
        result = registry.find_capable_peers("design")
        assert len(result) == 1


class TestA2ATask:
    def test_create_task(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)
        task = registry.create_task(
            receiver_id="pybot-2",
            action="translate",
            payload={"text": "Hello", "target_lang": "zh"},
        )
        assert task.sender_id == "pybot-1"
        assert task.receiver_id == "pybot-2"
        assert task.state == TaskState.PENDING

    def test_update_task(self, local_card):
        registry = A2ARegistry(local_card=local_card)
        task = registry.create_task("peer", "test")
        updated = registry.update_task(
            task.task_id,
            state=TaskState.COMPLETED,
            result={"output": "done"},
        )
        assert updated.state == TaskState.COMPLETED
        assert updated.result == {"output": "done"}

    def test_update_nonexistent_task(self, local_card):
        registry = A2ARegistry(local_card=local_card)
        assert registry.update_task("fake_id", state=TaskState.FAILED) is None

    def test_list_tasks_by_state(self, local_card):
        registry = A2ARegistry(local_card=local_card)
        t1 = registry.create_task("peer", "task1")
        t2 = registry.create_task("peer", "task2")
        registry.update_task(t1.task_id, state=TaskState.COMPLETED)

        pending = registry.list_tasks(state=TaskState.PENDING)
        assert len(pending) == 1
        completed = registry.list_tasks(state=TaskState.COMPLETED)
        assert len(completed) == 1

    def test_receive_task(self, local_card):
        registry = A2ARegistry(local_card=local_card)
        incoming = {
            "task_id": "remote-001",
            "sender_id": "pybot-3",
            "receiver_id": "pybot-1",
            "action": "summarize",
            "payload": {"text": "Long document..."},
        }
        task = registry.receive_task(incoming)
        assert task.task_id == "remote-001"
        assert task.state == TaskState.PENDING

    def test_task_serialization(self):
        task = A2ATask(
            sender_id="a",
            receiver_id="b",
            action="test",
            state=TaskState.IN_PROGRESS,
        )
        data = task.to_dict()
        restored = A2ATask.from_dict(data)
        assert restored.state == TaskState.IN_PROGRESS


class TestRegistryOverview:
    def test_to_dict(self, local_card, peer_card):
        registry = A2ARegistry(local_card=local_card)
        registry.register_peer(peer_card)
        registry.create_task("pybot-2", "test")
        overview = registry.to_dict()
        assert overview["local_card"]["agent_id"] == "pybot-1"
        assert len(overview["peers"]) == 1
        assert overview["pending_tasks"] == 1
        assert overview["total_tasks"] == 1


# ── Interactive CLI Application support ────────────────────────────────

class FakeStorage:
    def __init__(self) -> None:
        self.tools = {
            "demo_tool": {
                "description": "Demo tool",
                "parameters": [{"name": "value", "type": "str"}],
                "usage_count": 2,
            }
        }

    def remove_tool(self, name: str) -> bool:
        self.tools.pop(name, None)
        return True


class FakeBot:
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.storage = FakeStorage()

    def chat(self, message: str) -> str:
        return f"reply:{message}"

    def list_tools(self) -> dict[str, str]:
        return {name: tool["description"] for name, tool in self.storage.tools.items()}

    def list_agents(self) -> dict[str, str]:
        return {"helper": "Shared helper agent"}

    def get_tool_usage_stats(self) -> dict[str, int]:
        return {"demo_tool": 2}


@pytest.fixture
def cli_config() -> dict:
    return {
        "llm_config": {
            "api_key": "test-key",
            "api_base": "https://example.com/v1",
            "model": "gpt-4o-mini",
            "temperature": 0.2,
        },
        "agent_config": {"thread_id": "seed-thread"},
    }


def make_console() -> Console:
    import io
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=100)


def test_load_required_config_raises_without_api_key(tmp_path):
    with pytest.raises(CliConfigError):
        load_required_config(tmp_path / "missing.json")


def test_reset_command_rebuilds_runtime(temp_paths, cli_config):
    created_threads: list[str] = []

    def fake_agent_factory(**kwargs):
        created_threads.append(kwargs["thread_id"])
        return FakeBot(kwargs["thread_id"])

    app = InteractiveCliApp(
        config=cli_config,
        paths=temp_paths,
        console=make_console(),
        agent_factory=fake_agent_factory,
        confirm=lambda *args, **kwargs: True,
        sleep=lambda *_: None,
    )

    app.initialize_agent()
    original_thread = app.thread_id
    should_continue = app.handle_command("/reset")

    assert should_continue is True
    assert app.thread_id != original_thread
    assert created_threads[0] == "seed-thread"
    assert created_threads[1] == app.thread_id


def test_clear_command_removes_tools_and_rebuilds_runtime(temp_paths, cli_config):
    created_bots: list[FakeBot] = []

    def fake_agent_factory(**kwargs):
        bot = FakeBot(kwargs["thread_id"])
        created_bots.append(bot)
        return bot

    app = InteractiveCliApp(
        config=cli_config,
        paths=temp_paths,
        console=make_console(),
        agent_factory=fake_agent_factory,
        confirm=lambda *args, **kwargs: True,
        sleep=lambda *_: None,
    )

    app.initialize_agent()
    app.handle_command("/clear")

    assert len(created_bots) == 2
    assert created_bots[0].storage.tools == {}


def test_quit_command_stops_loop(temp_paths, cli_config):
    app = InteractiveCliApp(
        config=cli_config,
        paths=temp_paths,
        console=make_console(),
        agent_factory=lambda **kwargs: FakeBot(kwargs["thread_id"]),
        confirm=lambda *args, **kwargs: True,
        sleep=lambda *_: None,
    )

    app.initialize_agent()
    assert app.handle_command("/quit") is False


# ── Mock LLM Basics and Callers ────────────────────────────────────────

class TestMockLLMBasics:
    def test_sequential_responses(self):
        llm = MockLLM(responses=["first", "second", "third"])
        assert llm.invoke("a").content == "first"
        assert llm.invoke("b").content == "second"
        assert llm.invoke("c").content == "third"
        assert llm.call_count == 3

    def test_default_fallback(self):
        llm = MockLLM(default_response="fallback")
        assert llm.invoke("anything").content == "fallback"

    def test_pattern_matching(self):
        llm = MockLLM(pattern_responses={
            r"weather": "It's sunny",
            r"time": "It's noon",
        })
        assert llm.invoke("What's the weather?").content == "It's sunny"
        assert llm.invoke("What time is it?").content == "It's noon"
        assert llm.invoke("hello").content == "Mock response"

    def test_tool_call_simulation(self):
        llm = MockLLM(tool_call_responses={
            r"calculate": [ToolCallCompat(name="calculator", args={"expr": "2+2"})],
        })
        result = llm.invoke("Please calculate 2+2")
        assert result.content == ""
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "calculator"

    def test_history_recording(self):
        llm = MockLLM(default_response="ok")
        llm.invoke("first prompt")
        llm.invoke("second prompt")
        assert len(llm.history) == 2
        assert llm.history[0]["prompt"] == "first prompt"

    def test_reset(self):
        llm = MockLLM(responses=["a", "b"])
        llm.invoke("x")
        llm.reset()
        assert llm.call_count == 0
        assert llm.invoke("y").content == "a"

    def test_bind_tools_returns_self(self):
        llm = MockLLM()
        assert llm.bind_tools([]) is llm


class TestMockLLMFactory:
    def test_factory_creates_configured_instances(self):
        factory = MockLLMFactory(default_response="factory_default")
        llm = factory(model="gpt-4o", temperature=0.5)
        assert llm.invoke("test").content == "factory_default"
        assert len(factory.created) == 1

    def test_factory_with_patterns(self):
        factory = MockLLMFactory(
            pattern_responses={r"code": "```python\nprint('hi')\n```"},
            default_response="no code",
        )
        llm = factory()
        assert "python" in llm.invoke("Write code").content
        assert llm.invoke("hello").content == "no code"


class TestMockLLMCaller:
    def test_simple_caller(self):
        caller = mock_llm_caller(response="distilled memory")
        result = caller("system prompt", "user input")
        assert result == "distilled memory"
        assert len(caller.call_log) == 1

    def test_pattern_caller(self):
        caller = mock_llm_caller(
            pattern_responses={
                r"归纳": "- [偏好] 用户喜欢Python",
                r"蒸馏|distill": "[MEMORY]\n## 技术偏好\n- Python",
            },
        )
        assert "偏好" in caller("sys", "请归纳对话")
        assert "[MEMORY]" in caller("sys", "请蒸馏记忆")


class TestAsyncMockLLM:
    def test_ainvoke(self):
        import asyncio

        async def _run() -> tuple[str, int]:
            llm = MockLLM(responses=["async response"])
            result = await llm.ainvoke("async prompt")
            return result.content, llm.call_count

        content, calls = asyncio.run(_run())
        assert content == "async response"
        assert calls == 1

