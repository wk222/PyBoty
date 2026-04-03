# Claude Code Alignment Plan

Last updated: 2026-04-01

## Latest Progress

- [x] Added a first persistent `SessionRuntime` backed by `sessions.json`
- [x] Unified chat conversations and gateway HTTP/WS sessions behind one shared session spine
- [x] Exposed session list/detail APIs plus lightweight session summary / note mutation endpoints
- [x] Moved gateway runs and workflow runs into the canonical session spine instead of leaving them as route-local state
- [x] Added explicit context-budget management and compaction policy on top of the session spine
- [x] Added append-only session event logging plus resume scrubbing for interrupted `gateway/tool/workflow/subagent/durable-task` state on restart
- [x] Replaced blunt session trimming with layered compaction: microcompaction, session-notebook compaction, then full budget trimming
- [x] Introduced a typed session-memory policy for `session_note / user / feedback / project / reference` writes with durable-memory guards
- [x] Promoted `events.jsonl` toward the canonical session ledger by replaying sessions from append-only events first and treating `sessions.json` as a checkpoint/cache
- [x] Added explicit compaction boundaries plus middleware-to-session compaction callbacks so conversation summarization and session compaction land on one spine
- [x] Extended typed-memory taxonomy into durable semantic memory + memory tools so save/search can use the same `user / feedback / project / reference` language
- [x] Removed snapshot fallback from session restore so `events.jsonl` is the actual ledger, then folded tool-heavy transcript and file-view context into the same session compaction coordinator
- [x] Turned `sessions.json` into a best-effort export, added compiled session artifacts with explicit invalidation, introduced a long-lived session kernel, and started using cache-safe sidechains for memory extraction and compaction work

## Why Study Claude Code

Claude Code is worth learning from not because it has more features than PyBot, but because it hides complexity behind a much harder interaction spine:

- one primary work session
- one execution engine
- one formal tool protocol
- one persistent recovery model
- one disciplined multi-agent organization model

PyBot already has deeper ecosystem, governance, capability-marketplace, and app-matrix ideas than Claude Code. What it still lacks is the same degree of compression around the user's primary workflow.

This plan is about importing that compression and runtime discipline without throwing away PyBot's three-mode system.

## The Main Lessons From Claude Code

### 1. Keep one hard execution spine

Claude Code consistently routes work through a small set of stable anchors:

- startup / assembly
- command surface
- tool surface
- QueryEngine session loop

PyBot should move closer to:

- one session engine
- one run timeline
- one clear distinction between user commands, model actions, and durable background tasks

### 2. Treat tools as execution protocol, not feature list

Claude Code's tools are not just functions. A tool call carries:

- permissions
- context
- progress semantics
- transcript semantics
- recoverability

PyBot already moved in this direction. The next step is to make more of the runtime go through the same protocol consistently.

### 3. Separate memory into distinct jobs

Claude Code clearly separates:

- workspace rules memory
- session memory
- agent memory
- compaction

PyBot already has semantic memory, executive memory, working state, and capability-gap state, but they still feel too blended at runtime.

### 4. Optimize context economics explicitly

Claude Code does not treat token growth as accidental. It has:

- read-state/cache mechanics
- micro-compaction
- session-memory-backed compaction
- projected history / replay ideas

PyBot needs a first-class context-budget manager instead of only summaries and compression helpers.

### 5. Organize multi-agent work like a team, not a universe

Claude Code's durable insight is not "many agents". It is:

- coordinator / lead
- worker
- verifier
- fork child
- approval bridge

PyBot should keep its richer mode model, but inside each mode it should adopt a more disciplined organization model.

### 6. Make runtime extensibility formal

Claude Code's hooks are runtime extension points, not decorative plugins. That is valuable for PyBot because it already has:

- plugins
- policy pipeline
- governance
- skills

but still needs a more unified runtime-hook story.

## What PyBot Should Not Copy

- Do not copy Claude Code's CLI-first product identity. PyBot should remain a multi-surface system.
- Do not collapse PyBot's three-mode architecture into a single assistant persona.
- Do not imitate every Anthropic-specific product shell, flag system, or hosted-service assumption.
- Do not turn PyBot into a generic "many agent sandbox". Claude Code is useful precisely because it is disciplined.

## Current Gap Assessment

### Already Strong In PyBot

- mode packs and mode profiles
- governance center and approvals
- capability registry and app-to-app service exchange
- durable admin/app-matrix runtimes
- gateway/operator surfaces
- OpenClaw-style ecosystem work

### Still Weaker Than Claude Code

- one obvious session spine
- command surface as a first-class layer
- context economics and compaction discipline
- memory taxonomy clarity
- coordinator/worker/verifier organization defaults
- unified task/run/resume semantics across chat, background, and subagents
- runtime hooks that feel like one system instead of several adjacent systems

## Four Implementation Phases

### Phase 1. Session Spine And Interaction Compression

North-star outcome:
PyBot feels like one persistent work session with mode profiles, not a constellation of parallel products.

Concrete work:

- Create a `PyBotSessionEngine` as the canonical work-session loop above chat, tool execution, background runs, and resume.
- Introduce a small command surface for high-frequency actions such as mode switch, run inspect, approval jump, ecosystem open, and memory inspect.
- Standardize one run timeline model for chat turns, tool runs, workflow runs, and delegated subagent runs.
- Make the three modes behave like profiles over the same session spine instead of looking like three separate systems.
- Keep `Chat / Governance / Ecosystem` as the only top-level interaction surfaces in UI, docs, and prompt summaries.

Why first:
This is the highest-leverage simplification. Without it, every new capability continues to increase front-end and mental complexity.

### Phase 2. Memory Taxonomy And Context Economics

North-star outcome:
PyBot can run long-lived work without context sprawl, and each memory layer has one job.

Concrete work:

- Split memory into four explicit buckets:
  - workspace rules memory
  - session working memory
  - agent/profile memory
  - durable ecosystem/admin memory
- Add a context-budget manager that tracks token pressure and decides when to:
  - micro-trim high-cost tool outputs
  - compact history
  - project or replay only the needed slice
  - refresh from session memory instead of replaying raw history
- Add a read-state / file-context cache so repeated file work does not keep bloating prompts.
- Introduce durable compaction boundaries so resumed sessions do not re-ingest the whole past.

Why second:
PyBot already wants to be durable and autopoietic. Without memory taxonomy and context economics, that ambition will keep colliding with token ceilings.

### Phase 3. Team-Style Agent Organization

North-star outcome:
PyBot's agent runtime behaves like a disciplined engineering team, not an open-ended multi-agent cloud.

Concrete work:

- Add first-class internal roles:
  - coordinator
  - worker
  - verifier
  - fork-child / inherited-context worker
- Distinguish inherited-context delegation from fresh-role delegation, similar to Claude Code's fork vs fresh subagent economics.
- Standardize which roles may:
  - synthesize
  - implement
  - verify
  - approve
  - ask for escalation
- Make background/resume semantics uniform for:
  - delegated agents
  - admin synthesis tasks
  - app-matrix orchestrations
  - workflow-assisted agent runs

Why third:
PyBot already has lots of delegation power. This phase converts that power into organizational clarity.

### Phase 4. Runtime Extensibility And Managed Policy

North-star outcome:
PyBot can change behavior through formal runtime extension points instead of adding more special-case logic into the core loop.

Concrete work:

- Unify plugin hooks, tool policy pipeline, governance checks, and skill-time hooks behind one runtime hook plane.
- Add session-scoped hooks so a run or mode profile can temporarily alter behavior without changing global config.
- Separate managed policy, project policy, session policy, and local override more clearly.
- Promote command hooks and operator hooks into first-class extension surfaces.

Why fourth:
This phase keeps the core small while still letting the ecosystem grow.

## Suggested Execution Order

1. Build `PyBotSessionEngine` and a unified run timeline.
2. Add explicit memory taxonomy plus a context-budget manager.
3. Formalize coordinator / worker / verifier / inherited-context worker roles.
4. Unify runtime hooks and managed policy layers.

## Immediate Next Slices

These are the next concrete slices that should be implemented first:

1. `Session Spine`
   - [x] add a canonical session-engine module
   - [x] route chat turns, gateway runs, and background task continuation through it
   - [x] expose a first session registry/detail API
   - [x] expose kernel / artifact / sidechain surfaces on top of the session ledger

2. `Mode As Profile`
   - [x] make current mode/profile visible and switchable everywhere through the same session model
   - [x] reduce remaining mode-specific special cases in public surfaces (identity prompt + startup summary now driven by ModeProfile fields)

3. `Memory Taxonomy`
   - [x] introduce explicit workspace/session/agent/admin memory descriptors (LAYER_DESCRIPTORS + type→layer matrix in memory_taxonomy.py)
   - [x] stop treating all summaries as one class of memory (validate_layer_for_type, default_layer_for_type, session_memory_policy uses taxonomy)

4. `Context Budget Manager`
   - [x] add cost accounting and micro-trim rules for expensive tool outputs (record_tool_output, apply_micro_trim, top_expensive_tools, pressure-aware _TRIM_CHARS_BY_PRESSURE)
   - [x] establish compaction boundaries for resumed sessions
   - [x] fold tool-heavy transcript and file-view context into the same coordinator

5. `Compiled Artifacts → Prompt Assembly`
   - [x] add render_artifact_context() to session_artifacts.py — formats working_summary, context_notes, typed_memory, notebook summaries, file views, prompt_injection into prompt sections
   - [x] add session_artifacts param to build_runtime_prompt_sections() in prompts.py
   - [x] add artifacts_provider: Callable[[], dict | None] param to build_root_langchain_middleware() in agent_middleware_factory.py
   - [x] add get_compiled_artifacts(session_key) to SessionRuntime so callers get a thread-safe compiled snapshot
   - [x] wire artifacts_provider closure in create_root_agent() via session_runtime + thread_id — every model call now injects live session spine context into the prompt

6. `Unified Tool Inventory`
   - [x] create `UnifiedToolInfo` dataclass — flat, source-agnostic view of any tool (direct or skill-backed)
   - [x] create `UnifiedAssetInventory` — single discovery/build layer wrapping ToolStorage + SkillRegistry
         - `list_all()` merges both sources; skill tools carry `layer="skill_tool"` + `source="skill:{name}"`
         - `get(name)` / `find(query, layer, tags)` / `enabled_names()`
         - `build_langchain_tools(names=None)` delegates to the right runtime (tool_creator vs skill_tool_resolver)
   - [x] wire into `CapabilityRegistry.refresh_local_index()` so bus accepts `unified_inventory=` param
   - [x] add `skill_registry` param to `DynamicToolInventory` so `list_dynamic_tools()` includes enabled skill tools

7. `Agent Role Taxonomy`
   - [x] add `AgentRole` enum (coordinator / worker / verifier / fork_child) to agent_role_policy.py
   - [x] create `agent_role_policy.py` — per-role defaults for autonomy, tool access, approval thresholds
   - [x] update `AgentDefinition` with `team_role: str = "worker"` (distinct from persona `role` field)
   - [x] apply role policy in `build_agent_tool_inventory` (role_policy key) and `build_effective_profiles()`

## Success Criteria

This Claude Code alignment is working when:

- PyBot feels simpler to use despite having more capabilities.
- A user can explain PyBot's runtime around one main work session.
- Long sessions degrade gracefully instead of feeling increasingly heavy.
- Subagents feel like organized teammates, not a noisy swarm.
- New capabilities enter through the ecosystem without expanding the top-level mental model.
