"""Runtime logic for evaluating responses and running test suites."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from .eval_models import DEFAULT_CRITERIA, EvalResult, TestCase


class EvalRuntime:
    """Evaluate single responses and execute evaluation suites."""

    def __init__(self) -> None:
        self._agent_callback: Callable[[str], str] | None = None
        self._results_history: list[EvalResult] = []

    @property
    def results_history(self) -> list[EvalResult]:
        return list(self._results_history)

    def set_agent_callback(self, callback: Callable[[str], str] | None) -> None:
        self._agent_callback = callback

    def eval_response(
        self,
        prompt: str,
        response: str,
        criteria: Sequence[str] | None = None,
    ) -> EvalResult:
        resolved_criteria = list(criteria or DEFAULT_CRITERIA)
        if self._agent_callback:
            return self._llm_eval(prompt, response, resolved_criteria)
        return self._heuristic_eval(prompt, response)

    def run_test_suite(
        self,
        test_cases: Sequence[TestCase],
        *,
        agent_callback: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        callback = agent_callback or self._agent_callback
        if not callback:
            return {"success": False, "error": "未设置 Agent 回调"}

        results: list[dict[str, Any]] = []
        passed = 0
        failed = 0
        start_time = time.time()

        for test_case in test_cases:
            case_start = time.time()
            try:
                response = callback(test_case.input_prompt)
                duration = (time.time() - case_start) * 1000

                contains_ok = all(keyword in response for keyword in test_case.expected_contains)
                not_contains_ok = all(keyword not in response for keyword in test_case.expected_not_contains)

                eval_result = self.eval_response(
                    test_case.input_prompt,
                    response,
                    test_case.criteria or None,
                )
                eval_result.test_name = test_case.name
                eval_result.duration_ms = duration
                eval_result.passed = eval_result.score >= test_case.min_score and contains_ok and not_contains_ok

                if not contains_ok:
                    missing = [keyword for keyword in test_case.expected_contains if keyword not in response]
                    eval_result.feedback += f"\n缺少关键词: {missing}"
                if not not_contains_ok:
                    unwanted = [keyword for keyword in test_case.expected_not_contains if keyword in response]
                    eval_result.feedback += f"\n包含不期望的内容: {unwanted}"

                if eval_result.passed:
                    passed += 1
                else:
                    failed += 1

                results.append(eval_result.to_dict())
                self._results_history.append(eval_result)
            except Exception as exc:
                failed += 1
                results.append(
                    {
                        "test_name": test_case.name,
                        "passed": False,
                        "score": 0,
                        "error": str(exc),
                        "duration_ms": (time.time() - case_start) * 1000,
                    }
                )

        total_ms = (time.time() - start_time) * 1000
        total_cases = len(test_cases)
        return {
            "total": total_cases,
            "passed": passed,
            "failed": failed,
            "pass_rate": f"{passed / total_cases * 100:.1f}%" if total_cases else "N/A",
            "total_ms": round(total_ms, 1),
            "results": results,
        }

    def _llm_eval(self, prompt: str, response: str, criteria: Sequence[str]) -> EvalResult:
        criteria_text = "\n".join(f"{index + 1}. {criterion}" for index, criterion in enumerate(criteria))
        eval_prompt = f"""你是一个严格的AI输出质量评估专家。请评估以下回答的质量。

**用户提问**: {prompt[:1000]}

**AI回答**: {response[:2000]}

**评估标准**:
{criteria_text}

请为每个标准打分(0-10)，然后给出总体评分和反馈。

**必须返回纯 JSON 格式**:
{{
  "criteria_scores": {{
    "标准1名称": 8,
    "标准2名称": 7
  }},
  "overall_score": 7.5,
  "feedback": "总体评价...",
  "passed": true
}}"""
        try:
            assert self._agent_callback is not None
            result = self._agent_callback(eval_prompt)
            json_str = result.strip()
            start = json_str.find("{")
            end = json_str.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = json_str[start:end]
            data = json.loads(json_str)
            scores = data.get("criteria_scores", {})
            overall = float(data.get("overall_score", 5))
            feedback = str(data.get("feedback", ""))
            passed = bool(data.get("passed", overall >= 6))
            return EvalResult(
                test_name="llm_eval",
                passed=passed,
                score=overall / 10.0,
                criteria_scores={str(key): float(value) / 10.0 for key, value in scores.items()},
                feedback=feedback,
                timestamp=time.time(),
            )
        except Exception:
            return self._heuristic_eval(prompt, response)

    def _heuristic_eval(self, prompt: str, response: str) -> EvalResult:
        scores: dict[str, float] = {}
        total = 0.0

        length_score = min(len(response) / 200, 1.0)
        scores["长度充分"] = length_score
        total += length_score

        if any("\u4e00" <= char <= "\u9fff" for char in prompt):
            lang_match = any("\u4e00" <= char <= "\u9fff" for char in response)
        else:
            lang_match = True
        scores["语言匹配"] = 1.0 if lang_match else 0.3
        total += scores["语言匹配"]

        has_structure = bool(re.search(r"[\n\-\*\d+\.]", response))
        scores["结构化"] = 0.8 if has_structure else 0.4
        total += scores["结构化"]

        has_code = "```" in response or "def " in response or "import " in response
        code_needed = any(keyword in prompt.lower() for keyword in ["代码", "code", "script", "函数", "实现"])
        scores["代码提供"] = 0.9 if code_needed and has_code else 0.2 if code_needed else 0.7
        total += scores["代码提供"]

        error_phrases = ["我不知道", "我无法", "抱歉", "sorry", "i can't", "i don't know"]
        has_refusal = any(phrase in response.lower() for phrase in error_phrases)
        scores["有效回答"] = 0.3 if has_refusal else 0.9
        total += scores["有效回答"]

        average = total / len(scores)
        return EvalResult(
            test_name="heuristic_eval",
            passed=average >= 0.6,
            score=round(average, 3),
            criteria_scores={key: round(value, 3) for key, value in scores.items()},
            feedback=f"启发式评估 (无LLM): 总分 {average:.1%}",
            timestamp=time.time(),
        )
