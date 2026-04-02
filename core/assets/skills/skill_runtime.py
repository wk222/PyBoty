"""Runtime helpers for turning skills into executable tools."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from core.assets.skills.openclaw_compat import resolve_openclaw_runtime_env_for_skill
from core.systems.runtime.project_paths import ProjectPaths

TYPE_MAP = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
    "list": list,
    "array": list,
    "dict": dict,
    "object": dict,
}

TRUSTED_SKILLS_ENV = "SKILL_TRUSTED_MODULES"


def build_tool_from_definition(
    tool_def: dict[str, Any],
    skill_name: str,
    *,
    env_overrides: dict[str, str] | None = None,
) -> BaseTool | None:
    name = tool_def.get("name")
    description = tool_def.get("description", "")
    parameters = tool_def.get("parameters", [])
    code = tool_def.get("code", "")
    dependencies = tool_def.get("dependencies", [])

    if not name or not code:
        return None

    field_definitions: dict[str, tuple[type[Any], Field]] = {}
    for param in parameters:
        field_name = param["name"]
        field_type = TYPE_MAP.get(param.get("type", "str"), str)
        field_desc = param.get("description", "")
        field_default = param.get("default")
        if field_default is None:
            field_definitions[field_name] = (field_type, Field(description=field_desc))
        else:
            field_definitions[field_name] = (
                field_type,
                Field(default=field_default, description=field_desc),
            )

    if field_definitions:
        input_model = create_model(f"{name}Input", **field_definitions)
    else:

        class EmptyInput(BaseModel):
            pass

        input_model = EmptyInput

    tool_name = str(name)
    tool_description = str(description)
    tool_code = str(code)
    tool_dependencies = list(dependencies)
    owner_skill = skill_name
    skill_env = dict(env_overrides or {})

    class SkillTool(BaseTool):
        name: str = tool_name
        description: str = tool_description
        args_schema: type[BaseModel] = input_model
        model_config = ConfigDict(arbitrary_types_allowed=True)

        def _run(self, **kwargs) -> str:
            try:
                run_id = __import__("uuid").uuid4().hex[:12]
                project_paths = ProjectPaths.from_root()
                workspace_dir = Path(project_paths.tools_workspace_dir).resolve() / "skills" / owner_skill / run_id
                workspace_dir.mkdir(parents=True, exist_ok=True)

                script_path = workspace_dir / "script.py"
                input_path = workspace_dir / "input.json"
                output_path = workspace_dir / "output.json"
                input_path.write_text(json.dumps(kwargs, ensure_ascii=False), encoding="utf-8")

                workspace_root = str(project_paths.workspace_dir)
                script_content = f"""# /// script
# requires-python = ">=3.10"
# dependencies = {json.dumps(tool_dependencies)}
# ///
import json, sys, os, traceback

WORKSPACE_ROOT = {json.dumps(workspace_root)}
os.environ['WORKSPACE_ROOT'] = WORKSPACE_ROOT

try:
    with open(sys.argv[1], 'r', encoding='utf-8') as f:
        input_params = json.load(f)
    globals().update(input_params)
    result = None

    # ==================== skill tool code ====================
{chr(10).join("    " + line for line in tool_code.split(chr(10)))}
    # =========================================================

    with open(sys.argv[2], 'w', encoding='utf-8') as f:
        json.dump({{"result": result}}, f, ensure_ascii=False)
except Exception:
    sys.stderr.write(traceback.format_exc())
    sys.exit(1)
"""
                script_path.write_text(script_content, encoding="utf-8")

                start_time = time.time()
                cmd = ["uv", "run", str(script_path), str(input_path), str(output_path)]
                if shutil.which("uv") is None:
                    cmd = [sys.executable, str(script_path), str(input_path), str(output_path)]
                process_env = os.environ.copy()
                process_env.update(skill_env)
                process_env["WORKSPACE_ROOT"] = workspace_root
                process = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=False,
                    timeout=120,
                    env=process_env,
                )
                stdout_str = process.stdout.decode("utf-8", errors="replace") if process.stdout else ""
                stderr_str = process.stderr.decode("utf-8", errors="replace") if process.stderr else ""
                execution_time = time.time() - start_time

                if process.returncode != 0:
                    return json.dumps(
                        {
                            "success": False,
                            "tool": tool_name,
                            "skill": owner_skill,
                            "error": "执行失败",
                            "traceback": stderr_str[-1000:],
                            "stdout": stdout_str[-500:],
                        },
                        ensure_ascii=False,
                    )

                if not output_path.exists():
                    return json.dumps(
                        {
                            "success": False,
                            "tool": tool_name,
                            "skill": owner_skill,
                            "error": "脚本未返回结果（result 变量未赋值）",
                        },
                        ensure_ascii=False,
                    )

                result = json.loads(output_path.read_text(encoding="utf-8")).get("result")
                return json.dumps(
                    {
                        "success": True,
                        "tool": tool_name,
                        "skill": owner_skill,
                        "result": result,
                        "output": stdout_str[-500:],
                        "execution_time": round(execution_time, 3),
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            except Exception as exc:
                return json.dumps(
                    {
                        "success": False,
                        "tool": tool_name,
                        "skill": owner_skill,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )

    return SkillTool()


def load_python_module_tools(
    skill_dir: str | Path,
    skill_name: str,
    *,
    env_overrides: dict[str, str] | None = None,
) -> list[BaseTool]:
    tools_py = Path(skill_dir) / "tools.py"
    if not tools_py.exists():
        return []

    trusted = os.environ.get(TRUSTED_SKILLS_ENV, "")
    if trusted != "*":
        allowed = {item.strip() for item in trusted.split(",") if item.strip()}
        if skill_name not in allowed:
            print(
                f"[SkillRegistry] 跳过 {skill_name}/tools.py"
                f"（未在受信任白名单中，设置 {TRUSTED_SKILLS_ENV} 环境变量来启用）"
            )
            return []

    module_name = f"skill_tools_{skill_name}"
    try:
        with _patched_environ(env_overrides):
            spec = importlib.util.spec_from_file_location(module_name, str(tools_py))
            if spec is None or spec.loader is None:
                return []
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    except Exception as exc:
        print(f"[SkillRegistry] 加载 {tools_py} 失败: {exc}")
        return []

    tools: list[BaseTool] = []
    with _patched_environ(env_overrides):
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if isinstance(obj, type) and issubclass(obj, BaseTool) and obj is not BaseTool:
                try:
                    tools.append(_wrap_tool_env(obj(), env_overrides))
                except Exception as exc:
                    print(f"[SkillRegistry] 实例化 {attr_name} 失败: {exc}")
            elif isinstance(obj, BaseTool):
                tools.append(_wrap_tool_env(obj, env_overrides))

        if hasattr(module, "get_tools") and callable(module.get_tools):
            extra = module.get_tools()
            if isinstance(extra, list):
                tools.extend(_wrap_tool_env(tool, env_overrides) for tool in extra if isinstance(tool, BaseTool))

    return tools


def resolve_skill_runtime_env(skill) -> dict[str, str]:
    """Resolve OpenClaw-provided env vars for a skill, if configured."""
    if getattr(skill, "skill_format", "") != "openclaw":
        return {}
    from core.systems.runtime import get_openclaw_compat_config

    compat = get_openclaw_compat_config()
    config_path = compat.get("config_path") if isinstance(compat, dict) else None
    return resolve_openclaw_runtime_env_for_skill(skill, config_path=config_path)


@contextmanager
def _patched_environ(overrides: dict[str, str] | None):
    payload = {key: value for key, value in (overrides or {}).items() if isinstance(key, str)}
    if not payload:
        yield
        return

    sentinel = object()
    previous: dict[str, object] = {key: os.environ.get(key, sentinel) for key in payload}
    try:
        for key, value in payload.items():
            os.environ[key] = str(value)
        yield
    finally:
        for key, value in previous.items():
            if value is sentinel:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)


def _wrap_tool_env(tool: BaseTool, env_overrides: dict[str, str] | None) -> BaseTool:
    if not env_overrides:
        return tool

    original_invoke = getattr(tool, "invoke", None)
    if callable(original_invoke):

        def wrapped_invoke(*args, **kwargs):
            with _patched_environ(env_overrides):
                return original_invoke(*args, **kwargs)

        object.__setattr__(tool, "invoke", wrapped_invoke)

    original_ainvoke = getattr(tool, "ainvoke", None)
    if callable(original_ainvoke):

        async def wrapped_ainvoke(*args, **kwargs):
            with _patched_environ(env_overrides):
                return await original_ainvoke(*args, **kwargs)

        object.__setattr__(tool, "ainvoke", wrapped_ainvoke)

    original_run = getattr(tool, "run", None)
    if callable(original_run):

        def wrapped_run(*args, **kwargs):
            with _patched_environ(env_overrides):
                return original_run(*args, **kwargs)

        object.__setattr__(tool, "run", wrapped_run)

    original_arun = getattr(tool, "arun", None)
    if callable(original_arun):

        async def wrapped_arun(*args, **kwargs):
            with _patched_environ(env_overrides):
                return await original_arun(*args, **kwargs)

        object.__setattr__(tool, "arun", wrapped_arun)

    return tool
