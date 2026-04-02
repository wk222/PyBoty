"""Runtime helpers for building and executing dynamic tools."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field, create_model

from core.systems.runtime.project_paths import ProjectPaths

_TYPE_MAP: dict[str, type[Any]] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
}
_DEFAULT_TIMEOUT_SECONDS = 30


def build_input_model(name: str, parameters: list[dict[str, Any]]) -> type[BaseModel]:
    """Build a Pydantic args schema from persisted parameter metadata."""
    field_definitions: dict[str, tuple[type[Any], Field]] = {}

    for parameter in parameters:
        field_name = str(parameter["name"])
        field_type = _TYPE_MAP.get(str(parameter.get("type", "str")), str)
        field_description = str(parameter.get("description", ""))

        if "default" in parameter and parameter["default"] is not None:
            field_definitions[field_name] = (
                field_type,
                Field(default=parameter["default"], description=field_description),
            )
        else:
            field_definitions[field_name] = (field_type, Field(description=field_description))

    if field_definitions:
        return create_model(f"{name}Input", **field_definitions)

    class EmptyInputModel(BaseModel):
        """Fallback args schema for tools without parameters."""

    return EmptyInputModel


def render_tool_script(code: str, dependencies: list[str]) -> str:
    """Render the isolated script executed for a dynamic tool invocation."""
    indented_code = "\n".join(f"    {line}" for line in code.splitlines()) or "    pass"
    return f"""# /// script
# requires-python = ">=3.10"
# dependencies = {json.dumps(dependencies)}
# ///
import json
import sys
import traceback

try:
    with open(sys.argv[1], "r", encoding="utf-8") as file:
        input_params = json.load(file)

    globals().update(input_params)
    result = None

{indented_code}

    with open(sys.argv[2], "w", encoding="utf-8") as file:
        json.dump({{"result": result}}, file, ensure_ascii=False, default=str)
except Exception:
    sys.stderr.write(traceback.format_exc())
    sys.exit(1)
"""


def execute_tool_script(
    *,
    tool_name: str,
    code: str,
    dependencies: list[str],
    kwargs: dict[str, Any],
    project_paths: ProjectPaths | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute a persisted tool definition inside an isolated subprocess."""
    paths = project_paths or ProjectPaths.from_root()
    run_dir = paths.tools_workspace_dir / tool_name / uuid.uuid4().hex[:12]
    run_dir.mkdir(parents=True, exist_ok=True)

    script_path = run_dir / "script.py"
    input_path = run_dir / "input.json"
    output_path = run_dir / "output.json"

    input_path.write_text(json.dumps(kwargs, ensure_ascii=False), encoding="utf-8")
    script_path.write_text(render_tool_script(code, dependencies), encoding="utf-8")

    if shutil.which("uv"):
        command = ["uv", "run", str(script_path), str(input_path), str(output_path)]
    else:
        command = [sys.executable, str(script_path), str(input_path), str(output_path)]

    start_time = time.time()
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=False,
            cwd=run_dir,
            timeout=timeout_seconds,
            check=False,
        )
        stdout_str = process.stdout.decode("utf-8", errors="replace") if process.stdout else ""
        stderr_str = process.stderr.decode("utf-8", errors="replace") if process.stderr else ""
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "tool": tool_name,
            "error": f"脚本执行超时（{timeout_seconds}s）",
            "suggestion": "请优化代码逻辑，减少阻塞操作，或将任务拆成更小的步骤。",
        }
    except Exception as exc:
        return {
            "success": False,
            "tool": tool_name,
            "error": f"执行异常: {exc}",
            "suggestion": "请检查代码逻辑。",
        }

    execution_time = time.time() - start_time
    if process.returncode != 0:
        return {
            "success": False,
            "tool": tool_name,
            "error": "脚本执行失败",
            "traceback": stderr_str,
            "stdout": stdout_str,
            "suggestion": "请分析上述 traceback，使用 create_custom_tool 传入修改后的代码和依赖来覆盖并修复此工具。",
        }

    if not output_path.exists():
        return {
            "success": False,
            "tool": tool_name,
            "error": "脚本未返回结果",
            "traceback": "代码执行完毕，但没有生成 output.json 文件。请确保你的代码最终给 result 变量赋了值。",
            "stdout": stdout_str,
            "suggestion": "请检查代码逻辑，确保最终将结果赋值给了 result 变量。",
        }

    try:
        output_payload = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "tool": tool_name,
            "error": f"工具输出解析失败: {exc}",
            "stdout": stdout_str,
        }

    return {
        "success": True,
        "tool": tool_name,
        "result": output_payload.get("result"),
        "output": stdout_str,
        "execution_time": round(execution_time, 3),
    }


def build_dynamic_tool(
    tool_definition: dict[str, Any],
    *,
    project_paths: ProjectPaths | None = None,
) -> BaseTool:
    """Build a concrete LangChain tool instance from persisted metadata."""
    tool_name = str(tool_definition["name"])
    tool_description = str(tool_definition["description"])
    parameters = list(tool_definition.get("parameters", []))
    code = str(tool_definition["code"])
    dependencies = list(tool_definition.get("dependencies", []))

    compile(code, f"<tool:{tool_name}>", "exec")
    input_model = build_input_model(tool_name, parameters)

    class DynamicTool(BaseTool):
        """Concrete BaseTool wrapper around a persisted tool definition."""

        name: str = tool_name
        description: str = tool_description
        args_schema: type[BaseModel] = input_model

        def _run(self, **kwargs: Any) -> str:
            result = execute_tool_script(
                tool_name=tool_name,
                code=code,
                dependencies=dependencies,
                kwargs=kwargs,
                project_paths=project_paths,
            )
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    return DynamicTool()
