---
name: create_app_sop
description: 创建和开发子应用的指南与最佳实践
---

# 创建和开发子应用指南

当你需要创建一个新的子应用时，请遵循以下指南：

## 1. 选择正确的应用模式 (Mode)
- `chat`: 完整的 AI 聊天界面，支持流式响应。适用于：聊天机器人、客服、家教。
- `rag`: 知识库问答，带语义搜索和 AI 回答。适用于：文档、FAQ、研究工具。
- `workflow`: 运行特定的工作流并提供表单输入。适用于：自动化、数据处理、批处理。
- `assistant`: 拥有工具访问权限的完整智能体。适用于：AI 助手、任务自动化。
- `static`: 纯 HTML/CSS/JS。适用于：仪表盘、简单工具、落地页。

**优先选择** 智能体驱动的模式 (chat/rag/workflow/assistant)，除非用户明确只需要一个静态页面。

## 2. 前端可用的内置 JS 助手函数
在开发智能体驱动的应用时，前端可以直接使用以下全局函数：
- `agentChat(message, onChunk)`: 与 AI 智能体进行流式对话
- `agentRunWorkflow(name, vars)`: 触发一个工作流
- `agentKnowledgeQuery(query, collection, topK)`: 搜索知识库
- `agentSearch(query)`: 全局搜索
- `agentCallTool(toolName, args)`: 调用已注册的工具。直接返回工具结果（如果工具返回列表，这里返回的就是 Array，请使用 `Array.isArray()` 检查）。
- `dbQuery(sql)`, `dbWrite(sql, params)`: 数据库访问

## 3. 开发流程
1. 使用 `create_app` 工具创建应用基础结构。
2. 使用 `update_app_file` 工具进一步自定义 HTML/CSS/JS。
3. **重要**：始终编写测试接口（例如在 `api.py` 中写 `test` action，或在 `app.js` 中写 `window.runSelfTest()`），并使用 `test_app_api` 来验证你的后端逻辑是否正常工作！
