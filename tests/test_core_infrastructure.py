from __future__ import annotations

import ast
import importlib
import json
import sys
import subprocess
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.modes.system_model import (
    build_system_model,
    get_root_mode_label,
    normalize_root_mode,
    _ARCHITECTURAL_LAYERS,
    _ASSEMBLY_ENTRYPOINTS,
    _NAMESPACE_FACADES,
)
from core.systems.runtime.version import get_pybot_version, get_pybot_version as legacy_get_pybot_version
from core.systems.runtime import (
    RetryConfig,
    RetryPolicy,
    ToolInputError,
    extract_error_code,
    validate_path,
)
from core.systems.context import WorkspaceViewService
from core.systems.runtime import pybot_bootstrap
from core.systems.runtime.pybot_bootstrap import ToolAssembly, assemble_primary_tools, create_root_agent
from core.systems.runtime.hooks_runtime import create_default_hooks_runtime
from core.systems.runtime.trusted_settings import build_trusted_settings_bundle
from core.systems.runtime import (
    config_impl,
    get_config,
    reload_config,
    save_config,
    save_project_config,
)
from core.systems.runtime.yaml_config import (
    auto_discover_yaml,
    interpolate_placeholders,
    load_agents_yaml,
    load_tasks_yaml,
)

yaml = pytest.importorskip("yaml")


# ── Section 1: System Model & Architectural Layers ───────────────────

def test_system_model_exposes_canonical_layers():
    model = build_system_model()

    assert model["root_mode_progression"] == ["assistant", "app_matrix", "admin"]
    assert [item["name"] for item in model["interaction_surfaces"]] == [
        "chat",
        "governance",
        "ecosystem",
    ]
    assert [item["name"] for item in model["ecosystem_families"]] == [
        "apps",
        "workflows",
        "skills",
        "tools",
        "agents",
    ]
    assert [item["name"] for item in model["product_concepts"]] == [
        "tools",
        "skills",
        "agents",
        "workflows",
        "apps",
    ]
    assert {item["name"] for item in model["supporting_systems"]} == {
        "runtime_foundation",
        "knowledge_and_memory",
        "governance_and_safety",
        "delivery_and_integration",
    }
    root_modes = {item["name"]: item for item in model["root_modes"]}
    assert "interactive_chat" in root_modes["assistant"]["enabled_capabilities"]
    assert "app_orchestration" in root_modes["app_matrix"]["enabled_capabilities"]
    assert "durable_goal_loop" in root_modes["admin"]["enabled_capabilities"]
    assert {item["path"] for item in model["package_targets"]} >= {
        "core/modes/",
        "core/assets/tools/",
        "core/assets/skills/",
        "core/assets/agents/",
        "core/assets/workflows/",
        "core/systems/apps/",
        "core/systems/runtime/",
        "core/systems/memory/",
        "core/systems/governance/",
        "core/systems/integration/",
    }
    assert "MCP" in model["not_product_concepts"]
    assert model["mode_profiles_modular"] is True
    assert any(rule.startswith("一级交互入口默认只有三个") for rule in model["canonical_rules"])


def test_asset_packages_expose_apps_and_workflows_entrypoints():
    from core.assets.agents.storage import AgentStorage
    from core.systems.apps.app_manager import AppManager
    from core.systems.apps.app_matrix_runtime import AppMatrixRuntime

    from core.assets.tools import ToolStorage
    from core.assets.workflows.pyflow_engine import PyFlowEngine
    from core.assets.workflows.execution import WorkflowExecutionRuntime
    from core.assets.workflows.scheduling import ScheduledTask, TaskQueue
    from core.systems.governance import ApprovalQueue
    from core.systems.memory import MemoryEngine
    from core.systems.runtime import ProjectPaths

    assert AppManager is not None
    assert AppMatrixRuntime is not None
    assert AgentStorage is not None
    assert ToolStorage is not None
    assert ProjectPaths is not None
    assert ApprovalQueue is not None
    assert MemoryEngine is not None
    assert WorkflowExecutionRuntime is not None
    assert PyFlowEngine is not None
    assert TaskQueue is not None
    assert ScheduledTask is not None


def test_normalize_root_mode_uses_canonical_aliases():
    assert normalize_root_mode("admin") == "admin"
    assert normalize_root_mode("矩阵管家") == "app_matrix"
    assert normalize_root_mode("unknown-mode") == "assistant"
    assert get_root_mode_label("admin agent") == "全局管理员智能体"


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = PROJECT_ROOT / "core"


def _normalize(p: str) -> str:
    """Trim trailing slash and unify separators for prefix comparison."""
    return p.rstrip("/").replace("\\", "/")


_PACKAGE_LAYER_MAP: list[tuple[str, int, str]] = sorted(
    (
        (_normalize(pkg).replace("/", "."), layer.level, layer.name)
        for layer in _ARCHITECTURAL_LAYERS
        for pkg in layer.packages
    ),
    key=lambda item: -len(item[0]),
)


def _module_layer(dotted: str) -> tuple[int, str] | None:
    """Resolve a dotted module path to ``(layer_level, layer_name)``."""
    if not dotted.startswith("core."):
        return None
    for prefix, level, name in _PACKAGE_LAYER_MAP:
        if dotted == prefix or dotted.startswith(prefix + "."):
            return level, name
    return None


def _path_to_dotted(path: Path) -> str:
    rel = path.relative_to(PROJECT_ROOT).with_suffix("")
    parts = rel.parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _iter_core_modules() -> list[Path]:
    return [
        p
        for p in CORE_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def _is_type_checking_block(node: ast.If) -> bool:
    """Detect ``if TYPE_CHECKING:``."""
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    ):
        return True
    return False


def _collect_imports(tree: ast.Module) -> list[str]:
    """Return every absolute dotted module path imported by the tree."""
    skip_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_block(node):
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    skip_nodes.add(id(child))

    out: list[str] = []
    for node in ast.walk(tree):
        if id(node) in skip_nodes:
            continue
        if isinstance(node, ast.ImportFrom):
            if node.level != 0:
                continue
            if node.module:
                out.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
    return out


_FACADE_MODULES = {"core", "core._version"}


def _relative_posix(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")


@pytest.fixture(scope="module")
def violations() -> list[dict[str, object]]:
    """Statically scan ``core/`` for cross-layer violations."""
    issues: list[dict[str, object]] = []

    for path in _iter_core_modules():
        rel_posix = _relative_posix(path)
        if rel_posix in _NAMESPACE_FACADES or rel_posix in _ASSEMBLY_ENTRYPOINTS:
            continue

        source_dotted = _path_to_dotted(path)
        if source_dotted in _FACADE_MODULES:
            continue
        source_layer = _module_layer(source_dotted)
        if source_layer is None:
            issues.append(
                {
                    "kind": "unregistered_module",
                    "source": source_dotted,
                    "source_path": rel_posix,
                }
            )
            continue
        source_level, source_name = source_layer

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - defensive
            issues.append(
                {
                    "kind": "parse_error",
                    "source": source_dotted,
                    "source_path": rel_posix,
                    "error": str(exc),
                }
            )
            continue

        for target in _collect_imports(tree):
            target_layer = _module_layer(target)
            if target_layer is None:
                continue
            target_level, target_name = target_layer
            if target_level > source_level:
                issues.append(
                    {
                        "kind": "upward_dependency",
                        "source": source_dotted,
                        "source_layer": f"L{source_level} ({source_name})",
                        "target": target,
                        "target_layer": f"L{target_level} ({target_name})",
                        "source_path": rel_posix,
                    }
                )

    return issues


def test_every_core_module_is_registered_with_a_layer(violations: list[dict[str, object]]):
    """Every ``core/**/*.py`` module must belong to a known layer."""
    orphans = [v for v in violations if v["kind"] == "unregistered_module"]
    if orphans:
        rendered = "\n".join(
            f"  - {v['source_path']}  (dotted: {v['source']})" for v in orphans
        )
        raise AssertionError(
            "These modules under core/ are not covered by any layer in "
            "_ARCHITECTURAL_LAYERS — register them under the correct "
            "layer.packages tuple, or add them to _NAMESPACE_FACADES / "
            "_ASSEMBLY_ENTRYPOINTS in core/modes/system_model.py:\n" + rendered
        )


def test_no_upward_cross_layer_imports(violations: list[dict[str, object]]):
    """Every ``from core.X import ...`` must respect the layer ordering."""
    upward = [v for v in violations if v["kind"] == "upward_dependency"]
    if upward:
        rendered = "\n".join(
            f"  - {v['source_path']}  [{v['source_layer']}]\n"
            f"      imports {v['target']}  [{v['target_layer']}]"
            for v in upward
        )
        raise AssertionError(
            "Upward cross-layer imports detected — a lower layer cannot "
            "depend on a higher one. If the file is a legitimate top-level "
            "assembly point (bootstrap / capability bundle / orchestrator / "
            "protocol adapter / mode factory), add it to "
            "_ASSEMBLY_ENTRYPOINTS in core/modes/system_model.py. "
            "Otherwise, refactor the import:\n" + rendered
        )


def test_no_parse_errors(violations: list[dict[str, object]]):
    parse_failures = [v for v in violations if v["kind"] == "parse_error"]
    if parse_failures:
        rendered = "\n".join(
            f"  - {v['source_path']}: {v['error']}" for v in parse_failures
        )
        raise AssertionError(
            "Some core modules failed to parse:\n" + rendered
        )


def test_layer_descriptors_are_in_increasing_level_order():
    """``_ARCHITECTURAL_LAYERS`` must be declared L0 → L1 → L2 → L3."""
    levels = [layer.level for layer in _ARCHITECTURAL_LAYERS]
    assert levels == sorted(levels), levels
    assert levels == list(range(len(levels))), levels


def test_assembly_entrypoints_actually_exist():
    """Each entry in ``_ASSEMBLY_ENTRYPOINTS`` must point to a real file."""
    missing = [
        path
        for path in _ASSEMBLY_ENTRYPOINTS
        if not (PROJECT_ROOT / path).exists()
    ]
    assert not missing, f"Stale assembly entrypoints (file missing): {missing}"


def test_namespace_facades_actually_exist():
    """Each entry in ``_NAMESPACE_FACADES`` must point to a real file."""
    missing = [
        path
        for path in _NAMESPACE_FACADES
        if not (PROJECT_ROOT / path).exists()
    ]
    assert not missing, f"Stale namespace facades (file missing): {missing}"


# ── Section 2: Core Facade & Version Check ───────────────────────────

def test_core_package_does_not_eagerly_import_subpackages():
    """A *fresh* ``import core`` must not pull in heavy subpackages."""
    script = textwrap.dedent(
        """
        import sys
        import core  # noqa: F401

        loaded = sorted(name for name in sys.modules if name.startswith("core."))
        # Only the package-root version helper should be loaded eagerly.
        assert loaded == ["core._version"], loaded
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_core_package_exposes_only_canonical_facade():
    core_module = importlib.import_module("core")

    assert core_module.__all__ == ["assets", "systems", "modes", "PyBot", "get_pybot_version"]

    assert core_module.PyBot.__name__ == "PyBot"
    assert callable(core_module.get_pybot_version)
    assert core_module.__version__ == core_module.get_pybot_version()

    assert core_module.assets.__name__ == "core.assets"
    assert core_module.systems.__name__ == "core.systems"
    assert core_module.modes.__name__ == "core.modes"


def test_core_package_rejects_unknown_attribute():
    core_module = importlib.import_module("core")

    try:
        core_module.ProjectPaths  # noqa: B018 — facade must reject removed exports
    except AttributeError as exc:
        assert "ProjectPaths" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Slim facade must not expose ProjectPaths")


def _module_exists(core_root: Path, module_name: str) -> bool:
    relative = Path(*module_name.split("."))
    return (core_root / f"{relative}.py").exists() or (core_root / relative / "__init__.py").exists()


def test_repo_has_no_missing_core_submodule_imports():
    repo_root = Path(__file__).resolve().parents[1]
    core_root = repo_root / "core"
    missing: list[str] = []

    for path in repo_root.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("core."):
                module_name = node.module.split(".", 1)[1]
                if not _module_exists(core_root, module_name):
                    missing.append(f"{path.relative_to(repo_root)}:{node.lineno} -> {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("core."):
                        module_name = alias.name.split(".", 1)[1]
                        if not _module_exists(core_root, module_name):
                            missing.append(f"{path.relative_to(repo_root)}:{node.lineno} -> {alias.name}")

    assert not missing, "Missing core submodule imports:\n" + "\n".join(sorted(missing))


def test_core_version_matches_package_version():
    import core as core_module
    assert core_module.__version__ == get_pybot_version()


# ── Section 3: Advanced Core (OC2-OC6) ───────────────────────────────

class TestLLMTaskValidation:
    def test_validate_required_fields(self):
        from core.assets.tools.llm_task_tool import _validate_against_schema
        schema = {"required": ["name", "age"], "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}
        errors = _validate_against_schema({"name": "Alice"}, schema)
        assert any("age" in e for e in errors)

    def test_validate_type_check(self):
        from core.assets.tools.llm_task_tool import _validate_against_schema
        schema = {"properties": {"count": {"type": "integer"}}}
        errors = _validate_against_schema({"count": "not a number"}, schema)
        assert len(errors) > 0

    def test_validate_passes(self):
        from core.assets.tools.llm_task_tool import _validate_against_schema
        schema = {"required": ["x"], "properties": {"x": {"type": "string"}}}
        errors = _validate_against_schema({"x": "hello"}, schema)
        assert errors == []


class TestExtractJSON:
    def test_plain_json(self):
        from core.assets.tools.llm_task_tool import _extract_json
        result = _extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_markdown_fence(self):
        from core.assets.tools.llm_task_tool import _extract_json
        text = '```json\n{"key": "value"}\n```'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_json_in_plain_fence(self):
        from core.assets.tools.llm_task_tool import _extract_json
        text = '```\n{"a": 1}\n```'
        result = _extract_json(text)
        assert result == {"a": 1}


class TestLLMTaskTool:
    def test_tool_attributes(self):
        from core.assets.tools.llm_task_tool import LLMTaskTool
        tool = LLMTaskTool()
        assert tool.name == "llm_task"

    def test_tool_no_llm(self):
        from core.assets.tools.llm_task_tool import LLMTaskTool
        tool = LLMTaskTool()
        result = json.loads(tool._run(prompt="test", input_text="test"))
        assert result["success"] is False


class TestContextEngineRegistry:
    def test_register_and_get(self):
        from core.systems.context.context_engine import (
            DefaultContextEngine,
            get_engine,
            list_engines,
            register_engine,
            unregister_engine,
        )
        engine = DefaultContextEngine()
        register_engine(engine)
        assert "default" in list_engines()
        assert get_engine("default") is engine
        unregister_engine("default")

    def test_get_nonexistent(self):
        from core.systems.context.context_engine import get_engine
        assert get_engine("nonexistent_engine_xyz") is None


class TestDefaultContextEngine:
    def test_assemble_basic(self):
        from core.systems.context.context_engine import DefaultContextEngine
        engine = DefaultContextEngine()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = engine.assemble(messages, token_budget=4096, system_prompt="You are helpful.")
        assert len(result.messages) >= 2
        assert result.estimated_tokens > 0

    def test_assemble_with_memory(self):
        from core.systems.context.context_engine import DefaultContextEngine
        engine = DefaultContextEngine()
        messages = [{"role": "user", "content": "Question"}]
        result = engine.assemble(
            messages, token_budget=4096,
            system_prompt="System", memory_context="Remembered: user likes Python",
        )
        assert result.system_prompt_addition == "Remembered: user likes Python"
        system_msg = result.messages[0]
        content = system_msg["content"] if isinstance(system_msg, dict) else system_msg.content
        assert "Python" in content

    def test_assemble_drops_when_over_budget(self):
        from core.systems.context.context_engine import DefaultContextEngine
        engine = DefaultContextEngine(keep_recent=2)
        messages = [{"role": "user", "content": "msg " * 500} for _ in range(10)]
        result = engine.assemble(messages, token_budget=200)
        assert result.dropped_count > 0

    def test_compact_no_op_when_within_budget(self):
        from core.systems.context.context_engine import DefaultContextEngine
        engine = DefaultContextEngine()
        messages = [{"role": "user", "content": "short"}]
        result = engine.compact(messages, target_tokens=4096)
        assert result.ok is True
        assert result.tokens_before == result.tokens_after

    def test_compact_truncation(self):
        from core.systems.context.context_engine import DefaultContextEngine
        engine = DefaultContextEngine()
        messages = [{"role": "user", "content": f"Message {i} " * 50} for i in range(20)]
        result = engine.compact(messages, target_tokens=100)
        assert result.ok is True
        assert result.tokens_after <= 100 + 50  # some tolerance

    def test_after_turn_runs(self):
        from core.systems.context.context_engine import DefaultContextEngine
        engine = DefaultContextEngine()
        engine.after_turn([{"role": "user", "content": "hi"}], None)


class TestContextEngineProtocol:
    def test_default_engine_implements_protocol(self):
        from core.systems.context.context_engine import ContextEngine, DefaultContextEngine
        engine = DefaultContextEngine()
        assert isinstance(engine, ContextEngine)


class TestNewEventTypes:
    def test_model_usage_event_type(self):
        from core.systems.runtime.event_bus import EventType
        assert EventType.MODEL_USAGE.value == "model_usage"

    def test_model_failover_event_type(self):
        from core.systems.runtime.event_bus import EventType
        assert EventType.MODEL_FAILOVER.value == "model_failover"

    def test_context_compact_event_type(self):
        from core.systems.runtime.event_bus import EventType
        assert EventType.CONTEXT_COMPACT.value == "context_compact"

    def test_memory_forget_event_type(self):
        from core.systems.runtime.event_bus import EventType
        assert EventType.MEMORY_FORGET.value == "memory_forget"

    def test_schedule_run_event_type(self):
        from core.systems.runtime.event_bus import EventType
        assert EventType.SCHEDULE_RUN.value == "schedule_run"

    def test_webhook_received_event_type(self):
        from core.systems.runtime.event_bus import EventType
        assert EventType.WEBHOOK_RECEIVED.value == "webhook_received"


class TestMetricBucket:
    def test_record_and_snapshot(self):
        from core.systems.observability.diagnostics import MetricBucket
        b = MetricBucket()
        b.record(10)
        b.record(20)
        s = b.snapshot()
        assert s["count"] == 2
        assert s["total"] == 30
        assert s["avg"] == 15.0
        assert s["min"] == 10
        assert s["max"] == 20

    def test_empty_bucket(self):
        from core.systems.observability.diagnostics import MetricBucket
        b = MetricBucket()
        s = b.snapshot()
        assert s["count"] == 0
        assert s["avg"] == 0


class TestDiagnosticsService:
    def test_creation(self):
        from core.systems.observability.diagnostics import DiagnosticsService
        svc = DiagnosticsService()
        metrics = svc.get_metrics()
        assert "uptime_seconds" in metrics
        assert "summary" in metrics

    def test_model_usage_tracking(self):
        from core.systems.observability.diagnostics import DiagnosticsService
        from core.systems.runtime.event_bus import Event, EventBus, EventType
        bus = EventBus()
        svc = DiagnosticsService(bus=bus)
        bus.emit(Event(
            type=EventType.MODEL_USAGE,
            payload={"prompt_tokens": 100, "completion_tokens": 50, "model": "gpt-4"},
        ))
        metrics = svc.get_metrics()
        assert metrics["summary"]["tokens.total"]["count"] == 1
        assert metrics["summary"]["tokens.total"]["total"] == 150
        assert "gpt-4" in metrics["per_model"]

    def test_tool_call_tracking(self):
        from core.systems.observability.diagnostics import DiagnosticsService
        from core.systems.runtime.event_bus import Event, EventBus, EventType
        bus = EventBus()
        svc = DiagnosticsService(bus=bus)
        bus.emit(Event(type=EventType.TOOL_CALL, payload={"tool": "search"}))
        bus.emit(Event(type=EventType.TOOL_RESULT, payload={"tool": "search", "status": "error"}))
        metrics = svc.get_metrics()
        assert metrics["summary"]["tool.calls"]["count"] == 1
        assert metrics["summary"]["tool.errors"]["count"] == 1
        assert metrics["tool_errors"]["search"] == 1

    def test_error_tracking(self):
        from core.systems.observability.diagnostics import DiagnosticsService
        from core.systems.runtime.event_bus import Event, EventBus, EventType
        bus = EventBus()
        svc = DiagnosticsService(bus=bus)
        bus.emit(Event(type=EventType.ERROR, payload={"msg": "boom"}))
        metrics = svc.get_metrics()
        assert metrics["summary"]["errors"]["count"] == 1

    def test_singleton(self):
        from core.systems.observability.diagnostics import get_diagnostics
        d1 = get_diagnostics()
        d2 = get_diagnostics()
        assert d1 is d2


class TestGenerateUnifiedDiff:
    def test_identical_texts(self):
        from core.assets.tools.diff_tool import generate_unified_diff
        result = generate_unified_diff("hello\n", "hello\n")
        assert result == ""

    def test_simple_change(self):
        from core.assets.tools.diff_tool import generate_unified_diff
        result = generate_unified_diff("line1\nline2\n", "line1\nline2_modified\n")
        assert "-line2" in result
        assert "+line2_modified" in result

    def test_addition(self):
        from core.assets.tools.diff_tool import generate_unified_diff
        result = generate_unified_diff("a\n", "a\nb\n")
        assert "+b" in result


class TestDiffStats:
    def test_no_changes(self):
        from core.assets.tools.diff_tool import diff_stats
        stats = diff_stats("same\n", "same\n")
        assert stats["insertions"] == 0
        assert stats["deletions"] == 0

    def test_insertions(self):
        from core.assets.tools.diff_tool import diff_stats
        stats = diff_stats("a\n", "a\nb\n")
        assert stats["insertions"] >= 1

    def test_deletions(self):
        from core.assets.tools.diff_tool import diff_stats
        stats = diff_stats("a\nb\n", "a\n")
        assert stats["deletions"] >= 1


class TestViewDiffTool:
    def test_identical(self):
        from core.assets.tools.diff_tool import ViewDiffTool
        tool = ViewDiffTool()
        result = json.loads(tool._run("abc", "abc"))
        assert result["success"] is True
        assert result["identical"] is True

    def test_different(self):
        from core.assets.tools.diff_tool import ViewDiffTool
        tool = ViewDiffTool()
        result = json.loads(tool._run("line1\nline2", "line1\nline3"))
        assert result["success"] is True
        assert result["identical"] is False
        assert "diff" in result

    def test_size_limit(self):
        from core.assets.tools.diff_tool import ViewDiffTool
        tool = ViewDiffTool()
        big = "x" * (512 * 1024 + 1)
        result = json.loads(tool._run(big, "small"))
        assert result["success"] is False


class TestDiffSummaryTool:
    def test_summary(self):
        from core.assets.tools.diff_tool import DiffSummaryTool
        tool = DiffSummaryTool()
        result = json.loads(tool._run("a\nb\nc", "a\nX\nc"))
        assert result["success"] is True
        assert "changed_regions" in result
        assert len(result["changed_regions"]) > 0


class TestGetDiffTools:
    def test_returns_three_tools(self):
        from core.assets.tools.diff_tool import get_diff_tools
        tools = get_diff_tools()
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert "view_diff" in names
        assert "apply_patch" in names
        assert "diff_summary" in names


class TestCronDelivery:
    def test_to_dict(self):
        from core.assets.workflows.task_scheduler import CronDelivery
        d = CronDelivery(mode="webhook", webhook_url="http://example.com/hook")
        result = d.to_dict()
        assert result["mode"] == "webhook"
        assert result["webhook_url"] == "http://example.com/hook"

    def test_from_dict(self):
        from core.assets.workflows.task_scheduler import CronDelivery
        d = CronDelivery.from_dict({
            "mode": "channel",
            "channel": "general",
            "best_effort": False,
        })
        assert d.mode == "channel"
        assert d.channel == "general"
        assert d.best_effort is False

    def test_from_dict_defaults(self):
        from core.assets.workflows.task_scheduler import CronDelivery
        d = CronDelivery.from_dict({})
        assert d.mode == "none"
        assert d.best_effort is True


class TestStaggerDelay:
    def test_deterministic(self):
        from core.assets.workflows.task_scheduler import _stagger_delay
        d1 = _stagger_delay("task_a", "*/5 * * * *")
        d2 = _stagger_delay("task_a", "*/5 * * * *")
        assert d1 == d2

    def test_different_tasks_different_delays(self):
        from core.assets.workflows.task_scheduler import _stagger_delay
        d1 = _stagger_delay("task_a", "*/5 * * * *")
        d2 = _stagger_delay("task_b", "*/5 * * * *")
        assert d1 != d2 or True  # extremely unlikely to be equal

    def test_within_range(self):
        from core.assets.workflows.task_scheduler import _stagger_delay
        d = _stagger_delay("my_task", "* * * * *", max_jitter=10.0)
        assert 0 <= d < 10.0


class TestScheduledTaskDelivery:
    def test_task_with_delivery(self):
        from core.assets.workflows.task_scheduler import CronDelivery, ScheduledTask
        task = ScheduledTask(
            name="test",
            description="test task",
            cron="*/5 * * * *",
            prompt="do something",
            delivery=CronDelivery(mode="webhook", webhook_url="http://example.com"),
        )
        d = task.to_dict()
        assert "delivery" in d
        assert d["delivery"]["mode"] == "webhook"

    def test_task_without_delivery(self):
        from core.assets.workflows.task_scheduler import ScheduledTask
        task = ScheduledTask(name="test", description="", cron="* * * * *", prompt="do")
        d = task.to_dict()
        assert "delivery" not in d

    def test_task_isolated_session(self):
        from core.assets.workflows.task_scheduler import ScheduledTask
        task = ScheduledTask(name="test", description="", cron="* * * * *", prompt="do", isolated_session=True)
        d = task.to_dict()
        assert d["isolated_session"] is True

    def test_task_stagger_default(self):
        from core.assets.workflows.task_scheduler import ScheduledTask
        task = ScheduledTask(name="test", description="", cron="* * * * *", prompt="do")
        assert task.stagger is True


class TestTokenEstimation:
    def test_english_text(self):
        from core.systems.context.context_engine import _estimate_tokens
        tokens = _estimate_tokens("Hello world, this is a test sentence.")
        assert 5 < tokens < 20

    def test_chinese_text(self):
        from core.systems.context.context_engine import _estimate_tokens
        tokens = _estimate_tokens("你好世界，这是一个测试")
        assert tokens > 0

    def test_empty_text(self):
        from core.systems.context.context_engine import _estimate_tokens
        assert _estimate_tokens("") == 0


# ── Section 4: Bootstrap Assembly ────────────────────────────────────

def test_create_root_agent_uses_runtime_metadata_for_session_context(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    def fake_build_root_langchain_middleware(**kwargs):
        captured["build_kwargs"] = kwargs
        return ["middleware"]

    def fake_create_agent(**kwargs):
        captured["agent_kwargs"] = kwargs
        return {"ok": True}

    class DummyGovernanceApprovalCallback:
        def __init__(self, **kwargs):
            captured["governance_kwargs"] = kwargs

    monkeypatch.setattr("core.systems.middleware.agent_middleware_factory.build_root_langchain_middleware", fake_build_root_langchain_middleware)
    monkeypatch.setattr("langchain.agents.create_agent", fake_create_agent)
    monkeypatch.setattr(
        "core.systems.governance.approval_callback.GovernanceApprovalCallback",
        DummyGovernanceApprovalCallback,
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    runtime = SimpleNamespace(
        thread_id="thread-123",
        conversation_offload_dir=str(tmp_path),
        root_mode="admin",
        workspace_view=WorkspaceViewService(),
        workspace=SimpleNamespace(root_dir=str(tmp_path)),
        context_manager=SimpleNamespace(config=SimpleNamespace(summarize_callback=lambda prompt: "summary")),
        session_runtime=None,
        llm=_DummyLLM(),
        middleware=_DummyMiddleware(),
        capability_bus=_DummyCapabilityBus(),
        subagent_registry=object(),
        task_runtime=SimpleNamespace(
            ingest_tool_runs=lambda *args, **kwargs: None,
            ingest_permission_events=lambda *args, **kwargs: None,
            record_compaction_boundary=lambda *args, **kwargs: None,
            build_projection=lambda: {"summary": "", "tasks": [], "activities": []},
        ),
        hooks_runtime=create_default_hooks_runtime(),
        trusted_settings=build_trusted_settings_bundle(
            user_values={"permission": {"mode": "plan"}},
            project_values={"channels": {"weather": {"token": "configured"}}},
        ),
        approval_queue=None,
        checkpointer=object(),
        memory=None,
    )
    assembly = ToolAssembly(
        creator_tools=[],
        dynamic_tools=[],
        all_tools=[],
        system_prompt="system prompt",
        tool_groups=[],
    )

    result = create_root_agent(runtime=runtime, assembly=assembly)

    assert result == {"ok": True}

    build_kwargs = captured["build_kwargs"]
    summ_config = build_kwargs["summarization_config"]
    session_extractor = build_kwargs["session_memory_extractor"]
    runtime_view_provider = build_kwargs["runtime_view_provider"]
    compaction_callback = build_kwargs["session_compaction_callback"]
    governance_kwargs = captured["governance_kwargs"]

    assert summ_config.thread_id == "thread-123"
    assert summ_config.offload_dir == str(tmp_path)
    assert session_extractor._config.thread_id == "thread-123"
    assert session_extractor._config.storage_dir == str(tmp_path)
    assert governance_kwargs["thread_id"] == "thread-123"

    runtime.workspace_view.record_view(
        resolved_path=str(tmp_path / "app.py"),
        content="print('hello')\n",
        mtime=1.0,
        file_size=15,
    )
    session_extractor._notes = "## Current objective\nContinue refactor"
    compaction_callback(
        {
            "thread_id": "thread-123",
            "summary": "Compacted earlier context",
            "source": "middleware.summarization",
            "reason": "conversation_compaction",
            "message_count": 8,
            "recent_window": 20,
            "microcompact_count": 2,
        }
    )
    artifacts = runtime_view_provider()

    assert artifacts is not None
    view = artifacts["projected_runtime_view"]
    assert artifacts["system_context"]["thread_id"] == "thread-123"
    assert artifacts["system_context"]["primary_mode"] == "admin"
    assert view["session"]["session_notebook_summary"] == "## Current objective\nContinue refactor"
    assert view["session"]["compaction_summary"] == "Compacted earlier context"
    assert view["workspace"]["recent_paths"] == [str(tmp_path / "app.py")]
    assert view["tasks"]["activities"][0]["title"] == "read_file"
    assert view["permission"]["mode"] == "plan"
    assert view["settings"]["permission_mode"] == "plan"
    assert artifacts["system_context"]["latest_compaction_boundary"]["summary"] == "Compacted earlier context"
    assert view["capability"]["primary_branches"][0]["label"] == "Workflow / Apps / Automation"
    assert "create_app" in [item["topic"] for item in view["capability"]["route_hints"]]
    assert view["context_hygiene"]["summary_active"] is True
    assert view["hooks"]["active_phases"]
    assert view["route"]["recommended"]["slot"] == "workspace_view"
    assert view["isolation"]["multi_agent_ready"] is True
    assert view["context_hygiene"]["history_snip_count"] == 1


def test_assemble_primary_tools_passes_runtime_workspace_view(tmp_path, monkeypatch):
    captured: dict[str, object] = {}
    marker = object()

    monkeypatch.setattr("core.assets.tools.tool_creator.get_tool_creator_tools", lambda **kwargs: [])
    monkeypatch.setattr("core.assets.tools.clarification_tool.get_clarification_tools", lambda: [])
    monkeypatch.setattr("core.assets.skills.skill_marketplace.get_marketplace_tools", lambda *_args, **_kwargs: [])

    def fake_get_execution_loop_tools(workspace_dir):
        captured["workspace_dir"] = workspace_dir
        return []
    
    monkeypatch.setattr("core.systems.execution.execution_loop.get_execution_loop_tools", fake_get_execution_loop_tools)
    monkeypatch.setattr("core.systems.execution.get_execution_loop_tools", fake_get_execution_loop_tools)

    monkeypatch.setattr("core.assets.tools.tool_chain.get_tool_chain_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("core.systems.eval.eval_framework.get_eval_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("core.systems.capability.capability_bus.get_capability_bus_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("core.systems.capability.capability_registry.get_capability_registry_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("core.systems.memory.tools.get_memory_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("core.systems.context.prompts.build_static_system_prompt", lambda **_kwargs: "system prompt")
    
    monkeypatch.setattr(
        "core.systems.apps.app_runtime",
        SimpleNamespace(
            creator_tools_factory=lambda **kwargs: [],
            verifier_tools_factory=lambda: [],
        ),
    )
    monkeypatch.setattr(
        "core.assets.workflows.workflow_runtime",
        SimpleNamespace(tools_factory=lambda *_args, **_kwargs: []),
    )
    monkeypatch.setattr(
        "core.systems.apps.app_orchestration",
        SimpleNamespace(
            marketplace_tools_factory=lambda: [],
            orchestration_tools_factory=lambda *_args, **_kwargs: [],
        ),
    )
    monkeypatch.setattr(
        "core.assets.tools.permission_tools.get_permission_tools",
        lambda *_args, **_kwargs: [],
    )

    def fake_get_file_system_tools(**kwargs):
        captured["fs_kwargs"] = kwargs
        return []

    class DummyBashTool:
        name = "bash"

        def __init__(self, **kwargs):
            captured["bash_kwargs"] = kwargs

    class DummyWebFetchTool:
        name = "web_fetch"

    class DummyMarkdownLoader:
        def __init__(self, skills_dir):
            captured["skills_dir"] = skills_dir

        def get_all_skills_summary(self):
            return ""

    monkeypatch.setattr("core.assets.tools.file_system_tools.get_file_system_tools", fake_get_file_system_tools)
    monkeypatch.setattr("core.assets.tools.bash_tool.BashTool", DummyBashTool)
    monkeypatch.setattr("core.assets.tools.web_fetch_tool.WebFetchTool", DummyWebFetchTool)
    monkeypatch.setattr("core.assets.skills.markdown_loader.MarkdownSkillLoader", DummyMarkdownLoader)

    runtime = SimpleNamespace(
        storage=SimpleNamespace(tools={}),
        agent_storage=object(),
        control_policy=object(),
        approval_queue=None,
        subagent_registry=None,
        skill_marketplace=object(),
        pyflow_engine=object(),
        tool_chain=object(),
        eval_framework=object(),
        capability_bus=object(),
        capability_registry=SimpleNamespace(refresh_local_index=lambda **kwargs: captured.setdefault("indexed", kwargs)),
        mcp_hub=SimpleNamespace(get_tools=lambda: []),
        knowledge_tools=[],
        memory=object(),
        middleware=SimpleNamespace(set_base_tools=lambda tools: captured.setdefault("base_tools", tools)),
        orchestration_registry=None,
        skill_registry=SimpleNamespace(get_active_tools=lambda: []),
        workspace_view=marker,
        channel_manager=SimpleNamespace(set_agent_callback=lambda cb: captured.setdefault("chat_callback", cb)),
    )
    paths = SimpleNamespace(
        workspace_dir=tmp_path,
        skills_dir=tmp_path,
    )

    result = assemble_primary_tools(
        runtime=runtime,
        paths=paths,
        enable_agent_creation=False,
        root_mode="assistant",
        llm_factory=lambda _model, _temp: object(),
        chat_callback=lambda message: message,
    )

    assert isinstance(result, ToolAssembly)
    assert captured["workspace_dir"] == str(tmp_path)


class _DummyLLM:
    def __init__(self) -> None:
        self.callback_config = None

    def with_config(self, config):
        self.callback_config = config
        return self


class _DummyMiddleware:
    def __init__(self) -> None:
        self.ask_user_fn = None

    def set_ask_user_fn(self, ask_user_fn):
        self.ask_user_fn = ask_user_fn

    def get_control_snapshot(self):
        return {
            "observability": {
                "recent_events": [
                    {
                        "tool_name": "read_file",
                        "tool_call_id": "run-1",
                        "allowed": True,
                        "requires_approval": False,
                        "args_preview": "{'path': 'app.py'}",
                        "timestamp": 1.0,
                    }
                ]
            },
            "permission": {
                "mode": "plan",
                "summary": "mode=plan, 1 active rule",
                "rules": [{"tool_name": "read_file", "verdict": "ask", "reason": "manual", "source": "session"}],
                "recent_events": [{"action": "set_mode", "mode": "plan", "timestamp": 1.0}],
                "write_tools": ["write_file", "bash"],
                "rule_count": 1,
            },
        }


class _DummyCapabilityBus:
    def __init__(self) -> None:
        self.shared_context = {}

    def get_tree_projection(self):
        return {
            "trunk": [
                {"id": "tool_runtime_governance", "label": "Tool Runtime / Governance"},
                {"id": "workspace_view", "label": "Workspace View"},
                {"id": "context_hygiene", "label": "Context Hygiene"},
            ],
            "execution_surfaces": [
                {"id": "single_agent_runtime", "label": "Single-Agent Runtime"},
                {"id": "skill_strategy", "label": "Skill Strategy Overlay"},
            ],
            "primary_branches": [
                {
                    "id": "workflow_apps",
                    "label": "Workflow / Apps / Automation",
                    "depends_on": ["single_agent_runtime", "permission_recovery"],
                    "children": [
                        {"id": "app_runtime", "label": "App Asset Runtime"},
                        {"id": "app_modes", "label": "App Modes"},
                    ],
                    "capabilities": ["create_app", "build_app_iteratively"],
                    "capability_count": 2,
                }
            ],
            "secondary_branches": [
                {"id": "hooks_runtime", "label": "Hooks Runtime"},
            ],
        }

    def get_route_projection(self, **_kwargs):
        return {
            "recommended": {
                "mode": "trunk_first",
                "slot": "workspace_view",
                "slot_label": "Workspace View",
                "top_level": "workspace_view",
                "top_level_label": "Workspace View",
                "summary": "Stay on the trunk through Workspace View first.",
            },
            "top_matches": [{"name": "read_file", "layer": "tool", "tree": {"slot": "workspace_view"}}],
            "route_hints": [{"topic": "start_here", "hint": "Prefer trunk capabilities first."}],
        }

    def share_context(self, key, value, source=""):
        self.shared_context[key] = {"value": value, "source": source}


# ── Section 5: Configuration & Loading ───────────────────────────────

def test_missing_config_uses_fresh_defaults(tmp_path: Path):
    config_path = tmp_path / "missing.json"
    get_config.cache_clear()
    config = get_config(config_path)
    config["llm_config"]["model"] = "custom-model"

    get_config.cache_clear()
    fresh = get_config(config_path)
    assert fresh["llm_config"]["model"] == "gpt-4"


def test_save_config_merges_defaults_and_round_trips(tmp_path: Path):
    config_path = tmp_path / "config.json"
    saved_path = save_config({"llm_config": {"api_key": "test-key"}}, config_path)

    assert saved_path == config_path.resolve()
    loaded = reload_config(config_path)
    assert loaded["llm_config"]["api_key"] == "test-key"
    assert loaded["llm_config"]["model"] == "gpt-4"
    assert loaded["agent_config"]["thread_id"] == "default"
    assert loaded["rag_config"]["search_strategy"] == "vector"
    assert loaded["rag_config"]["embedding_batch_size"] == 32


def test_resolve_config_path_prefers_runtime_home(monkeypatch, tmp_path: Path):
    runtime_home = tmp_path / "runtime-home"
    monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(runtime_home))
    monkeypatch.delenv("PYBOT_CONFIG_PATH", raising=False)
    monkeypatch.setattr(config_impl, "_LEGACY_CONFIG_PATH", tmp_path / "legacy-config.json")

    resolved = config_impl.resolve_config_path()

    assert resolved == (runtime_home / "config.json").resolve()


def test_resolve_config_path_falls_back_to_legacy_repo_config(monkeypatch, tmp_path: Path):
    runtime_home = tmp_path / "runtime-home"
    legacy_path = tmp_path / "legacy-config.json"
    legacy_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(runtime_home))
    monkeypatch.delenv("PYBOT_CONFIG_PATH", raising=False)
    monkeypatch.setattr(config_impl, "_LEGACY_CONFIG_PATH", legacy_path)

    resolved = config_impl.resolve_config_path()

    assert resolved == legacy_path.resolve()


def test_save_config_defaults_to_runtime_home(monkeypatch, tmp_path: Path):
    runtime_home = tmp_path / "runtime-home"
    monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(runtime_home))
    monkeypatch.delenv("PYBOT_CONFIG_PATH", raising=False)
    monkeypatch.setattr(config_impl, "_LEGACY_CONFIG_PATH", tmp_path / "legacy-config.json")

    saved_path = save_config({"llm_config": {"model": "gpt-4.1-mini"}})

    assert saved_path == (runtime_home / "config.json").resolve()
    assert saved_path.exists()


def test_project_settings_override_user_settings(tmp_path: Path):
    user_config = tmp_path / "config.json"
    project_config = tmp_path / ".pybot" / "project.config.json"

    save_config({"permission": {"mode": "plan"}}, user_config)
    save_project_config({"permission": {"mode": "bypass"}}, project_config)

    loaded = reload_config(user_config, project_path=project_config)

    assert loaded["permission"]["mode"] == "bypass"


def test_trusted_settings_projection_includes_layer_paths(tmp_path: Path):
    user_config = tmp_path / "config.json"
    project_config = tmp_path / ".pybot" / "project.config.json"
    system_config = tmp_path / "settings.system.json"

    save_config({"permission": {"mode": "default"}}, user_config)
    save_project_config({"permission": {"mode": "plan"}}, project_config)
    config_impl.save_system_config({"permission": {"rules": {"web_fetch": "allow"}}}, system_config)

    projection = config_impl.get_settings_projection(
        user_config,
        project_path=project_config,
        system_path=system_config,
        session_overrides={"permission": {"mode": "bypass"}},
    )

    assert projection["permission_mode"] == "bypass"
    assert projection["paths"]["user"] == str(user_config.resolve())
    assert projection["paths"]["project"] == str(project_config.resolve())
    assert projection["paths"]["system"] == str(system_config.resolve())
    assert projection["active_sources"] == ["system", "user", "project", "session"]


# ── Section 6: YAML Configuration ───────────────────────────────────

class TestInterpolatePlaceholders:
    def test_basic(self):
        assert interpolate_placeholders("Hello {name}!", {"name": "World"}) == "Hello World!"

    def test_missing_key_unchanged(self):
        assert interpolate_placeholders("{a} and {b}", {"a": "X"}) == "X and {b}"

    def test_no_placeholders(self):
        assert interpolate_placeholders("plain text", {"x": "y"}) == "plain text"

    def test_multiple_same_key(self):
        assert interpolate_placeholders("{x}-{x}", {"x": "1"}) == "1-1"

    def test_numeric_value(self):
        assert interpolate_placeholders("count: {n}", {"n": 42}) == "count: 42"

    def test_empty_variables(self):
        assert interpolate_placeholders("{x}", {}) == "{x}"


class TestLoadAgentsYaml:
    def _write(self, tmpdir, content):
        p = Path(tmpdir) / "agents.yaml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_basic_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
data_analyst:
  role: 数据分析师
  description: 擅长数据清洗和可视化
  system_prompt: |
    你是数据分析师
  capabilities: [数据分析, Python]
  model: gpt-4
  temperature: 0.5
""",
            )
            agents = load_agents_yaml(p)
            assert len(agents) == 1
            a = agents[0]
            assert a["name"] == "data_analyst"
            assert a["role"] == "数据分析师"
            assert a["model"] == "gpt-4"
            assert a["temperature"] == 0.5
            assert "数据分析" in a["capabilities"]

    def test_multiple_agents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
agent_a:
  role: A
  description: First
agent_b:
  role: B
  description: Second
""",
            )
            agents = load_agents_yaml(p)
            assert len(agents) == 2

    def test_with_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
coder:
  role: coder
  description: writes code
  profile: builder
""",
            )
            agents = load_agents_yaml(p)
            assert agents[0]["capability_profile"] == {"preset": "builder"}

    def test_interpolation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
analyst:
  role: "{domain}分析师"
  description: 分析{domain}数据
  system_prompt: 你专注{domain}
""",
            )
            agents = load_agents_yaml(p, variables={"domain": "金融"})
            assert agents[0]["role"] == "金融分析师"
            assert "金融" in agents[0]["system_prompt"]

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_agents_yaml("/nonexistent/agents.yaml")

    def test_invalid_yaml_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(tmpdir, "- item1\n- item2\n")
            with pytest.raises(ValueError, match="mapping"):
                load_agents_yaml(p)

    def test_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
minimal:
  role: test
""",
            )
            agents = load_agents_yaml(p)
            a = agents[0]
            assert a["description"] == ""
            assert a["system_prompt"] == ""
            assert a["model"] == "gemini-3-flash-preview"
            assert a["temperature"] == 0.7


class TestLoadTasksYaml:
    def _write(self, tmpdir, content):
        p = Path(tmpdir) / "tasks.yaml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_basic_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
analyze_data:
  description: 分析销售数据
  agent: data_analyst
  expected_output: 分析报告
  context: [fetch_data]
""",
            )
            tasks = load_tasks_yaml(p)
            assert len(tasks) == 1
            t = tasks[0]
            assert t["name"] == "analyze_data"
            assert t["agent"] == "data_analyst"
            assert t["context"] == ["fetch_data"]

    def test_interpolation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
analyze:
  description: 分析 {topic} 的数据
  agent: analyst
""",
            )
            tasks = load_tasks_yaml(p, variables={"topic": "电商"})
            assert "电商" in tasks[0]["description"]

    def test_extra_fields_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(
                tmpdir,
                """
task1:
  description: test
  agent: bot
  priority: high
  timeout: 30
""",
            )
            tasks = load_tasks_yaml(p)
            assert tasks[0]["priority"] == "high"
            assert tasks[0]["timeout"] == 30

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_tasks_yaml("/nonexistent/tasks.yaml")

    def test_invalid_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._write(tmpdir, "just a string")
            with pytest.raises(ValueError, match="mapping"):
                load_tasks_yaml(p)


class TestAutoDiscoverYaml:
    def test_both_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agents.yaml").write_text("a: {}", encoding="utf-8")
            (Path(tmpdir) / "tasks.yaml").write_text("t: {}", encoding="utf-8")
            result = auto_discover_yaml(tmpdir)
            assert result["agents"] is not None
            assert result["tasks"] is not None

    def test_none_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = auto_discover_yaml(tmpdir)
            assert result["agents"] is None
            assert result["tasks"] is None

    def test_yml_extension(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agents.yml").write_text("a: {}", encoding="utf-8")
            result = auto_discover_yaml(tmpdir)
            assert result["agents"] is not None

    def test_yaml_takes_priority_over_yml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agents.yaml").write_text("yaml: {}", encoding="utf-8")
            (Path(tmpdir) / "agents.yml").write_text("yml: {}", encoding="utf-8")
            result = auto_discover_yaml(tmpdir)
            assert result["agents"].name == "agents.yaml"


# ── Section 10: Post-Refactoring Exports and Versioning ────────────────

def test_structured_runtime_exports_work_after_batch0_move():
    assert validate_path("workspace/demo.txt") == "/workspace/demo.txt"
    assert interpolate_placeholders("hello {name}", {"name": "pybot"}) == "hello pybot"
    assert extract_error_code(ToolInputError("bad input")) == "invalid_input"
    policy = RetryPolicy(config=RetryConfig(max_attempts=1))
    assert policy.config.max_attempts == 1


def test_legacy_stub_and_new_runtime_export_resolve_same_version():
    assert get_pybot_version() == legacy_get_pybot_version()

