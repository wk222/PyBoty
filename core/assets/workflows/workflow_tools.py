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
变量通过 ${node_id.output} 在节点间传递。

当任务是“可复用的多步流程”“需要分支/审批/调度”时优先用它。
如果只是一次性的直线任务，优先停留在 Single-Agent + trunk 工具面。
其中 `agent / debate / consensus / supervisor` 节点属于 workflow collaboration，
它们依赖已注册智能体和 multi-agent 分支。"""
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
用户也可以在 Workflow 编辑器中修改和复用。

适合把“人脑里的步骤”沉淀成长期可运行流程。
如果需求本质上是产品界面，走 app branch；如果只是一次性专家协作，先评估 single-agent 或 delegate 是否更轻。"""
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

---

## 节点类型 & 示例

### 基础节点

**exec** — 运行 Shell 命令
  属性: command(必填), timeout(秒,默认30), cwd(默认项目根)
  示例:
  - id: install
    type: exec
    label: 安装依赖
    command: "pip install requests"

**tool** — 调用已注册工具
  属性: tool(工具名,必填), args(参数字典)
  示例:
  - id: fetch
    type: tool
    label: 获取数据
    tool: fetch_bilibili_ranking
    args: {{}}

**llm** — AI 文本处理
  属性: prompt(必填)
  示例:
  - id: analyze
    type: llm
    label: AI分析
    prompt: "分析以下数据并给出趋势: ${{fetch.output}}"

**code** — 运行代码片段
  属性: code(必填), language(python/javascript,默认python), timeout(默认15s)
  示例:
  - id: process
    type: code
    label: 数据清洗
    language: python
    code: |
      import json
      data = json.loads('${{fetch.output}}')
      print(json.dumps([d['title'] for d in data]))

### 流程控制节点

**condition** — 条件分支
  属性: expression(Python表达式,必填), true_branch(节点id), false_branch(节点id)
  表达式可用: workflow.variables 中的变量, len(), int(), str(), bool()
  示例:
  - id: check
    type: condition
    expression: "len('${{fetch.output}}') > 10"
    true_branch: analyze
    false_branch: fallback

**router** — 多路由选择（多个条件依次匹配）
  属性: routes(条件-目标列表,必填), default(默认目标)
  示例:
  - id: route
    type: router
    routes:
      - condition: "'error' in '${{fetch.output}}'"
        target: handle_error
      - condition: "len('${{fetch.output}}') > 100"
        target: deep_analyze
    default: simple_analyze

**parallel** — 并行执行多分支
  属性: branches(分支列表,必填), ignore_errors(bool,默认false)
  每个 branch 可包含 command/tool/prompt/code 属性
  示例:
  - id: multi_source
    type: parallel
    branches:
      - id: bili
        tool: fetch_bilibili_ranking
        args: {{}}
      - id: weibo
        tool: fetch_weibo_hot
        args: {{}}

**foreach** — 遍历列表执行
  属性: items(列表或变量引用,必填), body(循环体,必填), max_items(默认100), ignore_errors(bool)
  body 中用 ${{_foreach_item}} 引用当前项, ${{_foreach_index}} 引用索引
  示例:
  - id: process_each
    type: foreach
    items: "${{fetch.output}}"
    body:
      prompt: "总结这条内容: ${{_foreach_item}}"

**subflow** — 调用子工作流
  属性: workflow(已保存的工作流名,必填), input(传入变量字典)
  示例:
  - id: sub
    type: subflow
    workflow: data_cleaning_flow
    input:
      raw_data: "${{fetch.output}}"

**delay** — 延时等待
  属性: seconds(秒数,必填)

### 数据处理节点

**transform** — 数据变换
  操作类型: json_parse / json_stringify / extract(需要key) / merge(需要sources) / filter(需要condition) / map(需要expression) / passthrough
  示例:
  - id: extract_titles
    type: transform
    operation: extract
    data: "${{fetch.output}}"
    key: title

**merge** — 合并多个前驱节点的输出
  属性: strategy(collect=保持字典 / flatten=合并为一个字典)
  示例:
  - id: combine
    type: merge
    strategy: flatten

### 协作智能体节点（需要已注册的智能体）

**agent** — 委派给子智能体执行
  属性: agent_name(已注册智能体名,必填), task(任务描述,必填), context(上下文), retry_on_fail(bool), timeout(秒,默认300)
  注意: agent_name 必须是已通过系统注册的智能体
  示例:
  - id: expert
    type: agent
    agent_name: bilibili_analyst
    task: "分析B站热点趋势: ${{fetch.output}}"
    retry_on_fail: true

**debate** — 两个智能体辩论 + 裁判总结
  属性: topic(辩题,必填), agent_a(正方智能体名,必填), agent_b(反方智能体名,必填), judge(裁判智能体名), rounds(轮数,默认2)
  流程: agent_a 发言 → agent_b 反驳 → (重复 N 轮) → judge 总结裁决
  输出: {{topic, transcript, conclusion, response}}
  示例:
  - id: debate_ai
    type: debate
    topic: "AI是否应该替代程序员？"
    agent_a: pro_ai_agent
    agent_b: con_ai_agent
    judge: judge_agent
    rounds: 2

**consensus** — 多专家独立回答 + 汇总共识
  属性: question(问题,必填), agents(专家智能体名列表,必填,>=2), aggregator(汇总智能体名,可选)
  流程: 所有 agents 独立回答 → aggregator(或主Agent) 汇总提取共识
  输出: {{question, expert_responses, consensus, response}}
  示例:
  - id: expert_panel
    type: consensus
    question: "2024年最值得学习的编程语言是什么？"
    agents: [frontend_expert, backend_expert, ai_expert]
    aggregator: senior_architect

**supervisor** — 主管智能路由
  属性: task(任务描述,必填), workers(可选专家列表,必填)
  流程: 主Agent分析任务 → 自动路由给最合适的 worker
  输出: {{task, chosen_worker, response, delegation}}
  示例:
  - id: smart_route
    type: supervisor
    task: "用户问：如何优化数据库查询性能？"
    workers: [db_expert, cache_expert, architecture_expert]

### 审批节点

**approve** — 人工审批（暂停工作流等待人工确认）
  属性: prompt(审批提示语)
  示例:
  - id: review
    type: approve
    prompt: "以上分析结果是否符合预期？确认后将自动发布。"

---

**通用规则**:
- 属性直接写在节点上（不用嵌套 config）
- 变量引用: ${{node_id.output}}
- 边语法: source -> target 或 source -> target | condition
- start/end 节点自动补全，不用写
- 复杂度: simple=2-4步, medium=4-8步, complex=8+步

**路径规范**:
- exec/code 节点的 cwd 默认是项目根目录
- 文件输出路径必须使用 workspace/ 前缀（如 workspace/reports/xxx.md）
- 禁止写入 workspace/ 外的路径
- 使用 os.makedirs('workspace/xxx', exist_ok=True) 确保目录存在

**YAML 格式注意**:
- 多行字符串用引号包裹，内嵌换行用 \\n，例如: prompt: "第一行\\n第二行"
- 不要使用 YAML 多行块语法 (|, >, |-, >-)，因为解析兼容性差
- code 节点中的多行代码也用引号 + \\n: code: "import os\\nprint('hello')"
- 长字符串可以分成多行引号拼接: prompt: "line1\\nline2\\nline3"

只返回 YAML，不要任何其他文字。"""

    _REQUIRED_FIELDS: dict[str, list[str]] = {
        "exec": ["command"],
        "tool": ["tool"],
        "llm": ["prompt"],
        "code": ["code"],
        "agent": ["agent_name", "task"],
        "debate": ["topic", "agent_a", "agent_b"],
        "consensus": ["question", "agents"],
        "supervisor": ["task", "workers"],
        "condition": ["expression"],
        "foreach": ["items", "body"],
        "subflow": ["workflow"],
        "approve": ["prompt"],
    }

    _MAX_RETRIES = 2

    _FIX_PROMPT = """你上次生成的工作流 YAML 有以下问题：

{issues}

原始 YAML（前2000字符）：
{yaml_preview}

请修复以上问题，重新生成完整的工作流 YAML。

注意：
- 多行字符串必须用引号包裹 + \\n，不要用 YAML | 或 > 多行块语法
- 所有必填字段必须有实质性内容，不能为空
- topic/question/prompt/code/command 等字段至少要有完整的描述

只返回修复后的纯 YAML，不要其他文字。"""

    def _run(self, description: str, complexity: str = "medium") -> str:
        agent_cb = getattr(self.engine.node_runtime, "agent_callback", None)
        if not agent_cb:
            return json.dumps({"success": False, "error": "未设置 Agent 回调"}, ensure_ascii=False)

        prompt = self._GENERATE_PROMPT.format(description=description, complexity=complexity)
        last_yaml = ""
        last_issues: list[str] = []

        for attempt in range(1 + self._MAX_RETRIES):
            try:
                if attempt == 0:
                    response = agent_cb(prompt)
                else:
                    fix_prompt = self._FIX_PROMPT.format(
                        issues="\n".join(f"- {issue}" for issue in last_issues),
                        yaml_preview=last_yaml[:2000],
                    )
                    response = agent_cb(fix_prompt)

                yaml_str = self._strip_code_fences(response.strip())
                workflow = self.engine.parse_workflow(yaml_str)
                issues = self._validate_workflow_nodes(workflow)

                if issues:
                    last_yaml = yaml_str
                    last_issues = issues
                    if attempt < self._MAX_RETRIES:
                        continue
                    return json.dumps(
                        {
                            "success": False,
                            "error": f"工作流验证失败（已重试{self._MAX_RETRIES}次）",
                            "issues": issues,
                            "raw_yaml_preview": yaml_str[:2000],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )

                filepath = self.engine.save_workflow_file(workflow)
                result: dict[str, Any] = {
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
                }
                if attempt > 0:
                    result["retries"] = attempt
                return json.dumps(result, ensure_ascii=False, indent=2)

            except Exception as exc:
                if attempt < self._MAX_RETRIES:
                    last_yaml = yaml_str if "yaml_str" in dir() else ""
                    last_issues = [f"解析异常: {exc}"]
                    continue
                return json.dumps(
                    {
                        "success": False,
                        "error": f"生成工作流失败（已重试{self._MAX_RETRIES}次）: {exc}",
                        "raw_response": response[:1000] if "response" in dir() else "",
                    },
                    ensure_ascii=False,
                )
        return json.dumps({"success": False, "error": "意外退出重试循环"}, ensure_ascii=False)

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        if not text.startswith("```"):
            return text
        lines = text.split("\n")
        body: list[str] = []
        inside = False
        for line in lines:
            if line.strip().startswith("```") and not inside:
                inside = True
                continue
            if line.strip() == "```" and inside:
                break
            if inside:
                body.append(line)
        return "\n".join(body) if body else text

    def _validate_workflow_nodes(self, workflow: Any) -> list[str]:
        issues: list[str] = []
        for node_id, node in workflow.nodes.items():
            if node.type.value in ("start", "end"):
                continue
            required = self._REQUIRED_FIELDS.get(node.type.value, [])
            for field in required:
                value = node.config.get(field)
                if value is None or value == "" or value == "|":
                    issues.append(
                        f"节点 '{node_id}' (type={node.type.value}): "
                        f"必填字段 '{field}' 为空或无效值 '{value}'"
                    )
                elif isinstance(value, str) and len(value.strip()) < 3 and field in ("prompt", "topic", "question", "task", "code", "command"):
                    issues.append(
                        f"节点 '{node_id}' (type={node.type.value}): "
                        f"字段 '{field}' 内容过短 ('{value}')，可能是多行语法解析失败"
                    )
        return issues


class TriggerWorkflowTool(BaseTool):
    name: str = "trigger_workflow"
    description: str = (
        "触发执行一个已保存的工作流（从 workspace/workflows/ 目录加载并运行）。"
        " 适合已经沉淀成流程资产的任务，不适合临时性单次直线工作。"
    )
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
