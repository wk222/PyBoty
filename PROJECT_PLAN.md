# PyBot Maturity Plan

Last updated: 2026-04-02

## North Star

PyBot should mature into a clear three-mode system instead of a repo-shaped feature pile:

1. `人类助手模式` for general human collaboration
2. `应用矩阵模式` for cross-app orchestration and central scheduling
3. `全局管理员模式` for durable autonomous execution and capability creation

The common foundation underneath those modes should stay governed, testable, and easy to navigate.

## What “More Mature” Means Here

- Root modes have clear boundaries and public APIs
- APP orchestration is explicit, persisted, and inspectable
- Runtime data does not sprawl through the repo root without clear rules
- Packaging, naming, and docs tell one coherent story
- Quality gates expand steadily instead of depending on local memory

## Recently Completed

- [x] Completed the mainline import sweep: all flat `core.*` imports removed from `agent.py`, `web/app.py`, `web/state.py`, and every `web/routers/*` file; all now point directly to `core/assets/` or `core/systems/` canonical paths. Also fixed 5 silent import bugs in `core/__init__.py` (`AppMatrixRuntime`, `AppPackager`, `BatchProcessor`, `InsightVaultMiddleware`, `ReasoningFrameMiddleware` were referencing non-existent flat stubs) and migrated all 270+ `_EXPORTS` entries to canonical paths.
- [x] Extended the session spine into a real run timeline by feeding tool runs, delegated subagent runs, and background durable-task slices into `SessionRuntime`, and added budget-driven context compaction with durable compaction metadata
- [x] Refactored the session spine into the canonical run backbone by projecting gateway runs and workflow runs into the same timeline and wiring the web service's system agent to that shared runtime
- [x] Hardened the session spine with append-only session event logs, a restart-time resume scrubber for interrupted run state, layered compaction, and a typed memory policy for durable notes
- [x] Promoted the session spine toward a true canonical ledger by replaying from `events.jsonl`, adding explicit compaction boundaries, wiring middleware compaction back into the session spine, and extending typed memory taxonomy into durable recall/search surfaces
- [x] Finished the session-ledger cutover by removing snapshot fallback, exposing explicit session file-view APIs, and folding tool-heavy transcript plus file-view context into the same compaction coordinator
- [x] Strengthened the session spine again by making `sessions.json` a best-effort checkpoint/export only, adding compiled context artifacts with explicit invalidation, introducing a long-lived session kernel plus cache-safe sidechains, and exposing kernel/artifact/session-checkpoint APIs
- [x] Slimmed `agent.py` by auto-attaching mode-pack public APIs from `core/modes/api_surface.py` instead of hand-writing long Admin/APP Matrix dispatch wrappers
- [x] Added a persistent `SessionRuntime` backed by `sessions.json`, unified chat + gateway HTTP/WS sessions behind it, and exposed session list/detail plus summary/note mutation APIs
- [x] Simplified the web IA around `Chat / Governance / Ecosystem`, added an `Ecosystem` aggregation view, exposed current runtime mode in the top bar, and pushed asset-heavy pages behind grouped navigation instead of flat first-level nouns
- [x] Added post-publish rollout tracking and evaluation so capability-gap candidates can move from `published` to `rollout_active`, `resolved`, or `regressed` with durable observations and admin/API controls
- [x] Closed the first end-to-end telemetry synthesis loop by turning capability-gap candidates into draft assets, validating them, and publishing them through Admin / APP Matrix runtime + API surfaces
- [x] Strengthened APP-to-APP capability exchange with richer caller identity, provider policy enforcement, grant listing, and admin-facing service grant visibility
- [x] Added a persistent Admin capability-gap candidate pool plus promote-to-task controls so telemetry heuristics can become durable synthesis work items
- [x] Added app-to-app capability grants with quota/TTL, APP Matrix service discovery/invoke surfaces, and structured AdminWatcher capability-gap candidates that can seed autonomous synthesis work
- [x] Enriched capability-gap candidates with synthesis blueprints, rollout recommendations, provider-match hints, and an Admin/API detail surface
- [x] Exposed capability-gap management to APP Matrix mode so the orchestration brain can inspect and promote ecosystem gaps directly
- [x] Added draft materialization for capability-gap candidates so Admin/APP Matrix can spin up draft skills, tools, apps, and workflows before full publish automation
- [x] Started the autopoietic-capability track by unifying local bus discovery, skill packaging, and optional hub exchange behind a real `CapabilityRegistry` with runtime + Web/API surfaces
- [x] Shifted default config resolution toward runtime-home `config.json` while preserving legacy repo-root and compatibility override behavior for existing tests and local setups
- [x] Cut mainline runtime surfaces (`agent.py`, `api_server.py`, `web/state.py`, `pybot_bootstrap.py`) over to `core/assets/*` and `core/systems/*` imports instead of relying on flat `core.*` stubs
- [x] Reduced the most important old flat-import hotspots across agent/tool/app/workflow runtime modules and cleaned remaining UI auth prompts/branding from `PyBoty` to `PyBot`
- [x] Added a first node control plane with `node.invoke`, persistent pending-command queues, and `node.pending.pull/ack` over REST + gateway WS
- [x] Hardened gateway identity + continuity by adding optional paired-device tokens for WS auth and honoring `previous_response_id` to continue OpenResponses sessions on prior run context
- [x] Added real gateway run-level operator control with a persistent run registry, REST/WS run lookup/abort surfaces, session-level abort/inject endpoints, and OpenResponses lookup/cancel compatibility routes
- [x] Merged approvals, policy editing, and gateway pairing/channel-route visibility into a single governance-center control surface inspired by OpenClaw's operator UI
- [x] Expanded gateway compatibility again with node identity records, channel-route control surfaces, route-aware webhook dispatch, and an OpenResponses-style client tool-turn bridge
- [x] Expanded the gateway compatibility layer with a first WebSocket operator surface, persistent pairing approvals, live presence tracking, and a shared session-identity registry across REST + WS flows
- [x] Added a first gateway compatibility layer with `/v1/models`, `/v1/responses`, mode-aware session routing, operator-facing gateway session/channel/tool/approval surfaces, gateway auth config, and SSE streaming on top of the PyBot runtime
- [x] Turned the OpenClaw bridge from report-only into runtime integration by injecting `skills.entries.env/apiKey` into OpenClaw skill execution and importing PyBot-supported channel configs during OpenClaw import
- [x] Added an OpenClaw repo+config bridge so PyBot can import `openclaw.json`, register repo and `skills.load.extraDirs` as skill sources, and surface `skills.entries/channels` compatibility reports
- [x] Added a dedicated OpenClaw skill-source registration flow plus per-skill dependency diagnostics for missing bins / env / config
- [x] Hardened OpenClaw skill compatibility so OpenClaw repo roots can mount directly as skill sources, metadata/requirements are preserved, and `{baseDir}` placeholders resolve at read time
- [x] Landed Phase 4 of the OpenClaw-alignment track: tool policy pipeline, plugin lifecycle loader, SDK decorators, live tool/message hooks, and plugin admin surfaces
- [x] Landed Phase 3 of the OpenClaw-alignment track: subagent registry, lifecycle events, depth/concurrency limits, timeout cleanup, and first steer/abort controls
- [x] Landed Phase 2 of the OpenClaw-alignment track: vector memory backend matrix, richer embedding providers, hybrid/MMR/temporal retrieval, and runtime config wiring
- [x] Landed Phase 1 channel foundations for the OpenClaw-alignment track: upgraded webhook protocol, WeChat/WeCom adapters, config wiring, and first route coverage
- [x] Added an OpenClaw-alignment plan covering channels, vector memory, subagent lifecycle, and plugin/security pipeline work
- [x] Wrote a physical core reorganization plan and started Batch 0 with real `git mv + compatibility stub` runtime leaf-module migration
- [x] Made root-mode capabilities effective runtime switches with guarded public surfaces instead of prompt-only labels
- [x] Surfaced mode-pack registry metadata and current effective mode capabilities through the admin/system API
- [x] Added modular mode profiles so the three root modes behave like capability bundles instead of scattered `if root_mode == ...` checks
- [x] Expanded `agents/tools` asset entrypoints with service/runtime/creation surfaces and moved more control-plane tests and admin routes onto those packages
- [x] Lit up the first real `core/systems/*` entrypoints for runtime, memory, governance, and integration, and switched Web/runtime/test surfaces onto them
- [x] Added first real `agents/tools` asset entrypoints and routed the admin/control-plane surface through them
- [x] Submoduleized `apps` and `workflows` asset packages so mainline consumers can target `manager / runtime / planning / orchestration / scheduling / spec / execution` instead of one broad barrel
- [x] Started real asset-family migration by exposing `apps` and `workflows` through `core/assets/*` entrypoints and switching core/web consumers to those packages
- [x] Moved root-mode lifecycle, APP Brain operations, and mode factory helpers into `core/modes/` and kept `agent.py` as a thinner façade
- [x] Added a target package-layout model and migration skeleton for modes / assets / systems
- [x] Routed prompt boundaries, health summaries, and runtime startup summaries through the canonical system model
- [x] Added a System Map control-plane surface so the console can explain PyBot using the same canonical model as docs and runtime
- [x] Added a canonical executable system model so root modes, product concepts, supporting systems, and internal code domains come from one source
- [x] Added `应用矩阵模式` as a first-class root mode alongside assistant and ultimate
- [x] Added persistent admin memory with compression, summary, and runtime replanning
- [x] Added an APP Brain orchestration registry runtime that syncs real apps into orchestration topology
- [x] Added public APP Brain methods for topology sync, overview, bindings, and pipelines
- [x] Surfaced APP Brain topology through Web/API overview, sync, and node summary routes
- [x] Added durable APP node contract metadata for shared DB/schema/contracts across apps
- [x] Added durable approval-aware recovery for admin/app-matrix tasks after process restart
- [x] Split source root vs runtime root so default runtime state no longer has to live in the repo

## In Progress

- [ ] Turn the new `CapabilityRegistry` into the single discovery plane for apps, skills, workflows, and future zero-trust capability exchange with stronger caller identity, quota enforcement, and APP-to-APP execution contracts
- [ ] Extend the first telemetry synthesis close-loop from local draft/validate/publish/rollout into stronger automated remote publish, post-release verification, and rollback automation
- [ ] Harden Phase 1 of the OpenClaw-alignment track with richer channel message coverage, outbound auth flows, and operator-facing surfaces
- [ ] Keep converging docs, prompts, UI, and runtime summaries around the canonical system model
- [x] Cut all remaining flat `core.*` imports in `agent.py`, `web/app.py`, `web/state.py`, and all `web/routers/*` to canonical `core/assets/` and `core/systems/` paths — main entry points are now flat-import-free
- [x] Fixed 5 silent import bugs in `core/__init__.py` where `AppMatrixRuntime`, `AppPackager`, `BatchProcessor`, `InsightVaultMiddleware`, and `ReasoningFrameMiddleware` referenced non-existent flat stub modules; updated all 270+ `_EXPORTS` entries to canonical paths
- [ ] Push more contributor-facing and creator-facing surfaces to classify new abilities by root mode / product concept / supporting system before adding them
- [ ] Keep refining the APP Brain control plane so app-to-app collaboration is explicit and reusable
- [ ] Keep shrinking the "repo as runtime" feel by moving more state behind clear runtime services
- [ ] Continue the Claude-Code-alignment track by building on the new session spine and extending it toward memory taxonomy, context economics, and team-style agent organization
- [x] Standardize public branding around `PyBot`

## Workstreams

### 1. Product Surface

- [x] Land three explicit root modes
- [x] Turn the three root modes into modular capability profiles that can enable/disable durable loops and APP orchestration surfaces
- [x] Guard mode-specific public methods behind effective capability switches so future modes can be composed by profile instead of copy-pasted branching
- [x] Add concise usage examples for all three modes in `README.md`
- [x] Standardize public naming on `PyBot`
- [x] Remove the remaining old plugin/runtime compatibility identifiers from live surfaces

### 2. APP Brain Control Plane

- [x] Persist app orchestration topology in a dedicated registry
- [x] Sync generated apps into the APP Brain topology
- [x] Expose app-to-app bindings and orchestration pipelines through the public runtime
- [x] Add richer node metadata for shared DB/schema/contracts between apps
- [x] Add APP Brain-specific planning helpers that can propose topologies from business goals
- [x] Surface APP Brain topology through Web/API views, not only Python/runtime methods
- [x] Add APP-to-APP service discovery, grant issuance, and invocation through the unified capability registry
- [ ] Add stronger zero-trust service identity and per-provider policy/quota controls for APP-to-APP calls

### 3. Durable Runtime

- [x] Add compressed admin memory with rolling summaries
- [x] Add runtime replanning hooks for persistent goals
- [x] Add durable approval-aware recovery after full process restart
- [x] Start converting telemetry reports into structured capability-gap candidates for Admin autonomous synthesis loops
- [x] Persist capability-gap candidates and allow promoting them into durable synthesis tasks
- [ ] Separate long-term memory, working memory, and orchestration memory more clearly
- [x] Add append-only session events and restart-time resume repair instead of relying only on mutable session snapshots

### 4. Repo Boundaries

- [x] Decide which runtime directories should move outside the repo root by default
- [ ] Separate sample/demo state from real runtime state
- [ ] Review whether embedded upstream reference trees (`deepagents`, `langchain`) should be moved to `references/` or external links

### 5. Packaging And Naming

- [ ] Resolve the remaining legacy branding residue in paper artifacts and repo naming
- [ ] Audit public module/factory names for consistency

### 6. Quality Gates

- [ ] Keep expanding targeted tests around APP Brain and durable modes
- [ ] Revisit full-suite runtime and split slow tests if needed
- [ ] Continue shrinking any remaining large facade modules into thinner service layers

### 8. OpenClaw Alignment

- [x] Phase 1 foundation: channel protocol upgrade with WeChat / WeCom adapters
- [ ] Phase 1 hardening: richer message coverage, auth/refresh polish, and channel operations surface
- [x] Phase 2: vector memory backend matrix and richer retrieval strategies
- [x] Phase 3: subagent registry, lifecycle events, steering, and cleanup
- [x] Phase 4: tool policy pipeline and plugin lifecycle / SDK surface
- [x] Cross-cutting follow-up: first-class OpenClaw skill source/format compatibility
- [x] Cross-cutting follow-up: OpenClaw repo/config bridge for `skills.entries`, `skills.load.extraDirs`, and channel compatibility reporting
- [x] Cross-cutting follow-up: first gateway compatibility layer for `/v1/models` and `/v1/responses`
- [x] Cross-cutting follow-up: gateway WS/pairing/channel-routing hardening, richer node identity, and run-level operator controls
- [ ] Cross-cutting follow-up: stronger gateway WS auth/device identity, richer operator controls, and deeper OpenResponses parity

### 7. Code Organization

- [x] Define the target package layout as `modes / assets / systems / surfaces`
- [x] Add migration skeleton packages under `core/`
- [x] Start the physical move phase with Batch 0 runtime leaf modules (`errors / path_utils / retry_policy / version / yaml_config`)
- [x] Move root-mode wiring out of `agent.py` into `core/modes/`
- [x] Start migrating asset families into `core/assets/*`
- [x] Split `apps` and `workflows` asset packages into clearer submodules (`manager / runtime / planning / orchestration / scheduling / spec / execution`)
- [ ] Continue migrating `apps` and `workflows` implementation modules behind those submodule entrypoints
- [x] Start migrating `agents` and `tools` into `core/assets/*`
- [x] Continue migrating more agent/tool runtime surfaces behind those new asset entrypoints
- [ ] Keep moving remaining agent/tool helpers and tests behind those asset entrypoints
- [x] Start migrating cross-cutting planes into `core/systems/*`
- [ ] Continue migrating more runtime / memory / governance / integration surfaces behind those new system entrypoints
- [x] Sweep all mainline consumers (`agent.py`, `web/app.py`, `web/state.py`, `web/routers/*`) off flat `core.*` stubs onto canonical `core/assets/` and `core/systems/` paths
- [x] Align `core/__init__.py` `_EXPORTS` map to use only canonical paths — no flat-stub indirection in the public API surface

## Suggested Build Order

1. APP Brain topology and planning surfaces
2. Packaging and naming cleanup
3. Runtime/state boundary cleanup
4. Web/API visibility for APP Brain
5. Full durable recovery improvements

## Current Construction Notes

This file is meant to stay live. Each round should do two things:

1. Update this plan
2. Implement at least one concrete slice from the highest-priority workstream

See [docs/CORE_REORGANIZATION_PLAN.md](/c:/Users/wzx/Documents/GitHub/PyBoty/docs/CORE_REORGANIZATION_PLAN.md) for the physical move strategy, batch order, and migration rules.
See [docs/OPENCLAW_ALIGNMENT_PLAN.md](/c:/Users/wzx/Documents/GitHub/PyBoty/docs/OPENCLAW_ALIGNMENT_PLAN.md) for the four-phase maturity track inspired by OpenClaw.
See [docs/CLAUDECODE_ALIGNMENT_PLAN.md](/c:/Users/wzx/Documents/GitHub/PyBoty/docs/CLAUDECODE_ALIGNMENT_PLAN.md) for the session-spine, memory-taxonomy, and team-organization plan inspired by Claude Code.
See [docs/SESSION_SPINE_PHASE2_PLAN.md](/c:/Users/wzx/Documents/GitHub/PyBoty/docs/SESSION_SPINE_PHASE2_PLAN.md) for the current session-ledger, compaction, and context-artifact build order.
