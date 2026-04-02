"""LangChain tools for creating and loading dynamic PyBot tools."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from core.assets.agents.agent_storage import AgentStorage
from core.systems.runtime.project_paths import ProjectPaths

from .tool_creation_support import (
    ToolCreationError,
    build_tool_definition,
    compile_tool_code,
    normalize_dependencies,
    parse_parameter_definitions,
    persist_validated_tool_definition,
    resolve_target_storage,
    validate_tool_name,
)
from .tool_runtime import build_dynamic_tool
from .tool_storage import ToolStorage


def _json_error(error: str, *, suggestion: str | None = None, **extra: Any) -> str:
    payload: dict[str, Any] = {"success": False, "error": error}
    if suggestion:
        payload["suggestion"] = suggestion
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


class ToolCreatorInput(BaseModel):
    """Input schema for custom-tool creation."""

    tool_name: str = Field(description="工具名称（英文+下划线，如 calculate_score）")
    description: str = Field(description="工具功能描述，清晰说明工具的作用")
    parameters: str = Field(
        description="""参数定义（JSON格式），例如：
[
  {"name": "radius", "type": "float", "description": "圆的半径", "default": null},
  {"name": "unit", "type": "str", "description": "单位", "default": "cm"}
]
支持的类型：str, int, float, bool, list, dict
"""
    )
    dependencies: list[str] = Field(
        description="需要的第三方Python包列表（如 ['requests', 'beautifulsoup4']）。不需要写内置模块。",
        default_factory=list,
    )
    code: str = Field(
        description="""Python执行代码，可使用以下变量和模块：
- 所有输入参数（直接使用参数名）
- result 变量（必须设置，作为返回值）
- print() 函数用于输出日志

注意：代码将在独立的 uv 虚拟环境中运行，请在代码开头 import 你在 dependencies 中声明的库。

示例：
import requests
result = radius ** 2 * 3.14159
print(f"计算结果: {result}")
"""
    )
    usage_guide: str = Field(description="使用指南，说明何时使用此工具", default="")
    target_agent: str | None = Field(
        description="目标智能体名称（可选）。如果指定，工具将创建在该智能体的专属工具库中（即该智能体的文件夹内）。",
        default=None,
    )


class TemplateToolInput(BaseModel):
    """Input schema for template-based tool creation."""

    template_name: str = Field(description="模板名称，如 http_get, web_scraper, web_search, calculator 等")
    custom_name: str = Field(description="自定义工具名称(英文+下划线)", default="")
    target_agent: str | None = Field(description="目标智能体名称（可选）", default=None)


class TemplateToolCreator(BaseTool):
    """Create a tool from the verified template catalogue."""

    name: str = "create_tool_from_template"
    description: str = """
📋 模板工具安装器 — 从预制模板一键创建经过验证的工具

可用模板:
- **http_get** — HTTP GET请求
- **http_post** — HTTP POST请求
- **web_scraper** — 网页内容提取(去HTML标签)
- **web_search** — DuckDuckGo搜索引擎
- **read_file** — 读取文件
- **write_file** — 写入文件
- **list_directory** — 列出目录
- **run_python** — 执行Python代码
- **json_processor** — JSON解析/过滤
- **csv_reader** — CSV读取
- **text_summarizer** — 文本统计分析
- **calculator** — 数学计算器
- **datetime_tool** — 日期时间工具
- **url_info** — URL元信息获取

使用方式: 直接指定 template_name 即可创建
"""
    args_schema: type[BaseModel] = TemplateToolInput
    storage: ToolStorage | None = Field(default=None, exclude=True)
    agent_storage: AgentStorage | None = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        storage: ToolStorage | None = None,
        agent_storage: AgentStorage | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(storage=storage, agent_storage=agent_storage, **kwargs)

    def _run(self, template_name: str, custom_name: str = "", target_agent: str | None = None) -> str:
        from .tool_templates import get_template, list_templates

        template = get_template(template_name)
        if template is None:
            names = [item["name"] for item in list_templates()]
            return _json_error(f"模板 '{template_name}' 不存在", available_templates=names)

        tool_name = custom_name or str(template["name"])
        try:
            validate_tool_name(tool_name)
            target = resolve_target_storage(
                self.storage,
                agent_storage=self.agent_storage,
                target_agent=target_agent,
            )
            tool_definition = build_tool_definition(
                tool_name=tool_name,
                description=str(template["description"]),
                parameters=list(template["parameters"]),
                code=str(template["code"]),
                dependencies=normalize_dependencies(template.get("dependencies", [])),
                usage_guide=str(template["description"]),
                from_template=template_name,
            )
            persist_validated_tool_definition(
                target.storage,
                tool_definition,
                validator=build_dynamic_tool,
            )
        except ToolCreationError as exc:
            return _json_error(exc.message, suggestion=exc.suggestion)

        return json.dumps(
            {
                "success": True,
                "tool_name": tool_name,
                "message": f"✅ 模板工具 '{tool_name}' 已从模板 '{template_name}' 创建到 {target.location}！",
                "location": target.location,
                "template": template_name,
                "parameters": [parameter["name"] for parameter in template["parameters"]],
            },
            ensure_ascii=False,
        )


class ListTemplatesInput(BaseModel):
    """Input schema for listing templates."""

    category: str = Field(description="筛选分类(可选)", default="")


class ListTemplatesTool(BaseTool):
    """List all tool templates grouped by category."""

    name: str = "list_tool_templates"
    description: str = "📋 列出所有可用的预制工具模板及分类"
    args_schema: type[BaseModel] = ListTemplatesInput
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _run(self, category: str = "") -> str:
        from .tool_templates import get_templates_by_category

        categories = get_templates_by_category()
        if category and category in categories:
            categories = {category: categories[category]}
        return json.dumps(categories, ensure_ascii=False, indent=2)


class RemoveToolInput(BaseModel):
    """Input schema for deleting custom tools."""

    tool_name: str = Field(description="要删除的废弃或失败工具的名称")


class RemoveToolTool(BaseTool):
    """Delete a custom tool from storage."""

    name: str = "remove_custom_tool"
    description: str = """
🗑️ 工具清理器 - 删除无法修复或不再需要的工具

**核心能力**：
- 当你发现某个工具多次尝试修复仍然失败时，使用此工具将其删除。
- 避免工具库中堆积过多无用工具。

**示例**：
"删除 yt_trend_fetcher_v3 工具"
"""
    args_schema: type[BaseModel] = RemoveToolInput
    storage: ToolStorage | None = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, storage: ToolStorage | None = None, **kwargs: Any) -> None:
        super().__init__(storage=storage, **kwargs)

    def _run(self, tool_name: str) -> str:
        if self.storage is None:
            return _json_error("Storage not configured")

        if self.storage.remove_tool(tool_name):
            return json.dumps(
                {
                    "success": True,
                    "message": f"✅ 工具 '{tool_name}' 已成功删除。你可以尝试使用新的名称重新创建该工具。",
                },
                ensure_ascii=False,
            )
        return _json_error(f"工具 '{tool_name}' 不存在")


class ToolCreatorTool(BaseTool):
    """Create a custom persisted tool definition."""

    name: str = "create_custom_tool"
    description: str = """
🛠️ 工具制造器 - 创建自定义工具供后续使用

**核心能力**：
- ✅ 动态创建新工具
- ✅ 工具持久化保存
- ✅ 可以创建到全局工具库
- ✅ 也可以创建到指定智能体的专属工具库

**适用场景**：
1. 发现某个操作需要重复执行
2. 需要特定领域的计算或处理
3. 想要封装复杂逻辑为简单接口
4. 为特定智能体创建专属能力

**示例**：
"创建一个计算圆面积的工具，输入半径，返回面积"
"为 data_analyst 智能体创建一个数据清洗工具"
"""
    args_schema: type[BaseModel] = ToolCreatorInput
    storage: ToolStorage | None = Field(default=None, exclude=True)
    agent_storage: AgentStorage | None = Field(default=None, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        storage: ToolStorage | None = None,
        agent_storage: AgentStorage | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(storage=storage, agent_storage=agent_storage, **kwargs)

    def _run(
        self,
        tool_name: str,
        description: str,
        parameters: str,
        code: str,
        dependencies: list[str] | str | None = None,
        usage_guide: str = "",
        target_agent: str | None = None,
    ) -> str:
        try:
            validate_tool_name(tool_name)
            parsed_parameters = parse_parameter_definitions(parameters)
            parsed_dependencies = normalize_dependencies(dependencies)
            target = resolve_target_storage(
                self.storage,
                agent_storage=self.agent_storage,
                target_agent=target_agent,
            )
            compile_tool_code(tool_name, code)
            tool_definition = build_tool_definition(
                tool_name=tool_name,
                description=description,
                parameters=parsed_parameters,
                code=code,
                dependencies=parsed_dependencies,
                usage_guide=usage_guide,
            )
            persist_validated_tool_definition(
                target.storage,
                tool_definition,
                validator=build_dynamic_tool,
            )
        except ToolCreationError as exc:
            return _json_error(exc.message, suggestion=exc.suggestion)

        return json.dumps(
            {
                "success": True,
                "tool_name": tool_name,
                "message": f"✅ 工具 '{tool_name}' 已成功创建到 {target.location}！",
                "location": target.location,
                "usage": f"现在可以在相关上下文中使用 {tool_name} 工具了",
                "details": {
                    "description": description,
                    "parameters": [parameter["name"] for parameter in parsed_parameters],
                    "usage_guide": usage_guide or description,
                },
            },
            ensure_ascii=False,
        )


def create_dynamic_tool(
    tool_definition: dict[str, Any],
    *,
    project_paths: ProjectPaths | None = None,
) -> BaseTool:
    """Backward-compatible wrapper around the dynamic tool runtime builder."""
    return build_dynamic_tool(tool_definition, project_paths=project_paths)


def get_tool_creator_tools(
    storage: ToolStorage,
    agent_storage: AgentStorage | None = None,
) -> list[BaseTool]:
    """Return the tool-management toolset exposed to agents."""
    return [
        ToolCreatorTool(storage=storage, agent_storage=agent_storage),
        TemplateToolCreator(storage=storage, agent_storage=agent_storage),
        ListTemplatesTool(),
        RemoveToolTool(storage=storage),
    ]


def get_dynamic_tools(
    storage: ToolStorage,
    *,
    project_paths: ProjectPaths | None = None,
) -> list[BaseTool]:
    """Instantiate every persisted tool definition from storage."""
    tools: list[BaseTool] = []
    for tool_name, tool_definition in storage.tools.items():
        try:
            tools.append(create_dynamic_tool(tool_definition, project_paths=project_paths))
        except Exception as exc:
            print(f"⚠️  创建工具 '{tool_name}' 失败: {exc}")
    return tools
