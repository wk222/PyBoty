"""
需求澄清工具 — 让 Agent 能够主动追问，而不是盲目执行

灵感来源：OpenClaw 的交互式决策机制 + Cursor 的追问能力

核心理念：
当用户需求模糊或缺少关键信息时，Agent 应该先追问，再行动。
这避免了 LLM 生成低质量/不符合预期的输出。
"""

import json
from typing import Any, Union

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClarifyInput(BaseModel):
    questions: Union[str, list[Any]] = Field(
        description="""需要向用户确认的问题列表(JSON数组格式)。
每个问题应包含:
- question: 问题内容
- why: 为什么需要这个信息(简短说明)
- default: 如果用户不回答，默认采用什么方案(可选)

示例:
[
  {"question": "应用需要支持哪些数据字段？", "why": "决定数据库表结构", "default": "id, name, date, amount"},
  {"question": "界面风格偏好？现代极简还是丰富仪表板？", "why": "决定UI布局", "default": "现代极简"}
]
"""
    )
    context: str = Field(description="当前理解到的需求摘要，展示给用户确认", default="")

    @field_validator("questions", mode="before")
    @classmethod
    def coerce_questions(cls, v: Any) -> Union[str, list[Any]]:
        """LLM 可能传 str 也可能传原生 list，都接受并统一处理。"""
        if isinstance(v, list):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, dict):
            return json.dumps([v], ensure_ascii=False)
        return str(v)


class AskClarificationTool(BaseTool):
    name: str = "ask_clarification"
    description: str = """在执行复杂任务之前，向用户提出澄清问题。

**何时使用**：
- 用户需求模糊或缺少关键信息
- 创建应用/工具前需要确认具体需求
- 任务有多种可能的实现方式，需要用户选择
- 需要确认数据格式、UI风格、功能范围等

**何时不使用**：
- 需求已经很明确
- 是简单的工具调用或查询
- 用户明确说"随便你决定"

**示例场景**：
用户说"做一个管理系统" → 应该追问：管理什么？需要哪些功能？
用户说"计算半径5的圆面积" → 不需要追问，直接做
"""
    args_schema: type[BaseModel] = ClarifyInput
    return_direct: bool = True
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, questions: str | list[Any], context: str = "") -> str:
        try:
            if isinstance(questions, list):
                q_list = questions
            elif isinstance(questions, str):
                q_list = json.loads(questions)
            else:
                q_list = [{"question": str(questions), "why": "", "default": ""}]
            if not isinstance(q_list, list):
                q_list = [{"question": str(q_list), "why": "", "default": ""}]
        except (json.JSONDecodeError, ValueError):
            q_list = [{"question": str(questions), "why": "", "default": ""}]

        formatted_questions = []
        for i, q in enumerate(q_list, 1):
            if isinstance(q, dict):
                text = f"{i}. {q.get('question', '')}"
                if q.get("why"):
                    text += f"\n   (原因: {q['why']})"
                if q.get("default"):
                    text += f"\n   [默认: {q['default']}]"
                formatted_questions.append(text)
            else:
                formatted_questions.append(f"{i}. {q}")

        output_parts = []
        if context:
            output_parts.append(f"## 当前理解\n{context}")
        output_parts.append("## 需要确认的问题\n" + "\n\n".join(formatted_questions))
        output_parts.append('\n请回答以上问题，或说"用默认方案"让我自行决定。')

        return "\n\n".join(output_parts)


class AnalyzeRequirementInput(BaseModel):
    user_request: str = Field(description="用户的原始请求")
    task_type: str = Field(description="任务类型: app/tool/agent/general", default="general")


class AnalyzeRequirementTool(BaseTool):
    name: str = "analyze_requirement"
    description: str = """分析用户需求的完整性，判断是否需要追问。

返回需求分析报告，包含:
- 已知信息
- 缺失信息
- 建议的追问问题
- 是否可以直接执行

在创建应用、工具或智能体之前使用此工具进行需求分析。
"""
    args_schema: type[BaseModel] = AnalyzeRequirementInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, user_request: str, task_type: str = "general") -> str:
        checklist = {
            "app": {
                "required": ["应用名称", "核心功能", "数据模型"],
                "optional": ["UI风格", "配色方案", "响应式需求", "数据量预期"],
                "quality_factors": ["表单验证", "错误处理", "加载状态", "空状态提示", "分页/搜索"],
            },
            "tool": {
                "required": ["工具名称", "输入参数", "输出格式"],
                "optional": ["错误处理策略", "超时设置", "依赖库"],
                "quality_factors": ["输入验证", "边界条件", "错误信息清晰度"],
            },
            "agent": {
                "required": ["角色定义", "专业领域", "核心任务"],
                "optional": ["个性风格", "知识范围", "协作方式"],
                "quality_factors": ["系统提示完整性", "工具配备", "边界约束"],
            },
            "general": {
                "required": ["任务目标"],
                "optional": ["输出格式", "约束条件"],
                "quality_factors": ["完整性", "可验证性"],
            },
        }

        check = checklist.get(task_type, checklist["general"])
        request_len = len(user_request)

        completeness = "high" if request_len > 200 else ("medium" if request_len > 50 else "low")

        result = {
            "task_type": task_type,
            "request_length": request_len,
            "completeness_estimate": completeness,
            "checklist": check,
            "recommendation": "proceed" if completeness == "high" else "clarify",
            "suggestion": (
                "需求描述较为简短，建议使用 ask_clarification 工具向用户确认关键细节。"
                if completeness == "low"
                else "需求描述较为充分，可以开始执行，但建议确认几个关键点。"
                if completeness == "medium"
                else "需求描述详细，可以直接开始执行。"
            ),
        }

        return json.dumps(result, ensure_ascii=False, indent=2)


def get_clarification_tools() -> list[BaseTool]:
    return [
        AskClarificationTool(),
        AnalyzeRequirementTool(),
    ]
