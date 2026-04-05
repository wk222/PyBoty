"""Atomic file system operations for the agent."""

import os
from typing import Any, Type
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

class WriteFileInput(BaseModel):
    path: str = Field(description="文件的绝对或相对路径")
    content: str = Field(description="要写入的文件内容")

class WriteFileTool(BaseTool):
    name: str = "write_file"
    description: str = "写入或覆盖文件内容。常用于创建新脚本、配置文件或前端页面。"
    args_schema: Type[BaseModel] = WriteFileInput
    risk_level: str = "medium"  # 标记为中等风险，可能需要审批

    def _run(self, path: str, content: str) -> str:
        try:
            # 安全检查：防止路径穿越 (简单版)
            if ".." in path:
                return "❌ 写入失败: 不允许使用 '..' 进行路径穿越"
                
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"✅ 成功写入文件: {path}"
        except Exception as e:
            return f"❌ 写入失败: {str(e)}"

class ReadFileInput(BaseModel):
    path: str = Field(description="要读取的文件路径")

class ReadFileTool(BaseTool):
    name: str = "read_file"
    description: str = "读取指定文件的内容。"
    args_schema: Type[BaseModel] = ReadFileInput
    risk_level: str = "low"

    def _run(self, path: str) -> str:
        try:
            if not os.path.exists(path):
                return f"❌ 读取失败: 文件不存在 ({path})"
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"❌ 读取失败: {str(e)}"

def get_file_system_tools() -> list[BaseTool]:
    """返回所有原子化文件系统工具"""
    return [
        WriteFileTool(),
        ReadFileTool(),
    ]
