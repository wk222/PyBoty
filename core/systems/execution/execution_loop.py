"""Execution feedback loop — thin tool wrappers for code execution, project scanning, and validation."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from core.assets.tools import TodoWriteTool

from .execution_runtime import ExecutionRuntime
from .execution_scanner import ProjectScanner
from .execution_validation import IterativeResourceValidator


class ExecCodeInput(BaseModel):
    code: str = Field(description="要执行的代码")
    language: str = Field(description="语言: python 或 javascript", default="python")
    timeout: int = Field(description="超时时间(秒)", default=15)
    cwd: str = Field(description="工作目录(相对于workspace)", default="")


class ExecCodeTool(BaseTool):
    name: str = "exec_code"
    description: str = (
        "Execute code in a sandboxed environment and return stdout/stderr. "
        "Supports Python and JavaScript. Use for testing, validation, and iterative debugging."
    )
    args_schema: type[BaseModel] = ExecCodeInput
    workspace_dir: str = Field(default="workspace")
    sandbox_backend: Any = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, code: str, language: str = "python", timeout: int = 15, cwd: str = "") -> str:
        if self.sandbox_backend is not None and language == "python":
            from core.systems.runtime.backend_protocol import SandboxBackendProtocol

            if isinstance(self.sandbox_backend, SandboxBackendProtocol):
                result = self.sandbox_backend.run_python(code, timeout=timeout)
                return json.dumps(
                    {"success": result.success, "stdout": result.stdout, "stderr": result.stderr},
                    ensure_ascii=False,
                )
        runtime = ExecutionRuntime(workspace_dir=self.workspace_dir)
        result = runtime.run_code(code=code, language=language, timeout=timeout, cwd=cwd)
        return runtime.dumps(result)


class ScanProjectInput(BaseModel):
    path: str = Field(description="要扫描的目录路径(相对于workspace)", default="")
    max_depth: int = Field(description="最大扫描深度", default=3)
    include_content: bool = Field(description="是否包含文件内容摘要", default=False)


class ScanProjectTool(BaseTool):
    name: str = "scan_project"
    description: str = """扫描项目结构，提供完整的上下文意识。

返回目录树、文件列表、大小、类型统计等信息。
帮助理解项目全貌，在修改代码前了解现有架构。

**用途**:
- 了解项目结构和文件组织
- 在修改代码前获取上下文
- 找到相关文件和依赖关系
- 生成项目概览报告
"""
    args_schema: type[BaseModel] = ScanProjectInput
    workspace_dir: str = Field(default="workspace")
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, path: str = "", max_depth: int = 3, include_content: bool = False) -> str:
        scanner = ProjectScanner(workspace_dir=self.workspace_dir)
        return json.dumps(
            scanner.scan(path=path, max_depth=max_depth, include_content=include_content),
            ensure_ascii=False,
            indent=2,
        )


class IterativeFixInput(BaseModel):
    resource_path: str = Field(description="Resource path relative to workspace (e.g. 'apps/my-app')")
    test_command: str = Field(description="Command to run within the resource directory", default="")
    max_iterations: int = Field(description="Maximum number of repair iterations", default=3)


class IterativeFixTool(BaseTool):
    name: str = "iterative_test"
    description: str = """Run an iterative test-and-repair loop for a generated resource.

**Workflow**: 
1. Run structural checks and/or custom test command.
2. Collect error messages and stack traces.
3. Return error analysis and fix suggestions.
4. Agent repairs code and calls this again.

This tool is the core of the "generate -> execute -> observe -> fix -> repeat" loop.
"""
    args_schema: type[BaseModel] = IterativeFixInput
    workspace_dir: str = Field(default="workspace")
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, resource_path: str, test_command: str = "", max_iterations: int = 3) -> str:
        from .execution_validation import IterativeResourceValidator
        
        validator = IterativeResourceValidator(workspace_dir=self.workspace_dir)
        return json.dumps(
            validator.validate(
                resource_path=resource_path,
                test_command=test_command,
                max_iterations=max_iterations,
            ),
            ensure_ascii=False,
            indent=2,
        )


def get_execution_loop_tools(workspace_dir: str = "workspace") -> list[BaseTool]:
    return [
        ExecCodeTool(workspace_dir=workspace_dir),
        ScanProjectTool(workspace_dir=workspace_dir),
        IterativeFixTool(workspace_dir=workspace_dir),
        TodoWriteTool(),
    ]
