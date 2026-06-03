# PyBot — Self-Evolving Multi-Agent Platform

<p align="center">
  <img src="https://img.shields.io/badge/LangChain-1.0.0--alpha-blue?style=for-the-badge" alt="LangChain">
  <img src="https://img.shields.io/badge/LangGraph-0.3+-green?style=for-the-badge" alt="LangGraph">
  <img src="https://img.shields.io/badge/Vue_3-CDN-42b883?style=for-the-badge" alt="Vue 3">
  <img src="https://img.shields.io/badge/Python-3.10+-yellow?style=for-the-badge" alt="Python">
  <img src="https://img.shields.io/badge/Tests-610+-brightgreen?style=for-the-badge" alt="Tests">
  <img src="https://img.shields.io/badge/License-Apache%202.0-orange?style=for-the-badge" alt="License">
</p>

**PyBot** is a self-evolving AI agent platform. Unlike frameworks that only *call* predefined tools, PyBot can **write new Python tools at runtime** (sandboxed), **run DAG workflows** with debate/consensus/supervisor patterns, **spawn sub-agents** with isolated tool sets and governance, **ingest knowledge** for RAG retrieval, **fail over across LLM providers**, and **deploy full web apps** from the workspace — all sharing one capability pool.

### What makes PyBot different

| Capability | PyBot | Typical Agent Frameworks |
|---|---|---|
| **Runtime tool creation** | Agent writes Python tools on the fly, auto-fixes on error | Predefined tool sets only |
| **Multi-LLM failover** | 10+ providers with automatic fallback chain | Single provider, manual switching |
| **Built-in RAG** | File ingestion pipeline + vector search + Agent tools | External setup required |
| **Sub-agent governance** | Approval pipeline, risk levels, delegation chains, audit trail | No governance layer |
| **Workflow patterns** | Debate, consensus, supervisor, HITL approval nodes | Basic sequential chains |
| **Self-deploying apps** | Frontend + backend apps served from workspace | No app deployment |
| **Observability** | LangSmith/Langfuse/Console tracing + cost tracking | Manual instrumentation |
| **Full MCP protocol** | stdio subprocess, JSON-RPC 2.0, dynamic tool discovery | Partial or none |
| **Semantic memory** | Vector-backed + file-based hybrid with fallback search | Rule-based text only |
| **Capability accumulation** | Tools/skills/workflows grow the shared pool over time | Isolated per-session |

---

## Architecture: Five Layers

Capabilities are organized in five layers, from low-level executables to full apps:

### Layer 1: Tools

- **Dynamic tools**: Python scripts generated at runtime, executed in isolated `uv` environments. Failed runs trigger automatic diagnosis and code rewrite.
- **MCP bridge**: Model Context Protocol support for community tools (GitHub, Notion, DBs, etc.).
- **Templates**: Parameterized patterns (API wrappers, data transforms) with dependency handling.

### Layer 2: Skills

- **Markdown-defined**: Each skill is a Markdown file with scenario prompts, preconditions, and tool bindings.
- **Lazy loading**: Skills are pulled into context only when needed.
- **Multi-source**: Local filesystem, HTTP registries, or PyHub. Supports bundle/materialize and descriptor/ETag/Retry-After semantics.

### Layer 3: Agents

- **Spawned workers**: Sub-agents get their own persona, tools, and memory.
- **Task dispatch**: Isolated state via `task` with inheritance chain visibility.
- **Governance**: High-risk actions go through an approval pipeline with configurable risk levels and audit logs.

### Layer 4: Workflows (PyFlow v3)

- **DAG engine**: Directed acyclic graphs defined with a minimal Workflow Spec DSL.
- **Patterns**: `debate` (multi-round argument + judge), `consensus` (parallel experts + merge), `supervisor` (dynamic routing).
- **HITL**: `approve` nodes pause until confirmed in the Governance dashboard.
- **Cron**: Native cron expressions; engine scans and triggers on schedule.

### Layer 5: Apps

- **Deploy from workspace**: Full web apps (HTML/JS/CSS + Python backend) written and served from the workspace.
- **Frontend bridge**: `pybot-helpers.js` (`apiCall`, `triggerWorkflow`) wires UIs to workflows and agents.
- **No extra hosting**: Apps are served by the same process with automatic routes.

### Integration

- **Channel**: Webhook-style send/receive for Feishu, DingTalk, WeCom, Slack, custom endpoints.
- **PyHub**: Package registry for discovering and sharing tools, skills, workflows, and apps.

---

## Architecture Overview

The following diagram shows how the core engine drives tool creation, agents, skills, and tools, and deploys agent-driven apps on top of a shared workspace.

<p align="center">
  <img src="pybot1.png" alt="PyBot architecture — core engine and layer stack" width="90%">
</p>

Below: the core engine, shared data/memory/capability hub, and how multiple apps (e.g. analytics, CRM, marketing) share tools, skills, and the central task scheduler.

<p align="center">
  <img src="pybot2.png" alt="PyBot shared hub and app examples" width="90%">
</p>

---

## Matrix Console

A Vue 3 console ships with PyBot for managing the platform:

- **Dashboard** — Agents, tools, skills, workflows, apps (counts and status)
- **Chat** — Multi-turn conversation, context kept, tool runs shown
- **Hub** — Browse, search, install packages from PyHub
- **Governance** — Approval queue, risk level, resolve/reject
- **Workflow editor** — Dual-pane Spec editor and live DAG view
- **Entity views** — CRUD for tools, skills, agents, workflows, apps, settings

---

## PyHub

PyBot can use **PyHub** as a package marketplace:

| Type       | Description                          |
|-----------|--------------------------------------|
| Tool      | Python tool scripts + deps           |
| Skill     | Markdown bundles + tool refs         |
| Workflow  | DAG specs for business processes     |
| App       | Full-stack micro app (UI + backend)  |

- **Console**: Browse, inspect versions, install.
- **CLI**: `pyhub search`, `pyhub install`, `pyhub publish`.
- **Code**: `PyHubClient` for programmatic access.

---

## Engine Details

- **Multi-LLM**: `model_resolver` supports OpenAI, Anthropic, Google, Ollama, Groq, Mistral, Fireworks, DeepSeek, Together, Bedrock. `model_failover` handles transient errors (429/500/timeout) with automatic fallback chain. One-line config: `"provider": "anthropic"`.
- **RAG Pipeline**: `document_pipeline` ingests txt/md/py/json/csv/html/pdf → chunks → ChromaDB vectors. `knowledge_tools` exposes 5 agent tools (search, ingest, ingest_text, list, delete). Zero external services with embedded ChromaDB.
- **Observability**: `observability.py` integrates LangSmith (env-based), Langfuse (callback handler), or Console tracing. `cost_tracker.py` records token usage per model with USD estimates.
- **MCP Protocol**: Full JSON-RPC 2.0 over stdio subprocess — initialize handshake, tools/list, tools/call, resources/list, resources/read. Dynamic tool discovery and LangChain wrapping.
- **Structured Output**: `invoke_structured()` auto-falls through 3 strategies: native `.with_structured_output()`, JSON mode, text extraction. Built-in `TaskAnalysis` and `CodeReview` schemas.
- **Semantic Memory**: `MemoryEngine` (SQLite + FTS5/embedding hybrid recall, Graph-Lite associations, counterfactual correction). Falls back to keyword matching without vector store.
- **Task Queue**: `TaskQueue` with ThreadPoolExecutor for background operations — RAG ingestion, long workflows, webhook processing. `CheckpointerFactory` for SQLite/PostgreSQL.
- **Middleware**: LangChain memory + event bus for cross-component capability sharing; stack is composable.
- **Errors**: Typed tool errors (`ToolInputError`/`ToolAuthorizationError`/`ToolNotFoundError`/`ToolTimeoutError`/`ToolRateLimitError`), truncation, retry with backoff/jitter, self-healing.
- **Security**: Path validation, sanitized IDs, approval pipeline, audit trail, Docker sandbox isolation.
- **Capability bus**: Runtime-registered tools/skills visible to all agents; cross-app reuse and usage stats.

---

## Debug Panel

The web console includes a Debug Panel (`/debug`) with live system introspection:

- **Cost Tracker** — Token usage, USD estimates, per-model breakdown
- **Task Queue** — Background task status, pending/running/completed
- **MCP Status** — Server connections, discovered tools and resources
- **Memory Stats** — File-based + vector-backed memory counts
- **RAG Status** — Knowledge base enabled, tools count
- **Provider Status** — Which LLM providers are installed and available

---

## Why PyBot

- **Self-evolving**: Tools and skills accumulate over time; each new app enriches the shared pool.
- **Reuse**: A tool created in one app (e.g. analytics) is available to others (e.g. CRM) via the global pool.
- **Shared data**: Apps share the same SQLite-backed data; no duplicate ingestion for cross-domain views.
- **Resilient**: Multi-LLM failover + retry policies mean the system keeps working when one provider has issues.
- **Observable**: Built-in cost tracking and tracing from day one, not bolted on later.
- **Orchestration**: Workflows split work across specialists (debate/consensus/supervisor); Channel can push reports to chat groups.

---

## Quick Start

### Install

```bash
pip install -e .[dev]
# or: pip install -r requirements.txt
```

### Configure

```bash
pybot-onboard
# or: python onboard.py
```

### Run

| Mode        | Command     | URL                  |
|------------|-------------|----------------------|
| Web console| `pybot-web` | http://localhost:5000 |
| API server | `pybot-api` | http://localhost:8000 |
| CLI        | `pybot-cli` | —                    |

### Dev

```bash
ruff check agent.py api_server.py interactive_cli.py onboard.py service_mode.py core web tests
ruff format agent.py api_server.py interactive_cli.py onboard.py service_mode.py core web tests
pytest
pre-commit run --all-files
```

---

## Project Layout

```
pybot/
├── agent.py                 # Core agent and tool orchestration
├── service_mode.py          # Web console (FastAPI + Vue static UI)
├── api_server.py            # REST API (FastAPI)
├── interactive_cli.py       # Terminal REPL
├── onboard.py               # Setup wizard
├── core/                    # Engine: model_resolver, model_failover, vector_store, document_pipeline,
│                            #   knowledge_tools, observability, cost_tracker, mcp_hub, memory engine,
│                            #   structured_output, task_queue, capability_bus, approvals, errors, retry, ...
├── static/                  # Vue 3 SPA (app.js, style.css, views/, components/, api/)
├── web/                     # FastAPI routers (chat, admin, workflows, apps, workspace, debug panel)
├── tests/                   # 610+ pytest tests
├── workspace/               # Runtime data
└── pyproject.toml
```

---

## Example Use Cases

- **Self-healing tools**: On tool error, PyBot reads the traceback, fixes deps or code, and retries.
- **App from prompt**: e.g. “Build a customer management system” → frontend, API, schema, deployed under `/apps/crm/`.
- **Scheduled workflows**: `schedule: "0 8 * * *"` in a workflow spec → daily 8am run.
- **Multi-actor decisions**: `debate` node with frontend/backend “experts” and an “architect” summarizer.
- **Sharing**: Publish tools/workflows to PyHub with `pyhub publish` for others to install.

---

## Tech Stack

| Area           | Choice                      |
|----------------|-----------------------------|
| Orchestration  | LangChain 1.x, LangGraph 0.3+ |
| LLM Providers  | OpenAI, Anthropic, Google, Ollama, Groq, Mistral, +5 more |
| Console        | FastAPI + Jinja2             |
| API            | FastAPI + Uvicorn            |
| Frontend       | Vue 3 (CDN), Vue Router 4    |
| Workflows      | PyFlow v3 (DAG + debate/consensus/supervisor) |
| RAG            | ChromaDB (embedded) + document pipeline |
| Storage        | SQLite (+ PostgreSQL via factory) |
| Tool sandbox   | `uv` venvs + Docker sandbox |
| MCP            | Full JSON-RPC 2.0 (stdio)   |
| Observability  | LangSmith, Langfuse, cost tracker |
| Marketplace    | PyHub (FastAPI + PostgreSQL) |
| Tests / Lint   | pytest (610+), Ruff          |

---

## License

[Apache License 2.0](LICENSE)
