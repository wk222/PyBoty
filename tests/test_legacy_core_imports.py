from __future__ import annotations

import ast
import importlib
from pathlib import Path


LEGACY_ROOT_MODULES = [
    "admin_runtime",
    "app_verifier_checks",
    "config",
    "context_engine",
    "directive_parser",
    "hook_context",
    "link_safety",
    "llm_task_tool",
    "loop_guard_middleware",
    "media_understanding",
    "memory_manager",
    "middleware_registry",
    "private_state",
    "reasoning_frame_middleware",
    "summarization_middleware",
    "tool_risk",
    "workflow_as_tool",
    "workflow_graph_runtime",
    "workflow_spec",
]


def _module_exists(core_root: Path, module_name: str) -> bool:
    relative = Path(*module_name.split("."))
    return (core_root / f"{relative}.py").exists() or (core_root / relative / "__init__.py").exists()


def test_legacy_root_core_shims_are_importable():
    for module_name in LEGACY_ROOT_MODULES:
        module = importlib.import_module(f"core.{module_name}")
        assert module is not None


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
