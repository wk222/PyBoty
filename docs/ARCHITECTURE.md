# PyBot Architecture

## Why This Document Exists

PyBot now has a lot of capabilities: dynamic tools, MCP, RAG, approvals, subagents, workflows, apps, middleware, and more.

To keep the project understandable, we separate:

1. Product concepts
2. Cross-cutting systems
3. Code organization

This file is the canonical model for that separation.

---

## Product Concepts

PyBot has **five** user-facing product concepts. Everything else should map back to one of them or be treated as a supporting system.

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

Primary modules:
- `core/config.py`
- `core/project_paths.py`
- `core/model_*`
- `core/errors.py`
- `core/retry_policy.py`
- `core/event_bus.py`
- `core/session_events.py`

### 2. Knowledge And Memory

- document ingestion
- vector storage
- retrieval formatting
- semantic memory

Primary modules:
- `core/document_pipeline.py`
- `core/vector_store.py`
- `core/knowledge_*`
- `core/memory_*`
- `core/semantic_memory.py`

### 3. Governance And Safety

- approvals
- risk grading
- guardrails
- sandbox adapters
- auditability

Primary modules:
- `core/approval_*`
- `core/agent_control.py`
- `core/guardrails.py`
- `core/subagent_governance.py`
- `core/subagent_sandbox.py`
- `core/docker_sandbox.py`

### 4. Delivery And Integration

- Web/API/CLI entrypoints
- workspace-hosted apps
- external channels
- PyHub packaging
- MCP connectivity

Primary modules:
- `web/`
- `agent.py`
- `service_mode.py`
- `api_server.py`
- `interactive_cli.py`
- `core/channel_*`
- `core/pyhub_client.py`
- `core/mcp_hub.py`

---

## Repository Map

| Path | Role |
|---|---|
| `core/` | Runtime engine and domain logic |
| `web/` | FastAPI service and routers |
| `static/` | Console frontend assets |
| `tests/` | Behavioral and regression coverage |
| `workspace/` | Runtime data, skills, workflows, hosted apps |
| `docs/` | Human-facing architecture and operating model |

### Inside `core/`

Use this grouping when navigating code:

| Internal Domain | Purpose |
|---|---|
| Foundation | config, paths, errors, events, retries, model plumbing |
| Capabilities | tools, skills, knowledge, memory |
| Agents | agent runtime, delegation, capability profiles |
| Orchestration | workflows, scheduling, pause/resume |
| Governance | approvals, guardrails, sandbox policy |
| Surfaces | apps, channels, evaluation, bootstrap |

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

**Externally explain PyBot with five product concepts; internally organize code by domains.**
