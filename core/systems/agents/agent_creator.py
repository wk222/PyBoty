"""Tool façade for persisted subagent management."""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from core.assets.tools import ToolStorage
from core.systems.governance.agent_control import AgentControlPolicy
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.runtime.event_bus import Event, EventType, event_bus
from core.systems.runtime.project_paths import ProjectPaths

from .agent_services import create_agent_record, delegate_agent_task
from core.assets.agents.storage import AgentDefinition, AgentModelConfig, AgentStorage
from .subagent_runtime import create_sub_agent_instance


class AgentCreatorInput(BaseModel):
    """Input schema for creating a persisted subagent."""

    agent_name: str = Field(description="智能体名称（英文+下划线，如 data_analyst）")
    role: str = Field(description="智能体角色（如：数据分析师、代码审查员、文档撰写者）")
    description: str = Field(description="智能体功能描述，清晰说明该智能体的专长和用途")
    system_prompt: str = Field(
        description="""智能体的系统提示词，定义其行为和能力。
示例：
"你是一个专业的数据分析师，擅长：
1. 数据清洗和预处理
2. 统计分析和可视化
3. 生成分析报告
请用专业但易懂的语言回答问题。"
"""
    )
    capabilities: str = Field(
        description="""能力标签（JSON数组格式），用于分类和查找。
示例：["数据分析", "Python", "可视化"]
""",
        default="[]",
    )
    capability_profile: str = Field(
        description="""能力画像（JSON格式或 preset 名称）。
示例：
{"preset":"builder"}
{"preset":"researcher"}
{"preset":"coordinator"}
{"preset":"maintainer"}
{"allow_local_tool_creation": true, "allow_agent_delegation": false}
""",
        default="specialist",
    )
    middleware_profile: str = Field(
        description="""中间件画像（JSON格式或 preset 名称）。
示例：
{"preset":"default"}
{"preset":"coordinator"}
{"sections":["prompt_context","delegation_context","tool_control"]}
""",
        default="default",
    )
    model: str = Field(description="使用的模型", default="gemini-3-flash-preview")
    temperature: float = Field(description="温度参数（0-1，越高越有创造性）", default=0.7)


class AgentCreatorTool(BaseTool):
    name: str = "create_agent"
    description: str = """
🤖 智能体制造器 - 创建专门化的子智能体

适用场景：
1. 需要特定领域专家（如数据分析师、代码审查员）
2. 需要分工协作完成复杂任务
3. 需要不同风格/角色的回答
4. 构建智能体团队
"""
    args_schema: type[BaseModel] = AgentCreatorInput
    agent_storage: Any = Field(default=None, exclude=True)
    tool_storage: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, agent_storage=None, tool_storage=None, **kwargs):
        super().__init__(agent_storage=agent_storage, tool_storage=tool_storage, **kwargs)

    def _run(
        self,
        agent_name: str,
        role: str,
        description: str,
        system_prompt: str,
        capabilities: str = "[]",
        capability_profile: str = "specialist",
        middleware_profile: str = "default",
        model: str = "gemini-3-flash-preview",
        temperature: float = 0.7,
    ) -> str:
        result = create_agent_record(
            agent_storage=self.agent_storage,
            agent_name=agent_name,
            role=role,
            description=description,
            system_prompt=system_prompt,
            capabilities=capabilities,
            capability_profile=capability_profile,
            middleware_profile=middleware_profile,
            model=model,
            temperature=temperature,
        )
        return json.dumps(result, ensure_ascii=False)


class DelegateToAgentInput(BaseModel):
    agent_name: str = Field(description="目标智能体名称")
    task: str = Field(description="要委派的任务描述")
    context: str = Field(description="任务上下文信息（可选）", default="")
    cwd: str | None = Field(description="执行工作目录（可选），相对路径将相对于主工作区", default=None)
    worktree_dir: str | None = Field(description="工作树目录（可选），用于多版本/分支协作隔离", default=None)
    remote_target: str | None = Field(description="远程执行目标（可选），用于跨机器/容器调度", default=None)
    capability_override: str | None = Field(
        description="能力画像覆盖（可选，JSON格式）。例如：{\"allow_code_execution\": false}",
        default=None,
    )
    policy_override: str | None = Field(
        description="控制策略覆盖（可选，JSON格式）。用于父智能体向子智能体做细粒度的权限控制（如 tool_budgets, blocked_tools）。",
        default=None,
    )
    stream: bool = Field(description="是否以流式返回结果", default=False)


class DelegateToAgentTool(BaseTool):
    name: str = "delegate_to_agent"
    description: str = """
📤 任务委派器 - 将任务委派给子智能体

适合复杂、独立、上下文密集的任务拆分。子智能体会在隔离线程中执行，
并只把最终结果和受控状态更新返回给主智能体。

何时选用：
- 一次性的直线任务，优先停留在 Single-Agent + trunk 工具面，不需要委派。
- 需要 app runtime / workflow runtime 的能力时，先尝试 run_workflow / 调用 App API；
  仅当任务本身需要"另一个独立智能体的判断或长上下文"再走 delegate_to_agent。

你可以指定 execution options (cwd, worktree_dir, remote_target) 和
capability_override / policy_override 来动态控制子智能体的隔离环境与细粒度权限。

例如，使用 policy_override: {"blocked_tools": ["delete_file"], "tool_budgets": {"web_search": 5}}
来严格限制子智能体的行为边界。
"""
    args_schema: type[BaseModel] = DelegateToAgentInput
    agent_storage: Any = Field(default=None, exclude=True)
    tool_storage: Any = Field(default=None, exclude=True)
    llm_factory: Any = Field(default=None, exclude=True)
    control_policy: Any = Field(default=None, exclude=True)
    global_tool_storage: Any = Field(default=None, exclude=True)
    approval_queue: Any = Field(default=None, exclude=True)
    project_paths: Any = Field(default=None, exclude=True)
    subagent_registry: Any = Field(default=None, exclude=True)
    runtime_context: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        agent_storage=None,
        tool_storage=None,
        llm_factory=None,
        control_policy=None,
        global_tool_storage=None,
        approval_queue=None,
        project_paths=None,
        subagent_registry=None,
        runtime_context=None,
        **kwargs,
    ):
        super().__init__(
            agent_storage=agent_storage,
            tool_storage=tool_storage,
            llm_factory=llm_factory,
            control_policy=control_policy,
            global_tool_storage=global_tool_storage,
            approval_queue=approval_queue,
            project_paths=project_paths,
            subagent_registry=subagent_registry,
            runtime_context=runtime_context or {"current_agent_name": "root", "depth": 0},
            **kwargs,
        )

    def _run(
        self,
        agent_name: str,
        task: str,
        context: str = "",
        cwd: str | None = None,
        worktree_dir: str | None = None,
        remote_target: str | None = None,
        capability_override: str | None = None,
        policy_override: str | None = None,
        stream: bool = False,
    ) -> str | Any:
        event_bus.emit(
            Event(
                type=EventType.AGENT_START,
                payload={"agent_name": agent_name, "task": task, "mode": "delegate"},
                source="delegate_to_agent",
            )
        )
        try:
            runtime_context = self.runtime_context or {"current_agent_name": "root", "depth": 0}
            result = delegate_agent_task(
                agent_storage=self.agent_storage,
                llm_factory=self.llm_factory,
                agent_name=agent_name,
                task=task,
                context=context,
                control_policy=self.control_policy,
                global_tool_storage=self.global_tool_storage,
                approval_queue=self.approval_queue,
                project_paths=self.project_paths,
                subagent_registry=self.subagent_registry,
                parent_agent_name=str(runtime_context.get("current_agent_name", "root") or "root"),
                parent_run_id=runtime_context.get("run_id"),
                parent_thread_id=runtime_context.get("thread_id"),
                parent_depth=int(runtime_context.get("depth", 0) or 0),
                stream=stream,
                cwd=cwd,
                worktree_dir=worktree_dir,
                remote_target=remote_target,
                capability_override=capability_override,
                policy_override=policy_override,
            )
            if stream:
                return result
        except Exception as exc:
            result = {
                "success": False,
                "agent_name": agent_name,
                "error": str(exc),
            }
        event_bus.emit(
            Event(
                type=EventType.AGENT_END,
                payload={"agent_name": agent_name, "success": result.get("success", False), "mode": "delegate"},
                source="delegate_to_agent",
            )
        )
        return json.dumps(result, ensure_ascii=False, indent=2)


class SpawnSubagentInput(BaseModel):
    agent_name: str = Field(description="目标智能体名称")
    task: str = Field(description="要委派的任务描述")
    context: str = Field(description="任务上下文信息（可选）", default="")
    cwd: str | None = Field(description="执行工作目录", default=None)
    capability_override: str | None = Field(description="能力画像覆盖 (JSON)", default=None)
    policy_override: str | None = Field(description="控制策略覆盖 (JSON)", default=None)


class SpawnSubagentTool(BaseTool):
    name: str = "spawn_subagent"
    description: str = """
🚀 异步任务启动器 - 在后台启动一个子智能体任务并立即返回 run_id。
适用于启动多个并行任务（Swarm 模式）。启动后，你可以继续执行其它操作，
稍后使用 `wait_subagent` 工具来同步结果。
你可以通过 policy_override (JSON) 给子智能体传递更细粒度的控制策略。
"""
    args_schema: type[BaseModel] = SpawnSubagentInput
    agent_storage: Any = Field(default=None, exclude=True)
    tool_storage: Any = Field(default=None, exclude=True)
    llm_factory: Any = Field(default=None, exclude=True)
    control_policy: Any = Field(default=None, exclude=True)
    global_tool_storage: Any = Field(default=None, exclude=True)
    approval_queue: Any = Field(default=None, exclude=True)
    project_paths: Any = Field(default=None, exclude=True)
    subagent_registry: Any = Field(default=None, exclude=True)
    runtime_context: Any = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, agent_name: str, task: str, context: str = "", cwd: str | None = None, capability_override: str | None = None, policy_override: str | None = None) -> str:
        target_thread_id = f"swarm_{agent_name}_{uuid.uuid4().hex[:8]}"
        
        def bg_run():
            try:
                delegate_agent_task(
                    agent_storage=self.agent_storage,
                    agent_name=agent_name,
                    task=task,
                    context=context,
                    llm_factory=self.llm_factory,
                    control_policy=self.control_policy,
                    global_tool_storage=self.global_tool_storage,
                    approval_queue=self.approval_queue,
                    project_paths=self.project_paths,
                    subagent_registry=self.subagent_registry,
                    cwd=cwd,
                    capability_override=capability_override,
                    policy_override=policy_override,
                    parent_agent_name=self.runtime_context.get("current_agent_name"),
                    parent_run_id=self.runtime_context.get("run_id"),
                    parent_thread_id=self.runtime_context.get("thread_id"),
                    parent_depth=self.runtime_context.get("depth", 0),
                    thread_id=target_thread_id,
                )
            except Exception:
                pass 

        thread = threading.Thread(target=bg_run, daemon=True)
        thread.start()
        
        for _ in range(10):
            record = self.subagent_registry.get_latest(agent_name=agent_name, thread_id=target_thread_id)
            if record:
                return json.dumps({"success": True, "run_id": record.run_id, "message": f"智能体 {agent_name} 已在后台启动。"}, ensure_ascii=False)
            time.sleep(0.1)
            
        return json.dumps({"success": False, "error": "启动超时或失败，请检查智能体状态。"})


class WaitSubagentInput(BaseModel):
    run_id: str = Field(description="要等待的任务运行 ID")
    timeout: float = Field(description="最大等待秒数", default=60.0)


class WaitSubagentTool(BaseTool):
    name: str = "wait_subagent"
    description: str = """
⏳ 任务同步器 - 等待一个异步启动的子智能体任务完成并返回结果。
如果任务已完成，立即返回结果；否则将阻塞当前执行直到超时或任务结束。
"""
    args_schema: type[BaseModel] = WaitSubagentInput
    subagent_registry: Any = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, run_id: str, timeout: float = 60.0) -> str:
        finished = self.subagent_registry.wait(run_id, timeout=timeout)
        record = self.subagent_registry.get(run_id)
        if not record:
            return f"❌ 错误：找不到任务 ID 为 {run_id} 的运行记录。"
            
        if not finished:
            return f"⏳ 等待超时（已等待 {timeout}s）。任务 {run_id} (智能体: {record.agent_name}) 仍在后台运行中。你可以稍后再次调用 `wait_subagent`。"
            
        if record.status == "completed":
            return json.dumps({
                "status": record.status,
                "agent_name": record.agent_name,
                "response": record.last_response,
                "notes": record.context_notes
            }, ensure_ascii=False, indent=2)
            
        # Handle failures with rich context
        error_msg = [f"⚠️ 任务执行失败 (状态: {record.status})"]
        error_msg.append(f"智能体: {record.agent_name}")
        error_msg.append(f"错误信息: {record.error or '未知错误'}")
        
        if record.error_context:
            error_msg.append("\n错误上下文 (Traceback):")
            error_msg.append("```python")
            error_msg.append(record.error_context)
            error_msg.append("```")
            
        if record.context_notes:
            error_msg.append("\n执行期间笔记:")
            for note in record.context_notes:
                error_msg.append(f"- {note}")
                
        return "\n".join(error_msg)


class AskAgentInput(BaseModel):
    """Input schema for asking a question to an existing agent."""

    agent_name: str = Field(description="目标智能体名称")
    question: str = Field(description="要问的问题")
    context: str = Field(description="相关背景信息（可选）", default="")


class AskAgentTool(BaseTool):
    name: str = "ask_agent"
    description: str = """
❓ 向智能体提问 - 向已有智能体问一个问题并获取回答

与 delegate_to_agent 不同，这是轻量级的问答，不创建完整任务。
适合快速咨询某个领域专家的意见。
"""
    args_schema: type[BaseModel] = AskAgentInput
    agent_storage: Any = Field(default=None, exclude=True)
    tool_storage: Any = Field(default=None, exclude=True)
    llm_factory: Any = Field(default=None, exclude=True)
    project_paths: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        agent_storage=None,
        tool_storage=None,
        llm_factory=None,
        project_paths=None,
        **kwargs,
    ):
        super().__init__(
            agent_storage=agent_storage,
            tool_storage=tool_storage,
            llm_factory=llm_factory,
            project_paths=project_paths,
            **kwargs,
        )

    def _run(self, agent_name: str, question: str, context: str = "") -> str:
        if self.agent_storage is None:
            return json.dumps({"success": False, "error": "agent_storage not configured"})

        agent_def = self.agent_storage.get_agent(agent_name)
        if agent_def is None:
            available = list(self.agent_storage.agents.keys()) if self.agent_storage else []
            return json.dumps(
                {
                    "success": False,
                    "error": f"智能体 '{agent_name}' 不存在",
                    "available_agents": available,
                },
                ensure_ascii=False,
            )

        event_bus.emit(
            Event(
                type=EventType.AGENT_START,
                payload={"agent_name": agent_name, "question": question, "mode": "ask"},
                source="ask_agent",
            )
        )

        try:
            if self.llm_factory is None:
                return json.dumps({"success": False, "error": "llm_factory not configured"})

            llm = self.llm_factory(
                model=agent_def.model,
                temperature=agent_def.temperature,
            )

            prompt_parts = [agent_def.system_prompt]
            if context:
                prompt_parts.append(f"\n背景信息:\n{context}")
            prompt_parts.append(f"\n问题: {question}")
            full_prompt = "\n".join(prompt_parts)

            response = llm.invoke(full_prompt)
            answer = response.content if hasattr(response, "content") else str(response)

            event_bus.emit(
                Event(
                    type=EventType.AGENT_END,
                    payload={"agent_name": agent_name, "success": True, "mode": "ask"},
                    source="ask_agent",
                )
            )

            return json.dumps(
                {
                    "success": True,
                    "agent_name": agent_name,
                    "answer": answer,
                },
                ensure_ascii=False,
                indent=2,
            )

        except Exception as exc:
            event_bus.emit(
                Event(
                    type=EventType.AGENT_END,
                    payload={"agent_name": agent_name, "success": False, "mode": "ask", "error": str(exc)},
                    source="ask_agent",
                )
            )
            return json.dumps(
                {
                    "success": False,
                    "agent_name": agent_name,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )


class ListAgentsInput(BaseModel):
    capability_filter: str = Field(description="按能力筛选（可选）", default="")


class ListAgentsTool(BaseTool):
    name: str = "list_agents"
    description: str = "📋 列出所有已创建的子智能体及其信息"
    args_schema: type[BaseModel] = ListAgentsInput
    agent_storage: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, agent_storage=None, **kwargs):
        super().__init__(agent_storage=agent_storage, **kwargs)

    def _run(self, capability_filter: str = "") -> str:
        if capability_filter:
            agent_list = [agent.to_dict() for agent in self.agent_storage.get_agents_by_capability(capability_filter)]
        else:
            agent_list = [agent.to_dict() for agent in self.agent_storage.agents.values()]
        return json.dumps(
            {
                "success": True,
                "count": len(agent_list),
                "agents": agent_list,
            },
            ensure_ascii=False,
            indent=2,
        )


class RemoveAgentInput(BaseModel):
    agent_name: str = Field(description="要删除的智能体名称")


class RemoveAgentTool(BaseTool):
    name: str = "remove_agent"
    description: str = "🗑️ 删除一个已创建的子智能体"
    args_schema: type[BaseModel] = RemoveAgentInput
    agent_storage: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, agent_storage=None, **kwargs):
        super().__init__(agent_storage=agent_storage, **kwargs)

    def _run(self, agent_name: str) -> str:
        if self.agent_storage.remove_agent(agent_name):
            return json.dumps(
                {
                    "success": True,
                    "message": f"✅ 智能体 '{agent_name}' 已删除",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "success": False,
                "error": f"智能体 '{agent_name}' 不存在",
            },
            ensure_ascii=False,
        )


def get_agent_creator_tools(
    agent_storage: AgentStorage,
    tool_storage: ToolStorage | None = None,
    llm_factory=None,
    control_policy: AgentControlPolicy | None = None,
    approval_queue: ApprovalQueue | None = None,
    project_paths: ProjectPaths | None = None,
    subagent_registry=None,
    runtime_context: dict[str, Any] | None = None,
    *,
    include_ask: bool = True,
) -> list[BaseTool]:
    """Return the tool bundle for creating, listing, and delegating subagents."""
    tools: list[BaseTool] = [
        AgentCreatorTool(agent_storage=agent_storage, tool_storage=tool_storage),
        DelegateToAgentTool(
            agent_storage=agent_storage,
            tool_storage=tool_storage,
            llm_factory=llm_factory,
            control_policy=control_policy,
            global_tool_storage=tool_storage,
            approval_queue=approval_queue,
            project_paths=project_paths,
            subagent_registry=subagent_registry,
            runtime_context=runtime_context or {"current_agent_name": "root", "depth": 0},
        ),
        SpawnSubagentTool(
            agent_storage=agent_storage,
            tool_storage=tool_storage,
            llm_factory=llm_factory,
            control_policy=control_policy,
            global_tool_storage=tool_storage,
            approval_queue=approval_queue,
            project_paths=project_paths,
            subagent_registry=subagent_registry,
            runtime_context=runtime_context or {"current_agent_name": "root", "depth": 0},
        ),
        WaitSubagentTool(subagent_registry=subagent_registry),
        ListAgentsTool(agent_storage=agent_storage),
        RemoveAgentTool(agent_storage=agent_storage),
    ]
    if include_ask:
        tools.append(
            AskAgentTool(
                agent_storage=agent_storage,
                tool_storage=tool_storage,
                llm_factory=llm_factory,
                project_paths=project_paths,
            )
        )
    return tools


__all__ = [
    "AgentCreatorInput",
    "AgentCreatorTool",
    "AskAgentInput",
    "AskAgentTool",
    "create_agent_record",
    "create_sub_agent_instance",
    "delegate_agent_task",
    "DelegateToAgentInput",
    "DelegateToAgentTool",
    "SpawnSubagentInput",
    "SpawnSubagentTool",
    "WaitSubagentInput",
    "WaitSubagentTool",
    "get_agent_creator_tools",
    "ListAgentsInput",
    "ListAgentsTool",
    "RemoveAgentInput",
    "RemoveAgentTool",
]
