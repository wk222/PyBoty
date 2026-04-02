"""
评估/测试框架 — 自动评估 Agent 输出质量

灵感来源：
- LangChain: TrajectoryEvalChain 评估 Agent 执行轨迹
- LangChain: CriteriaEvalChain 基于标准评分
- DeepAgents: Plan→Exec→Verify 循环

核心能力：
1. 输出质量评估 — 基于可定义标准评估 Agent 响应
2. 轨迹评估 — 评估工具调用序列的合理性
3. 自动化测试用例 — 定义输入/期望输出，批量运行
4. 回归测试 — 对比前后版本输出差异
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from .eval_models import DEFAULT_CRITERIA, EvalResult, TestCase
from .eval_runtime import EvalRuntime
from .eval_storage import EvalStorage


class EvalFramework:
    """Thin façade around evaluation runtime and persistence."""

    def __init__(self, workspace_dir: str = "workspace"):
        self.storage = EvalStorage(workspace_dir)
        self.runtime = EvalRuntime()
        self.workspace_dir = str(self.storage.workspace_dir)
        self.tests_dir = str(self.storage.tests_dir)
        self.results_dir = str(self.storage.results_dir)

    @property
    def results_history(self) -> list[EvalResult]:
        return self.runtime.results_history

    def set_agent_callback(self, callback: Callable[[str], str] | None) -> None:
        self.runtime.set_agent_callback(callback)

    def eval_response(
        self,
        prompt: str,
        response: str,
        criteria: list[str] | None = None,
    ) -> EvalResult:
        return self.runtime.eval_response(prompt, response, criteria or DEFAULT_CRITERIA)

    def run_test_suite(
        self,
        test_cases: list[TestCase],
        agent_callback: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        report = self.runtime.run_test_suite(test_cases, agent_callback=agent_callback)
        if report.get("success", True):
            self.storage.save_report(report)
        return report

    def load_test_suite(self, name: str) -> list[TestCase]:
        return self.storage.load_test_suite(name)

    def save_test_suite(self, name: str, test_cases: list[TestCase]) -> None:
        self.storage.save_test_suite(name, test_cases)


class EvalResponseInput(BaseModel):
    prompt: str = Field(description="原始用户提问")
    response: str = Field(description="要评估的 AI 回答")
    criteria: str = Field(description="评估标准(JSON 列表)，留空使用默认标准", default="")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RunTestsInput(BaseModel):
    suite_name: str = Field(description="测试套件名称（从 workspace/tests/ 加载）", default="")
    test_cases: str = Field(
        description="""测试用例（JSON 格式），直接定义时使用。

格式: [{"name": "测试名", "input_prompt": "输入", "expected_contains": ["关键词"], "min_score": 0.6}]

示例:
[
  {"name": "数学计算", "input_prompt": "1+1等于几?", "expected_contains": ["2"], "min_score": 0.5},
  {"name": "代码生成", "input_prompt": "写一个Python hello world", "expected_contains": ["print"], "min_score": 0.6}
]""",
        default="",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class EvalResponseTool(BaseTool):
    name: str = "eval_response"
    description: str = """评估 AI 回答的质量。支持 LLM 评估（更准确）和启发式评估（更快）。
返回总分、各标准得分和改进建议。"""
    args_schema: type[BaseModel] = EvalResponseInput
    framework: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, prompt: str, response: str, criteria: str = "") -> str:
        resolved_criteria = None
        if criteria:
            try:
                resolved_criteria = json.loads(criteria)
            except json.JSONDecodeError:
                resolved_criteria = [item.strip() for item in criteria.split(",") if item.strip()]

        result = self.framework.eval_response(prompt, response, resolved_criteria)
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


class RunTestsTool(BaseTool):
    name: str = "run_tests"
    description: str = """运行测试套件评估 Agent 质量。可以从文件加载测试用例或直接定义。
返回通过率、各测试详细结果和改进建议。"""
    args_schema: type[BaseModel] = RunTestsInput
    framework: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, suite_name: str = "", test_cases: str = "") -> str:
        cases: list[TestCase] = []

        if suite_name:
            cases = self.framework.load_test_suite(suite_name)
            if not cases:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"测试套件 '{suite_name}' 不存在或为空",
                    },
                    ensure_ascii=False,
                )

        if test_cases:
            try:
                raw_cases = json.loads(test_cases)
                for raw_case in raw_cases:
                    cases.append(
                        TestCase(
                            name=raw_case.get("name", "unnamed"),
                            input_prompt=raw_case.get("input_prompt", ""),
                            expected_contains=raw_case.get("expected_contains", []),
                            expected_not_contains=raw_case.get("expected_not_contains", []),
                            criteria=raw_case.get("criteria", []),
                            min_score=raw_case.get("min_score", 0.6),
                            tags=raw_case.get("tags", []),
                        )
                    )
            except json.JSONDecodeError:
                return json.dumps({"success": False, "error": "测试用例 JSON 解析失败"}, ensure_ascii=False)

        if not cases:
            return json.dumps({"success": False, "error": "没有测试用例"}, ensure_ascii=False)

        result = self.framework.run_test_suite(cases)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def get_eval_tools(framework: EvalFramework) -> list[BaseTool]:
    return [
        EvalResponseTool(framework=framework),
        RunTestsTool(framework=framework),
    ]
