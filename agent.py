"""Root PyBot runtime and public factory."""

from __future__ import annotations

import re
import traceback
from typing import Any

from core.agent_capability_profile import AgentCapabilityProfile
from core.agent_middleware_profile import AgentMiddlewareProfile
from core.agent_services import resume_persisted_agent_approval
from core.agent_tool_inventory import build_agent_tool_inventory
from core.approval_queue import ApprovalQueue
from core.project_paths import ProjectPaths
from core.pybot_bootstrap import (
    assemble_primary_tools,
    build_runtime,
    create_llm_client,
    create_root_agent,
    invoke_sub_agent,
)
from core.pybot_streaming import stream_chat_events
from core.subagent_governance import build_subagent_governance_snapshot
from core.tool_approval_runtime import (
    approval_interrupt_from_metadata,
    build_delegated_approval_resume_command,
    build_tool_approval_resume_command,
    create_tool_approval_request,
    extract_delegated_approval_interrupts,
    extract_tool_approval_interrupts,
)
from core.tool_storage import ToolStorage


class PyBot:
    """PyBot v4 runtime composed from smaller bootstrap modules."""

    def __init__(
        self,
        model: str = "gpt-4",
        thread_id: str = "default",
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.7,
        enable_agent_creation: bool = True,
        workspace_dir: str = "workspace",
        paths: ProjectPaths | None = None,
        control_config: dict[str, Any] | None = None,
        approval_queue: ApprovalQueue | None = None,
        provider: str | None = None,
        fallback_configs: list[dict[str, Any]] | None = None,
    ):
        self.paths = paths or ProjectPaths.from_root(workspace_dir=workspace_dir)
        self.paths.ensure_runtime_dirs()
        self.model_name = model
        self.thread_id = thread_id
        self.temperature = temperature
        self.enable_agent_creation = enable_agent_creation
        self.workspace_dir = str(self.paths.workspace_dir)
        self._api_key = api_key
        self._base_url = base_url
        self._provider = provider
        self._fallback_configs = fallback_configs or []
        self._control_config = control_config
        self._approval_queue = approval_queue

        self.runtime = build_runtime(
            paths=self.paths,
            model=model,
            temperature=temperature,
            api_key=api_key,
            base_url=base_url,
            provider=provider,
            fallback_configs=fallback_configs,
            thread_id=thread_id,
            control_config=control_config,
            approval_queue=approval_queue,
            summarize_callback=self._lazy_agent_callback,
            tool_callback=self._pyflow_tool_callback,
            agent_callback=self._pyflow_agent_callback,
            delegate_callback=self._pyflow_delegate_callback,
        )
        self._bind_runtime()
        self._initialize_agent()
        self._print_startup_summary()

    def _bind_runtime(self) -> None:
        """Expose runtime services on the public instance for backward compatibility."""
        self.storage = self.runtime.storage
        self.agent_storage = self.runtime.agent_storage
        self.backend = self.runtime.backend
        self.workspace = self.runtime.workspace
        self.memory = self.runtime.memory
        self.skill_registry = self.runtime.skill_registry
        self.scheduler = self.runtime.scheduler
        self.app_manager = self.runtime.app_manager
        self.skill_marketplace = self.runtime.skill_marketplace
        self.mcp_hub = self.runtime.mcp_hub
        self.channel_manager = self.runtime.channel_manager
        self.pyflow_engine = self.runtime.pyflow_engine
        self.tool_chain = self.runtime.tool_chain
        self.eval_framework = self.runtime.eval_framework
        self.context_manager = self.runtime.context_manager
        self.capability_bus = self.runtime.capability_bus
        self.mw_stack = self.runtime.middleware_stack  # legacy; new pipeline in LangChain middleware
        self.llm = self.runtime.llm
        self.checkpointer = self.runtime.checkpointer
        self.middleware = self.runtime.middleware
        self.control_policy = self.runtime.control_policy
        self.approval_queue = self.runtime.approval_queue

    def _print_startup_summary(self) -> None:
        skills_count = len(self.skill_registry.skills)
        tasks_count = len(self.scheduler.tasks)
        print("✅ PyBot v4.0 已初始化")
        print(f"   模型: {self.model_name}")
        print(f"   会话ID: {self.thread_id}")
        print(f"   工作空间: {self.workspace_dir}/")
        print(f"   后端: {type(self.backend).__name__}")
        print(f"   技能: {skills_count} 个已加载（渐进式披露）")
        print(f"   定时任务: {tasks_count} 个已配置")
        lc_mw_names = []
        for mw in self._lc_middleware_names():
            lc_mw_names.append(mw)
        print(f"   中间件: {' → '.join(lc_mw_names)}")

    def _lc_middleware_names(self) -> list[str]:
        """Return display names for the active LangChain middleware stack."""
        try:
            from core.agent_middleware_factory import build_root_langchain_middleware

            mws = build_root_langchain_middleware(runtime=self.runtime)
            return [getattr(m, "name", None) or type(m).__name__ for m in mws]
        except Exception:
            return self.mw_stack.layers

    def _create_llm(self, model: str | None = None, temperature: float | None = None):
        """Create LLM instances for delegated sub-agents."""
        return create_llm_client(
            model=model or self.model_name,
            temperature=self.temperature if temperature is None else temperature,
            api_key=self._api_key,
            base_url=self._base_url,
            provider=self._provider,
        )

    def _initialize_agent(self) -> None:
        """Assemble and create the root agent."""
        assembly = assemble_primary_tools(
            runtime=self.runtime,
            paths=self.paths,
            enable_agent_creation=self.enable_agent_creation,
            llm_factory=self._create_llm,
            chat_callback=self.chat,
        )
        for label, count in assembly.tool_groups:
            print(f"   {label}: {count} 个已注入")
        self.agent = create_root_agent(runtime=self.runtime, assembly=assembly)

    def _do_invoke(self, message: str, *, tools_before: int) -> dict[str, Any]:
        """Single invoke call.

        All middleware (context, summarization, memory, bus, eviction,
        tool-call patching) now runs through the unified LangChain pipeline.
        """
        config = self._invoke_config()
        invoke_state = {"messages": [{"role": "user", "content": message}]}
        response = self.agent.invoke(invoke_state, config=config)
        return self._finalize_invoke_response(response, config=config, tools_before=tools_before)

    def _finalize_invoke_response(
        self,
        response: dict[str, Any],
        *,
        config: dict[str, Any],
        tools_before: int,
    ) -> dict[str, Any]:
        pending = self._register_tool_approval(response, config=config)
        if pending is not None:
            return pending

        messages = response.get("messages", [])
        reply = self._extract_final_reply(messages)
        self._refresh_root_agent_if_tools_changed(tools_before)
        return {"status": "completed", "response": reply}

    @staticmethod
    def _extract_final_reply(messages: list[Any]) -> str:
        """Extract the best text reply from a finished agent message list.

        Strategy: walk backwards to find the last AIMessage with non-empty
        text content.  Falls back to the last ToolMessage content (which may
        be the result of a return_direct tool) if no AIMessage is found.
        Applies deduplication to catch LLM repetition loops.
        """
        from langchain_core.messages import AIMessage, ToolMessage

        last_tool_content: str | None = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                text = (msg.content or "").strip()
                if text:
                    return _deduplicate_response(text)
            elif isinstance(msg, ToolMessage) and last_tool_content is None:
                text = (msg.content or "").strip()
                if text:
                    last_tool_content = text

        if last_tool_content:
            return last_tool_content
        return "（无回复）"

    def _register_tool_approval(
        self,
        response: dict[str, Any],
        *,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        delegated = self._register_delegated_tool_approval(response, config=config)
        if delegated is not None:
            return delegated

        interrupts = extract_tool_approval_interrupts(response, scope=self.middleware.approval_scope)
        if not interrupts:
            return None

        approval = interrupts[0]
        request = create_tool_approval_request(
            approval_queue=self.approval_queue,
            approval=approval,
            thread_id=self.thread_id,
            target="root_agent",
            callback=lambda approved, note: self._resume_tool_approval(
                approval=approval,
                config=config,
                approved=approved,
                note=note,
            ),
        )
        return {
            "status": "waiting_approval",
            "approval_id": request.approval_id,
            "response": (f"⏸️ 已暂停，等待人工审批（{request.approval_id}）。可在 Approval Center 中批准或拒绝后继续。"),
        }

    def _register_delegated_tool_approval(
        self,
        response: dict[str, Any],
        *,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        interrupts = extract_delegated_approval_interrupts(response, scope=self.middleware.approval_scope)
        if not interrupts:
            return None

        delegated = interrupts[0]
        request = self.approval_queue.get_request(delegated.approval_id)
        if request is not None:
            self.approval_queue.update_request_metadata(
                delegated.approval_id,
                parent_thread_id=self.thread_id,
                parent_target="root_agent",
            )
        prompt = request.prompt if request is not None else "子智能体工具调用需要审批。"
        return {
            "status": "waiting_approval",
            "approval_id": delegated.approval_id,
            "response": (f"⏸️ 子智能体委派已暂停，等待人工审批（{delegated.approval_id}）。\n{prompt}"),
        }

    def _resume_tool_approval(
        self,
        *,
        approval,
        config: dict[str, Any],
        approved: bool,
        note: str,
    ) -> dict[str, Any]:
        tools_before = len(self.storage.tools)
        response = self.agent.invoke(
            build_tool_approval_resume_command(approval, approved=approved, note=note),
            config=config,
        )
        return self._finalize_invoke_response(response, config=config, tools_before=tools_before)

    def _resume_delegated_tool_approval(
        self,
        *,
        approval_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        tools_before = len(self.storage.tools)
        response = self.agent.invoke(
            build_delegated_approval_resume_command(approval_id),
            config=config,
        )
        return self._finalize_invoke_response(response, config=config, tools_before=tools_before)

    def _invoke_config(self, *, thread_id: str | None = None) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id or self.thread_id},
            "recursion_limit": 100,
        }

    @staticmethod
    def _is_recorded_resolution(payload: Any) -> bool:
        return isinstance(payload, dict) and str(payload.get("status", "")).strip() == "recorded"

    def _resume_root_tool_approval_from_request(
        self,
        *,
        request: Any,
        approved: bool,
        note: str,
    ) -> dict[str, Any] | None:
        approval = approval_interrupt_from_metadata(
            request.metadata,
            fallback_scope=self.middleware.approval_scope,
        )
        if approval is None:
            return None
        thread_id = str(request.metadata.get("thread_id", "")).strip() or self.thread_id
        return self._resume_tool_approval(
            approval=approval,
            config=self._invoke_config(thread_id=thread_id),
            approved=approved,
            note=note,
        )

    def _resume_subagent_approval_from_request(
        self,
        *,
        request: Any,
        approved: bool,
        note: str,
    ) -> dict[str, Any] | None:
        target = str(request.metadata.get("target", "")).strip()
        if not target.startswith("subagent:"):
            return None
        agent_name = target.split(":", 1)[1].strip()
        thread_id = str(request.metadata.get("thread_id", "")).strip()
        if not agent_name or not thread_id:
            return None
        return resume_persisted_agent_approval(
            agent_storage=self.agent_storage,
            llm_factory=self._create_llm,
            approval_queue=self.approval_queue,
            approval_id=request.approval_id,
            agent_name=agent_name,
            thread_id=thread_id,
            approved=approved,
            note=note,
            control_policy=self.control_policy,
            global_tool_storage=self.storage,
            project_paths=self.paths,
        )

    def _rebuild_runtime_result_if_needed(
        self,
        *,
        request: Any | None,
        result: dict[str, Any],
        approved: bool,
        note: str,
    ) -> dict[str, Any]:
        if request is None or not result.get("success"):
            return result

        target = str(request.metadata.get("target", "")).strip()
        resolved_payload = result.get("result")
        if not self._is_recorded_resolution(resolved_payload):
            return result

        replayed = None
        if target == "root_agent":
            replayed = self._resume_root_tool_approval_from_request(
                request=request,
                approved=approved,
                note=note,
            )
        elif target.startswith("subagent:"):
            replayed = self._resume_subagent_approval_from_request(
                request=request,
                approved=approved,
                note=note,
            )

        if replayed is None:
            return result

        self.approval_queue.set_resolution_result(request.approval_id, replayed)
        result["result"] = replayed
        return result

    def _resume_parent_orchestration_if_needed(
        self,
        *,
        request: Any | None,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if request is None or not result.get("success"):
            return result
        parent_target = str(request.metadata.get("parent_target", "")).strip()
        parent_thread_id = str(request.metadata.get("parent_thread_id", "")).strip()
        if parent_target != "root_agent" or parent_thread_id != self.thread_id:
            return result

        orchestration_result = self._resume_delegated_tool_approval(
            approval_id=request.approval_id,
            config=self._invoke_config(thread_id=parent_thread_id),
        )
        result["subagent_result"] = result.get("result")
        result["result"] = orchestration_result
        return result

    def _refresh_root_agent_if_tools_changed(self, tools_before: int) -> None:
        tools_after = len(self.storage.tools)
        if tools_after <= tools_before:
            return
        new_tool_name = self.middleware.last_created_tool or "未知"
        print(f"[INFO] 新全局工具创建成功: {new_tool_name}")
        self._initialize_agent()
        print("[INFO] Agent 已更新，新工具可用")

    def chat(self, message: str) -> str:
        """Chat with the root agent and reload it when tools change."""
        try:
            tools_before = len(self.storage.tools)

            try:
                result = self._do_invoke(message, tools_before=tools_before)
            except Exception as invoke_err:
                err_str = str(invoke_err)
                tools_after = len(self.storage.tools)
                if "unknown tool" in err_str.lower() and tools_after > tools_before:
                    print("[INFO] 新工具创建后触发了 unknown tool 错误，重建 Agent 并重试...")
                    self._initialize_agent()
                    result = self._do_invoke(message, tools_before=len(self.storage.tools))
                else:
                    raise

            self.capability_bus.record_invocation("chat", True)
            return str(result.get("response", "（无回复）"))
        except Exception as exc:
            error_trace = traceback.format_exc()
            print(f"[ERROR] 对话出错:\n{error_trace}")
            self.capability_bus.record_invocation("chat", False)
            return f"❌ 错误: {exc!s}"

    def _lazy_agent_callback(self, prompt: str) -> str:
        try:
            return self.chat(prompt)
        except Exception as exc:
            return f"Agent 回调失败: {exc}"

    def _pyflow_tool_callback(self, tool_name: str, args: dict[str, Any]) -> Any:
        for tool in self.middleware.get_all_tools():
            if tool.name == tool_name:
                return tool.invoke(args)
        raise ValueError(f"工具 '{tool_name}' 不存在")

    def _pyflow_agent_callback(self, prompt: str) -> str:
        return self.chat(prompt)

    def _pyflow_delegate_callback(self, agent_name: str, task: str, context: str = "") -> dict[str, Any]:
        """Delegate a workflow node to a persisted sub-agent and return structured feedback."""
        return invoke_sub_agent(
            agent_storage=self.agent_storage,
            global_tool_storage=self.storage,
            llm_factory=self._create_llm,
            control_policy=self.control_policy,
            approval_queue=self.approval_queue,
            project_paths=self.paths,
            agent_name=agent_name,
            task=task,
            context=context,
        )

    def chat_stream(self, message: str):
        """Yield step events and the final result for a chat request."""
        yield from stream_chat_events(
            message=message,
            chat_callable=self.chat,
            list_agents_callable=self.list_agents,
            list_tools_callable=self.list_tools,
        )

    def list_tools(self) -> dict[str, str]:
        return self.storage.list_tools()

    def get_tool_usage_stats(self) -> dict[str, int]:
        return self.middleware.get_usage_stats()

    def get_control_snapshot(self) -> dict[str, Any]:
        return self.middleware.get_control_snapshot()

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        return self.approval_queue.list_pending()

    def resolve_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        note: str = "",
        approver: str = "",
        resolution_labels: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        request = self.approval_queue.get_request(approval_id)
        result = self.approval_queue.resolve(
            approval_id,
            approved=approved,
            note=note,
            resolved_by=approver,
            resolution_labels=resolution_labels,
        )
        result = self._rebuild_runtime_result_if_needed(
            request=request,
            result=result,
            approved=approved,
            note=note,
        )
        return self._resume_parent_orchestration_if_needed(
            request=request,
            result=result,
        )

    def list_agents(self) -> dict[str, str]:
        return self.agent_storage.list_agents()

    def get_agent_details(self) -> list[dict]:
        details: list[dict] = []
        for agent_def in self.agent_storage.agents.values():
            local_tool_storage = ToolStorage(str(self.agent_storage.tools_dir_for(agent_def.name)))
            details.append(
                {
                    **agent_def.to_dict(),
                    "tool_inventory": build_agent_tool_inventory(
                        agent_def=agent_def,
                        global_tool_storage=self.storage,
                        local_tool_storage=local_tool_storage,
                    ),
                    "governance": build_subagent_governance_snapshot(
                        base_policy=self.control_policy,
                        capability_profile=AgentCapabilityProfile.from_value(agent_def.capability_profile),
                        middleware_profile=AgentMiddlewareProfile.from_value(agent_def.middleware_profile),
                    ),
                }
            )
        return details

    def export_tools(self, filepath: str) -> None:
        self.storage.export_to_json(filepath)

    def export_agents(self, filepath: str) -> None:
        self.agent_storage.export_to_json(filepath)


def _deduplicate_response(text: str, min_block_len: int = 60) -> str:
    """Remove repeated text blocks from an LLM response.

    LLMs sometimes enter a token-level repetition loop, producing the same
    paragraph many times.  This function detects and truncates such output.
    """
    if len(text) < min_block_len * 2:
        return text

    lines = text.split("\n")
    if len(lines) < 4:
        best = text
        for block_len in range(min_block_len, len(text) // 2 + 1, 20):
            tail = text[-block_len:]
            first_occurrence = text.find(tail)
            if first_occurrence < len(text) - block_len:
                best = text[: first_occurrence + block_len].rstrip()
                break
        return best

    seen_blocks: list[str] = []
    result_lines: list[str] = []
    repeat_count = 0
    max_repeats = 2

    i = 0
    while i < len(lines):
        line = lines[i]

        matched = False
        for block_size in range(3, min(8, len(lines) - i + 1)):
            candidate = "\n".join(lines[i : i + block_size])
            if len(candidate) < min_block_len:
                continue
            if candidate in seen_blocks:
                repeat_count += 1
                if repeat_count >= max_repeats:
                    i = len(lines)
                    matched = True
                    break
                i += block_size
                matched = True
                break

        if not matched:
            result_lines.append(line)
            i += 1

        if len(result_lines) >= 3:
            block = "\n".join(result_lines[-3:])
            if len(block) >= min_block_len and block not in seen_blocks:
                seen_blocks.append(block)

    return "\n".join(result_lines).rstrip()


def create_tool_creator_agent(
    model: str = "gpt-4",
    thread_id: str = "default",
    paths: ProjectPaths | None = None,
    control_config: dict[str, Any] | None = None,
    approval_queue: ApprovalQueue | None = None,
    **kwargs,
) -> PyBot:
    """Factory for the root PyBot instance."""
    return PyBot(
        model=model,
        thread_id=thread_id,
        paths=paths,
        control_config=control_config,
        approval_queue=approval_queue,
        **kwargs,
    )


if __name__ == "__main__":
    from core.config import get_llm_config, get_llm_fallback_config

    llm_config = get_llm_config()
    agent = create_tool_creator_agent(
        model=llm_config.get("model", "gpt-4"),
        thread_id="test-session-dir",
        api_key=llm_config.get("api_key"),
        base_url=llm_config.get("api_base"),
        provider=llm_config.get("provider"),
        fallback_configs=get_llm_fallback_config(),
    )

    print("\n" + "=" * 40)
    print("测试1：创建智能体")
    print("=" * 40)
    print(agent.chat("创建一个数学专家智能体(name: math_expert)，擅长数值计算"))
