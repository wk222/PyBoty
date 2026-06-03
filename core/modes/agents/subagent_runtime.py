"""Isolated subagent runtime helpers inspired by deepagents task workers."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.language_models import BaseChatModel

from core.assets.tools import DynamicToolMiddleware
from core.assets.tools import ToolStorage
from core.systems.governance.agent_control import AgentControlPolicy
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.governance.subagent_checkpointing import SubagentCheckpointBundle, build_subagent_checkpointer
from core.systems.governance.subagent_sandbox import (
    SubagentSandbox,
    build_subagent_builtin_tools,
    build_subagent_sandbox,
)
from core.systems.governance.tool_approval_runtime import (
    approval_interrupt_from_metadata,
    build_tool_approval_resume_command,
    create_tool_approval_request,
    extract_tool_approval_interrupts,
)
from core.modes.factories import build_subagent_langchain_middleware
from core.systems.runtime.private_state import get_private_keys
from core.systems.runtime.project_paths import ProjectPaths
from core.systems.runtime.subagent_isolation import (
    build_subagent_isolation_projection,
    materialize_isolation_execution_options,
)

from .agent_capability_profile import AgentCapabilityProfile
from .agent_middleware_profile import AgentMiddlewareProfile
from .agent_storage import AgentDefinition
from .subagent_governance import derive_subagent_control_policy, filter_tools_for_policy
from .subagent_registry import SubagentRegistry

logger = logging.getLogger(__name__)

EXCLUDED_SUBAGENT_STATE_KEYS = get_private_keys()


def filter_subagent_state(parent_state: dict[str, Any] | None) -> dict[str, Any]:
    if not parent_state:
        return {}
    private_keys = get_private_keys()
    return {key: value for key, value in parent_state.items() if key not in private_keys}


def build_subagent_prompt(task: str, context: str = "") -> str:
    if not context:
        return task
    return f"{task}\n\n上下文：{context}"


def resolve_subagent_tools(
    *,
    agent_def: AgentDefinition,
    tool_storage: ToolStorage | None = None,
    global_tool_storage: ToolStorage | None = None,
    agent_storage: Any = None,
    llm_factory: Any = None,
    control_policy: AgentControlPolicy | None = None,
    approval_queue: ApprovalQueue | None = None,
    project_paths: ProjectPaths | None = None,
    runtime_context: dict[str, Any] | None = None,
    registry: SubagentRegistry | None = None,
    summarize_fn: Any | None = None,
) -> list[Any]:
    from core.assets.tools.tool_creator import get_dynamic_tools
    from core.assets.tools.tool_runtime import build_dynamic_tool

    profile = AgentCapabilityProfile.from_value(agent_def.capability_profile)
    effective_policy = derive_subagent_control_policy(base_policy=control_policy, capability_profile=profile)
    sandbox = build_subagent_sandbox(
        agent_name=agent_def.name,
        capability_profile=profile,
        project_paths=project_paths,
    )
    tools: list[Any] = []
    seen_names: set[str] = set()
    dynamic_tool_names: set[str] = set()

    if tool_storage is not None and profile.allow_local_dynamic_tools:
        for tool in get_dynamic_tools(tool_storage):
            if tool.name in seen_names:
                continue
            tools.append(tool)
            seen_names.add(tool.name)
            dynamic_tool_names.add(tool.name)

    if global_tool_storage is not None and agent_def.tools:
        for tool_name in agent_def.tools:
            if tool_name in seen_names:
                continue
            tool_definition = global_tool_storage.get_tool(tool_name)
            if not tool_definition:
                continue
            tools.append(build_dynamic_tool(tool_definition))
            seen_names.add(tool_name)
            dynamic_tool_names.add(tool_name)

    for tool in build_subagent_builtin_tools(capability_profile=profile, sandbox=sandbox):
        if tool.name in seen_names:
            continue
        tools.append(tool)
        seen_names.add(tool.name)

    for tool in _resolve_capability_management_tools(
        capability_profile=profile,
        local_tool_storage=tool_storage,
        global_tool_storage=global_tool_storage,
        agent_storage=agent_storage,
        llm_factory=llm_factory,
        control_policy=control_policy,
        approval_queue=approval_queue,
        project_paths=project_paths,
        runtime_context=runtime_context,
        registry=registry,
    ):
        if tool.name in seen_names:
            continue
        tools.append(tool)
        seen_names.add(tool.name)

    if profile.allow_memory_garden:
        from core.systems.memory.garden_tools import get_garden_tools
        ws = str(sandbox.workspace_dir) if sandbox else "workspace"
        for tool in get_garden_tools(workspace_dir=ws, summarize_fn=summarize_fn):
            if tool.name in seen_names:
                continue
            tools.append(tool)
            seen_names.add(tool.name)

    if profile.allow_dense_memory:
        from core.systems.knowledge.vector_store import create_vector_store
        from core.systems.knowledge.knowledge_tools import get_knowledge_tools
        ws = str(sandbox.workspace_dir) if sandbox else "workspace"
        persist_dir = os.path.join(ws, ".vector_store")
        
        # Determine embedding function (fallback to None if not easily available)
        # In a real scenario, we might want to pass this down.
        vector_store = create_vector_store(backend="sqlite-vec", persist_dir=persist_dir)
        for tool in get_knowledge_tools(vector_store=vector_store):
            if tool.name in seen_names:
                continue
            tools.append(tool)
            seen_names.add(tool.name)

    # Always allow taking notes for team/context sync
    from core.assets.tools.session_notes_tool import get_session_note_tools
    for tool in get_session_note_tools(runtime_context=runtime_context, registry=registry):
        if tool.name in seen_names:
            continue
        tools.append(tool)
        seen_names.add(tool.name)

    return filter_tools_for_policy(
        tools=tools,
        control_policy=effective_policy,
        dynamic_tool_names=dynamic_tool_names,
    )


@dataclass(frozen=True)
class SubAgentRuntimeConfig:
    recursion_limit: int = 80
    excluded_state_keys: frozenset[str] = field(default_factory=lambda: EXCLUDED_SUBAGENT_STATE_KEYS)


@dataclass(frozen=True)
class SubAgentInvocationResult:
    status: str
    response: str
    agent_name: str
    role: str
    success: bool
    state_update: dict[str, Any]
    tool_names: list[str]
    thread_id: str
    sandbox: dict[str, Any]
    isolation: dict[str, Any]
    context_notes: list[str] = field(default_factory=list)
    approval_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "response": self.response,
            "agent_name": self.agent_name,
            "role": self.role,
            "success": self.success,
            "state_update": self.state_update,
            "tool_names": self.tool_names,
            "thread_id": self.thread_id,
            "sandbox": self.sandbox,
            "isolation": self.isolation,
            "context_notes": list(self.context_notes),
            "approval_id": self.approval_id,
        }


class SubAgentRuntime:
    """Thin wrapper around a compiled LangChain subagent graph."""

    def __init__(
        self,
        *,
        graph: Any,
        definition: AgentDefinition,
        tools: list[Any],
        tool_names: list[str],
        control_policy: AgentControlPolicy,
        sandbox: SubagentSandbox,
        checkpoint_bundle: SubagentCheckpointBundle,
        approval_queue: ApprovalQueue | None = None,
        registry: SubagentRegistry | None = None,
        runtime_context: dict[str, Any] | None = None,
        runtime_config: SubAgentRuntimeConfig | None = None,
    ):
        self.graph = graph
        self.definition = definition
        self.tools = tools
        self.tool_names = tool_names
        self.control_policy = control_policy
        self.sandbox = sandbox
        self.checkpoint_bundle = checkpoint_bundle
        self.approval_queue = approval_queue or ApprovalQueue()
        self.registry = registry
        self.runtime_context = runtime_context if runtime_context is not None else {}
        self.runtime_config = runtime_config or SubAgentRuntimeConfig()

    def invoke(
        self,
        task: str,
        context: str = "",
        thread_id: str = "default",
        parent_state: dict[str, Any] | None = None,
        parent_agent_name: str | None = None,
        parent_run_id: str | None = None,
        parent_thread_id: str | None = None,
        parent_depth: int = 0,
        stream: bool = False,
    ) -> dict[str, Any] | Any:
        run_record = self._begin_run(
            thread_id=thread_id,
            parent_agent_name=parent_agent_name,
            parent_run_id=parent_run_id,
            parent_thread_id=parent_thread_id,
            parent_depth=parent_depth,
        )
        subagent_state = {
            "messages": [{"role": "user", "content": build_subagent_prompt(task, context)}],
            **filter_subagent_state(parent_state),
        }

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.runtime_config.recursion_limit,
        }
        # Initialize context notes buffer for this run
        self.runtime_context["context_notes_buffer"] = []
        
        try:
            isolation_projection = build_subagent_isolation_projection(
                agent_name=self.definition.name,
                sandbox=self.sandbox,
                thread_id=thread_id,
                parent_thread_id=parent_thread_id or "",
                depth=parent_depth + 1,
                owner_session_key=str(self.runtime_context.get("session_key", "") or ""),
            )
            self._set_runtime_context(
                thread_id=thread_id,
                run_id=run_record.run_id if run_record is not None else self.runtime_context.get("run_id"),
                depth=(run_record.depth if run_record is not None else max(0, int(parent_depth)) + 1),
                isolation=isolation_projection,
            )
            if stream:
                return self._invoke_stream(
                    subagent_state=subagent_state,
                    config=config,
                    thread_id=thread_id,
                    run_record=run_record,
                )

            result = self.graph.invoke(subagent_state, config=config)
            
            # Extract notes taken during this run
            context_notes = list(self.runtime_context.get("context_notes_buffer", []))

            pending = self._register_tool_approval(result, thread_id=thread_id, config=config)
            if pending is not None:
                if self.registry is not None:
                    self.registry.mark_waiting_approval(
                        agent_name=self.definition.name,
                        thread_id=thread_id,
                        approval_id=pending.approval_id,
                    )
                return SubAgentInvocationResult(
                    status="waiting_approval",
                    response=(f"子智能体 '{self.definition.name}' 已暂停，等待人工审批（{pending.approval_id}）。"),
                    agent_name=self.definition.name,
                    role=self.definition.role,
                    success=False,
                    state_update={},
                    tool_names=self.tool_names,
                    thread_id=thread_id,
                    sandbox=self.sandbox.to_dict(),
                    isolation=isolation_projection,
                    context_notes=context_notes,
                    approval_id=pending.approval_id,
                ).to_dict()

            final_messages = result.get("messages", [])
            response_text = final_messages[-1].content if final_messages else ""
            success = getattr(final_messages[-1], "status", "success") != "error" if final_messages else True
            state_update = {
                key: value for key, value in result.items() if key not in self.runtime_config.excluded_state_keys
            }

            if self.registry is not None:
                if success:
                    self.registry.complete(
                        agent_name=self.definition.name,
                        thread_id=thread_id,
                        response=response_text,
                        context_notes=context_notes,
                    )
                else:
                    self.registry.fail(
                        agent_name=self.definition.name,
                        thread_id=thread_id,
                        error=response_text or "subagent returned an error status",
                        context_notes=context_notes,
                    )

            return SubAgentInvocationResult(
                status="completed",
                response=response_text,
                agent_name=self.definition.name,
                role=self.definition.role,
                success=success,
                state_update=state_update,
                tool_names=self.tool_names,
                thread_id=thread_id,
                sandbox=self.sandbox.to_dict(),
                isolation=isolation_projection,
                context_notes=context_notes,
            ).to_dict()
        except Exception as exc:
            import traceback
            error_context = traceback.format_exc()
            if self.registry is not None:
                self.registry.fail(
                    agent_name=self.definition.name,
                    thread_id=thread_id,
                    error=str(exc),
                    error_context=error_context,
                )
            raise
        finally:
            if self.registry is None:
                self._clear_runtime_context()
            elif run_record is None or self.registry.get_active(agent_name=self.definition.name, thread_id=thread_id):
                current_depth = (
                    run_record.depth if run_record is not None else int(self.runtime_context.get("depth", 0) or 0)
                )
                self._set_runtime_context(
                    thread_id=thread_id,
                    run_id=run_record.run_id if run_record is not None else self.runtime_context.get("run_id"),
                    depth=current_depth,
                    isolation=self.runtime_context.get("isolation", {}),
                )
            else:
                self._clear_runtime_context()

    def _invoke_stream(
        self,
        *,
        subagent_state: dict[str, Any],
        config: dict[str, Any],
        thread_id: str,
        run_record: Any,
    ) -> Any:
        """Yield stream events from the subagent graph."""
        try:
            # We can intercept events here later if we need to enrich the stream.
            yield from self.graph.stream(subagent_state, config=config, stream_mode="values")

            # Note: Streaming doesn't currently auto-resolve approvals in the same way
            # as the sync invoke. The caller needs to handle the final state.
            if self.registry is not None:
                self.registry.complete(
                    agent_name=self.definition.name,
                    thread_id=thread_id,
                    response="Stream completed",
                )
        except Exception as exc:
            import traceback
            error_context = traceback.format_exc()
            if self.registry is not None:
                self.registry.fail(
                    agent_name=self.definition.name,
                    thread_id=thread_id,
                    error=str(exc),
                    error_context=error_context,
                )
            raise
        finally:
            self._clear_runtime_context()

    def resume_approval(
        self,
        *,
        approval_id: str,
        thread_id: str,
        approved: bool,
        note: str = "",
    ) -> dict[str, Any]:
        active_record = (
            self.registry.get_latest(agent_name=self.definition.name, thread_id=thread_id) if self.registry else None
        )
        if active_record is not None and active_record.status == "aborted":
            return {
                "status": "aborted",
                "response": f"子智能体 '{self.definition.name}' 已终止，不再继续执行。",
                "thread_id": thread_id,
            }
        if active_record is not None and active_record.status == "timed_out":
            return {
                "status": "timed_out",
                "response": f"子智能体 '{self.definition.name}' 已超时，不再继续执行。",
                "thread_id": thread_id,
            }
        if active_record is not None:
            self._set_runtime_context(
                thread_id=thread_id,
                run_id=active_record.run_id,
                depth=active_record.depth,
            )
        request = self.approval_queue.get_request(approval_id)
        approval = approval_interrupt_from_metadata(
            request.metadata if request is not None else None,
            fallback_scope=f"subagent:{self.definition.name}",
        )
        if approval is None:
            raise ValueError(f"无法从审批请求 '{approval_id}' 恢复子智能体工具审批")

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.runtime_config.recursion_limit,
        }
        return self._resume_tool_approval(
            approval=approval,
            thread_id=thread_id,
            config=config,
            approved=approved,
            note=note,
        )

    def steer(self, *, thread_id: str, instructions: str) -> dict[str, Any]:
        if self.registry is None:
            return {"success": False, "error": "subagent registry not configured", "thread_id": thread_id}
        record = self.registry.record_steer(
            agent_name=self.definition.name,
            thread_id=thread_id,
            instructions=instructions,
        )
        if record is None:
            return {"success": False, "error": "subagent not active", "thread_id": thread_id}
        return {
            "success": True,
            "status": record.status,
            "thread_id": thread_id,
            "queued_instructions": len(record.steering_instructions),
        }

    def abort(self, *, thread_id: str, reason: str = "") -> dict[str, Any]:
        if self.registry is None:
            return {"success": False, "error": "subagent registry not configured", "thread_id": thread_id}
        record = self.registry.abort(
            agent_name=self.definition.name,
            thread_id=thread_id,
            reason=reason or "aborted by operator",
        )
        if record is None:
            return {"success": False, "error": "subagent not active", "thread_id": thread_id}
        self.close()
        return {
            "success": True,
            "status": "aborted",
            "thread_id": thread_id,
            "reason": record.error,
        }

    def _register_tool_approval(
        self,
        response: dict[str, Any],
        *,
        thread_id: str,
        config: dict[str, Any],
    ):
        interrupts = extract_tool_approval_interrupts(
            response,
            scope=f"subagent:{self.definition.name}",
        )
        if not interrupts:
            return None
        approval = interrupts[0]
        return create_tool_approval_request(
            approval_queue=self.approval_queue,
            approval=approval,
            thread_id=thread_id,
            target=f"subagent:{self.definition.name}",
            callback=lambda approved, note: self._resume_tool_approval(
                approval=approval,
                thread_id=thread_id,
                config=config,
                approved=approved,
                note=note,
            ),
        )

    def _resume_tool_approval(
        self,
        *,
        approval,
        thread_id: str,
        config: dict[str, Any],
        approved: bool,
        note: str,
    ) -> dict[str, Any]:
        if self.registry is not None:
            steering_notes = self.registry.consume_steering(
                agent_name=self.definition.name,
                thread_id=thread_id,
            )
            if steering_notes:
                steer_text = "\n".join(f"- {item}" for item in steering_notes)
                note = (
                    (note.strip() + "\n\n附加调度指令：\n" + steer_text).strip()
                    if note.strip()
                    else ("附加调度指令：\n" + steer_text)
                )
        response = self.graph.invoke(
            build_tool_approval_resume_command(approval, approved=approved, note=note),
            config=config,
        )
        pending = self._register_tool_approval(response, thread_id=thread_id, config=config)
        if pending is not None:
            if self.registry is not None:
                self.registry.mark_waiting_approval(
                    agent_name=self.definition.name,
                    thread_id=thread_id,
                    approval_id=pending.approval_id,
                )
            return {
                "status": "waiting_approval",
                "response": (
                    f"子智能体 '{self.definition.name}' 继续执行后再次暂停，等待审批（{pending.approval_id}）。"
                ),
                "approval_id": pending.approval_id,
                "thread_id": thread_id,
            }
        final_messages = response.get("messages", [])
        response_text = final_messages[-1].content if final_messages else ""
        success = getattr(final_messages[-1], "status", "success") != "error" if final_messages else approved
        if self.registry is not None:
            if success:
                self.registry.complete(
                    agent_name=self.definition.name,
                    thread_id=thread_id,
                    response=response_text,
                )
            else:
                self.registry.fail(
                    agent_name=self.definition.name,
                    thread_id=thread_id,
                    error=response_text or "subagent returned an error status",
                )
            self._clear_runtime_context()
        return {
            "status": "completed",
            "response": response_text,
            "success": success,
            "thread_id": thread_id,
        }

    def _begin_run(
        self,
        *,
        thread_id: str,
        parent_agent_name: str | None,
        parent_run_id: str | None,
        parent_thread_id: str | None,
        parent_depth: int,
    ):
        if self.registry is None:
            self._set_runtime_context(thread_id=thread_id, run_id=None, depth=max(0, int(parent_depth)) + 1)
            return None
        record = self.registry.spawn(
            agent_name=self.definition.name,
            thread_id=thread_id,
            parent_agent_name=parent_agent_name,
            parent_run_id=parent_run_id,
            parent_thread_id=parent_thread_id,
            parent_depth=parent_depth,
            team_key=str(self.runtime_context.get("team_key", "")).strip(),
            owner_session_key=str(self.runtime_context.get("session_key", "")).strip(),
            owner_thread_id=str(self.runtime_context.get("owner_thread_id", "")).strip(),
            timeout_seconds=self.control_policy.subagent_timeout_seconds,
            metadata={"role": self.definition.role},
        )
        self._set_runtime_context(thread_id=thread_id, run_id=record.run_id, depth=record.depth)
        return record

    def _set_runtime_context(
        self,
        *,
        thread_id: str,
        run_id: str | None,
        depth: int,
        isolation: dict[str, Any] | None = None,
    ) -> None:
        self.runtime_context["current_agent_name"] = self.definition.name
        self.runtime_context["thread_id"] = thread_id
        self.runtime_context["run_id"] = run_id
        self.runtime_context["depth"] = max(0, int(depth))
        if isolation is not None:
            payload = dict(isolation)
            self.runtime_context["isolation"] = payload
            exec_opts = materialize_isolation_execution_options(payload)
            self.runtime_context["execution_options"] = exec_opts
            self.runtime_context["permission_scope"] = str(payload.get("permission_scope", "")).strip()
            self.runtime_context["audit_scope"] = str(payload.get("audit_scope", "")).strip()
            self.runtime_context["session_key"] = str(payload.get("owner_session_key", "")).strip()
            self.runtime_context["owner_thread_id"] = str(payload.get("owner_thread_id", "")).strip()
            self.runtime_context["team_key"] = (
                str(payload.get("owner_session_key", "")).strip()
                or str(payload.get("owner_thread_id", "")).strip()
                or thread_id
            )
            
            if hasattr(self, "tools"):
                target_cwd = exec_opts.get("cwd") or ""
                target_worktree = exec_opts.get("worktree_dir") or target_cwd
                for tool in self.tools:
                    if hasattr(tool, "workspace_dir") and target_worktree:
                        tool.workspace_dir = target_worktree
                    if hasattr(tool, "cwd") and target_cwd:
                        tool.cwd = target_cwd

    def _clear_runtime_context(self) -> None:
        self.runtime_context["thread_id"] = None
        self.runtime_context["run_id"] = None
        self.runtime_context["depth"] = 0
        self.runtime_context["isolation"] = {}
        self.runtime_context["execution_options"] = {}
        self.runtime_context["permission_scope"] = ""
        self.runtime_context["audit_scope"] = ""
        self.runtime_context["session_key"] = ""
        self.runtime_context["owner_thread_id"] = ""
        self.runtime_context["team_key"] = ""

    def close(self) -> None:
        self.checkpoint_bundle.close()


def _default_llm(model: str, temperature: float) -> BaseChatModel:
    """Fallback LLM creation when no factory is provided."""
    from core.systems.runtime.model_resolver import ModelProviderError, resolve_model

    try:
        return resolve_model(model, temperature=temperature).model
    except ModelProviderError:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=temperature)


def create_sub_agent_instance(
    *,
    agent_def: AgentDefinition,
    tool_storage: ToolStorage | None = None,
    global_tool_storage: ToolStorage | None = None,
    agent_storage: Any = None,
    llm_factory: Any = None,
    control_policy: AgentControlPolicy | None = None,
    approval_queue: ApprovalQueue | None = None,
    project_paths: ProjectPaths | None = None,
    registry: SubagentRegistry | None = None,
    runtime_context: dict[str, Any] | None = None,
    runtime_config: SubAgentRuntimeConfig | None = None,
    cwd: str | None = None,
    worktree_dir: str | None = None,
    remote_target: str | None = None,
    capability_override: dict[str, Any] | str | None = None,
    policy_override: dict[str, Any] | str | None = None,
) -> SubAgentRuntime:
    """Create an isolated subagent runtime with its own checkpoint scope."""
    from langchain.agents import create_agent

    base_profile = AgentCapabilityProfile.from_value(agent_def.capability_profile)
    if capability_override:
        # Merge override into base profile
        override_data = base_profile.to_dict()
        if isinstance(capability_override, str):
            try:
                ov = json.loads(capability_override)
                if isinstance(ov, dict):
                    override_data.update(ov)
            except Exception:
                pass
        elif isinstance(capability_override, dict):
            override_data.update(capability_override)
        profile = AgentCapabilityProfile.from_dict(override_data)
    else:
        profile = base_profile

    middleware_profile = AgentMiddlewareProfile.from_value(agent_def.middleware_profile)
    subagent_policy = derive_subagent_control_policy(base_policy=control_policy, capability_profile=profile)
    
    if policy_override:
        subagent_policy = subagent_policy.merge_with_override(policy_override)

    llm = (
        llm_factory(model=agent_def.model, temperature=agent_def.temperature)
        if llm_factory
        else _default_llm(model=agent_def.model, temperature=agent_def.temperature)
    )
    sandbox = build_subagent_sandbox(
        agent_name=agent_def.name,
        capability_profile=profile,
        project_paths=project_paths,
    )
    
    # Materialize execution options
    options = {}
    if cwd:
        options["cwd"] = str(cwd).strip()
    if worktree_dir:
        options["worktree_dir"] = str(worktree_dir).strip()
    if remote_target:
        options["remote_target"] = str(remote_target).strip()

    runtime_context_val: dict[str, Any] = {
        "current_agent_name": agent_def.name,
        "thread_id": None,
        "run_id": None,
        "depth": 0,
        "session_key": "",
        "permission_scope": "",
        "audit_scope": "",
        "isolation": {},
        "execution_options": options,
    }
    if runtime_context:
        runtime_context_val.update(runtime_context)
        if options:
            runtime_context_val.setdefault("execution_options", {}).update(options)

    from core.systems.memory.admin_memory import create_llm_summarizer
    summarize_fn = create_llm_summarizer(llm)

    tools = resolve_subagent_tools(
        agent_def=agent_def,
        tool_storage=tool_storage,
        global_tool_storage=global_tool_storage,
        agent_storage=agent_storage,
        llm_factory=llm_factory,
        control_policy=control_policy,
        approval_queue=approval_queue,
        project_paths=project_paths,
        runtime_context=runtime_context_val,
        registry=registry,
        summarize_fn=summarize_fn,
    )
    tool_names = [tool.name for tool in tools]
    known_dynamic_tool_names = set(agent_def.tools)
    if tool_storage is not None:
        known_dynamic_tool_names.update(tool_storage.list_tools().keys())
    middleware = DynamicToolMiddleware(
        tool_storage=tool_storage,
        control_policy=subagent_policy,
        approval_queue=approval_queue,
        approval_scope=f"subagent:{agent_def.name}",
        event_context_resolver=lambda: {
            "thread_id": str(runtime_context.get("thread_id", "") or ""),
            "run_id": str(runtime_context.get("run_id", "") or ""),
            "current_agent_name": str(runtime_context.get("current_agent_name", agent_def.name) or agent_def.name),
            "approval_scope": str(runtime_context.get("permission_scope", "") or f"subagent:{agent_def.name}"),
            "audit_scope": str(runtime_context.get("audit_scope", "") or ""),
            "cwd": str(runtime_context.get("execution_options", {}).get("cwd", "") or ""),
            "worktree_dir": str(runtime_context.get("execution_options", {}).get("worktree_dir", "") or ""),
            "remote_target": str(runtime_context.get("execution_options", {}).get("remote_target", "") or ""),
            "root_mode": "assistant",
        },
    )
    middleware.set_base_tools(tools)
    middleware.set_known_dynamic_tools(sorted(known_dynamic_tool_names))
    checkpoint_bundle = build_subagent_checkpointer(
        agent_name=agent_def.name,
        project_paths=project_paths,
    )
    graph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=agent_def.system_prompt,
        middleware=build_subagent_langchain_middleware(
            definition=agent_def,
            sandbox=sandbox,
            capability_profile=profile,
            middleware_profile=middleware_profile,
            effective_policy=subagent_policy,
            tool_middleware=middleware,
        ),
        checkpointer=checkpoint_bundle.checkpointer,
    )
    return SubAgentRuntime(
        graph=graph,
        definition=agent_def,
        tools=tools,
        tool_names=tool_names,
        control_policy=subagent_policy,
        sandbox=sandbox,
        checkpoint_bundle=checkpoint_bundle,
        approval_queue=approval_queue,
        registry=registry,
        runtime_context=runtime_context,
        runtime_config=runtime_config,
    )


def _resolve_capability_management_tools(
    *,
    capability_profile: AgentCapabilityProfile,
    local_tool_storage: ToolStorage | None,
    global_tool_storage: ToolStorage | None,
    agent_storage: Any,
    llm_factory: Any,
    control_policy: AgentControlPolicy | None,
    approval_queue: ApprovalQueue | None,
    project_paths: ProjectPaths | None,
    runtime_context: dict[str, Any] | None = None,
    registry: SubagentRegistry | None = None,
) -> list[Any]:
    tools: list[Any] = []

    if capability_profile.allow_local_tool_creation or capability_profile.allow_template_tools:
        from core.assets.tools.tool_creator import ListTemplatesTool, TemplateToolCreator, ToolCreatorTool

        if capability_profile.allow_local_tool_creation and local_tool_storage is not None:
            tools.append(ToolCreatorTool(storage=local_tool_storage, agent_storage=None))
        if capability_profile.allow_template_tools and local_tool_storage is not None:
            tools.append(TemplateToolCreator(storage=local_tool_storage, agent_storage=None))
            tools.append(ListTemplatesTool())

    if capability_profile.allow_local_tool_removal and local_tool_storage is not None:
        from core.assets.tools.tool_creator import RemoveToolTool

        tools.append(RemoveToolTool(storage=local_tool_storage))

    if capability_profile.allow_agent_creation and agent_storage is not None:
        from .agent_creator import AgentCreatorTool

        tools.append(AgentCreatorTool(agent_storage=agent_storage))

    if capability_profile.allow_list_agents and agent_storage is not None:
        from .agent_creator import ListAgentsTool

        tools.append(ListAgentsTool(agent_storage=agent_storage))

    if capability_profile.allow_agent_delegation and agent_storage is not None and llm_factory is not None:
        from .agent_creator import DelegateToAgentTool

        tools.append(
            DelegateToAgentTool(
                agent_storage=agent_storage,
                llm_factory=llm_factory,
                control_policy=control_policy,
                global_tool_storage=global_tool_storage,
                approval_queue=approval_queue,
                project_paths=project_paths,
                runtime_context=runtime_context,
                subagent_registry=registry,
            )
        )

    if capability_profile.allow_agent_removal and agent_storage is not None:
        from .agent_creator import RemoveAgentTool

        tools.append(RemoveAgentTool(agent_storage=agent_storage))

    return tools
