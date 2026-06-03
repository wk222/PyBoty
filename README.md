<p align="center">
  <a href="README.md">English</a> | <a href="README_zh.md">中文</a>
</p>

<h1 align="center">PyBot</h1>

<p align="center">
  <strong>A Persistent Admin Runtime with Cross-Session Capability Accumulation</strong>
</p>

<p align="center">
  <em>An agent runtime that improves over time by persisting tools, skills,<br>
  workflows, and applications across sessions with built-in governance.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Tests-2071_passed-brightgreen?style=for-the-badge" alt="Tests">
  <img src="https://img.shields.io/badge/License-Apache%202.0-orange?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/LLM_Providers-10+-blueviolet?style=for-the-badge" alt="LLM">
  <img src="https://img.shields.io/badge/Channels-7-blue?style=for-the-badge" alt="Channels">
</p>

---

## Why PyBot?

Many agent frameworks treat each conversation as a **disposable session** — tools are forgotten, workflows are discarded, and the system restarts from scratch each time.

PyBot addresses this by building a **persistent runtime** where tools, skills, workflows, and applications are **retained across sessions**, gradually forming a reusable capability library.

| Common limitation | PyBot's approach |
|---|---|
| Tools vanish after the session ends | **Runtime tool creation** — tools are persisted, versioned, and auto-discovered |
| No cross-application orchestration | **App Matrix mode** — wire apps, workflows, and agents into pipelines |
| Governance is an afterthought | **Governance-first middleware** — approval queues, risk grading, sandboxing baked into every call |
| Flat memory, no long-term learning | **Five-layer memory + MemoryDistill** — conversation distills into durable knowledge |
| One-size-fits-all resource usage | **ExecutionCanvas** — per-session resource strategy (focused / balanced / deep) |

---

## Architecture

PyBot is built on a **four-layer dependency tree** with a strict rule: higher layers may import from lower layers, but never the reverse. This keeps 200+ modules organized without circular dependencies.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 3 · Identity Layer                                           │
│                                                                     │
│  ┌──────────────┐  ┌──────────────────┐  ┌───────────────────────┐ │
│  │ ModeProfile   │  │ ExecutionCanvas   │  │ Root Identities      │ │
│  │ assistant     │  │ focused (0.3/1.5K)│  │ 3 operating postures │ │
│  │ app_matrix    │  │ balanced(0.7/4K)  │  │ that share the same  │ │
│  │ admin         │  │ deep    (0.7/8K)  │  │ capability stack     │ │
│  └──────────────┘  └──────────────────┘  └───────────────────────┘ │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2 · Asset Domains                                            │
│                                                                     │
│  Tools ──→ Skills ──→ Agents ──→ Workflows ──→ Apps                 │
│  (runtime    (reusable   (delegated  (DAG         (hosted            │
│   created)    recipes)    workers)    pipelines)    services)        │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1 · Core Systems                                             │
│                                                                     │
│  Governance    Memory & MemoryDistill    CapabilityBus   Middleware  │
│  (approval,    (5-layer: semantic,       (cross-layer    (reasoning  │
│   risk,         garden, distill,          discovery &     frame,      │
│   sandbox)      admin, facade)            composition)    policy)    │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 0 · Runtime Foundation                                       │
│                                                                     │
│  LLM Client    Event Bus    Session Spine    Context Engine          │
│  (10+ providers, (typed      (event ledger,   (workspace view,      │
│   auto failover)  events)     compaction)      budget manager)      │
└─────────────────────────────────────────────────────────────────────┘
```

**Three Root Identities** share this stack but differ in behavior:

| Mode | Role | Key Capability |
|---|---|---|
| **Assistant** | Conversational helper | Answers questions, calls tools, creates capabilities on demand |
| **App Matrix** | Cross-app orchestration hub | Maintains an App Orchestration Registry with topology graph, data bindings, and pipeline schedules |
| **Admin** | Long-running autonomous agent | Decomposes goals into durable multi-step tasks with checkpointing, re-planning, and failure recovery |

**Unified Memory Engine** — SQLite-backed single-table architecture with cognitive extensions:

| Component | What it does | Persistence |
|---|---|---|
| **MemoryEngine** | Unified ingest/recall for facts, episodes, reflections, insights, journal, session notes | SQLite + optional embeddings |
| **MemoryPipeline** | 3-stage async distillation: Journal → Distill → Archive (LLM-driven) | Via MemoryEngine store |
| **Graph-Lite** | In-database association graph with 1-hop expansion on recall | `memory_links` table |
| **Counterfactual Correction** | Supersedes contradictory facts with `contradicted_by` / `supersedes` edges | Truth maintenance on ingest |
| **Markdown Garden** | Structured knowledge notes, user- or agent-authored | Per-workspace files |
| **Admin Memory** | Compressed step memory for durable admin tasks | Per-task |

**ExecutionCanvas** — per-conversation resource tuning (all parameters support user override):

| Canvas | Temperature | Max Tokens | Tool Budget | Memory Strategy | Distill Frequency |
|---|---|---|---|---|---|
| `focused` | 0.3 | 1,500 | 10 calls | MEMORY.md only (800 char cap) | Every 30 msgs |
| `balanced` | 0.7 | 8,192 | 20 calls | MEMORY.md + Semantic top-3 + Garden | Every 15 msgs |
| `deep` | 0.7 | 16,384 | 30 calls | MEMORY.md + Semantic top-8 + full Garden | Every turn |

---

## Advanced Capabilities

### Smart Model Router

Automatically routes LLM calls to the most cost-efficient model based on prompt complexity:

| Tier | When | Typical Models |
|---|---|---|
| **Light** | Simple Q&A, greetings, short lookups | GPT-4o-mini, Claude Haiku |
| **Medium** | Multi-step reasoning, tool use | GPT-4o, Claude Sonnet |
| **Heavy** | Deep analysis, code generation, long context | GPT-4.5, Claude Opus |

The router learns from `ExecutionCanvas` settings — `focused` mode biases toward lighter models, `deep` mode toward heavier ones. Explicit hints (`@heavy`, `@light`) override automatic classification.

### RAG & Document Intelligence

- **Hybrid Search** — combines vector similarity (0.7 weight) with keyword matching (0.3 weight) for better recall
- **Multi-format Parsing** — TXT, Markdown, Python, JSON, CSV, HTML, PDF, **Word (.docx)**, **Excel (.xlsx)**, RST, LaTeX
- **Chunking Pipeline** — configurable chunk size with overlap, metadata preservation

### Multi-Tenant Isolation

Deploy PyBot as a shared service with per-tenant workspace isolation:

- Isolated directories: tools, skills, agents, workflows, apps, memory, uploads
- Tenant resolution via `X-Tenant-ID` header or API key mapping
- Per-tenant configuration and usage statistics

### Agent-to-Agent (A2A) Protocol

Standardized cross-instance communication following the A2A specification:

- **AgentCard** — capability declaration and endpoint discovery
- **A2ATask** — structured task exchange with state machine (submitted → working → completed/failed)
- **A2ARegistry** — peer management, capability-based routing

### Shared Data Bus (App Matrix)

Cross-app data exchange for App Matrix orchestration:

- Named channels with publisher/subscriber pattern
- Access control (per-app read/write permissions)
- Data retention with configurable TTL
- Real-time subscriber notification

### Workflow Engine

Visual DAG editor with extended node types:

| Node Type | Purpose |
|---|---|
| `llm_call` | LLM invocation with prompt template |
| `tool_call` | Execute a registered tool |
| `condition` | Branching logic |
| `transform` | Data transformation |
| `data_source` | Fetch from HTTP API, file, or database |
| `notify` | Send alerts via channel (email, webhook, DingTalk) |
| `monitor` | Long-running conditional watch with alerting |
| `http_request` | Generic HTTP request |

Supports JSON/YAML import/export, execution history, and version control.

### Browser Automation

Integrated Playwright-based browser tool with PyBot-specific enhancements:

- **Canvas-aware capability gating** — `focused` mode is read-only, `balanced` allows interaction, `deep` unlocks full JS evaluation
- **Domain security** — configurable allowlist/blocklist + automatic blocking of internal IPs
- **Token-efficient snapshots** — DOM snapshotting with element ref numbers, size limits adjusted by canvas mode
- **Event tracing** — every browser action is recorded in the event bus for full auditability

### Vision Analysis

Multi-provider VLM tool for image understanding:

- **Source flexibility** — accepts local files, URLs, and `screenshot` (captures current browser page)
- **Auto provider routing** — tries LLM factory → OpenAI → Anthropic, with graceful fallback
- **Canvas-aware detail** — `focused` mode produces brief 2-3 sentence answers; `deep` mode provides thorough analysis

### Web Search

Multi-engine search tool with automatic engine selection:

| Engine | API Key Required | Coverage |
|---|---|---|
| DuckDuckGo | No | General web |
| Bing | `BING_SEARCH_KEY` | Microsoft index |
| SerpAPI | `SERPAPI_KEY` | Google results |

Auto mode tries SerpAPI → Bing → DuckDuckGo, falling back until one succeeds. Results are cached for 5 minutes.

### Conversation Fork

Fork any conversation at any message to explore alternative directions:

- Click the fork icon on any message to branch from that point
- All messages up to the fork point are copied to a new session
- Canvas and session settings are preserved in the fork
- Useful for "what if" exploration without losing original context

### Real-time Tool Progress

Long-running tools (browser, search, vision) now stream progress updates:

- Tool call start/completion events are captured from the EventBus
- Elapsed time is tracked per tool invocation
- Deduplicated step events prevent UI clutter
- Frontend shows animated progress indicators for active tools

### Full Observability

- **Global Event Tracing** — unified timeline of all system events across sessions
- **Cost Tracking** — per-model, per-tool cost breakdown with dashboard visualization
- **Filterable Timeline** — filter by event type, time range, session

---

## Feature Comparison

| Capability | LangGraph | CrewAI | AutoGPT | Temporal | **PyBot** |
|---|---|---|---|---|---|
| Persistent tool creation | - | - | - | - | **Yes** |
| Five-layer capability accumulation | - | Agents only | Tools only | - | **All layers** |
| Governance middleware | - | - | - | - | **Built-in** |
| Durable task execution | Checkpoints | - | - | **Yes** | **Yes + re-planning** |
| Multi-channel (7 platforms) | - | - | - | - | **Yes** |
| Memory distillation | - | - | - | - | **3-stage pipeline** |
| App orchestration registry | - | - | - | - | **Graph-based** |
| MCP ecosystem | - | - | - | - | **Full JSON-RPC** |
| Smart model routing | - | - | - | - | **Auto-classify** |
| Hybrid RAG search | LangChain RAG | - | - | - | **Vector + keyword** |
| Multi-tenant isolation | - | - | - | - | **Built-in** |
| Agent-to-Agent protocol | - | - | - | - | **A2A spec** |
| Cross-app data bus | - | - | - | - | **Pub/Sub channels** |
| Browser automation | - | - | WebDriver | - | **Playwright + governance** |
| Vision / VLM analysis | - | - | - | - | **Multi-provider** |
| Web search integration | - | - | Google only | - | **3 engines + fallback** |
| Conversation forking | - | - | - | - | **Branch at any msg** |
| Streaming tool progress | Callbacks | - | - | - | **EventBus + SSE** |
| Self-hosted observability | LangSmith (SaaS) | - | - | - | **Built-in tracing** |

---

## Quick Start

```bash
# Install
pip install -e .[dev]

# Configure API access (production: set a strong key; local: run.bat sets dev-key)
# export PYBOT_API_KEYS="your-secret:admin,chat"

# Configure
pybot-onboard

# Run
pybot-web     # → http://localhost:5000 (Web Console)
pybot-api     # → http://localhost:8000 (API Server)
pybot-cli     # → Terminal REPL
```

**Multi-provider LLM support:**
```bash
pip install -e .[all-llm]   # OpenAI / Anthropic / Google / Ollama / Groq / Mistral / ...
pip install -e .[rag]       # Vector retrieval with ChromaDB
pip install -e ".[dev,all-llm,rag]"   # full dev + providers + RAG
```

**Plain `pip` installs (no editable metadata):** use `requirements.txt` (core), `requirements-dev.txt`, `requirements-all-llm.txt`, `requirements-rag.txt`, or `requirements-full.txt` — kept in sync with `pyproject.toml` optional groups.

---

## Multi-Channel Access

PyBot connects to 7 platforms through a unified `ChannelManager`:

| Channel | Module | Features |
|---|---|---|
| Web Console | `web/` | Vue 3 SPA, dark/light theme, ExecutionCanvas selector |
| WeChat Official | `wechat_channel.py` | Passive reply + active push |
| WeCom | `wecom_channel.py` | AES encryption, app messages |
| Feishu (Lark) | `feishu_channel.py` | Webhook + auto token refresh |
| DingTalk | `dingtalk_channel.py` | Outgoing robot + HMAC signing |
| WeChat Personal | `wechat_claw_channel.py` | QR login + long polling |
| Terminal | `terminal_channel.py` | Local debug REPL |

---

## Governance & Safety

Every capability invocation passes through PyBot's governance middleware:

```
Request → Risk Assessment → [LOW]      Direct execution
                          → [MEDIUM]   Audit logging
                          → [HIGH]     Approval queue → Human review → Execute
                          → [CRITICAL] Blocked by default
```

- **AgentControlPolicy** — `strict` / `balanced` / `open` presets for tool, subagent, and loop limits
- **Tool Policy Pipeline** — path traversal guards, regex argument validation, budget quotas
- **Hierarchical Multi-Agent Governance** — monotonic capability constraints across agent swarms
- **Plan-Hash-Revalidate** — cryptographic integrity for high-risk tool payloads
- **Sandboxing** — in-process / uv venv / Docker isolation levels

---

## Hub & Ecosystem

| Component | Capability |
|---|---|
| **PyHub** | Plugin marketplace — search, install, publish, rate |
| **MCPHub** | MCP server management — stdio JSON-RPC, auto tool/resource discovery |
| **Gateway** | WebSocket device pairing, presence tracking, run management |
| **OpenResponses** | `/v1/responses` compatibility layer |

---

## Tech Stack

| Domain | Choice |
|---|---|
| Orchestration | LangChain 1.x + LangGraph 0.3+ |
| LLM | OpenAI, Anthropic, Google, Ollama, Groq, Mistral, and 10+ more |
| Model Routing | Smart Model Router (auto-classify light/medium/heavy) |
| Frontend | FastAPI + Vue 3 (CDN), Memory/Tracing/Workflow visual pages |
| Workflow | PyFlow v3 (DAG + data sources + monitors + import/export) |
| RAG | ChromaDB (embedded) + hybrid search (vector + keyword) |
| Browser | Playwright (Chromium, background thread, idle timeout) |
| Vision | Multi-provider VLM (OpenAI, Anthropic, factory-routed) |
| Web Search | DuckDuckGo + Bing + SerpAPI with auto fallback |
| Document Parsing | TXT, MD, PY, JSON, CSV, HTML, PDF, DOCX, XLSX |
| Storage | SQLite (swappable to PostgreSQL) |
| Multi-tenant | Isolated workspaces with per-tenant config |
| Sandbox | `uv` venvs + Docker |
| MCP | JSON-RPC 2.0 (stdio) |
| A2A Protocol | Cross-instance task exchange and capability discovery |
| Observability | Built-in event tracing + cost tracking + streaming tool progress |

---

## Academic Paper

PyBot's architecture is documented in a formal academic paper:
- **English** (primary): [`paper/pybot.tex`](paper/pybot.tex)
- **Chinese**: [`paper/pybot_zh.tex`](paper/pybot_zh.tex)

---

## Development

```bash
ruff check core web tests
ruff format core web tests
pytest    # 2,071 tests
```

## License

[Apache License 2.0](LICENSE)
