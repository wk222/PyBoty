"""Tool asset modules."""


def __getattr__(name: str):
    if name == "ToolStorage":
        from core.assets.tools.tool_storage import ToolStorage
        return ToolStorage
    if name == "DynamicToolMiddleware":
        from core.assets.tools.tool_middleware import DynamicToolMiddleware
        return DynamicToolMiddleware
    if name == "ToolChainExecutor":
        from core.assets.tools.tool_chain import ToolChainExecutor
        return ToolChainExecutor
    if name == "execute_tool_script":
        from core.assets.tools.tool_runtime import execute_tool_script
        return execute_tool_script
    if name == "get_tool_chain_tools":
        from core.assets.tools.tool_chain import get_tool_chain_tools
        return get_tool_chain_tools
    if name == "get_template_prompt_section":
        from core.assets.tools.tool_templates import get_template_prompt_section
        return get_template_prompt_section
    if name in ("TemplateToolCreator", "ToolCreationError", "ToolCreatorTool",
                "build_tool_definition", "create_dynamic_tool",
                "get_dynamic_tools", "get_tool_creator_tools",
                "persist_validated_tool_definition"):
        from core.assets.tools import tool_creator
        return getattr(tool_creator, name)
    if name == "TodoWriteTool":
        from core.assets.tools.todo_write import TodoWriteTool
        return TodoWriteTool
    if name == "UnifiedToolInfo":
        from core.assets.tools.unified_tool_info import UnifiedToolInfo
        return UnifiedToolInfo
    if name == "UnifiedAssetInventory":
        from core.assets.tools.unified_tool_inventory import UnifiedAssetInventory
        return UnifiedAssetInventory
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
