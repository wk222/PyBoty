# PyBot Tree Refactor Plan

## Ground Rules

- This repo is a test/dev branch.
- Backward compatibility is explicitly out of scope.
- Large refactors, renames, file moves, and deletion of duplicate layers are allowed.
- Prefer explicit state objects (Canonical Views) over nested metadata.
- High-level branches must grow from lower-level trunk capabilities.

---

## Architecture Tree (4-Layer Dependency Hierarchy)

Every capability in PyBot grows from a shared root. Higher layers depend on
lower layers; never the reverse. This is codified in `core/modes/system_model.py`
via `ArchitecturalLayerDescriptor` and enforced by convention.

```text
PyBot
│
├─ Layer 0 — Root (Runtime Foundation)  ← THE TRUNK
│  ├─ core/systems/runtime/         config, paths, errors, event bus, model, bootstrap
│  ├─ core/systems/runtime/session/ Session Spine: events, compaction, kernel, recorder
│  └─ core/systems/context/         Workspace View: context engine, strategies, file view
│
├─ Layer 1 — First Branches (Core Systems)
│  ├─ core/systems/governance/      approvals, guardrails, policies, sandbox
│  ├─ core/systems/memory/          Markdown Garden, semantic memory, admin memory, scoring
│  ├─ core/systems/knowledge/       document pipeline, vector stores, embedding providers
│  ├─ core/systems/bus/             CapabilityBus + CapabilityRegistry (single discovery plane)
│  ├─ core/systems/middleware/      middleware chain, summarization, reasoning frame
│  ├─ core/systems/execution/       code execution sandbox, project scanner
│  ├─ core/systems/eval/            evaluation framework
│  └─ core/systems/integration/     channels, MCP, PyHub, external content
│
├─ Layer 2 — Second Branches (Asset Domains)
│  ├─ core/assets/tools/            tool creation, runtime, storage, templates, risk
│  ├─ core/assets/skills/           skill registry, marketplace, HTTP backends
│  ├─ core/assets/agents/           agent storage, subagent registry, delegation, isolation
│  └─ core/assets/workflows/        PyFlow DAG engine, scheduling, pause/resume, plugins
│
└─ Layer 3 — Crown (Product Modes)
   ├─ core/assets/apps/             App Matrix runtime, Brain planner, orchestration
   └─ core/modes/                   assistant / app_matrix / admin mode packs
```

**Dependency rule**: Layer N may only import from layers 0..N, never from N+1.

---

## Current Status (Solidification Wave)

### 1. Engine Unification & Swarm Robustness
- [x] **Spawn/Wait Primitives**: Native asynchronous subagent orchestration.
- [x] **Admin Swarm Unification**: Inject `spawn_subagent` and `wait_subagent` into `PersistentAdminRuntime`.   
- [x] **Error Bubbling**: Update `SubagentRegistry` to capture and return specific traceback/logs for failed runs.

### 2. Closed-Loop Knowledge
- [x] **Markdown Garden**: Hierarchical directory-based long-term memory.
- [x] **Recursive MD Summarization**: Automatic compression of large notes.
- [x] **Garden Suggestion Hook**: Automatically flag session compacts for garden inclusion.

### 3. Canonical View Consolidation

### 4. Advanced Self-Healing (Runtime)
- [x] **Runtime API Error Auto-Recovery**: Intercept APP API errors (e.g. 500s or timeouts) and automatically promote them into repair tasks for the Admin swarm.
- [x] **Rename Wave**: `session_artifacts` -> `session_runtime_view`.
- [x] **Factory Refactor**: Move `_build_live_view` logic into `ProjectedRuntimeView.from_runtime()`.
### 4. Legacy Failure Closure (Verified)
- [x] **Historical Failing Cases Rechecked**: Previously failing tests for `parse_manifest`, app verifier checks, bootstrap runtime workspace mock, and middleware-memory patch targets now pass.
- [x] **Cross-File Regression Check**: `test_security_extensibility`, `test_pybot_bootstrap`, `test_app_verifier`, and `test_lc_memory_middleware` pass together.

---

## Functional Maturity Backlog (Next Wave)

### A. Import Surface Convergence (Session/Workspace-first)
- [x] **Bypass Import Redirection Sweep**: Replace direct deep imports (e.g. `core.systems.context.context_engine`) with package facades (e.g. `core.systems.context`).
- [x] **Consumption Mapping Report**: Produce a migration list of remaining bypass imports grouped by package (`context`, `memory`, `session`, `tools`).
- [x] **Shim Deletion Readiness Gate**: Define and track "all consumers migrated" criteria before deleting `core/` root shim files. (Completed: 19 shim files deleted).

### B. Capability Plane Hardening
- [x] **Registry Contract Tests**: Add tests that cover `discover`, `issue_grant`, `list_grants`, and `invoke` in `CapabilityRegistryTool`.
- [x] **Quota/TTL Edge Cases**: Add negative and boundary tests for grant quota exhaustion and ttl expiry.      
- [x] **Operational Visibility**: Add structured logs/metrics for grant issuance and invocation failures.
- [x] **Advanced Tool Policy Pipeline**: Implement sequential deterministic stages (Path Traversal, Regex Argument Validation, Budget Quotas) for hard middleware governance over tools.
- [x] **Constraint Feedback Loop**: Integrate policy rejections with Reasoning Frame Middleware to force autonomous LLM self-critique and alternative strategy selection on boundary violations.
- [x] **Hierarchical Multi-Agent Governance**: Enforce strict monotonic capability constraints (child permissions <= parent permissions) with dynamic fine-grained policy overrides during delegation.
### C. Session Spine + Workspace Reliability
- [x] **Session Recorder/Runtime Invariants**: Add invariant checks for event ordering, compaction boundaries, and replay safety.
- [x] **Context Engine Budget Guards**: Add tests for token-budget clipping and deterministic head-tail behavior in long sessions.
- [x] **Runbook Docs**: Add a short troubleshooting runbook for session/context mismatch incidents.

### D. Workflow & Skill Execution Quality
- [x] **Workflow Resume Robustness**: Add failure-recovery tests for pause/resume + plugin-backed nodes.
- [x] **Skill Packaging Consistency**: Validate all skill descriptors through one schema validator entrypoint.
- [x] **E2E Smoke Matrix**: Define minimal smoke tests spanning tool -> skill -> workflow -> app path.

---

## Technical Debt / Cleanup
- [x] Move all session core files to `core/systems/runtime/session/`.
- [x] Purge all remaining `runtime_bookkeeping` and `artifacts` terminology.
- [x] Update `paper/pybot.tex` to reflect the architectural tree model.

---

## Future Work (Advanced Research)

1. **Autonomous Re-planning (LATS/MCTS)**: Implement CriticAgent-driven tree search.
2. **Visual Editor for App Matrix**: Integrate low-code UI for manual app adjustment.
3. **Distributed Subagent Hosting**: Remote container targets for isolation.

---

## Acceptance Targets

- [x] Canonical `ProjectedRuntimeView` is the single source of truth for prompt, routing, and hygiene.
- [x] Session split is finished and encapsulated.
- [x] Multi-Agent control plane features real-time `TeamMemory` sync.
- [x] AppManager supports closed-loop VLM-based iterative generation.
- [x] Dual-mode memory (Garden + Vector) is active and integrated into presets.
- [x] All core tests pass.
