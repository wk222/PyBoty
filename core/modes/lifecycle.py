"""Lifecycle helpers for PyBot root modes.

Initialization is now delegated to the active ModePack via the global
registry.  The functions in this module remain as the public API so that
existing call sites (``agent.py``, tests) continue to work unchanged.
"""

from __future__ import annotations

from typing import Any

from core.modes.profile import ModeProfile, resolve_mode_profile
from core.systems.runtime.prompts import get_root_mode_label
from core.modes.system_model import build_system_summary


def should_attach_admin_runtime(
    *,
    root_mode: str | None = None,
    mode_profile: ModeProfile | None = None,
    attach_requested: bool,
) -> bool:
    profile = mode_profile or resolve_mode_profile(root_mode)
    return attach_requested or profile.attach_admin_runtime_by_default


def initialize_mode_services(host_agent: Any) -> None:
    """Attach mode-specific services by delegating to the registered pack."""
    from core.modes.builtin_packs import ensure_builtin_packs
    from core.modes.pack import get_global_registry

    ensure_builtin_packs()
    registry = get_global_registry()
    pack = registry.get_or_none(host_agent.mode_profile.name)
    if pack is not None:
        # Store the resolved pack on the host for later dispatch
        host_agent._mode_pack = pack
        pack.initialize(host_agent)
    else:
        # Fallback: unknown mode, set empty defaults
        host_agent._mode_pack = None
        host_agent.orchestration_registry = None
        host_agent.app_matrix = None


def ensure_admin_runtime(host_agent: Any) -> Any:
    """Guarantee that the host agent has a persistent runtime attached."""

    if host_agent.admin is None:
        host_agent._attach_admin_runtime = True
        # Re-delegate to pack if available
        pack = getattr(host_agent, "_mode_pack", None)
        if pack is not None:
            pack.initialize(host_agent)
        else:
            _legacy_initialize_persistent_runtime(host_agent)
    assert host_agent.admin is not None
    return host_agent.admin


def print_startup_summary(host_agent: Any) -> None:
    """Print a consistent startup summary for the root runtime."""
    skills_count = len(host_agent.skill_registry.skills)
    tasks_count = len(host_agent.scheduler.tasks)
    system_summary = build_system_summary()
    mode_profile = host_agent.get_mode_profile()
    enabled_capabilities = mode_profile.get("effective_enabled_capabilities") or mode_profile.get(
        "enabled_capabilities"
    )
    pack = getattr(host_agent, "_mode_pack", None)
    print("✅ PyBot Runtime 已初始化")
    print(f"   模型: {host_agent.model_name}")
    print(f"   会话ID: {host_agent.thread_id}")
    print(f"   源码根: {host_agent.paths.root_dir}")
    print(f"   运行时根: {host_agent.paths.runtime_root_dir}")
    print(f"   工作空间: {host_agent.workspace_dir}/")
    print(f"   后端: {type(host_agent.backend).__name__}")
    print(f"   技能: {skills_count} 个已加载（渐进式披露）")
    print(f"   定时任务: {tasks_count} 个已配置")
    print(f"   模式: {get_root_mode_label(host_agent.root_mode)}")
    print(f"   模式能力: {' / '.join(enabled_capabilities)}")
    if pack is not None:
        print(f"   模式包: {pack.name} (pluggable)")
    print(
        "   系统边界: "
        f"{system_summary['root_modes']} 根模式 / "
        f"{system_summary['product_concepts']} 产品概念 / "
        f"{system_summary['supporting_systems']} 横切系统"
    )
    print(f"   身份: {host_agent.mode_profile.identity_description}")
    if host_agent.admin is not None:
        print("   Admin Loop: 已挂载")
    if host_agent.orchestration_registry is not None:
        node_count = len(host_agent.orchestration_registry.list_nodes())
        print(f"   编排注册表: {node_count} 个节点")
    print(f"   中间件: {' → '.join(host_agent._lc_middleware_names())}")


# ---------------------------------------------------------------------------
# Legacy fallback (used only if no pack is registered)
# ---------------------------------------------------------------------------


def _legacy_initialize_persistent_runtime(host_agent: Any) -> None:
    """Fallback init when no ModePack is available."""
    if not host_agent._attach_admin_runtime or host_agent.admin is not None:
        return
    from core.modes.admin_runtime import PersistentAdminRuntime

    storage_dir = host_agent._admin_storage_dir or str(
        host_agent.paths.workspace_data_dir / host_agent.mode_profile.durable_runtime_dir
    )
    host_agent.admin = PersistentAdminRuntime(
        host_agent=host_agent,
        storage_dir=storage_dir,
        poll_interval=host_agent._admin_poll_interval,
        max_workers=host_agent._admin_workers,
        step_executor=host_agent._admin_step_executor,
    )
