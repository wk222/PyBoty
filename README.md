# PyBot — 自进化多智能体平台

<p align="center">
  <img src="https://img.shields.io/badge/LangChain-1.0.0--alpha-blue?style=for-the-badge" alt="LangChain">
  <img src="https://img.shields.io/badge/LangGraph-0.3+-green?style=for-the-badge" alt="LangGraph">
  <img src="https://img.shields.io/badge/Vue_3-CDN-42b883?style=for-the-badge" alt="Vue 3">
  <img src="https://img.shields.io/badge/Python-3.10+-yellow?style=for-the-badge" alt="Python">
  <img src="https://img.shields.io/badge/Tests-pytest-brightgreen?style=for-the-badge" alt="Tests">
  <img src="https://img.shields.io/badge/License-Apache%202.0-orange?style=for-the-badge" alt="License">
</p>

**PyBot** 它是一个**能自我进化的多智能体平台**——智能体可以在运行时**发明新工具**，不同应用间的工具、知识、工作流**共享复用**，越用越强。

---

## 核心优势

### 1. 工具系统：不是调用工具，而是主动创造工具

大多数框架只能调用预先定义好的工具。PyBot 的智能体能在对话过程中**动态编写 Python 工具**，在 `uv` 沙箱中隔离执行。失败了后自动诊断、改代码、重试——无需人工介入。

除了运行时创造，还有：
- **模板工具**：参数化的 API 包装器、数据转换器，秒级实例化
- **MCP 桥接**：完整的 JSON-RPC 2.0 协议实现（stdio 子进程通信），自动发现社区工具
- **Docker 沙箱**：敏感操作可以在容器中隔离执行，资源限制、自动回收

### 2. 共享复用：1+1 远大于 2

这是 PyBot 的设计理念。所有应用、智能体、工作流共享同一个自我增长的**能力池**：

```
分析应用创建了"数据清洗工具" → CRM 应用可以直接用
知识库摄入了行业文档 → 所有智能体都能检索
工作流定义了审批流程 → 任何应用可以触发
```

随着使用，平台的能力**指数级增长**。第 10 个应用不是从零开始，而是站在前 9 个应用积累的工具和知识之上。

### 3. 治理安全：生产级的可控性

PyBot 有完整的治理体系：

- **审批队列**：高风险操作（删除、部署、外部调用）自动暂停，等人工确认
- **风险等级**：工具按 `low/medium/high/critical` 分级，策略可配置
- **委托链可视化**：谁委托了谁，层级审批一目了然
- **审计追踪**：每一次工具调用、审批决策都有记录，支持合规审查
- **子智能体隔离**：每个子智能体有自己的工具集、权限配置和沙箱边界

### 4. 多 LLM 自动容灾

一行配置切换提供商。10+ 提供商开箱即用（OpenAI、Anthropic、Google、Ollama、Groq、Mistral 等）。配置故障转移链，主模型出错（429/500/超时）自动切换备用模型，业务无感知。

### 5. 内置 RAG 知识引擎

不需要外部向量数据库服务。文件摄入管线支持 txt/md/py/json/csv/html/pdf，自动分块、嵌入、存储。智能体通过 5 个内置工具直接搜索、摄入、管理知识库。知识在所有智能体之间共享。

### 6. 可观测性和成本控制
- 支持 LangSmith、Langfuse 或控制台三种 Tracing 后端
- 实时 Token 用量和 USD 成本估算（覆盖 10+ 主流模型定价）
- 调试面板实时展示所有运行时状态

---

## 概念模型



| 概念 | 它解决什么问题 | 典型产物 |
|------|----------------|----------|
| Tools | 系统到底能执行哪些具体动作 | 动态 Python 工具、模板工具、MCP 工具 |
| Skills | 经验和流程怎样复用 | Markdown 技能包、工具绑定、提示词约定 |
| Agents | 谁来承担任务与角色 | 主智能体、子智能体、领域 worker |
| Workflows | 多步骤和多角色怎样协作 | DAG、审批节点、定时任务、辩论/共识/主管模式 |
| Apps | 最终如何面向用户交付 | Web 控制台、工作区内应用、API/CLI 入口 |

## 横切系统

- **运行时底座**：模型解析、故障转移、观测、重试、事件、路径与会话管理
- **知识与记忆**：文档管线、向量检索、语义记忆
- **治理与安全**：审批队列、风险分级、沙箱、审计
- **集成与交付**：MCP、Channel、PyHub、Web/API/CLI

完整体系说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 架构示意

下图：核心引擎如何驱动工具创建、智能体、技能与工具，并在此基础上部署智能体驱动的应用，共享同一工作区。

<p align="center">
  <img src="pybot1.png" alt="PyBot 架构 — 核心引擎与层级栈" width="90%">
</p>

下图：核心引擎、共享数据/记忆/能力枢纽，以及多应用（如数据分析、CRM、营销）如何共享工具、技能与中央任务调度。

<p align="center">
  <img src="pybot2.png" alt="PyBot 共享枢纽与应用示例" width="90%">
</p>

## 控制台

Vue 3 构建的管理控制台：

- **仪表板** — 智能体、工具、技能、工作流、应用的状态总览
- **对话** — 多轮会话，上下文保持，工具调用过程可见
- **调试面板** — 实时成本、任务队列、MCP 连接、记忆状态、RAG 状态、提供商可用性
- **工作流编辑器** — 双栏编辑器 + 实时 DAG 可视化
- **治理中心** — 审批队列、风险等级、解决/拒绝
- **Hub** — 浏览、安装、发布包


## 引擎要点

- **中间件**：LangChain 记忆 + 事件总线，跨组件能力共享；栈可组合。
- **错误**：类型化工具错误、大输出截断、退避重试、失败自修复。
- **安全**：路径校验、ID 清洗、审批流水线、审计记录。
- **会话**：按次工作区、长对话的上下文卸载与摘要。
- **能力总线**：运行时注册的工具/技能对所有智能体可见；跨应用复用与使用统计。

---

## 为何用 PyBot

- **复用**：一个应用（如分析大屏）创建的工具，通过全局池被其他应用（如 CRM）直接使用。
- **数据共享**：应用共用同一 SQLite 数据，跨域分析无需重复采集。
- **编排**：工作流把任务拆给不同角色（辩论/共识/主管）；Channel 可将报告推到群聊。
- **沉淀**：应用和智能体越多，池中的工具、技能、工作流越丰富。

---

## 快速开始

### 安装

```bash
pip install -e .[dev]
# 或: pip install -r requirements.txt

# 可选：多 LLM 提供商
pip install -e .[all-llm]  # Anthropic + Google + Ollama + Mistral + Groq

# 可选：RAG
pip install -e .[rag]  # ChromaDB + 文本分块
```

### 配置

```bash
pybot-onboard
# 或: python onboard.py
```

### 运行

| 模式 | 命令 | URL |
|------|------|-----|
| Web 控制台 | `pybot-web` | http://localhost:5000 |
| API 服务 | `pybot-api` | http://localhost:8000 |
| 命令行 | `pybot-cli` | — |

### 开发

```bash
ruff check agent.py api_server.py interactive_cli.py onboard.py service_mode.py core web tests
ruff format agent.py api_server.py interactive_cli.py onboard.py service_mode.py core web tests
pytest
pre-commit run --all-files
```

---

## 典型场景

- **自愈工具**：工具执行报错 → 自动读取 traceback → 修复代码或依赖 → 重试成功
- **一句话建应用**：「做一个客户管理系统」→ 前端 + API + 数据模型 → 自动部署到 `/apps/crm/`
- **定时工作流**：`schedule: "0 8 * * *"` → 每天早 8 点自动执行
- **多专家决策**：辩论节点让前端专家和后端专家各抒己见，架构师裁判总结
- **知识增强**：摄入行业文档后，所有智能体的回答质量都会提升
- **故障无感**：主 LLM 限流 → 自动切换备用提供商 → 用户无感知

---

## 技术栈

| 领域 | 选型 |
|------|------|
| 编排 | LangChain 1.x + LangGraph 0.3+ |
| LLM | OpenAI, Anthropic, Google, Ollama, Groq, Mistral 等 10+ |
| 控制台 | FastAPI + Vue 3 (CDN) |
| 工作流 | PyFlow v3 (DAG + 辩论/共识/主管) |
| RAG | ChromaDB (内嵌) + 文档管线 |
| 存储 | SQLite（生产可切 PostgreSQL） |
| 工具沙箱 | `uv` venvs + Docker |
| MCP | 完整 JSON-RPC 2.0 (stdio) |
| 可观测性 | LangSmith / Langfuse / 成本追踪 |
| 测试 | pytest + Ruff |

---

## 许可证

[Apache License 2.0](LICENSE)
