"""Assembly helpers for workflow/capability runtime services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.assets.agents.agent_storage import AgentStorage
from core.assets.agents.subagent_registry import SubagentRegistry
from core.assets.apps.app_manager import AppManager
from core.assets.skills.skill_marketplace import SkillMarketplace
from core.assets.skills.skill_registry import SkillRegistry
from core.assets.tools import ToolChainExecutor
from core.assets.workflows import PyFlowEngine, TaskScheduler
from core.systems.bus import CapabilityBus, CapabilityRegistry
from core.systems.context.context_manager import ContextConfig, ContextWindowManager
from core.systems.eval.eval_framework import EvalFramework
from core.systems.governance import AgentControlPolicy, ApprovalQueue
from core.systems.middleware.middleware_stack import MiddlewareStack
from core.systems.runtime.config_impl import get_agent_control_config
from core.systems.runtime.project_paths import ProjectPaths


@dataclass
class CapabilityRuntimeBundle:
    """Shared workflow/capability services used by the root runtime."""

    approval_queue: ApprovalQueue
    pyflow_engine: PyFlowEngine
    tool_chain: ToolChainExecutor
    eval_framework: EvalFramework
    context_manager: ContextWindowManager
    capability_bus: CapabilityBus
    capability_registry: CapabilityRegistry
    control_policy: AgentControlPolicy
    subagent_registry: SubagentRegistry
    middleware_stack: MiddlewareStack


def build_capability_runtime_bundle(
    *,
    paths: ProjectPaths,
    thread_id: str,
    summarize_callback: Callable[[str], str],
    tool_callback: Callable[[str, dict[str, Any]], Any],
    agent_callback: Callable[[str], str],
    delegate_callback: Callable[[str, str, str], Any],
    skill_registry: SkillRegistry,
    skill_marketplace: SkillMarketplace,
    app_manager: AppManager,
    agent_storage: AgentStorage,
    control_config: dict[str, Any] | None = None,
    approval_queue: ApprovalQueue | None = None,
    session_runtime: Any | None = None,
) -> CapabilityRuntimeBundle:
    """Assemble workflow/capability services with one shared wiring path."""

    resolved_approval_queue = approval_queue or ApprovalQueue(storage_path=paths.approvals_file)
    pyflow_engine = PyFlowEngine(
        str(paths.workspace_dir),
        approval_queue=resolved_approval_queue,
        session_runtime=session_runtime,
    )
    pyflow_engine.configure_callbacks(
        tool_callback=tool_callback,
        agent_callback=agent_callback,
        delegate_callback=delegate_callback,
    )

    tool_chain = ToolChainExecutor()
    tool_chain.set_tool_callback(tool_callback)

    eval_framework = EvalFramework(str(paths.workspace_dir))
    eval_framework.set_agent_callback(summarize_callback)

    context_manager = ContextWindowManager(
        ContextConfig(
            max_tokens=12000,
            summarize_callback=summarize_callback,
            offload_dir=str(paths.conversation_offload_dir),
            thread_id=thread_id,
        )
    )
    capability_bus = CapabilityBus(str(paths.workspace_dir))
    capability_registry = CapabilityRegistry(
        workspace_dir=paths.workspace_dir,
        capability_bus=capability_bus,
        skill_marketplace=skill_marketplace,
        skill_registry=skill_registry,
        app_manager=app_manager,
        agent_storage=agent_storage,
        pyflow_engine=pyflow_engine,
    )
    control_policy = AgentControlPolicy.from_config(control_config or get_agent_control_config())
    subagent_registry = SubagentRegistry(
        max_depth=control_policy.max_subagent_depth,
        max_concurrent=control_policy.max_concurrent_subagents,
        default_timeout_seconds=control_policy.subagent_timeout_seconds,
    )

    return CapabilityRuntimeBundle(
        approval_queue=resolved_approval_queue,
        pyflow_engine=pyflow_engine,
        tool_chain=tool_chain,
        eval_framework=eval_framework,
        context_manager=context_manager,
        capability_bus=capability_bus,
        capability_registry=capability_registry,
        control_policy=control_policy,
        subagent_registry=subagent_registry,
        middleware_stack=MiddlewareStack(),
    )
