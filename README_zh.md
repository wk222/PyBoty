<p align="center">
  <a href="README.md">English</a> | <a href="README_zh.md">中文</a>
</p>

<h1 align="center">PyBot</h1>

<p align="center">
  <strong>跨会话能力积累的持久化管理员运行时</strong>
</p>

<p align="center">
  <em>在现有智能体框架基础上，通过持久化工具、技能、工作流和应用，<br>
  配合内置治理体系，实现跨会话的能力积累与复用。</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/测试-2071_通过-brightgreen?style=for-the-badge" alt="Tests">
  <img src="https://img.shields.io/badge/License-Apache%202.0-orange?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/LLM_供应商-10+-blueviolet?style=for-the-badge" alt="LLM">
  <img src="https://img.shields.io/badge/接入渠道-7-blue?style=for-the-badge" alt="Channels">
</p>

---

## 为什么选择 PyBot？

许多智能体框架将每次对话视为**一次性会话** — 工具被遗忘、工作流被丢弃、系统每次从零开始。

PyBot 在此基础上构建了**持久化运行时**，使工具、技能、工作流和应用能够**跨会话保留**，逐步形成一个可复用的能力库。

| 常见局限 | PyBot 的做法 |
|---|---|
| 工具在会话结束后消失 | **运行时工具创造** — 工具被持久化、版本化并自动发现 |
| 无跨应用编排能力 | **应用矩阵模式** — 将应用、工作流和智能体串联为流水线 |
| 治理是事后补丁 | **治理优先中间件** — 审批队列、风险分级、沙箱深嵌每次调用 |
| 扁平记忆，无长期学习 | **五层记忆 + MemoryDistill** — 对话蒸馏为持久化知识 |
| 资源使用一刀切 | **执行画布** — 按会话调节资源策略（focused / balanced / deep） |

---

## 架构

PyBot 建立在**四层依赖树**之上，遵循严格规则：高层可以导入低层，但不允许反向依赖。这让 200+ 模块保持有序，杜绝循环依赖。

```
┌──────────────────────────────────────────────────────────────────────┐
│  Layer 3 · 身份层                                                     │
│                                                                      │
│  ┌──────────────┐  ┌───────────────────┐  ┌────────────────────────┐ │
│  │ ModeProfile   │  │ ExecutionCanvas    │  │ 三种根身份             │ │
│  │ assistant     │  │ focused (0.3/1.5K) │  │ 共享同一能力栈，       │ │
│  │ app_matrix    │  │ balanced(0.7/4K)   │  │ 行为各异               │ │
│  │ admin         │  │ deep    (0.7/8K)   │  │                        │ │
│  └──────────────┘  └───────────────────┘  └────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 2 · 领域对象层                                                 │
│                                                                      │
│  工具 ──→ 技能 ──→ 智能体 ──→ 工作流 ──→ 应用                         │
│  (运行时     (可复用    (委托       (DAG        (托管                   │
│   创建)       配方)      工人)       流水线)      服务)                 │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 1 · 核心系统层                                                 │
│                                                                      │
│  治理          记忆 & MemoryDistill      能力总线       中间件        │
│  (审批、       (五层：语义、园林、        (跨层          (推理框架、    │
│   风控、        蒸馏、管理员、门面)         发现&组合)     策略)        │
├──────────────────────────────────────────────────────────────────────┤
│  Layer 0 · 基础层                                                     │
│                                                                      │
│  LLM 客户端     事件总线     会话脊柱       上下文引擎                │
│  (10+ 供应商、  (类型化      (事件账本、    (工作区视图、             │
│   自动切换)      事件)        压缩)          预算管理)                │
└──────────────────────────────────────────────────────────────────────┘
```

**三种根身份**共享此能力栈，但行为不同：

| 模式 | 角色 | 核心能力 |
|---|---|---|
| **助手模式** | 对话助手 | 回答问题、调用工具、按需创建能力 |
| **应用矩阵模式** | 跨应用编排中枢 | 维护应用编排注册表，支持拓扑图、数据绑定和流水线调度 |
| **管理员模式** | 长期自主智能体 | 将目标分解为持久化多步任务，支持检查点、重规划和故障恢复 |

**统一记忆引擎（MemoryEngine）** — SQLite 单表架构 + 认知扩展：

| 组件 | 功能 | 持久化 |
|---|---|---|
| **MemoryEngine** | 统一 ingest/recall（fact/episode/reflection/insight/journal/session_note） | SQLite + 可选向量 |
| **MemoryPipeline** | 三阶段异步蒸馏：Journal → Distill → Archive | 经 MemoryEngine 存储 |
| **Graph-Lite** | 库内关联图谱，召回时 1-hop 联想扩展 | `memory_links` 表 |
| **反事实修正** | 冲突事实软归档并建立 supersedes 边 | ingest 时真相维护 |
| **知识园林** | 结构化知识笔记，用户或 Agent 撰写 | 工作区级 |
| **管理员记忆** | 持久化任务步骤压缩 | 任务级 |

**执行画布** — 按会话调节资源（所有参数支持用户自定义覆盖）：

| 画布 | 温度 | 最大 Token | 工具预算 | 记忆策略 | 蒸馏频率 |
|---|---|---|---|---|---|
| `focused` | 0.3 | 1,500 | 10 次 | 仅 MEMORY.md（800 字符上限） | 每 30 条消息 |
| `balanced` | 0.7 | 8,192 | 20 次 | MEMORY.md + 语义检索 top-3 + 知识园林 | 每 15 条消息 |
| `deep` | 0.7 | 16,384 | 30 次 | MEMORY.md + 语义检索 top-8 + 完整园林 | 每轮 |

---

## 进阶能力

### 智能模型路由

根据提示词复杂度自动选择最优性价比的 LLM 模型：

| 层级 | 场景 | 典型模型 |
|---|---|---|
| **轻量** | 简单问答、打招呼、短查询 | GPT-4o-mini、Claude Haiku |
| **中等** | 多步推理、工具调用 | GPT-4o、Claude Sonnet |
| **重度** | 深度分析、代码生成、长上下文 | GPT-4.5、Claude Opus |

路由器会参考 `ExecutionCanvas` 设置 — `focused` 模式偏向轻量模型，`deep` 模式偏向重度模型。支持显式指定（`@heavy`、`@light`）覆盖自动分类。

### RAG 与文档智能

- **混合检索** — 向量相似度（权重 0.7）+ 关键词匹配（权重 0.3），提升召回率
- **多格式解析** — TXT、Markdown、Python、JSON、CSV、HTML、PDF、**Word (.docx)**、**Excel (.xlsx)**、RST、LaTeX
- **分块流水线** — 可配置块大小与重叠量，保留元数据

### 多租户隔离

将 PyBot 部署为共享服务，每个租户拥有独立工作空间：

- 隔离目录：工具、技能、智能体、工作流、应用、记忆、上传
- 通过 `X-Tenant-ID` 请求头或 API Key 映射解析租户
- 租户级配置和使用统计

### Agent-to-Agent (A2A) 协议

遵循 A2A 规范的标准化跨实例通信：

- **AgentCard** — 能力声明与端点发现
- **A2ATask** — 结构化任务交换，支持状态机（submitted → working → completed/failed）
- **A2ARegistry** — 对等节点管理、基于能力的路由

### 共享数据总线（应用矩阵）

应用矩阵模式下的跨应用数据交换：

- 命名通道 + 发布/订阅模式
- 访问控制（每应用读/写权限）
- 可配置 TTL 的数据保留
- 实时订阅通知

### 工作流引擎

可视化 DAG 编辑器，支持扩展节点类型：

| 节点类型 | 用途 |
|---|---|
| `llm_call` | 基于提示模板的 LLM 调用 |
| `tool_call` | 执行已注册的工具 |
| `condition` | 分支逻辑 |
| `transform` | 数据转换 |
| `data_source` | 从 HTTP API、文件或数据库获取数据 |
| `notify` | 通过渠道发送告警（邮件、Webhook、钉钉） |
| `monitor` | 长效条件监控与告警 |
| `http_request` | 通用 HTTP 请求 |

支持 JSON/YAML 导入导出、执行历史和版本控制。

### 浏览器自动化

集成 Playwright 的浏览器工具，带 PyBot 专属增强：

- **画布感知能力门控** — `focused` 模式只读浏览，`balanced` 允许交互，`deep` 解锁完整 JS 执行
- **域名安全策略** — 可配置白名单/黑名单 + 自动拦截内网 IP
- **Token 高效快照** — DOM 快照带元素引用编号，大小随画布模式调整
- **事件追踪** — 每个浏览器操作都记录到事件总线，完全可审计

### 视觉理解

多供应商 VLM 图像分析工具：

- **来源灵活** — 支持本地文件、URL、`screenshot`（截取当前浏览器页面）
- **自动供应商路由** — 依次尝试 LLM 工厂 → OpenAI → Anthropic，优雅降级
- **画布感知详细度** — `focused` 模式简短 2-3 句回答；`deep` 模式全面分析

### 网页搜索

多引擎搜索工具，自动选择可用引擎：

| 引擎 | 需要 API Key | 覆盖范围 |
|---|---|---|
| DuckDuckGo | 否 | 通用网页 |
| Bing | `BING_SEARCH_KEY` | 微软索引 |
| SerpAPI | `SERPAPI_KEY` | Google 结果 |

Auto 模式按 SerpAPI → Bing → DuckDuckGo 顺序尝试。结果缓存 5 分钟。

### 对话分叉

从任意消息处分叉对话，探索不同方向：

- 悬停消息时点击分叉图标，从该处创建分支
- 分叉点之前的所有消息复制到新会话
- 画布和会话设置在分叉中保留
- 适合"如果我改这样呢"的探索，不影响原始上下文

### 实时工具进度

长时间运行的工具（浏览器、搜索、视觉）现在会流式推送进度：

- 工具调用开始/完成事件从 EventBus 捕获
- 每个工具调用的耗时被追踪
- 去重的步骤事件避免 UI 杂乱
- 前端显示活跃工具的动画进度指示器

### 全链路观测

- **全局事件追踪** — 跨会话的统一事件时间线
- **成本追踪** — 按模型、按工具的成本明细与看板可视化
- **可筛选时间线** — 按事件类型、时间范围、会话过滤

---

## 功能对比

| 能力 | LangGraph | CrewAI | AutoGPT | Temporal | **PyBot** |
|---|---|---|---|---|---|
| 持久化工具创建 | - | - | - | - | **支持** |
| 五层能力积累 | - | 仅 Agent | 仅 Tool | - | **全层** |
| 治理中间件 | - | - | - | - | **内置** |
| 持久化任务执行 | 检查点 | - | - | **支持** | **支持 + 重规划** |
| 多渠道接入（7 平台） | - | - | - | - | **支持** |
| 记忆蒸馏 | - | - | - | - | **三阶段流水线** |
| 应用编排注册表 | - | - | - | - | **图结构** |
| MCP 生态 | - | - | - | - | **完整 JSON-RPC** |
| 智能模型路由 | - | - | - | - | **自动分类** |
| 混合 RAG 检索 | LangChain RAG | - | - | - | **向量+关键词** |
| 多租户隔离 | - | - | - | - | **内置** |
| A2A 协议 | - | - | - | - | **A2A 规范** |
| 跨应用数据总线 | - | - | - | - | **发布/订阅通道** |
| 浏览器自动化 | - | - | WebDriver | - | **Playwright + 治理** |
| 视觉/VLM 分析 | - | - | - | - | **多供应商** |
| 网页搜索集成 | - | - | 仅 Google | - | **3 引擎 + 降级** |
| 对话分叉 | - | - | - | - | **任意消息处分叉** |
| 流式工具进度 | 回调 | - | - | - | **EventBus + SSE** |
| 自托管观测 | LangSmith (SaaS) | - | - | - | **内置追踪** |

---

## 快速开始

```bash
# 安装
pip install -e .[dev]

# 配置
pybot-onboard

# 运行
pybot-web     # → http://localhost:5000（Web 控制台）
pybot-api     # → http://localhost:8000（API 服务）
pybot-cli     # → 命令行 REPL
```

**多供应商 LLM 支持：**
```bash
pip install -e .[all-llm]   # OpenAI / Anthropic / Google / Ollama / Groq / Mistral / ...
pip install -e .[rag]       # ChromaDB 向量检索
```

---

## 多渠道接入

通过统一的 `ChannelManager` 连接 7 个平台：

| 渠道 | 模块 | 特性 |
|---|---|---|
| Web 控制台 | `web/` | Vue 3 SPA，暗色/亮色主题，执行画布选择器 |
| 微信公众号 | `wechat_channel.py` | 被动回复 + 主动推送 |
| 企业微信 | `wecom_channel.py` | AES 加解密，应用消息 |
| 飞书 | `feishu_channel.py` | Webhook + Token 自动刷新 |
| 钉钉 | `dingtalk_channel.py` | Outgoing 机器人 + HMAC 签名 |
| 微信个人号 | `wechat_claw_channel.py` | QR 扫码登录 + 长轮询 |
| Terminal | `terminal_channel.py` | 本地调试 REPL |

---

## 治理与安全

每个能力调用都经过 PyBot 的治理中间件：

```
请求 → 风险评估 → [LOW]      直接执行
                → [MEDIUM]   审计记录
                → [HIGH]     审批队列 → 人工审核 → 执行
                → [CRITICAL] 默认阻止
```

- **AgentControlPolicy** — `strict` / `balanced` / `open` 预设，控制工具/子智能体/循环限制
- **工具策略流水线** — 路径穿越守卫、正则参数验证、预算配额
- **层级化多智能体治理** — 跨智能体集群的单调能力约束
- **计划-哈希-重验证** — 高风险工具载荷的密码学完整性保护
- **沙箱隔离** — 进程内 / uv venv / Docker 三级隔离

---

## Hub 与生态

| 组件 | 能力 |
|---|---|
| **PyHub** | 插件市场 — 搜索、安装、发布、评分 |
| **MCPHub** | MCP 服务器管理 — stdio JSON-RPC，自动发现工具和资源 |
| **Gateway** | WebSocket 设备配对、在线状态追踪、运行管理 |
| **OpenResponses** | `/v1/responses` 兼容层 |

---

## 技术栈

| 领域 | 选型 |
|---|---|
| 编排 | LangChain 1.x + LangGraph 0.3+ |
| LLM | OpenAI、Anthropic、Google、Ollama、Groq、Mistral 等 10+ |
| 模型路由 | 智能模型路由（自动分类 light/medium/heavy） |
| 前端 | FastAPI + Vue 3 (CDN)，记忆/追踪/工作流可视化页面 |
| 工作流 | PyFlow v3（DAG + 数据源 + 监控器 + 导入导出） |
| RAG | ChromaDB（内嵌） + 混合检索（向量+关键词） |
| 浏览器 | Playwright（Chromium，后台线程，空闲超时释放） |
| 视觉 | 多供应商 VLM（OpenAI、Anthropic、工厂路由） |
| 网页搜索 | DuckDuckGo + Bing + SerpAPI 自动降级 |
| 文档解析 | TXT、MD、PY、JSON、CSV、HTML、PDF、DOCX、XLSX |
| 存储 | SQLite（可切 PostgreSQL） |
| 多租户 | 独立工作空间 + 租户级配置 |
| 沙箱 | `uv` venvs + Docker |
| MCP | JSON-RPC 2.0 (stdio) |
| A2A 协议 | 跨实例任务交换与能力发现 |
| 观测 | 内置事件追踪 + 成本追踪 + 流式工具进度 |

---

## 学术论文

PyBot 的架构以学术论文形式记录：
- **英文版**（主维护）：[`paper/pybot.tex`](paper/pybot.tex)
- **中文版**：[`paper/pybot_zh.tex`](paper/pybot_zh.tex)

---

## 开发

```bash
ruff check core web tests
ruff format core web tests
pytest    # 2,071 个测试
```

依赖清单与 `pyproject.toml` 对齐：`requirements.txt`（核心）、`requirements-dev.txt`、`requirements-all-llm.txt`、`requirements-rag.txt`、`requirements-full.txt`。

## 许可证

[Apache License 2.0](LICENSE)
