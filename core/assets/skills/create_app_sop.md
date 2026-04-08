---
name: create_app_sop
description: 创建和开发子应用的指南与最佳实践
---

# 创建和开发子应用指南

当你需要创建一个新的子应用时，请遵循以下指南：

## 0. 先选对层级：不要把 create_app 直接退化成纯文件拼装

推荐优先级：
1. **一键成品优先**：优先使用 `build_app_iteratively`
   - 适合“我要一个能工作的 app”这类需求
   - 它内部自带 `create_app -> 生成文件 -> verify_app -> 自动修复` 闭环
2. **可控开发优先**：其次使用 `create_app -> update_app_file -> verify_app -> test_app_api`
   - 适合需要精细控制 UI / API / 验证过程的场景
3. **纯文件拼装只作为兜底**：`write_file/read_file` 直接手搓 app 只在底层兼容、迁移、非常规目录时使用

原因：
- `create_app` 会自动创建标准目录、`app.json`、模板文件、`static/pybot-helpers.js`
- `update_app_file` 会走 app manager、JS 修复与文件级校验
- `verify_app` / `test_app_api` 构成正式验证闭环

如果把 `create_app` 完全拆成“skill 指导 + 原子文件工具组合”，通常会：
- 增加模型可见的 tool call 次数
- 失去 `AppManager` 内置模板、helpers 注入、metadata 归一化
- 更容易漏掉 `verify_app / test_app_api` 这条修复闭环

因此更好的做法是：
- **skill 负责策略和步骤**
- **tool 负责原子执行与约束**
- **高层 app builder 负责一键闭环**

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
1. 先判断是否可以直接使用 `build_app_iteratively` 一步完成。
2. 如果需要手控开发，再使用 `create_app` 创建应用基础结构。
3. 使用 `update_app_file` 工具进一步自定义 HTML/CSS/JS/API。
4. **重要**：始终编写测试接口（例如在 `api.py` 中写 `test` action，或在 `app.js` 中写 `window.runSelfTest()`）。
5. 使用 `verify_app` 验证整体质量，并使用 `test_app_api` 验证后端逻辑是否正常工作。
6. 如验证失败，继续 `read_app_file / update_app_file / verify_app` 直到通过。
