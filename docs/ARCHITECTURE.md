# PyBot Architecture

## Why This Document Exists

PyBot now has a lot of capabilities: dynamic tools, MCP, RAG, approvals, subagents, workflows, apps, middleware, and more.

To keep the project understandable, we separate:

1. Product concepts
2. Cross-cutting systems
3. Code organization

The executable canonical model lives in `core/system_model.py`; this file is the human-facing companion for that separation.

---

## North Star

如果文档和实现出现分歧，以 `core/system_model.py` 为准，再回写本文档。

PyBot 的真正中心不应该是五个平级产品，而应该是一个**三层根身份模型**：

- `人类助手模式`: 默认交互入口，负责通用对话、工具协作与即时任务
- `应用矩阵模式`: 面向多应用协作的中央调度智能体，负责串联 APP、工作流、子智能体和共享能力
- `全局管理员模式`: 长期运行的根智能体运行时，负责持续目标、调度、创造与沉淀

其中，应用级协作主要由“应用矩阵模式”承担，长期演化方向主要由“全局管理员模式”承担。

应用矩阵模式负责：

- 识别当前需求涉及哪些 APP
- 决定是复用现有 APP、串联多个 APP，还是补一个新 APP
- 管理跨 APP 的数据流、状态流和任务流
- 把应用级协作收敛成清晰的调度链路
- 在需要时向全局管理员模式升级为长期自治任务

全局管理员模式负责：

- 持续接收目标
- 规划和拆解任务
- 调度子智能体、工作流和应用
- 在需要时创造新工具和新能力
- 通过审批、沙箱和审计保持可控
- 通过记忆、知识、调度器和持久任务保持长期连续性

因此：

- `Tools / Skills / Agents / Workflows / Apps` 是它调用和创造的能力形态
- `approvals / sandbox / middleware / RAG / MCP / failover / observability` 是它的支撑系统

不要把这些概念再讲成互相并列的平台层。

---

## Product Concepts

PyBot has **five** user-facing product concepts. Everything else should map back to one of them or be treated as a supporting system.

这些概念不是五个平级产品，而是长期运行总控智能体的五类外显能力。

| Concept | Question It Answers | Main Responsibility | Typical Modules |
|---|---|---|---|
| Tools | "What concrete action can the system execute?" | Python tools, MCP tools, templates, execution | `core/tool_*`, `core/mcp_hub.py`, `core/execution_*` |
| Skills | "How is reusable know-how packaged?" | Prompted capability bundles with tool bindings | `core/skill_*` |
| Agents | "Who owns a task or role?" | Persona, memory, tool access, delegation | `core/agent_*`, `core/subagent_*`, `core/society_of_mind.py` |
| Workflows | "How do multiple steps or roles coordinate?" | DAG execution, approvals, routing, scheduling | `core/workflow_*`, `core/pyflow_engine.py`, `core/task_*` |
| Apps | "How is capability exposed to end users?" | Hosted web apps, admin surfaces, workspace deployment | `core/app_*`, `web/`, `static/` |

### Important Rule

The following are **not** extra product concepts:

- MCP
- RAG
- approvals
- capability bus
- middleware
- observability
- failover

They are supporting systems that strengthen the five concepts above.

---

## Cross-Cutting Systems

These systems show up across multiple product concepts, so they should not be introduced as separate product layers.

### 1. Runtime Foundation

- config and project paths
- model resolution and failover
- retries, errors, events, session state
- structured output and observability

Primary modules: `core/systems/runtime/` (canonical), stubs at `core/config.py`, `core/project_paths.py` etc.

### 2. Knowledge And Memory

- document ingestion
- vector storage
- retrieval formatting
- semantic memory

Primary modules: `core/systems/memory/` (canonical), stubs at `core/semantic_memory.py`, `core/memory_*.py` etc.

### 3. Governance And Safety

- approvals
- risk grading
- guardrails
- sandbox adapters
- auditability

Primary modules: `core/systems/governance/` (canonical), stubs at `core/approval_*.py`, `core/agent_control.py` etc.

### 4. Delivery And Integration

- Web/API/CLI entrypoints
- workspace-hosted apps
- external channels
- PyHub packaging
- MCP connectivity

Primary modules: `core/systems/integration/` (canonical), `web/`, `agent.py`, stubs at `core/channel_*.py`, `core/mcp_hub.py` etc.

---

## Repository Map

| Path | Role |
|---|---|
| `core/` | Runtime engine and domain logic |
| `web/` | FastAPI service and routers |
| `static/` | Console frontend assets |
| `tests/` | Behavioral and regression coverage |
| `workspace/` | Sample/demo workspace content inside the repo |
| `docs/` | Human-facing architecture and operating model |

### Runtime Boundary Rule

真实运行时状态默认不再要求落在仓库根目录。

- `root_dir`: 源码与静态资源所在目录
- `runtime_root_dir`: 工具库、agent 状态、uv 环境、真实工作空间等运行时目录

默认情况下，`runtime_root_dir` 会走用户级目录；测试和显式 `root_dir` 场景可以继续共址，保证可控性。

### Inside `core/`

`core/` follows a stable four-layer physical layout:

| Layer | Path | Purpose |
|---|---|---|
| `systems/runtime/` | config, paths, errors, retries, model plumbing, bootstrap | Foundation services shared by all higher layers |
| `systems/governance/` | approvals, agent control, guardrails, sandbox | Safety and access control |
| `systems/memory/` | semantic memory, memory manager, scoring | Knowledge and recall |
| `systems/integration/` | channels, MCP hub, external content | External service connectivity |
| `assets/agents/` | agent runtime, delegation, capability profiles, storage | Persona and task ownership |
| `assets/tools/` | tool creation, runtime, storage, templates | Concrete executable actions |
| `assets/skills/` | skill loading, registry, HTTP backends | Reusable prompted capability bundles |
| `assets/apps/` | app manager, brain planner, orchestration | End-user exposed applications |
| `assets/workflows/` | DAG execution, scheduling, pause/resume, models | Multi-step coordination |
| `modes/` | human assistant, app brain, admin agent | Root identity mode profiles |

Backward compatibility: every moved module has a one-line stub at its original `core/foo.py` path, so `from core.foo import X` still works. New code should prefer the canonical `core.systems.*` / `core.assets.*` / `core.modes.*` paths.

These are **internal code domains**, not extra product layers.

---

## Naming Rules

Use names consistently so new concepts do not sprawl.

| Suffix | Meaning |
|---|---|
| `*Storage` | Persistence and file/DB CRUD |
| `*Runtime` | Request-time execution logic |
| `*Registry` | Discovery and indexed lookup |
| `*Manager` | Stateful owner of a broader subsystem |
| `*Profile` | Declarative policy/config preset |
| `*Orchestrator` | Coordination across multiple subsystems |
| `*Backend` | Adapter to an execution or remote system |

### Anti-Sprawl Rule

Before adding a new top-level concept, ask:

1. Is this really a new product concept, or just a subsystem behind tools/skills/agents/workflows/apps?
2. Can it be modeled as a runtime, backend, profile, or registry instead?
3. Does README need to teach it, or is it only relevant to contributors inside `core/`?

If the answer to (1) is "no", do not present it as a new platform layer.

---

## Current Project Diagnosis

PyBot already has strong implementation depth, but it has shown these signs of concept drift:

- product-facing docs and package docs used different layer counts
- README duplicated architecture sections
- some docs treated supporting systems as if they were first-class product concepts
- older docs mixed outdated implementation details with current architecture

The canonical rule going forward is:

**Externally explain PyBot as a persistent admin runtime with five product concepts; internally organize code by domains.**
