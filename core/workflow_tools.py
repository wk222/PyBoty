"""Tool façade for the workflow engine."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field


class RunWorkflowInput(BaseModel):
    definition: str = Field(
        description="""工作流规范。

默认推荐使用 YAML，也兼容 JSON。

**节点属性直接写在节点上**（不用嵌套 config），start/end 自动补全。

**节点类型**: exec(command) | tool(tool,args) | llm(prompt) | agent(agent_name,task)
  debate(topic,agent_a,agent_b,rounds) | consensus(question,agents) | supervisor(task,workers)
  code(code,language) | approve(prompt) | condition(expression,true_branch,false_branch)
  router(routes,default) | parallel(branches) | foreach(items,body) | subflow(workflow,input)
  transform(operation,data) | merge(strategy) | delay(seconds)

**变量引用**: ${node_id.output}
**边语法**: source -> target 或 source -> target | condition
**不写 edges 则自动按顺序连线**
"""
    )
    input_vars: str = Field(description="输入变量（JSON 格式），作为工作流的初始上下文", default="{}")
    save: bool = Field(description="是否保存工作流到 workflows 目录供复用", default=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ResumeWorkflowInput(BaseModel):
    workflow_id: str = Field(description="工作流 ID")
    resume_token: str = Field(description="恢复令牌")
    approved: bool = Field(description="是否批准继续执行", default=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ListWorkflowsInput(BaseModel):
    scope: str = Field(description="范围: active=运行中, saved=已保存, all=全部", default="all")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class GenerateWorkflowInput(BaseModel):
    description: str = Field(description="用自然语言描述你想要的工作流，AI 会自动生成 DAG 定义")
    complexity: str = Field(description="复杂度: simple/medium/complex", default="medium")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class TriggerWorkflowInput(BaseModel):
    name: str = Field(description="已保存的工作流名称")
    input_vars: str = Field(description="输入变量（JSON 格式）", default="{}")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RunWorkflowTool(BaseTool):
    name: str = "run_workflow"
    description: str = """执行 DAG 工作流。支持工作流规范（YAML/JSON），15 种节点类型，
条件分支、并行执行、循环遍历、子工作流、AI 处理、人工审批等。
变量通过 ${node_id.output} 在节点间传递。"""
    args_schema: type[BaseModel] = RunWorkflowInput
    engine: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, definition: str, input_vars: str = "{}", save: bool = True) -> str:
        try:
            workflow = self.engine.parse_workflow(definition)
            try:
                vars_dict = json.loads(input_vars) if input_vars else {}
                workflow.variables.update(vars_dict)
                workflow.variables["input"] = vars_dict
            except json.JSONDecodeError:
                pass

            if save:
                self.engine.save_workflow_file(workflow)

            result = self.engine.run_workflow(workflow)
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


class ResumeWorkflowTool(BaseTool):
    name: str = "resume_workflow"
    description: str = "恢复暂停的工作流（在 approve 节点等待用户审批后使用）"
    args_schema: type[BaseModel] = ResumeWorkflowInput
    engine: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, workflow_id: str, resume_token: str, approved: bool = True) -> str:
        result = self.engine.resume_workflow(workflow_id, resume_token, approved)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)


class ListWorkflowsTool(BaseTool):
    name: str = "list_workflows"
    description: str = "列出工作流：active=运行中的, saved=已保存到文件的, all=全部"
    args_schema: type[BaseModel] = ListWorkflowsInput
    engine: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, scope: str = "all") -> str:
        result = {}
        if scope in ("active", "all"):
            result["active"] = self.engine.list_active_workflows()
        if scope in ("saved", "all"):
            result["saved"] = self.engine.list_workflow_files()
        return json.dumps(result, ensure_ascii=False, indent=2)


class GenerateWorkflowTool(BaseTool):
    name: str = "generate_workflow"
    description: str = """根据自然语言描述自动生成 DAG 工作流规范。
AI 会分析需求，选择合适的节点类型，设计合理的流程图，并自动保存。
用户也可以在 Workflow 编辑器中修改和复用。"""
    args_schema: type[BaseModel] = GenerateWorkflowInput
    engine: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _GENERATE_PROMPT = """你是工作流设计专家。根据需求生成工作流规范。

**需求**: {description}
**复杂度**: {complexity}

**只返回纯 YAML**（不要 markdown 代码块），格式：

name: workflow_name
description: 描述
tags: [标签]

nodes:
  - id: step1
    type: llm
    label: 步骤描述
    prompt: "处理: ${{input.text}}"

  - id: step2
    type: tool
    label: 调用工具
    tool: tool_name
    args:
      param: ${{step1.output}}

edges:
  - step1 -> step2

**节点类型**:
- exec: Shell 命令 (command, timeout, cwd)
- tool: 调用工具 (tool, args)
- llm: AI 处理 (prompt)
- agent: 委派子智能体 (agent_name, task, context, retry_on_fail)
- debate: 智能体辩论 (topic, agent_a, agent_b, judge, rounds)
- consensus: 专家共识 (question, agents, aggregator)
- supervisor: 动态路由 (task, workers)
- code: 代码 (code, language: python/javascript)
- approve: 人工审批 (prompt)
- condition: 条件 (expression, true_branch, false_branch)
- router: 多路由 (routes: [{{condition, target}}], default)
- parallel: 并行 (branches: [{{id, ...}}])
- foreach: 遍历 (items, body)
- subflow: 子工作流 (workflow, input)
- transform: 数据转换 (operation, data, key)
- merge: 汇聚 (strategy: collect/flatten)
- delay: 延时 (seconds)

**属性直接写在节点上**（不用嵌套 config）
**变量引用**: ${{node_id.output}}
**边语法**: source -> target 或 source -> target | condition
**start/end 节点会自动补全，不用写**
**复杂度**: simple=2-4步, medium=4-8步, complex=8+步

只返回 YAML，不要任何其他文字。"""

    def _run(self, description: str, complexity: str = "medium") -> str:
        if not self.engine._agent_callback:
            return json.dumps({"success": False, "error": "未设置 Agent 回调"}, ensure_ascii=False)

        prompt = self._GENERATE_PROMPT.format(description=description, complexity=complexity)

        try:
            response = self.engine._agent_callback(prompt)
            yaml_str = response.strip()

            if yaml_str.startswith("```"):
                lines = yaml_str.split("\n")
                body = []
                inside = False
                for line in lines:
                    if line.strip().startswith("```") and not inside:
                        inside = True
                        continue
                    if line.strip() == "```" and inside:
                        break
                    if inside:
                        body.append(line)
                yaml_str = "\n".join(body)

            from core.workflow_spec import parse_workflow_spec

            data = parse_workflow_spec(yaml_str)
            workflow = self.engine._build_workflow(data)
            filepath = self.engine.save_workflow_file(workflow)

            return json.dumps(
                {
                    "success": True,
                    "workflow_name": workflow.name,
                    "workflow_id": workflow.id,
                    "saved_to": filepath,
                    "nodes_count": len(workflow.nodes),
                    "edges_count": len(workflow.edges),
                    "node_types": list(set(node.type.value for node in workflow.nodes.values())),
                    "spec_preview": workflow.to_workflow_spec()[:2000],
                    "message": f"工作流 '{workflow.name}' 已生成并保存为工作流规范。"
                    f"可在 Matrix UI → Workflows 中查看/编辑，"
                    f"或用 trigger_workflow 执行。",
                },
                ensure_ascii=False,
                indent=2,
            )

        except Exception as exc:
            return json.dumps(
                {
                    "success": False,
                    "error": f"生成工作流失败: {exc}",
                    "raw_response": response[:1000] if "response" in dir() else "",
                },
                ensure_ascii=False,
            )


class TriggerWorkflowTool(BaseTool):
    name: str = "trigger_workflow"
    description: str = "触发执行一个已保存的工作流（从 workspace/workflows/ 目录加载并运行）"
    args_schema: type[BaseModel] = TriggerWorkflowInput
    engine: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, name: str, input_vars: str = "{}") -> str:
        try:
            workflow = self.engine.load_workflow(name)
            if not workflow:
                available = self.engine.list_workflow_files()
                names = [workflow_item["name"] for workflow_item in available]
                return json.dumps(
                    {"success": False, "error": f"工作流 '{name}' 不存在", "available": names},
                    ensure_ascii=False,
                )

            try:
                vars_dict = json.loads(input_vars) if input_vars else {}
                workflow.variables.update(vars_dict)
                workflow.variables["input"] = vars_dict
            except json.JSONDecodeError:
                pass

            result = self.engine.run_workflow(workflow)
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def get_pyflow_tools(engine: Any) -> list[BaseTool]:
    return [
        RunWorkflowTool(engine=engine),
        ResumeWorkflowTool(engine=engine),
        ListWorkflowsTool(engine=engine),
        GenerateWorkflowTool(engine=engine),
        TriggerWorkflowTool(engine=engine),
    ]
