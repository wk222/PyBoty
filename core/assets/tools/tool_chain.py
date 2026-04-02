"""
工具链式调用优化器 — 自动将多个工具调用管道化

灵感来源：
- Unix Pipe: cmd1 | cmd2 | cmd3
- LangChain: RunnableSequence 链式组合
- DeepAgents: wrap_tool_call 中间件

核心能力：
1. 定义工具链（Pipeline）— 多个工具按顺序执行，前一个的输出作为后一个的输入
2. 自动输出映射 — 将上一步的 JSON 输出字段映射为下一步的参数
3. 条件链 — 根据中间结果决定是否继续
4. 工具调用统计 — 追踪调用次数、耗时、成功率
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field


@dataclass
class ChainStep:
    tool_name: str
    args_template: dict[str, Any] = field(default_factory=dict)
    output_mapping: dict[str, str] = field(default_factory=dict)
    condition: str | None = None
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "args_template": self.args_template,
            "output_mapping": self.output_mapping,
            "condition": self.condition,
            "label": self.label,
        }


@dataclass
class ToolCallRecord:
    tool_name: str
    args: dict[str, Any]
    result: Any
    duration_ms: float
    success: bool
    timestamp: float
    error: str | None = None


class ToolChainExecutor:
    def __init__(self):
        self._tool_callback: Callable | None = None
        self._call_history: list[ToolCallRecord] = []
        self._call_stats: dict[str, dict[str, Any]] = {}
        self.max_history = 200

    def set_tool_callback(self, callback: Callable):
        self._tool_callback = callback

    def execute_chain(self, steps: list[ChainStep], initial_input: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._tool_callback:
            return {"success": False, "error": "未设置工具回调"}

        context = initial_input or {}
        results = []
        chain_start = time.time()

        for i, step in enumerate(steps):
            if step.condition:
                try:
                    if not eval(step.condition, {"__builtins__": {}}, context):
                        results.append(
                            {
                                "step": i,
                                "tool": step.tool_name,
                                "status": "skipped",
                                "reason": f"条件不满足: {step.condition}",
                            }
                        )
                        continue
                except Exception:
                    pass

            args = self._resolve_args(step.args_template, context)

            start = time.time()
            try:
                result = self._tool_callback(step.tool_name, args)
                duration_ms = (time.time() - start) * 1000

                self._record_call(step.tool_name, args, result, duration_ms, True)

                parsed = self._try_parse(result)

                if step.output_mapping:
                    for target_key, source_path in step.output_mapping.items():
                        context[target_key] = self._extract_value(parsed, source_path)
                else:
                    context[f"step_{i}"] = parsed
                    context["_last_output"] = parsed

                results.append(
                    {
                        "step": i,
                        "tool": step.tool_name,
                        "label": step.label,
                        "status": "success",
                        "duration_ms": round(duration_ms, 1),
                        "output_preview": str(parsed)[:300],
                    }
                )

            except Exception as e:
                duration_ms = (time.time() - start) * 1000
                self._record_call(step.tool_name, args, None, duration_ms, False, str(e))
                results.append(
                    {
                        "step": i,
                        "tool": step.tool_name,
                        "status": "failed",
                        "error": str(e),
                        "duration_ms": round(duration_ms, 1),
                    }
                )
                return {
                    "success": False,
                    "failed_at_step": i,
                    "error": str(e),
                    "results": results,
                    "total_ms": round((time.time() - chain_start) * 1000, 1),
                }

        return {
            "success": True,
            "results": results,
            "final_context": {k: str(v)[:500] for k, v in context.items()},
            "total_ms": round((time.time() - chain_start) * 1000, 1),
        }

    def _resolve_args(self, template: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        resolved = {}
        for k, v in template.items():
            if isinstance(v, str) and v.startswith("$"):
                key = v[1:]
                resolved[k] = context.get(key, v)
            elif isinstance(v, str) and "${" in v:
                import re

                def replacer(match):
                    return str(context.get(match.group(1), match.group(0)))

                resolved[k] = re.sub(r"\$\{([^}]+)\}", replacer, v)
            else:
                resolved[k] = v
        return resolved

    def _try_parse(self, result: Any) -> Any:
        if isinstance(result, str):
            try:
                return json.loads(result)
            except (json.JSONDecodeError, TypeError):
                return result
        return result

    def _extract_value(self, data: Any, path: str) -> Any:
        if not path or path == ".":
            return data
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part, None)
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return current

    def _record_call(
        self, tool_name: str, args: dict, result: Any, duration_ms: float, success: bool, error: str | None = None
    ):
        record = ToolCallRecord(
            tool_name=tool_name,
            args=args,
            result=result,
            duration_ms=duration_ms,
            success=success,
            timestamp=time.time(),
            error=error,
        )
        self._call_history.append(record)
        if len(self._call_history) > self.max_history:
            self._call_history = self._call_history[-self.max_history :]

        if tool_name not in self._call_stats:
            self._call_stats[tool_name] = {
                "calls": 0,
                "successes": 0,
                "failures": 0,
                "total_ms": 0,
                "avg_ms": 0,
            }
        stats = self._call_stats[tool_name]
        stats["calls"] += 1
        if success:
            stats["successes"] += 1
        else:
            stats["failures"] += 1
        stats["total_ms"] += duration_ms
        stats["avg_ms"] = round(stats["total_ms"] / stats["calls"], 1)

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_calls": len(self._call_history),
            "tools": dict(self._call_stats),
        }

    def get_recent_calls(self, n: int = 10) -> list[dict]:
        return [
            {
                "tool": r.tool_name,
                "success": r.success,
                "duration_ms": round(r.duration_ms, 1),
                "error": r.error,
                "time": r.timestamp,
            }
            for r in self._call_history[-n:]
        ]


class RunChainInput(BaseModel):
    chain: str = Field(
        description="""工具链定义（JSON 格式）。

格式: {"steps": [{"tool": "工具名", "args": {"参数": "值"},
"output_map": {"变量名": "输出路径"}, "label": "描述"}],
"input": {"初始变量": "值"}}

变量引用: 在 args 中用 "$变量名" 引用上下文变量，用 "${变量名}" 在字符串中嵌入。

示例 — 搜索并分析:
{"steps": [
  {"tool": "web_search_duckduckgo", "args": {"query": "Python 最佳实践"},
   "output_map": {"search_result": "."}, "label": "搜索"},
  {"tool": "exec_code",
   "args": {"code": "print('分析完成')", "language": "python"},
   "label": "分析"}
]}

示例 — 数据库查询链:
{"steps": [
  {"tool": "db_list_tables", "args": {},
   "output_map": {"tables": "."}, "label": "列出表"},
  {"tool": "db_execute_sql",
   "args": {"sql": "SELECT count(*) FROM users"},
   "output_map": {"count": "."}, "label": "查询"}
]}"""
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ToolStatsInput(BaseModel):
    action: str = Field(description="操作: stats=查看统计, recent=最近调用记录", default="stats")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RunChainTool(BaseTool):
    name: str = "run_chain"
    description: str = """执行工具链（Pipeline）— 多个工具按顺序执行，自动传递数据。
类似 Unix 管道: tool1 | tool2 | tool3。
前一个工具的输出可以通过变量引用传给后一个工具。"""
    args_schema: type[BaseModel] = RunChainInput
    executor: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, chain: str) -> str:
        try:
            data = json.loads(chain)
        except json.JSONDecodeError:
            return json.dumps({"success": False, "error": "JSON 解析失败"}, ensure_ascii=False)

        steps_data = data.get("steps", [])
        initial_input = data.get("input", {})

        steps = []
        for sd in steps_data:
            steps.append(
                ChainStep(
                    tool_name=sd.get("tool", ""),
                    args_template=sd.get("args", {}),
                    output_mapping=sd.get("output_map", {}),
                    condition=sd.get("condition", None),
                    label=sd.get("label", ""),
                )
            )

        result = self.executor.execute_chain(steps, initial_input)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)


class ToolStatsTool(BaseTool):
    name: str = "tool_stats"
    description: str = "查看工具调用统计和最近调用记录，帮助优化工具使用"
    args_schema: type[BaseModel] = ToolStatsInput
    executor: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, action: str = "stats") -> str:
        if action == "recent":
            return json.dumps(self.executor.get_recent_calls(15), ensure_ascii=False, indent=2)
        return json.dumps(self.executor.get_stats(), ensure_ascii=False, indent=2)


def get_tool_chain_tools(executor: ToolChainExecutor) -> list[BaseTool]:
    return [
        RunChainTool(executor=executor),
        ToolStatsTool(executor=executor),
    ]
